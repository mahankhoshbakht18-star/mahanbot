"""
server.py — Clean & Stable FastAPI Entry Point for MahanBot
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles

from core.config import load_dotenv, get_csv_env, get_bool_env, snapshot_public_config
load_dotenv()

import core.state as _state
from core.state import STATIC_DIR, AUTH_SESSIONS, AUTH_COOKIE
from core.deps import _session_user
from captcha_service import CaptchaService
from core.job_queue import JobQueue, get_max_concurrency
from event_logger import set_main_loop
from database import DBHandler

# ── Lifecycle Handlers ────────────────────────────────────────────────
logger = logging.getLogger("mahanbot")
if not logging.getLogger().handlers:
    logging.basicConfig(level=os.getenv("MAHANBOT_LOG_LEVEL", "INFO"))

async def _startup():
    _state.MAIN_LOOP = asyncio.get_running_loop()
    set_main_loop(_state.MAIN_LOOP)
    _state.CAPTCHA_SERVICE = CaptchaService()
    try:
        _state.CAPTCHA_SERVICE.prewarm()
        logger.info("Local CAPTCHA solver pre-warmed into RAM.")
    except Exception as e:
        logger.warning("CAPTCHA pre-warm skipped: %s", e)
    if _state.JOB_QUEUE is None:
        _state.JOB_QUEUE = JobQueue(max_concurrency=get_max_concurrency())
        _state.JOB_QUEUE.start()
    
    logger.info("Startup config: %s", snapshot_public_config())
    
    # بازنشانی وضعیت‌های قفل‌شده قبلی
    try:
        from core.helpers import _open_aiosqlite
        async with _open_aiosqlite() as conn:
            await conn.execute(
                "UPDATE applicants SET status='Stopped' "
                "WHERE status IN ('Running','Selecting','Registering','Waiting SMS')"
            )
            await conn.commit()
    except Exception as e:
        logger.warning("DB startup reset failed: %s", e)

@asynccontextmanager
async def _lifespan(application: FastAPI):
    async with _state.STARTUP_LOCK:
        first_start = not _state.STARTUP_DONE
        if first_start:
            _state.STARTUP_DONE = True
            await _startup()
    yield
    try:
        if _state.JOB_QUEUE is not None:
            _state.JOB_QUEUE.stop()
    except Exception as e:
        logger.warning("Job queue shutdown failed: %s", e)

# ── App Definition ───────────────────────────────────────────────────
app = FastAPI(title="MahanBot Core", version="2.1.0", lifespan=_lifespan)

_cors_origins = get_csv_env("MAHANBOT_CORS_ORIGINS")
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
DBHandler.init_db()

# ── Middlewares ───────────────────────────────────────────────────────
@app.middleware("http")
async def require_subscription_login(request: Request, call_next):
    path = request.url.path
    public = (
        path == "/login" or 
        path.startswith("/static/") or 
        path == "/receive_sms" or 
        path == "/sms/webhook" or 
        path == "/api/sms/webhook" or 
        path.startswith("/wait_otp/") or 
        path == "/health"
    )
    if not public and not _session_user(request.cookies.get(AUTH_COOKIE)):
        if request.method == "GET" and path == "/":
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"detail": "اشتراک شما معتبر نیست یا وارد نشده‌اید"}, status_code=401)
    
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response

@app.middleware("http")
async def log_unhandled_exceptions(request: Request, call_next):
    try:
        return await call_next(request)
    except ConnectionResetError:
        return Response(status_code=204)
    except Exception:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        raise

# ── Router Registration ──────────────────────────────────────────────
from routes.auth import router as auth_router
from routes.applicants import router as applicants_router
from routes.otp import router as otp_router
from routes.settings import router as settings_router
from routes.jobs import router as jobs_router
from routes.websocket import router as ws_router
from routes.saved_pages import router as saved_pages_router

app.include_router(auth_router)
app.include_router(applicants_router)
app.include_router(otp_router)
app.include_router(settings_router)
app.include_router(jobs_router)
app.include_router(ws_router)
app.include_router(saved_pages_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host=os.getenv("MAHANBOT_HOST", "127.0.0.1"),
        port=int(os.getenv("MAHANBOT_PORT", "8001")),
        reload=False
    )