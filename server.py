# server.py
import os
import threading
import time
import json
import asyncio
import logging
from contextlib import asynccontextmanager
from collections import deque
from typing import Deque, Dict, Any, Optional, List, Tuple

import aiosqlite
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import DBHandler, resource_path, DB_PATH  # DB_PATH از database.py شما
from allowlist import get_allowlist_snapshot, get_allowed_domains, is_url_allowed, normalize_domain_list
from event_logger import (
    EVENT_BROADCASTER,
    build_event,
    build_log_callback,
    create_job_id,
    job_status_event,
    log_event,
    set_main_loop,
)
from browser_launcher import (
    BrowserLaunchError,
    close_browser,
    get_default_browser_profile,
    launch_browser,
    merge_browser_profiles,
    normalize_browser_profile,
)
from messages_fa import RESPONSES, LOG_MESSAGES, get_message

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
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

DBHandler.init_db()
MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None
CAPTCHA_SERVICE: Optional[CaptchaService] = None
JOB_QUEUE: Optional["JobQueue"] = None
OTP_EVENTS: Dict[str, asyncio.Event] = {}
OTP_EVENT_LOCK = asyncio.Lock()
STARTUP_DONE = False
STARTUP_LOCK = asyncio.Lock()


# ------------------------- Helpers -------------------------
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
    except ConnectionResetError:
        logger.debug("Connection reset by peer on %s %s", request.method, request.url.path)
        return Response(status_code=204)
    except Exception:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        raise


@asynccontextmanager
async def _open_aiosqlite():
    """
    برای جلوگیری از 'database is locked' و هماهنگی با DBHandler (sqlite3 sync)
    """
    conn = await aiosqlite.connect(DB_PATH, timeout=30)
    try:
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA busy_timeout=5000;")
        await conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
    finally:
        await conn.close()


async def _get_otp_event(nid: str) -> asyncio.Event:
    async with OTP_EVENT_LOCK:
        event = OTP_EVENTS.get(nid)
        if not event:
            event = asyncio.Event()
            OTP_EVENTS[nid] = event
        return event


async def _signal_otp_event(nid: str) -> None:
    event = await _get_otp_event(nid)
    event.set()


def _check_uvicorn_ws_backend():
    has_backend = False
    try:
        import websockets  # noqa
        has_backend = True
    except Exception:
        pass
    try:
        import wsproto  # noqa
        has_backend = True
    except Exception:
        pass
    if not has_backend:
        logger.warning(
            "WebSocket backend نصب نیست. یکی از اینها را نصب کن: "
            "`pip install websockets` یا `pip install wsproto` یا `pip install \"uvicorn[standard]\"`"
        )


def _normalize_nid(value: Optional[str]) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    # Normalize Persian/Arabic digits to ASCII
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    s = s.translate(trans)
    # Remove common separators
    s = s.replace("-", "").replace(" ", "")
    return s


@app.on_event("startup")
async def startup_event():
    global MAIN_LOOP, CAPTCHA_SERVICE, JOB_QUEUE, STARTUP_DONE
    async with STARTUP_LOCK:
        if STARTUP_DONE:
            return
        STARTUP_DONE = True
    MAIN_LOOP = asyncio.get_running_loop()
    set_main_loop(MAIN_LOOP)

    _check_uvicorn_ws_backend()

    model, ocr_firewall = load_captcha_resources()
    CAPTCHA_SERVICE = CaptchaService(model=model, ocr_firewall=ocr_firewall)

    if JOB_QUEUE is None:
        JOB_QUEUE = JobQueue(max_concurrency=get_max_concurrency())
        JOB_QUEUE.start()

    # Reset stuck statuses
    try:
        async with _open_aiosqlite() as conn:
            await conn.execute(
                "UPDATE applicants SET status='Stopped' "
                "WHERE status IN ('Running','Selecting','Registering','Waiting SMS')"
            )
            await conn.commit()
    except Exception as e:
        logger.warning("DB startup reset failed: %s", e)


