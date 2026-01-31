import os
import threading
import time
import json
import asyncio
import aiosqlite
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from collections import deque
from typing import Deque, Dict, Any, Optional
from database import DBHandler, resource_path
from event_logger import (
    EVENT_BROADCASTER,
    build_log_callback,
    create_job_id,
    job_status_event,
    log_event,
    set_main_loop,
)
from browser_launcher import (
    BrowserLaunchError,
    get_default_browser_profile,
    merge_browser_profiles,
    normalize_browser_profile,
)
from messages_fa import RESPONSES, LOG_MESSAGES, get_message

# ایمپورت ربات‌ها
from bot_register import RegistrationBot
from bot_select import BankSelectionBot
from bot_status import StatusBot
from captcha_service import CaptchaService, load_captcha_resources

app = FastAPI()

logger = logging.getLogger("mahanbot")
if not logging.getLogger().handlers:
    logging.basicConfig(level=os.getenv("MAHANBOT_LOG_LEVEL", "INFO"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Restrict origins here when you know the allowed domains.
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

DBHandler.init_db()
MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None
CAPTCHA_SERVICE: Optional[CaptchaService] = None
JOB_QUEUE: Optional["JobQueue"] = None

def require_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-KEY")):
    expected_key = os.getenv("MAHANBOT_API_KEY")
    if not expected_key:
        if not getattr(require_api_key, "_warned", False):
            logger.warning(get_message("log", "api_key_not_configured", LOG_MESSAGES["api_key_not_configured"]))
            require_api_key._warned = True
        return True
    if x_api_key != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=get_message("responses", "api_key_invalid", RESPONSES["api_key_invalid"]),
        )
    return True


@app.middleware("http")
async def log_unhandled_exceptions(request, call_next):
    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        raise

@app.on_event("startup")
async def startup_event():
    """پاکسازی وضعیت‌های گیر کرده هنگام شروع برنامه"""
    global MAIN_LOOP, CAPTCHA_SERVICE, JOB_QUEUE
    MAIN_LOOP = asyncio.get_running_loop()
    set_main_loop(MAIN_LOOP)
    model, ocr_firewall = load_captcha_resources()
    CAPTCHA_SERVICE = CaptchaService(model=model, ocr_firewall=ocr_firewall)
    JOB_QUEUE = JobQueue(max_concurrency=get_max_concurrency())
    JOB_QUEUE.start()
    try:
        async with aiosqlite.connect(resource_path('cbi_ultimate.db')) as conn:
            await conn.execute(
                "UPDATE applicants SET status='Stopped' WHERE status IN ('Running', 'Selecting', 'Registering', 'Waiting SMS')"
            )
            await conn.commit()
    except Exception as e:
        print(f"Error checking DB on startup: {e}")

# --- Models ---
class ApplicantModel(BaseModel):
    id: Optional[int] = None
    full_name: str
    national_id: str
    data: Dict[str, Any]

class BankSelectRequest(BaseModel):
    nid: str
    loan_type: Optional[str] = None

class SMSRequest(BaseModel):
    nid: str
    code: str

def get_captcha_service():
    global CAPTCHA_SERVICE
    if CAPTCHA_SERVICE is None:
        CAPTCHA_SERVICE = CaptchaService()
    return CAPTCHA_SERVICE

def require_job_queue() -> "JobQueue":
    if not JOB_QUEUE:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Job queue not ready")
    return JOB_QUEUE

class JobStartRequest(BaseModel):
    bot_name: str
    nid: str
    loan_type: Optional[str] = None
    browser_profile_override: Optional[Dict[str, Any]] = None


class BrowserProfileRequest(BaseModel):
    browser: Optional[str] = None
    headless: Optional[bool] = None
    slow_mo_ms: Optional[int] = None
    viewport: Optional[Dict[str, int]] = None
    user_data_dir: Optional[str] = None
    proxy: Optional[str] = None
    timeout_ms: Optional[int] = None


def load_browser_profile_settings() -> Dict[str, Any]:
    stored = DBHandler.get_setting("browser_profile") or {}
    try:
        return merge_browser_profiles(get_default_browser_profile(), stored)
    except BrowserLaunchError:
        return get_default_browser_profile()


def save_browser_profile_settings(profile: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_browser_profile(profile)
    DBHandler.update_setting("browser_profile", normalized)
    return normalized


def get_max_concurrency() -> int:
    try:
        value = int(os.getenv("MAHANBOT_MAX_CONCURRENCY", "2"))
    except ValueError:
        value = 2
    return max(1, value)


class Job:
    def __init__(self, bot_name: str, nid: str, payload: Dict[str, Any]):
        self.id = create_job_id()
        self.bot_name = bot_name
        self.nid = nid
        self.payload = payload
        self.status = "queued"
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.stop_event = threading.Event()
        self.cancel_requested = False
        self.error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "bot_name": self.bot_name,
            "nid": self.nid,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "payload": self.payload,
            "error": self.error,
        }


class JobQueue:
    def __init__(self, max_concurrency: int):
        self._max_concurrency = max_concurrency
        self._jobs: Dict[str, Job] = {}
        self._queue: Deque[str] = deque()
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._workers: list[threading.Thread] = []

    def start(self) -> None:
        for index in range(self._max_concurrency):
            worker = threading.Thread(target=self._worker_loop, args=(index,), daemon=True)
            worker.start()
            self._workers.append(worker)

    def enqueue(self, bot_name: str, nid: str, payload: Dict[str, Any]) -> Job:
        with self._condition:
            for job in self._jobs.values():
                if job.bot_name == bot_name and job.nid == nid and job.status in {"queued", "running", "cancelling"}:
                    raise ValueError("duplicate")
            job = Job(bot_name=bot_name, nid=nid, payload=payload)
            self._jobs[job.id] = job
            self._queue.append(job.id)
            self._condition.notify()
        job_status_event(nid, job.id, "queued")
        return job

    def cancel(self, job_id: str) -> Optional[Job]:
        with self._condition:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = time.time()
                try:
                    self._queue.remove(job_id)
                except ValueError:
                    pass
                job_status_event(job.nid, job.id, "cancelled")
                log_event(job.nid, job.id, "Job canceled", "warning")
                return job
            if job.status in {"running", "cancelling"}:
                job.cancel_requested = True
                job.stop_event.set()
                if job.status != "cancelling":
                    job.status = "cancelling"
                    job_status_event(job.nid, job.id, "cancelling")
                    log_event(job.nid, job.id, "Job cancel requested", "warning")
                return job
            return job

    def cancel_by_nid(self, nid: str) -> int:
        cancelled = 0
        for job in self.list_jobs():
            if job["nid"] == nid and job["status"] in {"queued", "running", "cancelling"}:
                if self.cancel(job["id"]):
                    cancelled += 1
        return cancelled

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[Dict[str, Any]]:
        with self._lock:
            return [job.to_dict() for job in self._jobs.values()]

    def is_nid_active(self, nid: str) -> bool:
        with self._lock:
            return any(
                job.nid == nid and job.status in {"queued", "running", "cancelling"}
                for job in self._jobs.values()
            )

    def _worker_loop(self, worker_id: int) -> None:
        while True:
            with self._condition:
                while not self._queue:
                    self._condition.wait()
                job_id = self._queue.popleft()
                job = self._jobs.get(job_id)
                if not job or job.status != "queued":
                    continue
                if job.stop_event.is_set() or job.cancel_requested:
                    job.status = "cancelled"
                    job.finished_at = time.time()
                    job_status_event(job.nid, job.id, "cancelled")
                    continue
                job.status = "running"
                job.started_at = time.time()
            job_status_event(job.nid, job.id, "running")
            log_event(job.nid, job.id, f"Job started ({job.bot_name})", "info")
            outcome_status = "stopped"
            try:
                self._run_job(job)
                if job.cancel_requested:
                    outcome_status = "cancelled"
            except Exception as e:
                job.error = str(e)
                log_event(job.nid, job.id, f"خطا: {e}", "error")
                outcome_status = "failed"
            finally:
                with self._lock:
                    job.status = outcome_status
                    job.finished_at = time.time()
                job_status_event(job.nid, job.id, outcome_status)
                log_event(job.nid, job.id, f"Job {outcome_status}", "info")

    def _run_job(self, job: Job) -> None:
        settings = DBHandler.get_config()
        base_profile = load_browser_profile_settings()
        override = job.payload.get("browser_profile_override")
        try:
            browser_profile = merge_browser_profiles(base_profile, override)
        except BrowserLaunchError as exc:
            raise ValueError(exc.message)
        captcha_service = get_captcha_service()
        log_callback = build_log_callback(job.id)
        if job.bot_name == "register":
            bot = RegistrationBot(
                job.nid,
                settings,
                log_callback=log_callback,
                captcha_service=captcha_service,
                browser_profile=browser_profile,
            )
            bot.run(job.stop_event)
            log_event(job.nid, job.id, "ربات متوقف شد", "stopped")
        elif job.bot_name == "select":
            loan_type = job.payload.get("loan_type")
            bot = BankSelectionBot(
                job.nid,
                settings,
                log_callback=log_callback,
                captcha_service=captcha_service,
                browser_profile=browser_profile,
            )
            bot.run(job.stop_event, loan_type)
            log_event(job.nid, job.id, "ربات متوقف شد", "stopped")
        elif job.bot_name == "status":
            bot = StatusBot(
                job.nid,
                settings,
                log_callback=log_callback,
                captcha_service=captcha_service,
                browser_profile=browser_profile,
            )
            bot.run(job.stop_event)
            log_event(job.nid, job.id, "عملیات پایان یافت", "stopped")
        else:
            raise ValueError("Unsupported bot name")

# --- Routes ---
@app.post("/jobs/start")
def start_job(req: JobStartRequest, _: bool = Depends(require_api_key)):
    queue = require_job_queue()
    bot_name = req.bot_name.lower()
    if bot_name not in {"register", "select", "status"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported bot name")
    if bot_name == "select" and not req.loan_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=get_message("responses", "loan_type_required", RESPONSES["loan_type_required"]),
        )
    if bot_name == "status":
        user = DBHandler.get_applicant(req.nid)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=get_message("responses", "user_not_found", RESPONSES["user_not_found"]),
            )
        try:
            raw_data = user["data"]
            if isinstance(raw_data, str):
                data = json.loads(raw_data)
            elif isinstance(raw_data, dict):
                data = raw_data
            else:
                data = {}
        except Exception as exc:
            logger.warning("Invalid user data for %s: %s", req.nid, exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=get_message("responses", "invalid_data", RESPONSES["invalid_data"]),
            )
        if not data.get('tracking_code'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=get_message("responses", "tracking_code_missing", RESPONSES["tracking_code_missing"]),
            )
    payload: Dict[str, Any] = {}
    if req.loan_type:
        payload["loan_type"] = req.loan_type
    if req.browser_profile_override:
        try:
            payload["browser_profile_override"] = normalize_browser_profile(req.browser_profile_override)
        except BrowserLaunchError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.to_dict())
    try:
        job = queue.enqueue(bot_name, req.nid, payload)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job already queued or running")
    return {"status": "queued", "job": job.to_dict()}

