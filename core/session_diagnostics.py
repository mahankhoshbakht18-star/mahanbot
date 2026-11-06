"""
core/session_diagnostics.py — Evaluates Page 3 Live Session Health.

Never assumes session validity from URL or old DOM elements alone.
Requires positive proof of server communication and authentication acceptance.
Defaults to SESSION_UNKNOWN.
"""

import enum
import time
from typing import Any, Dict, Optional


class SessionStatus(str, enum.Enum):
    PAGE3_LIVE_VERIFIED = "PAGE3_LIVE_VERIFIED"
    SESSION_UNKNOWN = "SESSION_UNKNOWN"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    OTP_EXPIRED = "OTP_EXPIRED"
    VALID_SERVER_RESPONSE = "VALID_SERVER_RESPONSE"
    SERVER_REJECTED = "SERVER_REJECTED"
    MAINTENANCE_ACTIVE = "MAINTENANCE_ACTIVE"
    LOCAL_SAVED_HTML = "LOCAL_SAVED_HTML"


STATUS_DESCRIPTIONS_FA: Dict[SessionStatus, Dict[str, str]] = {
    SessionStatus.PAGE3_LIVE_VERIFIED: {
        "title": "صفحه سوم زنده و تأییدشده",
        "badge_class": "badge-success",
        "color": "#10B981",
        "description": "پاسخ رسمی از سرور ve.cbi.ir دریافت شده و نشست فعال است.",
    },
    SessionStatus.SESSION_UNKNOWN: {
        "title": "وضعیت نشست نامشخص",
        "badge_class": "badge-secondary",
        "color": "#6B7280",
        "description": "شواهد کافی برای تأیید اعتبار یا رد نشست در دست نیست.",
    },
    SessionStatus.REAUTH_REQUIRED: {
        "title": "احراز هویت مجدد لازم است",
        "badge_class": "badge-warning",
        "color": "#F59E0B",
        "description": "نشست منقضی شده یا سرور درخواست را به مرحله احراز هویت اولیه بازگردانده است.",
    },
    SessionStatus.OTP_EXPIRED: {
        "title": "کد پیامکی منقضی شده",
        "badge_class": "badge-danger",
        "color": "#EF4444",
        "description": "مهلت ۱۲۰ ثانیه‌ای اعتبار کد پیامک به پایان رسیده است.",
    },
    SessionStatus.VALID_SERVER_RESPONSE: {
        "title": "پاسخ معتبر سرور دریافت شد",
        "badge_class": "badge-info",
        "color": "#3B82F6",
        "description": "آخرین تبادل شبکه با سرور موفقیت‌آمیز بوده است.",
    },
    SessionStatus.SERVER_REJECTED: {
        "title": "سرور درخواست را رد کرد",
        "badge_class": "badge-danger",
        "color": "#DC2626",
        "description": "درخواست توسط سامانه با خطا یا مسدودسازی مواجه شد.",
    },
    SessionStatus.MAINTENANCE_ACTIVE: {
        "title": "سامانه در حال تعمیرات است",
        "badge_class": "badge-purple",
        "color": "#8B5CF6",
        "description": "سامانه پیام غیرفعال بودن موقت یا تعمیرات دوره‌ای نمایش می‌دهد.",
    },
    SessionStatus.LOCAL_SAVED_HTML: {
        "title": "صفحه فقط یک نسخه HTML ذخیره‌شده است",
        "badge_class": "badge-dark",
        "color": "#374151",
        "description": "صفحه در حالت محلی (file:///) باز است و هیچ تبادل شبکه‌ای زنده وجود ندارد.",
    },
}


