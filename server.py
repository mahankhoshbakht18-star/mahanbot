import os
import json
import asyncio
from collections import deque
from datetime import datetime, timezone
import aiosqlite
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException, status, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
from database import DBHandler, resource_path
from job_runner import JobRunner, JobState, JobRecord

# ایمپورت ربات‌ها
from bot_register import RegistrationBot
from bot_select import BankSelectionBot
from bot_status import StatusBot
from captcha_service import CaptchaService, load_captcha_resources

app = FastAPI()

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
JOB_RUNNER: Optional[JobRunner] = None
EVENT_BUFFER_SIZE = int(os.getenv("EVENT_BUFFER_SIZE", "200"))
EVENT_QUEUE_SIZE = int(os.getenv("EVENT_QUEUE_SIZE", "100"))
EVENT_RETENTION_LIMIT = int(os.getenv("EVENT_RETENTION_LIMIT", "1000"))
JOB_MAX_CONCURRENCY = int(os.getenv("JOB_MAX_CONCURRENCY", "2"))

def require_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-KEY")):
    expected_key = os.getenv("MAHANBOT_API_KEY")
    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "API_KEY_MISSING", "message": "API key not configured"},
        )
    if x_api_key != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "API_KEY_INVALID", "message": "Invalid API key"},
        )
    return True


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "code" in exc.detail and "message" in exc.detail:
        payload = exc.detail
    else:
        payload = {"code": "HTTP_ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "code": "VALIDATION_ERROR",
            "message": "Invalid request payload",
            "details": exc.errors(),
        },
    )

@app.on_event("startup")
async def startup_event():
    """پاکسازی وضعیت‌های گیر کرده هنگام شروع برنامه"""
    global MAIN_LOOP, CAPTCHA_SERVICE, JOB_RUNNER
    MAIN_LOOP = asyncio.get_running_loop()
    model, ocr_firewall = load_captcha_resources()
    CAPTCHA_SERVICE = CaptchaService(model=model, ocr_firewall=ocr_firewall)
    JOB_RUNNER = JobRunner(max_workers=JOB_MAX_CONCURRENCY, on_state_change=handle_job_state_change)
    JOB_RUNNER.start()
    try:
        async with aiosqlite.connect(resource_path('cbi_ultimate.db')) as conn:
            await conn.execute(
                "UPDATE applicants SET status='Stopped' WHERE status IN ('Running', 'Selecting', 'Registering', 'Waiting SMS')"
            )
            await conn.commit()
    except Exception as e:
        print(f"Error checking DB on startup: {e}")

# --- WebSocket ---
class WSConnection:
    def __init__(self, websocket: WebSocket, queue_size: int):
        self.websocket = websocket
        self.queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self.task: Optional[asyncio.Task] = None

    async def sender(self):
        while True:
            event = await self.queue.get()
            await self.websocket.send_text(json.dumps(event))


class ConnectionManager:
    def __init__(self, buffer_size: int, queue_size: int):
        self._buffer = deque(maxlen=buffer_size)
        self._queue_size = queue_size
        self.active_connections: List[WSConnection] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        connection = WSConnection(websocket, self._queue_size)
        self.active_connections.append(connection)
        await self._send_buffer(connection)
        connection.task = asyncio.create_task(connection.sender())

    def disconnect(self, websocket: WebSocket):
        for connection in list(self.active_connections):
            if connection.websocket is websocket:
                if connection.task:
                    connection.task.cancel()
                self.active_connections.remove(connection)
                break

    def add_to_buffer(self, event: Dict[str, Any]):
        self._buffer.append(event)

    async def _send_buffer(self, connection: WSConnection):
        for event in list(self._buffer):
            try:
                await connection.websocket.send_text(json.dumps(event))
            except Exception:
                break

    async def broadcast(self, event: Dict[str, Any]):
        for connection in list(self.active_connections):
            try:
                if connection.queue.full():
                    _ = connection.queue.get_nowait()
                connection.queue.put_nowait(event)
            except Exception:
                self.disconnect(connection.websocket)


