"""
routes/auth.py — Login, logout, auth/me, admin user management.
"""
import os
import time
import secrets
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from database import DBHandler
from core.state import AUTH_SESSIONS, AUTH_COOKIE, STATIC_DIR
from core.deps import _current_user, _admin_user
from core.models import (
    LoginRequest,
    SubscriptionUserRequest,
    SubscriptionStateRequest,
)

router = APIRouter()


# ── Static pages ──────────────────────────────────────────────────────
@router.get("/health")
def health():
    return {"status": "ok", "service": "mahan-bot"}


@router.get("/login")
def serve_login(request: Request):
    from core.deps import _session_user
    if _session_user(request.cookies.get(AUTH_COOKIE)):
        return RedirectResponse("/", status_code=303)
    path = os.path.join(STATIC_DIR, "login.html")
    with open(path, "r", encoding="utf-8") as handle:
        return HTMLResponse(handle.read())


@router.post("/login")
def login(req: LoginRequest):
    user = DBHandler.authenticate_app_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="نام کاربری، رمز یا اعتبار اشتراک صحیح نیست")
    token = secrets.token_urlsafe(32)
    AUTH_SESSIONS[token] = {
        "username": user["username"],
        "expires_at": min(float(user["expires_at"]), time.time() + 30 * 86400),
    }
    response = JSONResponse({
        "status": "ok",
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
        "license_valid_until": user["expires_at"],
    })
    response.set_cookie(
        AUTH_COOKIE, token, httponly=True, samesite="strict", secure=False,
        max_age=max(1, int(AUTH_SESSIONS[token]["expires_at"] - time.time())), path="/",
    )
    return response


@router.post("/logout")
def logout(request: Request):
    AUTH_SESSIONS.pop(request.cookies.get(AUTH_COOKIE, ""), None)
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(AUTH_COOKIE, path="/")
    return response


@router.get("/auth/me")
def auth_me(user: Dict[str, Any] = Depends(_current_user)):
    return {
        "username": user["username"],
        "expires_at": user["expires_at"],
        "is_admin": bool(user["is_admin"]),
    }


# ── Admin user management ────────────────────────────────────────────
@router.get("/admin/users")
def list_subscription_users(_: Dict[str, Any] = Depends(_admin_user)):
    return DBHandler.list_app_users()


@router.post("/admin/users")
def save_subscription_user(req: SubscriptionUserRequest, _: Dict[str, Any] = Depends(_admin_user)):
    if not req.username.strip() or len(req.password) < 4 or not 1 <= req.days <= 3650:
        raise HTTPException(status_code=400, detail="نام کاربری، رمز حداقل ۴ حرف و تعداد روز ۱ تا ۳۶۵۰ لازم است")
    expires_at = DBHandler.save_app_user(req.username, req.password, req.days, req.is_admin)
    return {"status": "ok", "username": req.username.strip(), "expires_at": expires_at}


@router.put("/admin/users/{username}/state")
def set_subscription_user_state(username: str, req: SubscriptionStateRequest, admin: Dict[str, Any] = Depends(_admin_user)):
    if username.casefold() == str(admin["username"]).casefold() and not req.active:
        raise HTTPException(status_code=400, detail="حساب مدیرِ جاری را نمی‌توان غیرفعال کرد")
    if not DBHandler.set_app_user_active(username, req.active):
        raise HTTPException(status_code=404, detail="کاربر پیدا نشد")
    return {"status": "ok"}