@app.post("/jobs/cancel/{job_id}")
def cancel_job(job_id: str, _: bool = Depends(require_api_key)):
    queue = require_job_queue()
    job = queue.cancel(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return {"status": job.status, "job": job.to_dict()}

@app.get("/jobs")
def list_jobs(_: bool = Depends(require_api_key)):
    queue = require_job_queue()
    jobs = sorted(queue.list_jobs(), key=lambda item: item["created_at"], reverse=True)
    return {"jobs": jobs}

@app.get("/jobs/{job_id}")
def get_job(job_id: str, _: bool = Depends(require_api_key)):
    queue = require_job_queue()
    job = queue.get(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return {"job": job.to_dict()}

@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())
    return HTMLResponse("Error: index.html not found in static folder")

@app.get("/applicants")
def get_applicants():
    apps = DBHandler.get_all_applicants()
    for app_data in apps:
        if JOB_QUEUE:
            app_data['is_active'] = JOB_QUEUE.is_nid_active(app_data['national_id'])
        else:
            app_data['is_active'] = False
    return apps

@app.post("/applicants")
async def save_applicant(req: ApplicantModel):
    try:
        d_str = json.dumps(req.data)
        async with aiosqlite.connect(resource_path('cbi_ultimate.db')) as conn:
            if req.id:
                await conn.execute(
                    "UPDATE applicants SET full_name=?, national_id=?, data=? WHERE id=?",
                    (req.full_name, req.national_id, d_str, req.id),
                )
            else:
                await conn.execute(
                    "INSERT INTO applicants (full_name, national_id, data) VALUES (?, ?, ?)",
                    (req.full_name, req.national_id, d_str),
                )
            await conn.commit()
        return {"status": "ok"}
    except Exception as e: return {"status": "error", "msg": str(e)}

@app.delete("/applicants/{nid}")
async def delete_applicant(nid: str, _: bool = Depends(require_api_key)):
    if JOB_QUEUE:
        JOB_QUEUE.cancel_by_nid(nid)
    async with aiosqlite.connect(resource_path('cbi_ultimate.db')) as conn:
        await conn.execute("DELETE FROM applicants WHERE national_id=?", (nid,))
        await conn.commit()
    return {"status": "deleted"}

@app.post("/receive_sms")
def rec_sms(req: SMSRequest):
    if DBHandler.save_otp(req.nid, req.code):
        log_event(req.nid, None, f"پیامک: {req.code}", "success")
        return {"status": "ok"}
    return {"status": "error"}

@app.get("/settings")
def get_settings():
    return DBHandler.get_config()

@app.post("/settings")
def update_settings(payload: Dict[str, Any]):
    current = DBHandler.get_config()
    current.update(payload)
    DBHandler.update_config(current)
    return {"status": "ok", "settings": current}

@app.get("/settings/browser-profile")
def get_browser_profile():
    return load_browser_profile_settings()

@app.put("/settings/browser-profile")
def update_browser_profile(req: BrowserProfileRequest):
    data = req.dict(exclude_unset=True)
    try:
        merged = merge_browser_profiles(load_browser_profile_settings(), data)
    except BrowserLaunchError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.to_dict())
    saved = save_browser_profile_settings(merged)
    return saved

