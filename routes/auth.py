"""
routes/auth.py — Login, logout, auth/me, OTP multi-factor auth, password reset, and admin user/SMS management.
"""
import os
import re
import time
import secrets
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from database import DBHandler
from core.state import AUTH_SESSIONS, AUTH_COOKIE, STATIC_DIR
from core.deps import _current_user, _admin_user
from core.sms_gateway import get_sms_gateway, normalize_mobile, mask_mobile
from core.models import (
    LoginRequest,
    SubscriptionUserRequest,
    SubscriptionStateRequest,
    SendOTPRequest,
    VerifyOTPRequest,
    ResetPasswordRequest,
    SMSConfigRequest,
    TestSMSRequest,
)

router = APIRouter()


def _normalize_digits(s: str) -> str:
    fa_digits = "۰۱۲۳۴۵۶۷۸۹"
    ar_digits = "٠١٢٣٤٥٦٧٨٩"
    res = []
    for ch in str(s or ""):
        if ch in fa_digits:
            res.append(str(fa_digits.index(ch)))
        elif ch in ar_digits:
            res.append(str(ar_digits.index(ch)))
        else:
            res.append(ch)
    return "".join(res).strip()


def _issue_auth_session(user: Dict[str, Any]) -> JSONResponse:
    token = secrets.token_urlsafe(32)
    expires_at = min(float(user["expires_at"]), time.time() + 30 * 86400)
    AUTH_SESSIONS[token] = {
        "username": user["username"],
        "expires_at": expires_at,
    }
    DBHandler.save_app_session(token, user["username"], expires_at)
    response = JSONResponse({
        "status": "ok",
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
        "license_valid_until": user["expires_at"],
    })
    response.set_cookie(
        AUTH_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=max(1, int(expires_at - time.time())),
        path="/",
    )
    return response


# ── Static pages ──────────────────────────────────────────────────────
@router.get("/health")
def health():
    return {"status": "ok", "service": "mahan-bot"}


@router.get("/login")
def serve_login(request: Request):
    from core.deps import _session_user
    token = request.cookies.get(AUTH_COOKIE)
    if token and _session_user(token):
        return RedirectResponse("/", status_code=303)
    path = os.path.join(STATIC_DIR, "login.html")
    with open(path, "r", encoding="utf-8") as handle:
        return HTMLResponse(handle.read())


# ── Username/Password Login ───────────────────────────────────────────
@router.post("/login")
def login(req: LoginRequest):
    user = DBHandler.authenticate_app_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="نام کاربری، رمز یا اعتبار اشتراک صحیح نیست")
    return _issue_auth_session(user)


@router.post("/logout")
def logout(request: Request):
    token = request.cookies.get(AUTH_COOKIE, "")
    AUTH_SESSIONS.pop(token, None)
    DBHandler.delete_app_session(token)
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(AUTH_COOKIE, path="/")
    return response


@router.get("/auth/me")
def auth_me(user: Dict[str, Any] = Depends(_current_user)):
    edition = os.environ.get("MAHANBOT_EDITION", "").strip().upper()
    is_admin = bool(user["is_admin"])
    if edition == "CLIENT":
        is_admin = False
    return {
        "username": user["username"],
        "mobile": user.get("mobile") or "",
        "expires_at": user["expires_at"],
        "is_admin": is_admin,
        "edition": edition or ("ADMIN" if is_admin else "CLIENT"),
    }


# ── Real OTP Authentication Endpoints ─────────────────────────────────
@router.post("/auth/otp/send")
def send_otp(req: SendOTPRequest, request: Request):
    cfg = DBHandler.get_config()
    if not cfg.get("otp_auth_enabled", True):
        raise HTTPException(status_code=403, detail="ورود با پیامک و OTP در تنظیمات سامانه غیرفعال است.")

    mobile = normalize_mobile(req.mobile)
    if not mobile:
        raise HTTPException(status_code=400, detail="شماره موبایل وارد شده معتبر نیست (فرمت صحیح: ۰۹۱۲۳۴۵۶۷۸۹).")

    purpose = str(req.purpose or "login").strip().lower()
    if purpose not in ("login", "register", "reset_password", "link"):
        raise HTTPException(status_code=400, detail="هدف درخواست کد نامعتبر است.")

    # In reset_password mode, ensure account exists with this mobile
    if purpose == "reset_password":
        existing = DBHandler.get_user_by_mobile(mobile)
        if not existing:
            raise HTTPException(status_code=404, detail="حساب کاربری فعالی با این شماره موبایل یافت نشد.")

    # Cryptographically secure 6-digit random code
    code = f"{secrets.randbelow(900000) + 100000}"
    client_ip = request.client.host if request.client else ""

    ok, msg, cooldown = DBHandler.create_auth_otp(mobile, code, purpose=purpose, ip=client_ip)
    if not ok:
        status_code = 429 if ("شکیبا" in msg or "محدودیت" in msg or "بیش از حد" in msg) else 400
        raise HTTPException(status_code=status_code, detail=msg)

    # Dispatch via SMS gateway
    gateway = get_sms_gateway(cfg)
    sent, send_err = gateway.send_otp(mobile, code, cfg.get("sms_otp_template"))
    if not sent:
        raise HTTPException(status_code=502, detail=f"ارسال پیامک ناموفق بود: {send_err}")

    return {
        "status": "ok",
        "message": f"کد تایید ۶ رقمی به شماره {mask_mobile(mobile)} پیامک شد.",
        "resend_in": cooldown,
        "masked_mobile": mask_mobile(mobile),
    }