def get_captcha_service():
    global CAPTCHA_SERVICE
    if CAPTCHA_SERVICE is None:
        CAPTCHA_SERVICE = CaptchaService()
    return CAPTCHA_SERVICE


def get_max_concurrency() -> int:
    try:
        value = int(os.getenv("MAHANBOT_MAX_CONCURRENCY", "2"))
    except ValueError:
        value = 2
    return max(1, value)


def load_browser_profile_settings() -> Dict[str, Any]:
    stored = DBHandler.get_setting("browser_profile") or {}
    try:
        return merge_browser_profiles(get_default_browser_profile(), stored)
    except Exception:
        return get_default_browser_profile()


def save_browser_profile_settings(profile: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_browser_profile(profile)
    DBHandler.update_setting("browser_profile", normalized)
    return normalized


def _serialize_applicants() -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    try:
        with DBHandler._connect(row_factory=True) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM applicants ORDER BY id DESC")
            rows = cursor.fetchall()
            for row in rows:
                record = dict(row)
                try:
                    record["data"] = json.loads(record["data"]) if record.get("data") else {}
                except Exception:
                    record["data"] = {}
                results.append(record)
    except Exception:
        return []
    return results


def _emit_applicants_snapshot() -> None:
    EVENT_BROADCASTER.emit_event(
        build_event("applicants_snapshot", applicants=_serialize_applicants())
    )


def _handle_receive_sms(nid: str, code: str, status_label: str = "received", ts: Optional[float] = None) -> bool:
    otp_ts = float(ts) if ts is not None else time.time()
    success = DBHandler.set_otp(nid, code, ts=otp_ts, status=status_label)
    if not success:
        return False
    log_event(nid, None, "OTP received", "success")
    logger.info("OTP received for %s; notifying waiters", nid)
    if MAIN_LOOP:
        MAIN_LOOP.call_soon_threadsafe(lambda: asyncio.create_task(_signal_otp_event(nid)))
    return True


def receive_otp_from_sms_device(
    *,
    nid: str,
    code: str,
    received_at: float,
    device_id: str,
    message_id: str,
) -> str:
    """Attach a consented Android OTP only to a currently waiting applicant."""
    normalized_nid = _normalize_nid(nid)
    if len(normalized_nid) != 10 or not normalized_nid.isdigit():
        return "invalid"
    result = DBHandler.store_device_otp_if_waiting(
        normalized_nid,
        code,
        received_at=received_at,
        device_id=device_id,
        message_id=message_id,
    )
    if result == "accepted":
        log_event(normalized_nid, None, "OTP received from authorized Android device", "success")
        logger.info("Fresh Android OTP accepted for applicant %s", normalized_nid)
        if MAIN_LOOP:
            MAIN_LOOP.call_soon_threadsafe(
                lambda: asyncio.create_task(_signal_otp_event(normalized_nid))
            )
        _emit_applicants_snapshot()
    return result


# ------------------------- Models -------------------------
class ApplicantModel(BaseModel):
    id: Optional[int] = None
    full_name: str
    national_id: str
    data: Dict[str, Any]


class SMSRequest(BaseModel):
    nid: str
    code: str


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


class SettingsRequest(BaseModel):
    captcha_delay: Optional[float] = None
    retry_count: Optional[int] = None
    headless: Optional[bool] = None
    clear_cookies: Optional[bool] = None
    save_only_mode: Optional[bool] = None
    sms_auto_resend: Optional[bool] = None
    captcha_mode: Optional[str] = None
    interaction_mode: Optional[str] = None
    human_typing_delay_ms: Optional[int] = None
    fast_typing_delay_ms: Optional[int] = None
    final_submit: Optional[bool] = None
    use_proxy: Optional[bool] = None
    proxy_list: Optional[str] = None


class AllowedDomainsRequest(BaseModel):
    domains: List[str]


class AllowlistCheckRequest(BaseModel):
    url: str


# ------------------------- Job Queue -------------------------
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
        self.error: Optional[Any] = None
        self.runner_thread: Optional[threading.Thread] = None

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



class DuplicateJobError(ValueError):
    def __init__(self, job):
        super().__init__("Job already queued or running")
        self.job = job

class JobQueue:
    ACTIVE_STATUSES = {"queued", "running", "cancelling"}

    def __init__(self, max_concurrency: int):
        self._max_concurrency = max_concurrency
        self._jobs: Dict[str, Job] = {}
        self._queue: Deque[str] = deque()
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._workers: list[threading.Thread] = []

    def start(self) -> None:
        for index in range(self._max_concurrency):
            t = threading.Thread(target=self._worker_loop, args=(index,), daemon=True)
            t.start()
            self._workers.append(t)

    def find_active(self, bot_name: str, nid: str) -> Optional[Job]:
        with self._lock:
            for job in self._jobs.values():
                if job.bot_name == bot_name and job.nid == nid and job.status in self.ACTIVE_STATUSES:
                    return job
        return None

    def enqueue(self, bot_name: str, nid: str, payload: Dict[str, Any]) -> Optional[Job]:
        with self._condition:
            existing = None
            for job in self._jobs.values():
                if job.bot_name == bot_name and job.nid == nid and job.status in self.ACTIVE_STATUSES:
                    existing = job
                    break
            if existing:
                raise DuplicateJobError(existing)
            job = Job(bot_name, nid, payload)
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
                job.stop_event.set()
                job_status_event(job.nid, job.id, "cancelled")
                return job
            if job.status in {"running", "cancelling"}:
                job.cancel_requested = True
                job.stop_event.set()
                if job.status != "cancelling":
                    job.status = "cancelling"
                    job_status_event(job.nid, job.id, "cancelling")
                return job
            return job

    def cancel_by_nid(self, nid: str) -> Optional[Job]:
        job_id = None
        with self._lock:
            for job in self._jobs.values():
                if job.nid == nid and job.status in self.ACTIVE_STATUSES:
                    job_id = job.id
                    break
        if not job_id:
            return None
        return self.cancel(job_id)

    def list_jobs(self) -> list[Dict[str, Any]]:
        with self._lock:
            return [j.to_dict() for j in self._jobs.values()]

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def _worker_loop(self, worker_id: int) -> None:
        while True:
            with self._condition:
                while not self._queue:
                    self._condition.wait()
                job_id = self._queue.popleft()
                job = self._jobs.get(job_id)
                if not job or job.status != "queued":
                    continue
                job.status = "running"
                job.started_at = time.time()

            job_status_event(job.nid, job.id, "running")
            log_event(job.nid, job.id, f"Job started ({job.bot_name})", "info")

            try:
                self._run_job_isolated(job)
                if job.cancel_requested:
                    job.status = "cancelled"
                else:
                    job.status = "stopped"
            except Exception as e:
                job.status = "failed"
                job.error = str(e)
                log_event(job.nid, job.id, f"خطا: {e}", "error")
            finally:
                job.finished_at = time.time()
                job_status_event(job.nid, job.id, job.status)

    def _run_job_isolated(self, job: Job) -> None:
        """Run every bot in an isolated thread with a dedicated asyncio loop."""
        result: Dict[str, Any] = {}
        err: Dict[str, BaseException] = {}

        def _runner():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result["ok"] = True
                self._run_job(job)
            except BaseException as exc:  # noqa: BLE001
                err["exc"] = exc
            finally:
                try:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                finally:
                    asyncio.set_event_loop(None)
                    loop.close()

        runner = threading.Thread(target=_runner, name=f"job-{job.id}", daemon=True)
        job.runner_thread = runner
        runner.start()

        while runner.is_alive():
            if job.stop_event.is_set():
                # bots observe stop_event and close browser; keep join interval short for quick shutdown.
                runner.join(timeout=0.2)
            else:
                runner.join(timeout=0.5)

        if "exc" in err:
            raise err["exc"]

    def _run_job(self, job: Job) -> None:
        settings = DBHandler.get_config()
        base_profile = load_browser_profile_settings()
        override = job.payload.get("browser_profile_override")
        browser_profile = merge_browser_profiles(base_profile, override)

        captcha_service = get_captcha_service()
        log_callback = build_log_callback(job.id)

        if job.bot_name == "register":
            RegistrationBot(job.nid, settings, log_callback, captcha_service, browser_profile).run(job.stop_event)
        elif job.bot_name == "select":
            BankSelectionBot(job.nid, settings, log_callback, captcha_service, browser_profile).run(
                job.stop_event, job.payload.get("loan_type")
            )
        elif job.bot_name == "status":
            StatusBot(job.nid, settings, log_callback, captcha_service, browser_profile).run(job.stop_event)
        else:
            raise ValueError("Unsupported bot name")


# ------------------------- UI Routes -------------------------
@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("index.html not found")


# ✅ داشبورد شما این endpoint را لازم دارد
@app.get("/applicants")
def get_applicants():
    try:
        return _serialize_applicants()
    except Exception as exc:
        logger.warning("Failed to fetch applicants: %s", exc)
        return []


@app.post("/receive_sms")
def receive_sms(req: SMSRequest):
    raw_nid = str(req.nid or "").strip()
    nid = _normalize_nid(raw_nid)
    code = str(req.code or "").strip()
    if not (nid or raw_nid) or not code:
        raise HTTPException(status_code=400, detail="Invalid payload")

    request_ts = time.time()
    success = _handle_receive_sms(nid or raw_nid, code, status_label="received", ts=request_ts)
    if not success and raw_nid and raw_nid != nid:
        success = _handle_receive_sms(raw_nid, code, status_label="received", ts=request_ts)
    if not success:
        logger.warning("OTP not saved: applicant not found for nid=%s raw=%s", nid, raw_nid)
        raise HTTPException(status_code=404, detail="Applicant not found")
    _emit_applicants_snapshot()
    return {"status": "ok", "ts": request_ts}


@app.post("/manual_otp")
@app.post("/otp/manual")
def manual_otp(req: SMSRequest):
    raw_nid = str(req.nid or "").strip()
    nid = _normalize_nid(raw_nid)
    code = str(req.code or "").strip()
    if not (nid or raw_nid) or not code:
        raise HTTPException(status_code=400, detail="Invalid payload")

    success = DBHandler.save_otp(nid or raw_nid, code, status="received")
    if not success and raw_nid and raw_nid != nid:
        success = DBHandler.save_otp(raw_nid, code, status="received")
    if not success:
        logger.warning("Manual OTP not saved: applicant not found for nid=%s raw=%s", nid, raw_nid)
        raise HTTPException(status_code=404, detail="Applicant not found")
    final_nid = nid or raw_nid
    log_event(final_nid, None, "OTP received manually", "success")
    logger.info("Manual OTP received for %s; notifying waiters", final_nid)
    try:
        event = OTP_EVENTS.get(final_nid)
        if not event:
            event = asyncio.Event()
            OTP_EVENTS[final_nid] = event
        event.set()
    except Exception:
        pass
    if MAIN_LOOP:
        MAIN_LOOP.call_soon_threadsafe(lambda: asyncio.create_task(_signal_otp_event(final_nid)))
    _emit_applicants_snapshot()
    return {"status": "ok", "source": "manual"}


@app.get("/wait_otp/{nid}")
async def wait_otp(nid: str, timeout: int = 120, min_ts: float = 0.0):
    raw_nid = str(nid or "").strip()
    nid = _normalize_nid(raw_nid)
    if not (nid or raw_nid):
        raise HTTPException(status_code=400, detail="Invalid NID")

    try:
        min_ts = float(min_ts)
    except Exception:
        min_ts = 0.0
    timeout = max(1, min(int(timeout), 300))
    deadline = time.monotonic() + timeout
    logger.info("wait_otp waiting for %s (min_ts=%s)", nid, min_ts)

    while True:
        record = DBHandler.get_otp_record(nid or raw_nid)
        if not record and raw_nid and raw_nid != nid:
            record = DBHandler.get_otp_record(raw_nid)
        record_ts = 0.0
        if record:
            try:
                record_ts = float(record.get("ts") or 0.0)
            except Exception:
                record_ts = 0.0
        if record and record.get("otp") and record_ts > min_ts:
            logger.info("wait_otp released for %s ts=%s > min_ts=%s", nid, record_ts, min_ts)
            return {
                "nid": nid,
                "otp": record["otp"],
                "source": "fresh",
                "ts": record_ts or time.time(),
            }

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.info("wait_otp timeout for %s", nid)
            return Response(status_code=204)

        event = await _get_otp_event(nid)
        event.clear()
        try:
            await asyncio.wait_for(event.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            logger.info("wait_otp timeout for %s", nid)
            return Response(status_code=204)
        finally:
            event.clear()


# ✅ ذخیره متقاضی
@app.post("/applicants")
async def save_applicant(req: ApplicantModel):
    try:
        national_id = _normalize_nid(req.national_id)
        if not national_id:
            return {"status": "error", "msg": "Invalid national_id"}
        d_str = json.dumps(req.data, ensure_ascii=False)
        async with _open_aiosqlite() as conn:
            if req.id:
                await conn.execute(
                    "UPDATE applicants SET full_name=?, national_id=?, data=? WHERE id=?",
                    (req.full_name, national_id, d_str, req.id),
                )
            else:
                await conn.execute(
                    "INSERT INTO applicants (full_name, national_id, data) VALUES (?, ?, ?)",
                    (req.full_name, national_id, d_str),
                )
            await conn.commit()
        _emit_applicants_snapshot()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


# ✅ حذف متقاضی
@app.delete("/applicants/{nid}")
async def delete_applicant(nid: str, _: bool = Depends(require_api_key)):
    async with _open_aiosqlite() as conn:
        await conn.execute("DELETE FROM applicants WHERE national_id=?", (nid,))
        await conn.commit()
    _emit_applicants_snapshot()
    return {"status": "deleted"}


@app.post("/otp/clear/{nid}")
def clear_otp(nid: str):
    if not nid:
        raise HTTPException(status_code=400, detail="Invalid NID")
    DBHandler.clear_otp(nid)
    _emit_applicants_snapshot()
    return {"status": "cleared", "nid": nid}


@app.post("/applicants/{nid}/banks/stop")
def stop_bank(nid: str, payload: Dict[str, Any], _: bool = Depends(require_api_key)):
    bank_name = (payload.get("bank") or "").strip()
    if not bank_name:
        raise HTTPException(status_code=400, detail="Bank name required")
    success = DBHandler.add_stopped_bank(nid, bank_name)
    if not success:
        raise HTTPException(status_code=404, detail="Applicant not found")
    log_event(nid, None, f"⛔ توقف بانک: {bank_name}", "warning")
    _emit_applicants_snapshot()
    return {"status": "ok", "bank": bank_name}


@app.post("/bot/stop/{nid}")
def stop_bot_by_nid(nid: str, _: bool = Depends(require_api_key)):
    if not JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not ready")
    job = JOB_QUEUE.cancel_by_nid(nid)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _emit_applicants_snapshot()
    return {"status": job.status, "job": job.to_dict()}


@app.post("/bot/action/delete-request/{nid}")
def delete_request(nid: str, _: bool = Depends(require_api_key)):
    applicant = DBHandler.get_applicant(nid)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")
    log_event(nid, None, "Delete request submitted", "warning")
    _emit_applicants_snapshot()
    return {"status": "ok"}


# ✅ این endpoint را داشبورد شما لازم دارد
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
    return save_browser_profile_settings(merged)


@app.get("/settings")
def get_settings():
    return DBHandler.get_config()


@app.post("/settings")
def update_settings(req: SettingsRequest, _: bool = Depends(require_api_key)):
    payload = req.dict(exclude_unset=True)

    if "interaction_mode" in payload:
        mode = str(payload["interaction_mode"] or "").strip().lower()
        if mode not in {"human", "fast"}:
            raise HTTPException(status_code=400, detail="interaction_mode must be 'human' or 'fast'")
        payload["interaction_mode"] = mode

    for key, maximum in (("human_typing_delay_ms", 500), ("fast_typing_delay_ms", 100)):
        if key not in payload:
            continue
        value = int(payload[key])
        if value < 0 or value > maximum:
            raise HTTPException(status_code=400, detail=f"{key} must be between 0 and {maximum}")
        payload[key] = value

    current = DBHandler.get_config()
    current.update(payload)
    DBHandler.update_config(current)
    return {"status": "ok", "config": current}


@app.get("/settings/allowed-domains")
def get_allowed_domains_settings():
    return get_allowlist_snapshot()


@app.put("/settings/allowed-domains")
def update_allowed_domains(req: AllowedDomainsRequest, _: bool = Depends(require_api_key)):
    normalized, invalid = normalize_domain_list(req.domains)
    if invalid:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_domains", "details": {"invalid_domains": invalid}},
        )
    DBHandler.update_setting("allowed_domains", normalized)
    return get_allowlist_snapshot()


@app.post("/settings/allowed-domains/check")
def check_allowed_domain(req: AllowlistCheckRequest):
    allowed, host, effective = is_url_allowed(req.url)
    return {"allowed": allowed, "host": host, "effective": effective}


@app.post("/browser/test-launch")
def test_browser_launch(_: bool = Depends(require_api_key)):
    profile = load_browser_profile_settings()
    playwright = browser = context = page = None
    try:
        playwright, browser, context, page = launch_browser(profile)
        return {"status": "ok"}
    except BrowserLaunchError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.to_dict())
    finally:
        close_browser(playwright, browser, context, page)