manager = ConnectionManager(EVENT_BUFFER_SIZE, EVENT_QUEUE_SIZE)

# --- Models ---
class ErrorResponse(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class ApplicantModel(BaseModel):
    id: Optional[int] = None
    full_name: str
    national_id: str
    data: Dict[str, Any]


class ApplicantResponse(BaseModel):
    id: int
    full_name: str
    national_id: str
    status: str
    last_log: str
    data: Dict[str, Any]
    is_active: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ApplicantSaveResponse(BaseModel):
    status: str
    applicant_id: Optional[int] = None


class BankSelectRequest(BaseModel):
    nid: str
    loan_type: str


class SMSRequest(BaseModel):
    nid: str
    code: str


class JobResponse(BaseModel):
    job_id: str
    bot_name: str
    nid: str
    applicant_name: Optional[str]
    state: str
    created_at: Optional[str]
    started_at: Optional[str]
    finished_at: Optional[str]
    last_error: Optional[str]
    cancel_requested: bool


class JobStartResponse(BaseModel):
    status: str
    job_id: Optional[str] = None
    message: Optional[str] = None


class JobCancelResponse(BaseModel):
    status: str
    job_ids: List[str] = Field(default_factory=list)


class SettingsModel(BaseModel):
    captcha_delay: Optional[float] = 0.1
    retry_count: Optional[int] = 1000
    headless: Optional[bool] = False
    clear_cookies: Optional[bool] = True
    save_only_mode: Optional[bool] = False
    sms_auto_resend: Optional[bool] = True
    captcha_mode: Optional[str] = "human"
    final_submit: Optional[bool] = False
    use_proxy: Optional[bool] = False
    proxy_list: Optional[str] = ""


class StatsResponse(BaseModel):
    applicants: int
    events: int
    active_jobs: int
    queued_jobs: int
    running_jobs: int
    failed_jobs: int
    success_count: int
    tracking_codes: int

# --- Helper ---
def build_event(
    event_type: str,
    level: str,
    job_id: Optional[str],
    bot: Optional[str],
    nid: Optional[str],
    message: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = {
        "warning": "WARN",
        "warn": "WARN",
        "error": "ERROR",
        "debug": "DEBUG",
        "info": "INFO",
        "success": "INFO",
        "stopped": "INFO",
        "stopping": "INFO",
    }.get(level.lower(), level.upper())
    if normalized not in {"DEBUG", "INFO", "WARN", "ERROR"}:
        normalized = "INFO"
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "type": event_type,
        "ts": ts,
        "level": normalized,
        "job_id": job_id,
        "bot": bot,
        "nid": nid,
        "message": message,
        "meta": meta or {},
    }


def publish_event(event: Dict[str, Any]):
    manager.add_to_buffer(event)
    DBHandler.add_event(event, EVENT_RETENTION_LIMIT)
    try:
        if MAIN_LOOP and MAIN_LOOP.is_running():
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop and running_loop is MAIN_LOOP:
                asyncio.create_task(manager.broadcast(event))
            else:
                asyncio.run_coroutine_threadsafe(manager.broadcast(event), MAIN_LOOP)
    except Exception:
        pass


def make_log_callback(job_id: str, bot_name: str, nid: str):
    def _log_callback(log_nid: str, message: str, level: str = "info"):
        DBHandler.update_status(log_nid, level.title(), message)
        event = build_event(
            event_type="log",
            level=level,
            job_id=job_id,
            bot=bot_name,
            nid=log_nid,
            message=message,
        )
        publish_event(event)

    return _log_callback


def handle_job_state_change(job: JobRecord):
    status_map = {
        JobState.QUEUED: "Queued",
        JobState.RUNNING: "Running",
        JobState.SUCCEEDED: "Success",
        JobState.FAILED: "Failed",
        JobState.CANCELED: "Stopped",
    }
    level = "ERROR" if job.state == JobState.FAILED else "INFO"
    message = f"وضعیت کار: {job.state.value}"
    DBHandler.update_status(job.nid, status_map.get(job.state, job.state.value), message)
    job_payload = job.to_dict()
    event = build_event(
        event_type="job_status",
        level=level,
        job_id=job.job_id,
        bot=job.bot_name,
        nid=job.nid,
        message=message,
        meta={
            "state": job.state.value,
            "applicant_name": job.applicant_name,
            "started_at": job_payload.get("started_at"),
            "finished_at": job_payload.get("finished_at"),
            "cancel_requested": job.cancel_requested,
        },
    )
    publish_event(event)

def get_captcha_service():
    global CAPTCHA_SERVICE
    if CAPTCHA_SERVICE is None:
        CAPTCHA_SERVICE = CaptchaService()
    return CAPTCHA_SERVICE

# --- Job runners ---
def run_register_job(nid, settings, stop_event, captcha_service, log_callback):
    try:
        bot = RegistrationBot(nid, settings, log_callback=log_callback, captcha_service=captcha_service)
        bot.run(stop_event)
    except Exception as e:
        log_callback(nid, f"خطا: {e}", "error")
    finally:
        log_callback(nid, "ربات متوقف شد", "stopped")

def run_select_job(nid, settings, loan_type, stop_event, captcha_service, log_callback):
    try:
        bot = BankSelectionBot(nid, settings, log_callback=log_callback, captcha_service=captcha_service)
        bot.run(stop_event, loan_type)
    except Exception as e:
        log_callback(nid, f"خطا: {e}", "error")
    finally:
        log_callback(nid, "ربات متوقف شد", "stopped")

def run_status_job(nid, settings, stop_event, captcha_service, log_callback):
    try:
        bot = StatusBot(nid, settings, log_callback=log_callback, captcha_service=captcha_service)
        bot.run(stop_event)
    except Exception as e:
        log_callback(nid, f"خطای وضعیت: {e}", "error")
    finally:
        log_callback(nid, "عملیات پایان یافت", "stopped")

# --- Routes ---
@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())
    return HTMLResponse("Error: index.html not found in static folder")