@router.post("/auth/otp/verify")
def verify_otp(req: VerifyOTPRequest):
    cfg = DBHandler.get_config()
    if not cfg.get("otp_auth_enabled", True):
        raise HTTPException(status_code=403, detail="ورود با پیامک در تنظیمات سامانه غیرفعال است.")

    mobile = normalize_mobile(req.mobile)
    if not mobile:
        raise HTTPException(status_code=400, detail="شماره موبایل معتبر نیست.")

    clean_code = _normalize_digits(req.code)
    if not clean_code.isdigit() or len(clean_code) != 6:
        raise HTTPException(status_code=400, detail="کد تایید باید دقیقاً ۶ رقم باشد.")

    purpose = str(req.purpose or "login").strip().lower()
    ok, msg, user = DBHandler.verify_auth_otp(mobile, clean_code, purpose=purpose)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    # 1. Login Mode
    if purpose == "login":
        if user:
            return _issue_auth_session(user)

        # User does not exist yet -> create new account with 30-day trial
        requested_username = (req.username or "").strip()
        if not requested_username:
            # Auto-generate a friendly username like user_0912_xxxx
            requested_username = f"user_{mobile[-4:]}_{secrets.token_hex(2)}"

        success, cr_msg, new_user = DBHandler.create_user_with_mobile(
            username=requested_username,
            mobile=mobile,
            password=None,
            days=30,
            is_admin=False,
        )
        if not success or not new_user:
            raise HTTPException(status_code=400, detail=cr_msg or "خطا در ایجاد حساب کاربری با این شماره.")

        return _issue_auth_session(new_user)

    # 2. Reset Password Mode
    elif purpose == "reset_password":
        existing_user = DBHandler.get_user_by_mobile(mobile)
        if not existing_user:
            raise HTTPException(status_code=404, detail="حساب کاربری متصل به این شماره موبایل یافت نشد.")

        reset_token = DBHandler.create_password_reset_token(existing_user["username"])
        if not reset_token:
            raise HTTPException(status_code=500, detail="خطا در صدور توکن تغییر گذرواژه.")

        return {
            "status": "ok",
            "message": "کد تایید شد؛ اکنون گذرواژه جدید خود را ثبت کنید.",
            "reset_token": reset_token,
            "username": existing_user["username"],
        }

    # 3. Link or other purpose
    return {"status": "ok", "message": msg}


@router.post("/auth/reset-password")
def reset_password(req: ResetPasswordRequest):
    if len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="گذرواژه جدید باید حداقل ۴ کاراکتر باشد.")
    ok, msg = DBHandler.reset_password_with_token(req.reset_token, req.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


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


# ── Admin SMS Gateway Management ─────────────────────────────────────
@router.get("/admin/sms-config")
def get_sms_config(_: Dict[str, Any] = Depends(_admin_user)):
    cfg = DBHandler.get_config()
    return {
        "otp_auth_enabled": bool(cfg.get("otp_auth_enabled", True)),
        "sms_provider": str(cfg.get("sms_provider") or "mock"),
        "sms_sender_line": str(cfg.get("sms_sender_line") or ""),
        "sms_pattern_code": str(cfg.get("sms_pattern_code") or ""),
        "sms_otp_template": str(cfg.get("sms_otp_template") or "کد ورود به ماهان‌بات: {code}\nاعتبار: ۵ دقیقه"),
        "has_api_key": bool(cfg.get("sms_api_key")),
    }


@router.post("/admin/sms-config")
def save_sms_config(req: SMSConfigRequest, _: Dict[str, Any] = Depends(_admin_user)):
    cfg = DBHandler.get_config()
    cfg["otp_auth_enabled"] = bool(req.otp_auth_enabled)
    cfg["sms_provider"] = str(req.sms_provider or "mock").strip().lower()
    if req.sms_api_key is not None and req.sms_api_key.strip():
        cfg["sms_api_key"] = req.sms_api_key.strip()
    cfg["sms_sender_line"] = str(req.sms_sender_line or "").strip()
    cfg["sms_pattern_code"] = str(req.sms_pattern_code or "").strip()
    cfg["sms_otp_template"] = str(req.sms_otp_template or "کد ورود به ماهان‌بات: {code}\nاعتبار: ۵ دقیقه").strip()
    DBHandler.update_config(cfg)
    return {"status": "ok", "message": "تنظیمات پنل پیامک با موفقیت ذخیره شد."}


@router.post("/admin/sms-test")
def test_sms_gateway(req: TestSMSRequest, _: Dict[str, Any] = Depends(_admin_user)):
    mobile = normalize_mobile(req.mobile)
    if not mobile:
        raise HTTPException(status_code=400, detail="شماره موبایل وارد شده معتبر نیست.")
    cfg = DBHandler.get_config()
    gateway = get_sms_gateway(cfg)
    test_code = "123456"
    sent, msg = gateway.send_otp(mobile, test_code, cfg.get("sms_otp_template"))
    if not sent:
        raise HTTPException(status_code=502, detail=f"ارسال پیامک آزمایشی ناموفق بود: {msg}")
    return {"status": "ok", "message": f"پیامک آزمایشی با موفقیت ارسال شد: {msg}"}
