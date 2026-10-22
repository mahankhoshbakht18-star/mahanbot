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
if os.getenv('MAHANBOT_EDITION', 'ADMIN').strip().upper() != 'CLIENT':
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parent / '.licenses.private.env')

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
    from core.licensing import start_watchdog
    _state.LICENSE_WATCHDOG = start_watchdog(_state.JOB_QUEUE)
    
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
    if _state.LICENSE_WATCHDOG is not None:
        _state.LICENSE_WATCHDOG.set()
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
        path == "/health" or path.startswith('/api/license/')
    )
    user = _session_user(request.cookies.get(AUTH_COOKIE))
    if not public and not user:
        if request.method == "GET" and path == "/":
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"detail": "اشتراک شما معتبر نیست یا وارد نشده‌اید"}, status_code=401)
    
    from core.licensing import can_run, license_required
    safety_action = (path.startswith(('/jobs/cancel/', '/bot/stop/', '/api/capacity/pause/'))
                     or path in {'/api/capacity/stop-all', '/logout', '/login', '/receive_sms', '/sms/webhook', '/api/sms/webhook'}
                     or path.startswith('/api/license/'))
    if license_required() and request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and not safety_action:
        fresh = path in {'/jobs/start', '/jobs/restart', '/api/capacity/start-all'}
        if not await asyncio.to_thread(can_run, force=fresh):
            return JSONResponse({'detail': 'لایسنس معتبر نیست؛ بخش فعال‌سازی لایسنس را بررسی کنید.',
                                 'code': 'LICENSE_REQUIRED'}, status_code=402)

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    if path.startswith('/api/license/'):
        response.headers['Cache-Control'] = 'no-store'

    # If user is authenticated (e.g. local desktop access) and cookie is not yet in browser,
    # set the session cookie so WebSocket and all frontend requests maintain connection seamlessly
    if user and not request.cookies.get(AUTH_COOKIE) and getattr(response, "set_cookie", None):
        import secrets, time
        token = secrets.token_urlsafe(32)
        exp = time.time() + 30 * 86400
        AUTH_SESSIONS[token] = {"username": user.get("username", "admin"), "expires_at": exp}
        DBHandler.save_app_session(token, user.get("username", "admin"), exp)
        response.set_cookie(
            AUTH_COOKIE, token, httponly=True, samesite="lax", secure=False,
            max_age=30 * 86400, path="/"
        )
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
from routes.diagnostic import router as diagnostic_router
from routes.capacity import router as capacity_router
from routes.database_admin import router as database_admin_router
from routes.system_update import router as system_update_router
from routes.licenses import router as licenses_router

app.include_router(auth_router)
app.include_router(licenses_router)
app.include_router(applicants_router)
app.include_router(otp_router)
app.include_router(settings_router)
app.include_router(jobs_router)
app.include_router(ws_router)
app.include_router(saved_pages_router)
app.include_router(diagnostic_router)
app.include_router(capacity_router)
app.include_router(database_admin_router)
app.include_router(system_update_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host=os.getenv("MAHANBOT_HOST", "127.0.0.1"),
        port=int(os.getenv("MAHANBOT_PORT", "8000")),
        reload=False
    )