@app.get("/applicants", response_model=List[ApplicantResponse])
def get_applicants() -> List[ApplicantResponse]:
    apps = DBHandler.get_all_applicants()
    active_nids = set()
    if JOB_RUNNER:
        for job in JOB_RUNNER.list_jobs():
            if job.state in {JobState.QUEUED, JobState.RUNNING}:
                active_nids.add(job.nid)
    for app_data in apps:
        app_data['is_active'] = (app_data['national_id'] in active_nids)
    return apps

@app.post("/applicants", response_model=ApplicantSaveResponse)
async def save_applicant(req: ApplicantModel):
    try:
        d_str = json.dumps(req.data)
        async with aiosqlite.connect(resource_path('cbi_ultimate.db')) as conn:
            if req.id:
                await conn.execute(
                    "UPDATE applicants SET full_name=?, national_id=?, data=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (req.full_name, req.national_id, d_str, req.id),
                )
            else:
                cursor = await conn.execute(
                    "INSERT INTO applicants (full_name, national_id, data, created_at, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (req.full_name, req.national_id, d_str),
                )
            await conn.commit()
        applicant_id = req.id
        if not applicant_id:
            applicant_id = cursor.lastrowid
        return {"status": "ok", "applicant_id": applicant_id}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "APPLICANT_SAVE_FAILED", "message": "ذخیره متقاضی انجام نشد", "details": {"error": str(e)}},
        )

@app.delete("/applicants/{nid}", response_model=JobCancelResponse)
async def delete_applicant(nid: str, _: bool = Depends(require_api_key)):
    canceled_jobs = JOB_RUNNER.cancel_by_nid(nid) if JOB_RUNNER else []
    async with aiosqlite.connect(resource_path('cbi_ultimate.db')) as conn:
        await conn.execute("DELETE FROM applicants WHERE national_id=?", (nid,))
        await conn.commit()
    return {"status": "deleted", "job_ids": [job.job_id for job in canceled_jobs]}


