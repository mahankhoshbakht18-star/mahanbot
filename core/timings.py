"""Single source of truth for every speed/timing knob of the bot.

Code reads a value with ``timing(settings, key)``; the config normaliser, the
settings API validation and the dashboard form are all generated from TIMINGS,
so a knob is defined exactly once.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional

# key: (default, low, high, unit, group, label, help)
TIMINGS: Dict[str, tuple] = {
    # ── SMS / OTP ────────────────────────────────────────────────────
    "otp_idle_check_ms": (
        120, 20, 1000, "ms", "sms",
        "بررسی صفحه هنگام انتظار پیامک",
        "پیامکی که به سرور محلی برسد بات را فوراً بیدار می‌کند؛ این عدد فقط فاصله بررسی صفحه و ورود دستی است.",
    ),
    "otp_remote_poll_interval_sec": (
        0.5, 0.2, 5.0, "s", "sms",
        "استعلام پیامک از سرور/رله راه‌دور",
        "در پس‌زمینه انجام می‌شود و چرخه اصلی را متوقف نمی‌کند.",
    ),
    "otp_submit_settle_ms": (
        200, 0, 1000, "ms", "sms",
        "مکث قبل از کلیک «ادامه» در صفحه پیامک",
        "بعد از وارد شدن کد پیامک و کپچا.",
    ),
    # ── Captcha ──────────────────────────────────────────────────────
    "captcha_retry_delay": (
        0.25, 0.0, 10.0, "s", "captcha",
        "مکث بین تلاش‌های کپچای مرحله ۱",
        "پس از رد کپچا در فرم کد ملی.",
    ),
    "otp_captcha_retry_ms": (
        50, 0, 2000, "ms", "captcha",
        "مکث بین تلاش‌های کپچای مرحله پیامک",
        "وقتی مدل کپچای صفحه پیامک را نخواند.",
    ),
    "captcha_refresh_timeout_ms": (
        1200, 300, 5000, "ms", "captcha",
        "حداکثر انتظار برای تصویر جدید کپچا",
        "بعد از زدن دکمه تغییر تصویر.",
    ),
    # ── Page 3: bank selection ──────────────────────────────────────
    "page3_loop_ms": (
        80, 20, 2000, "ms", "bank",
        "فاصله چرخه پایش صفحه ۳",
        "هر چند میلی‌ثانیه وضعیت صفحه بانک بررسی شود.",
    ),
    "bank_postback_start_grace_ms": (
        150, 0, 1000, "ms", "bank",
        "مهلت شروع بارگذاری پس از انتخاب بانک/روش/شعبه",
        "به محض شروع بارگذاری سایت، انتظار تمام می‌شود؛ پس عدد کوچک‌تر فقط در حالت بدون بارگذاری سریع‌تر است.",
    ),
    "bank_postback_timeout_ms": (
        8000, 1000, 30000, "ms", "bank",
        "حداکثر انتظار پایان بارگذاری انتخاب",
        "سقف انتظار برای پاسخ سایت پس از هر انتخاب.",
    ),
    "bank_list_refresh_seconds": (
        3.5, 1.0, 300.0, "s", "bank",
        "فاصله ریلود فهرست بانک",
        "وقتی بانک اولویت‌دار در فهرست استانی باز نیست.",
    ),
    "bank_first_miss_reload_seconds": (
        0.8, 0.0, 30.0, "s", "bank",
        "اولین ریلود پس از نبودن بانک",
        "ریلود اول سریع‌تر از ریلودهای بعدی انجام می‌شود.",
    ),
    # ── Final submit ─────────────────────────────────────────────────
    "bank_capacity_check_interval": (
        20.0, 1.0, 600.0, "s", "submit",
        "فاصله تلاش مجدد ثبت نهایی",
        "پس از پیام تکمیل ظرفیت. هر متقاضی می‌تواند عدد اختصاصی داشته باشد.",
    ),
    "final_submit_response_timeout_seconds": (
        10.0, 2.0, 60.0, "s", "submit",
        "حداکثر انتظار پاسخ ثبت نهایی",
        "پس از کلیک ثبت؛ بعد از آن نتیجه نامعلوم ثبت می‌شود.",
    ),
    "final_submit_poll_ms": (
        50, 10, 500, "ms", "submit",
        "فاصله بررسی نتیجه ثبت نهایی",
        "بعد از کلیک ثبت، هر چند میلی‌ثانیه صفحه بررسی شود.",
    ),
}

GROUPS = (
    ("sms", "پیامک"),
    ("captcha", "کپچا"),
    ("bank", "صفحه ۳ — انتخاب بانک"),
    ("submit", "ثبت نهایی"),
)

# Older config keys that carried the same value.
ALIASES = {
    "bank_list_refresh_seconds": ("bank_refresh_interval", "bank_list_check_interval"),
    "bank_capacity_check_interval": ("capacity_check_interval_seconds", "bank_capacity_recheck_seconds"),
}


def _bounded(key: str, value: Any) -> Optional[float]:
    default, low, high = TIMINGS[key][:3]
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not low <= number <= high:
        return None
    return int(number) if isinstance(default, int) else number


def _raw(settings: Optional[Mapping[str, Any]], key: str) -> Any:
    if not settings:
        return None
    for name in (key, *ALIASES.get(key, ())):
        value = settings.get(name)
        if value is not None:
            return value
    return None


def timing(settings: Optional[Mapping[str, Any]], key: str) -> float:
    """Bounded value in the unit TIMINGS declares (seconds or milliseconds)."""
    value = _bounded(key, _raw(settings, key))
    return TIMINGS[key][0] if value is None else value


def seconds(settings: Optional[Mapping[str, Any]], key: str) -> float:
    value = float(timing(settings, key))
    return value / 1000.0 if TIMINGS[key][3] == "ms" else value


def normalize(config: Dict[str, Any]) -> Dict[str, Any]:
    """Write every knob (and its legacy aliases) back as one bounded value."""
    if not config.get("timings_v1"):
        # The old controller silently replaced 5s with 3.5s. Keep that speed for
        # untouched configs now that the real value is shown and respected.
        if _raw(config, "bank_list_refresh_seconds") in (5, 5.0):
            config["bank_list_refresh_seconds"] = 3.5
            for alias in ALIASES["bank_list_refresh_seconds"]:
                config.pop(alias, None)
        config["timings_v1"] = True
    for key in TIMINGS:
        value = timing(config, key)
        config[key] = value
        for alias in ALIASES.get(key, ()):
            config[alias] = value
    return config


def validate(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the timing keys of payload, bounded; raise ValueError naming the bad key."""
    clean: Dict[str, Any] = {}
    for key in TIMINGS:
        if key not in payload or payload[key] is None:
            continue
        value = _bounded(key, payload[key])
        if value is None:
            _, low, high, unit = TIMINGS[key][:4]
            raise ValueError(f"{TIMINGS[key][5]}: باید بین {low} و {high} {'میلی‌ثانیه' if unit == 'ms' else 'ثانیه'} باشد.")
        clean[key] = value
        for alias in ALIASES.get(key, ()):
            clean[alias] = value
    return clean


def schema(config: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    out = []
    for group, title in GROUPS:
        items = []
        for key, (default, low, high, unit, item_group, label, help_text) in TIMINGS.items():
            if item_group != group:
                continue
            items.append({
                "key": key, "default": default, "min": low, "max": high, "unit": unit,
                "label": label, "help": help_text, "value": timing(config, key),
            })
        out.append({"group": group, "title": title, "items": items})
    return out