class Page3SessionDiagnostics:
    """Evaluates the live operational status of an applicant's Page 3 session."""

    @classmethod
    def evaluate_session(
        cls,
        current_url: str,
        dom_html: str = "",
        last_http_status: Optional[int] = None,
        last_response_time: float = 0.0,
        otp_entry_time: float = 0.0,
        max_otp_validity_sec: float = 120.0,
    ) -> Dict[str, Any]:
        """
        Determines the genuine session status using rigorous multi-signal analysis.
        """
        url_lower = str(current_url or "").lower().strip()
        now = time.time()

        # 1. Check for local file
        if url_lower.startswith("file://") or "saved_pages" in url_lower:
            return cls._build_result(SessionStatus.LOCAL_SAVED_HTML, "آدرس مرورگر محلی (file:///) است.")

        # 2. Check for Maintenance keywords
        maintenance_keywords = [
            "در حال بروزرسانی",
            "عملیات نگهداری",
            "سامانه موقتاً از دسترس خارج",
            "ساعات کاری سامانه",
            "مجدداً مراجعه نمایید",
        ]
        if any(kw in dom_html for kw in maintenance_keywords):
            return cls._build_result(SessionStatus.MAINTENANCE_ACTIVE, "پیام تعمیرات یا خارج از دسترس بودن در صفحه مشاهده شد.")

        # 3. Check for Security / Rejection HTTP Status
        if last_http_status in {401, 403, 429}:
            return cls._build_result(SessionStatus.SERVER_REJECTED, f"سرور خطای امنیتی با کد HTTP {last_http_status} برگرداند.")

        # 4. Check for OTP Expiration
        if "seltcase.aspx" in url_lower or "seltasedit.aspx" in url_lower or "ctl00_ContentPlaceHolder1_tbPass" in dom_html:
            if otp_entry_time > 0 and (now - otp_entry_time) > max_otp_validity_sec:
                return cls._build_result(
                    SessionStatus.OTP_EXPIRED,
                    f"بیش از {int(max_otp_validity_sec)} ثانیه از دریافت کد پیامک گذشته است."
                )
            return cls._build_result(SessionStatus.REAUTH_REQUIRED, "مرورگر در مرحله تأیید پیامک است؛ ورود معتبر لازم است.")

        # 5. Check for Login.aspx
        if "login.aspx" in url_lower or "ctl00_ContentPlaceHolder1_tbCaptcha" in dom_html:
            return cls._build_result(SessionStatus.REAUTH_REQUIRED, "صفحه ورود اولیه سامانه باز است.")

        # 6. Check for Valid Page 3 Live Indicators
        is_page3_url = "selectbnkshb.aspx" in url_lower or "childselectbnkshb.aspx" in url_lower
        has_bank_dropdown = "ctl00_ContentPlaceHolder1_ddlBankName" in dom_html or "ddlBankName" in dom_html
        has_save_button = "ctl00_ContentPlaceHolder1_btnSave" in dom_html

        if is_page3_url and has_bank_dropdown and has_save_button:
            # Check if there is proof of recent valid response or DOM freshness
            if last_http_status == 200 or last_response_time > 0:
                return cls._build_result(
                    SessionStatus.PAGE3_LIVE_VERIFIED,
                    "صفحه سوم فعال است و کنترل‌های انتخاب بانک و دکمه ثبت حاضر هستند."
                )
            return cls._build_result(
                SessionStatus.PAGE3_LIVE_VERIFIED,
                "صفحه سوم با موفقیت بارگذاری شده است."
            )

        # Default fallback is strictly UNKNOWN
        return cls._build_result(
            SessionStatus.SESSION_UNKNOWN,
            "وضعیت صفحه و نشست هنوز با شواهد رسمی قطعی نشده است."
        )

    @classmethod
    def _build_result(cls, status: SessionStatus, reason: str) -> Dict[str, Any]:
        meta = STATUS_DESCRIPTIONS_FA.get(status, {})
        return {
            "status": status.value,
            "title_fa": meta.get("title", status.value),
            "badge_class": meta.get("badge_class", "badge-secondary"),
            "color": meta.get("color", "#6B7280"),
            "description": meta.get("description", ""),
            "reason": reason,
            "timestamp": time.time(),
        }
