"""
core/deps.py — FastAPI dependency functions (auth guards).
"""
import hmac
import logging
import os
import time
from typing import Any, Dict, Optional

from fastapi import Header, HTTPException, Request, status

from database import DBHandler
from messages_fa import RESPONSES, LOG_MESSAGES, get_message
from core.state import AUTH_SESSIONS, AUTH_COOKIE

logger = logging.getLogger("mahanbot")

_TRUE_VALUES = {"1", "true", "yes", "on"}


# ── API-key guard ─────────────────────────────────────────────────────
def require_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-KEY")):
    """Machine-to-machine guard layered on top of session auth.

    * ``MAHANBOT_API_KEY`` set            → always enforced (constant-time compare).
    * ``MAHANBOT_REQUIRE_API_KEY=true``   → fail-closed even when the key is unset.
    * otherwise                           → allowed with a one-time warning,
      because the dashboard itself authenticates via session cookies only.
    """
    expected_key = os.getenv("MAHANBOT_API_KEY")
    if not expected_key:
        if os.getenv("MAHANBOT_REQUIRE_API_KEY", "").strip().lower() in _TRUE_VALUES:
            logger.error("MAHANBOT_REQUIRE_API_KEY is enabled but MAHANBOT_API_KEY is not configured")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="کلید API سرور تنظیم نشده است؛ با مدیر سامانه تماس بگیرید",
            )
        if not getattr(require_api_key, "_warned", False):
            logger.warning(get_message("log", "api_key_not_configured", LOG_MESSAGES["api_key_not_configured"]))
            require_api_key._warned = True
        return True
    if x_api_key is None or not hmac.compare_digest(str(x_api_key), expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=get_message("responses", "api_key_invalid", RESPONSES["api_key_invalid"]),
        )
    return True


def require_otp_secret(x_otp_secret: Optional[str] = Header(None, alias="X-OTP-Secret")):
    """Guard for the public ``/receive_sms`` ingestion endpoint.

    When ``MAHANBOT_OTP_SECRET`` is configured, the Android SMS app must send
    it as the ``X-OTP-Secret`` header. Unconfigured → allowed (local-only
    deployments) after a one-time startup warning from server.py.
    """
    expected_secret = os.getenv("MAHANBOT_OTP_SECRET")
    if not expected_secret:
        if os.getenv("MAHANBOT_REQUIRE_OTP_SECRET", "").strip().lower() in _TRUE_VALUES:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Secret دریافت پیامک روی سرور تنظیم نشده است",
            )
        return True
    if x_otp_secret is None or not hmac.compare_digest(str(x_otp_secret), expected_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Secret دریافت پیامک نامعتبر است",
        )
    return True


# ── Session helpers ───────────────────────────────────────────────────
def _session_user(token: Optional[str]):
    # 1. Check in-memory cache
    if token:
        session = AUTH_SESSIONS.get(str(token))
        if session and float(session.get("expires_at") or 0) > time.time():
            user = DBHandler.get_app_user(session.get("username", ""))
            if user and user.get("active") and float(user.get("expires_at") or 0) > time.time():
                return user

    # 2. Check persistent DB sessions (restores session across server restarts)
    if token:
        db_session = DBHandler.get_app_session(str(token))
        if db_session:
            user = DBHandler.get_app_user(db_session.get("username", ""))
            if user and user.get("active") and float(user.get("expires_at") or 0) > time.time():
                AUTH_SESSIONS[str(token)] = {
                    "username": user["username"],
                    "expires_at": float(db_session["expires_at"]),
                }
                return user

    # 3. Local Desktop Mode: When MAHANBOT_REQUIRE_AUTH is not explicitly enforced,
    # auto-allow the active administrator account so the database, WebSocket, and bot are fully functional.
    is_auth_enforced = os.getenv("MAHANBOT_REQUIRE_AUTH", "").strip().lower() in _TRUE_VALUES
    if not is_auth_enforced:
        admin = DBHandler.get_app_user("admin")
        if admin and admin.get("active") and float(admin.get("expires_at") or 0) > time.time():
            return admin
        return {
            "id": 1,
            "username": "admin",
            "is_admin": 1,
            "active": 1,
            "expires_at": time.time() + 3650 * 86400,
        }

    return None


def _current_user(request: Request):
    user = _session_user(request.cookies.get(AUTH_COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="ابتدا وارد حساب کاربری شوید")
    return user


def _admin_user(request: Request):
    user = _current_user(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="این بخش فقط برای مدیر است")
    return user
