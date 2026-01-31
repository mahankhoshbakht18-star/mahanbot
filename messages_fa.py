import logging
from typing import Dict

RESPONSES: Dict[str, str] = {
    "api_key_invalid": "کلید API نامعتبر است",
    "api_key_missing": "کلید API تنظیم نشده است",
    "user_not_found": "کاربر یافت نشد",
    "invalid_data": "داده نامعتبر",
    "tracking_code_missing": "کد رهگیری ندارد",
    "status_job_queued": "استعلام وضعیت در صف قرار گرفت",
    "job_already_running": "وظیفه قبلاً در صف یا در حال اجراست",
    "job_queue_not_ready": "صف اجرا آماده نیست",
    "loan_type_required": "نوع وام برای انتخاب بانک الزامی است",
}

LOG_MESSAGES: Dict[str, str] = {
    "api_key_not_configured": "کلید API تنظیم نشده است؛ درخواست بدون احراز هویت پذیرفته شد.",
    "invalid_user_data": "داده کاربر نامعتبر است و قابل پردازش نیست.",
}

STATUS_MESSAGES: Dict[str, str] = {
    "queued": "در صف",
    "running": "در حال اجرا",
    "cancelled": "لغو شد",
    "completed": "پایان یافت",
}

_CATALOGS = {
    "responses": RESPONSES,
    "log": LOG_MESSAGES,
    "status": STATUS_MESSAGES,
}

_MISSING_KEYS = set()


def get_message(category: str, key: str, fallback: str | None = None) -> str:
    catalog = _CATALOGS.get(category, {})
    if key in catalog:
        return catalog[key]
    missing_key = f"{category}.{key}"
    if missing_key not in _MISSING_KEYS:
        logging.warning("Missing message key: %s", missing_key)
        _MISSING_KEYS.add(missing_key)
    return fallback if fallback is not None else key