@app.get("/settings", response_model=SettingsModel)
def get_settings():
    return DBHandler.get_config()


@app.post("/settings", response_model=JobStartResponse)
def save_settings(req: SettingsModel, _: bool = Depends(require_api_key)):
    current = DBHandler.get_config()
    payload = req.dict(exclude_none=True)
    current.update(payload)
    DBHandler.update_config(current)
    return {"status": "ok", "message": "تنظیمات ذخیره شد"}


@app.get("/health")
def health_check():
    try:
        stats = DBHandler.get_stats()
        return {"status": "ok", "db": "ok", "stats": stats}
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"code": "HEALTH_CHECK_FAILED", "message": "Health check failed", "details": {"error": str(exc)}})


@app.get("/stats", response_model=StatsResponse)
def get_stats():
    base_stats = DBHandler.get_stats()
    jobs = JOB_RUNNER.list_jobs() if JOB_RUNNER else []
    active_jobs = [job for job in jobs if job.state in {JobState.QUEUED, JobState.RUNNING}]
    return {
        "applicants": base_stats.get("applicants", 0),
        "events": base_stats.get("events", 0),
        "active_jobs": len(active_jobs),
        "queued_jobs": len([job for job in jobs if job.state == JobState.QUEUED]),
        "running_jobs": len([job for job in jobs if job.state == JobState.RUNNING]),
        "failed_jobs": len([job for job in jobs if job.state == JobState.FAILED]),
        "success_count": base_stats.get("success_count", 0),
        "tracking_codes": base_stats.get("tracking_codes", 0),
    }


@app.get("/jobs", response_model=List[JobResponse])
def list_jobs():
    jobs = JOB_RUNNER.list_jobs() if JOB_RUNNER else []
    return [JobResponse(**job.to_dict()) for job in jobs]


@app.post("/jobs/{job_id}/cancel", response_model=JobCancelResponse)
def cancel_job(job_id: str, _: bool = Depends(require_api_key)):
    if not JOB_RUNNER:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "JOB_RUNNER_OFFLINE", "message": "Job runner not ready"})
    job = JOB_RUNNER.cancel_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "JOB_NOT_FOUND", "message": "کار یافت نشد"})
    return {"status": "stopped", "job_ids": [job.job_id]}

@app.post("/receive_sms")
def rec_sms(req: SMSRequest) -> JobStartResponse:
    if DBHandler.save_otp(req.nid, req.code):
        event = build_event(
            event_type="log",
            level="INFO",
            job_id=None,
            bot="sms",
            nid=req.nid,
            message=f"پیامک دریافت شد: {req.code}",
        )
        publish_event(event)
        return {"status": "ok"}
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "OTP_SAVE_FAILED", "message": "ذخیره پیامک انجام نشد"},
    )

@app.post("/bot/start-register/{nid}", response_model=JobStartResponse)
def start_reg(nid: str, _: bool = Depends(require_api_key)):
    if not JOB_RUNNER:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "JOB_RUNNER_OFFLINE", "message": "Job runner not ready"})
    applicant = DBHandler.get_applicant(nid)
    if not applicant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "APPLICANT_NOT_FOUND", "message": "متقاضی یافت نشد"})
    settings = DBHandler.get_config()
    captcha_service = get_captcha_service()
    try:
        job_id = os.urandom(16).hex()
        handler = lambda stop_event: run_register_job(
            nid,
            settings,
            stop_event,
            captcha_service,
            make_log_callback(job_id, "register", nid),
        )
        job = JOB_RUNNER.enqueue(
            bot_name="register",
            nid=nid,
            applicant_name=applicant["full_name"] if applicant else None,
            handler=handler,
            job_id=job_id,
        )
    except ValueError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "JOB_EXISTS", "message": "برای این متقاضی یک کار فعال وجود دارد"})
    return {"status": "started", "job_id": job.job_id}