@app.post("/browser/test-launch")
def test_browser_launch(req: Optional[BrowserProfileRequest] = None):
    profile = load_browser_profile_settings()
    if req:
        override = req.dict(exclude_unset=True)
        try:
            profile = merge_browser_profiles(profile, override)
        except BrowserLaunchError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.to_dict())
    from browser_launcher import close_browser, ensure_allowed_url, launch_browser

    target_url = "http://127.0.0.1:8000/static/test.html"
    try:
        ensure_allowed_url(target_url)
    except BrowserLaunchError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.to_dict())
    playwright = None
    browser = None
    context = None
    page = None
    try:
        playwright, browser, context, page = launch_browser(profile)
        page.goto(target_url, wait_until="load")
        return {"status": "ok"}
    except BrowserLaunchError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.to_dict())
    finally:
        close_browser(playwright, browser, context, page)

@app.post("/bot/start-register/{nid}")
def start_reg(nid: str, _: bool = Depends(require_api_key)):
    if not JOB_QUEUE:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Job queue not ready")
    try:
        job = JOB_QUEUE.enqueue("register", nid, {})
    except ValueError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job already queued or running")
    return {"status": "queued", "job_id": job.id}

@app.post("/bot/start-select")
def start_sel(req: BankSelectRequest, _: bool = Depends(require_api_key)):
    nid = req.nid
    if not JOB_QUEUE:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=get_message("responses", "job_queue_not_ready", RESPONSES["job_queue_not_ready"]),
        )
    try:
        loan_type = req.loan_type or "rbtnNaghdi"
        job = JOB_QUEUE.enqueue("select", nid, {"loan_type": loan_type})
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=get_message("responses", "job_already_running", RESPONSES["job_already_running"]),
        )
    return {"status": "queued", "job_id": job.id}