@app.post("/jobs/start")
def start_job(req: JobStartRequest, _: bool = Depends(require_api_key)):
    if not JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not ready")

    payload: Dict[str, Any] = {}
    if req.loan_type:
        payload["loan_type"] = req.loan_type
    if req.browser_profile_override:
        payload["browser_profile_override"] = normalize_browser_profile(req.browser_profile_override)

    try:
        job = JOB_QUEUE.enqueue(req.bot_name.lower(), req.nid, payload)
    except DuplicateJobError as exc:
        existing = exc.job
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": "job_already_running",
                "message": "Job already queued or running for this NID.",
                "job": existing.to_dict() if existing else None,
            },
        )
    _emit_applicants_snapshot()
    return {"status": "queued", "job": job.to_dict()}


@app.post("/jobs/cancel/{job_id}")
def cancel_job(job_id: str, _: bool = Depends(require_api_key)):
    if not JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not ready")
    job = JOB_QUEUE.cancel(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _emit_applicants_snapshot()
    return {"status": job.status, "job": job.to_dict()}


# ✅ WebSocket پایدار (بدون اسپم لاگ)
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    websocket.state.send_lock = asyncio.Lock()
    await EVENT_BROADCASTER.connect(websocket)
    logger.info("WebSocket connected: %s", websocket.client)

    async def _safe_send(payload: Dict[str, Any]) -> bool:
        try:
            async with websocket.state.send_lock:
                await websocket.send_text(json.dumps(payload, ensure_ascii=False))
            return True
        except Exception:
            return False

    try:
        await _safe_send(build_event("applicants_snapshot", applicants=_serialize_applicants()))
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=25)
            except asyncio.TimeoutError:
                if not await _safe_send({"type": "ping", "ts": time.time()}):
                    break
                continue
            except WebSocketDisconnect:
                break

            payload: Optional[Dict[str, Any]] = None
            try:
                payload = json.loads(message)
            except Exception:
                payload = None

            if payload and payload.get("type") == "ping":
                await _safe_send({"type": "pong", "ts": time.time()})
                continue
            if payload and payload.get("type") == "pong":
                continue
    except ConnectionResetError:
        # قطع ناگهانی مرورگر/کلاینت
        pass
    except RuntimeError:
        # shutdown / close mid-await
        pass
    except Exception:
        logger.exception("WebSocket error")
    finally:
        EVENT_BROADCASTER.disconnect(websocket)
        logger.info("WebSocket disconnected: %s", websocket.client)


if __name__ == "__main__":
    import uvicorn
    # پیشنهاد بهتر:
    # uvicorn server:app --host 127.0.0.1 --port 8000 --reload
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