@app.post("/bot/start-select", response_model=JobStartResponse)
def start_sel(req: BankSelectRequest, _: bool = Depends(require_api_key)):
    if not JOB_RUNNER:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "JOB_RUNNER_OFFLINE", "message": "Job runner not ready"})
    nid = req.nid
    applicant = DBHandler.get_applicant(nid)
    if not applicant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "APPLICANT_NOT_FOUND", "message": "متقاضی یافت نشد"})
    settings = DBHandler.get_config()
    captcha_service = get_captcha_service()
    try:
        job_id = os.urandom(16).hex()
        handler = lambda stop_event: run_select_job(
            nid,
            settings,
            req.loan_type,
            stop_event,
            captcha_service,
            make_log_callback(job_id, "select", nid),
        )
        job = JOB_RUNNER.enqueue(
            bot_name="select",
            nid=nid,
            applicant_name=applicant["full_name"] if applicant else None,
            handler=handler,
            job_id=job_id,
        )
    except ValueError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "JOB_EXISTS", "message": "برای این متقاضی یک کار فعال وجود دارد"})
    return {"status": "started", "job_id": job.job_id}

@app.post("/bot/stop/{nid}", response_model=JobCancelResponse)
def stop_bot(nid: str, _: bool = Depends(require_api_key)):
    canceled_jobs = JOB_RUNNER.cancel_by_nid(nid) if JOB_RUNNER else []
    if canceled_jobs:
        publish_event(
            build_event(
                event_type="log",
                level="INFO",
                job_id=canceled_jobs[0].job_id,
                bot="system",
                nid=nid,
                message="درخواست توقف ثبت شد",
            )
        )
    else:
        DBHandler.update_status(nid, "Stopped", "Force Stop")
    return {"status": "stopped", "job_ids": [job.job_id for job in canceled_jobs]}

@app.post("/bot/action/view-status/{nid}", response_model=JobStartResponse)
def action_view_status(nid: str, _: bool = Depends(require_api_key)):
    if not JOB_RUNNER:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "JOB_RUNNER_OFFLINE", "message": "Job runner not ready"})
    user = DBHandler.get_applicant(nid)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "APPLICANT_NOT_FOUND", "message": "کاربر یافت نشد"})
    try:
        d = json.loads(user['data'])
        if not d.get('tracking_code'):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "TRACKING_CODE_MISSING", "message": "کد رهگیری ندارد"})
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "INVALID_DATA", "message": "داده نامعتبر"})

    settings = DBHandler.get_config()
    captcha_service = get_captcha_service()
    try:
        job_id = os.urandom(16).hex()
        handler = lambda stop_event: run_status_job(
            nid,
            settings,
            stop_event,
            captcha_service,
            make_log_callback(job_id, "status", nid),
        )
        job = JOB_RUNNER.enqueue(
            bot_name="status",
            nid=nid,
            applicant_name=user["full_name"] if user else None,
            handler=handler,
            job_id=job_id,
        )
    except ValueError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "JOB_EXISTS", "message": "برای این متقاضی یک کار فعال وجود دارد"})
    return {"status": "started", "job_id": job.job_id, "message": "استعلام وضعیت آغاز شد"}

@app.post("/bot/action/delete-request/{nid}")
def action_delete_request(nid: str, _: bool = Depends(require_api_key)):
    publish_event(
        build_event(
            event_type="log",
            level="WARN",
            job_id=None,
            bot="system",
            nid=nid,
            message="حذف درخواست (هنوز پیاده‌سازی نشده)",
        )
    )
    return {"status": "ok"}

@app.post("/bot/action/recover-code/{nid}")
def action_recover_code(nid: str):
    publish_event(
        build_event(
            event_type="log",
            level="INFO",
            job_id=None,
            bot="system",
            nid=nid,
            message="بازیابی کد (هنوز پیاده‌سازی نشده)",
        )
    )
    return {"status": "ok"}

# --- WebSocket Endpoint (اصلاح شده) ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    # اجرا روی پورت 8000
    uvicorn.run(app, host="127.0.0.1", port=8000)