@app.post("/bot/stop/{nid}")
def stop_bot(nid: str, _: bool = Depends(require_api_key)):
    if JOB_QUEUE:
        cancelled = JOB_QUEUE.cancel_by_nid(nid)
        if cancelled:
            log_event(nid, None, "توقف...", "stopping")
        else:
            DBHandler.update_status(nid, "Stopped", "Force Stop")
    else:
        DBHandler.update_status(nid, "Stopped", "Force Stop")
    return {"status": "stopped"}

@app.post("/bot/action/view-status/{nid}")
def action_view_status(nid: str, _: bool = Depends(require_api_key)):
    if not JOB_QUEUE:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=get_message("responses", "job_queue_not_ready", RESPONSES["job_queue_not_ready"]),
        )
    user = DBHandler.get_applicant(nid)
    if not user:
        return {"status": "error", "message": get_message("responses", "user_not_found", RESPONSES["user_not_found"])}
    
    try:
        raw_data = user["data"]
        if isinstance(raw_data, str):
            data = json.loads(raw_data)
        elif isinstance(raw_data, dict):
            data = raw_data
        else:
            data = {}
        if not data.get('tracking_code'):
            return {
                "status": "error",
                "message": get_message("responses", "tracking_code_missing", RESPONSES["tracking_code_missing"]),
            }
    except Exception as exc:
        logger.warning("Invalid user data for %s: %s", nid, exc)
        return {"status": "error", "message": get_message("responses", "invalid_data", RESPONSES["invalid_data"])}
    try:
        job = JOB_QUEUE.enqueue("status", nid, {})
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=get_message("responses", "job_already_running", RESPONSES["job_already_running"]),
        )
    return {
        "status": "queued",
        "job_id": job.id,
        "message": get_message("responses", "status_job_queued", RESPONSES["status_job_queued"]),
    }

@app.post("/bot/action/delete-request/{nid}")
def action_delete_request(nid: str, _: bool = Depends(require_api_key)):
    log_event(nid, None, "حذف درخواست (هنوز پیاده‌سازی نشده)", "warning")
    return {"status": "ok"}

@app.post("/bot/action/recover-code/{nid}")
def action_recover_code(nid: str):
    log_event(nid, None, "بازیابی کد (هنوز پیاده‌سازی نشده)", "info")
    return {"status": "ok"}

# --- WebSocket Endpoint (اصلاح شده) ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await EVENT_BROADCASTER.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        EVENT_BROADCASTER.disconnect(websocket)
    except Exception:
        EVENT_BROADCASTER.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    # اجرا روی پورت 8000
    uvicorn.run(app, host="127.0.0.1", port=8000)
