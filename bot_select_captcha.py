"""Compatibility facade. The captcha implementation lives in captcha_engine."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from captcha_engine import (
    IMG_SELECTORS as CAPTCHA_IMAGE_SELECTORS,
    RELOAD_SELECTORS as CAPTCHA_RELOAD_SELECTORS,
    ModelCaptchaSolver,
    solve_and_fill_captcha,
    refresh_captcha,
    capture_captcha_bytes,
)

__all__ = [
    "CAPTCHA_IMAGE_SELECTORS",
    "CAPTCHA_RELOAD_SELECTORS",
    "CaptchaResult",
    "ModelCaptchaSolver",
    "click_captcha_submit",
    "detect_captcha_failure",
    "detect_form_validation_error",
    "read_captcha_image_bytes",
    "refresh_captcha_or_reload",
    "resolve_captcha_step_name",
    "solve_captcha_step",
    "solve_captcha_wrapper",
    "solve_step_captcha",
    "validate_captcha_prediction",
    "wait_for_captcha_image_ready",
    "wait_for_manual_captcha_handoff",
]


@dataclass
class CaptchaResult:
    success: bool = False
    code: str = ""
    attempts_used: int = 0
    error_reason: Optional[str] = None


def validate_captcha_prediction(prediction: str, min_length: int = 4, max_length: int = 5) -> bool:
    if not prediction or not prediction.isalnum():
        return False
    return min_length <= len(prediction) <= max_length


def resolve_captcha_step_name(source: str = "", input_selector: Any = None) -> str:
    blob = f"{source} {input_selector}".lower()
    if any(key in blob for key in ("otp", "captcha2", "step_2", "step2")):
        return "STEP_2_OTP"
    if "status" in blob or "trace" in blob:
        return "STATUS"
    if "register" in blob and "3" in blob:
        return "REGISTER_3"
    if "register" in blob:
        return "REGISTER"
    return "STEP_1_ENTRY_FORM"


def read_captcha_image_bytes(page, image_elem):
    return capture_captcha_bytes(page, image_elem)


def detect_captcha_failure(page) -> bool:
    try:
        from otp_form_detector import normalize_text, live_scopes
        for scope in live_scopes(page):
            text = normalize_text(scope.locator("body").inner_text(timeout=500))
            if re.search(
                r"(?:کد امنیتی|کد تصویر|کپچا|عبارت امنیتی|security code).{0,30}"
                r"(?:اشتباه|نادرست|نامعتبر|صحیح نیست|صحیح نمی|incorrect|invalid)",
                text,
            ):
                return True
    except Exception:
        return False
    return False


def detect_form_validation_error(page) -> Optional[str]:
    selectors = (
        "#ctl00_ContentPlaceHolder1_lblMessage",
        "#ctl00_ContentPlaceHolder1_vsValidation",
        ".ValidationSummary",
        "span[id*='lblMessage']",
        "span[id*='lblError']",
        "div.alert-danger",
    )
    try:
        for selector in selectors:
            loc = page.locator(selector)
            if loc.count() > 0 and loc.first.is_visible():
                text = (loc.first.inner_text() or "").strip()
                if text and not any(word in text for word in ("کد امنیتی", "کپچا", "security")):
                    return text
    except Exception:
        return None
    return None


def wait_for_captcha_image_ready(page, timeout_ms: int = 5000, trigger_refresh_if_missing: bool = False, previous_src: Optional[str] = None):
    deadline = time.time() + max(0.3, timeout_ms / 1000.0)
    clicked = False
    while time.time() < deadline:
        for selector in CAPTCHA_IMAGE_SELECTORS:
            try:
                loc = page.locator(selector)
                if loc.count() > 0 and loc.first.is_visible():
                    src = ""
                    try:
                        src = str(loc.first.get_attribute("src") or "")
                    except Exception:
                        src = ""
                    if previous_src and src == previous_src:
                        continue
                    return loc.first, page
            except Exception:
                continue
        if trigger_refresh_if_missing and not clicked:
            clicked = True
            refresh_captcha_or_reload(None, page, captcha_rejected=True)
        time.sleep(0.15)
    return None, None


def refresh_captcha_or_reload(bot, page, input_sel="#ctl00_ContentPlaceHolder1_tbCaptcha", timeout=4000, max_retries=1, allow_reload=True, captcha_rejected=False, **kwargs):
    del timeout, max_retries, allow_reload
    rejected = captcha_rejected or kwargs.get("force") or getattr(bot, "_last_dialog_action", None) == "captcha_invalid"
    if not rejected:
        return False
    image, _ = wait_for_captcha_image_ready(page, timeout_ms=800, trigger_refresh_if_missing=False)
    reload = None
    for selector in CAPTCHA_RELOAD_SELECTORS:
        try:
            loc = page.locator(selector)
            if loc.count() > 0 and loc.first.is_visible():
                reload = loc.first
                break
        except Exception:
            continue
    if image is None:
        return False
    ok = refresh_captcha(page, image, reload, getattr(bot, 'settings', None))
    for selector in (input_sel, "input[id*='tbCaptcha' i]"):
        try:
            field = page.locator(selector)
            if field.count() > 0 and field.first.is_visible():
                field.first.fill("")
                break
        except Exception:
            continue
    if bot is not None:
        bot._last_dialog_action = None
        flags = getattr(bot, "_recovery_flags", None)
        if isinstance(flags, dict):
            flags["captcha_invalid"] = False
    return ok


def click_captcha_submit(page, button_selector, captcha_input=None) -> bool:
    from captcha_engine import _click_submit
    if not button_selector:
        return True
    if captcha_input is None:
        try:
            page.locator(str(button_selector).split(",")[0].strip()).first.click(timeout=2500)
            return True
        except Exception:
            return False
    return _click_submit(page, button_selector, captcha_input)


def solve_step_captcha(page, step_name: str = "captcha", force_refresh: bool = False, max_attempts: int = 3, bot=None, stop_event=None) -> CaptchaResult:
    del force_refresh
    result = solve_and_fill_captcha(
        page,
        submit=False,
        max_retries=max_attempts,
        bot=bot,
        stop_event=stop_event,
        step=step_name or "captcha",
    )
    return CaptchaResult(
        success=bool(result.filled),
        code=result.code,
        attempts_used=result.attempts,
        error_reason=None if result.filled else (result.reason or "unreadable"),
    )


def solve_captcha_step(bot, page, input_sel: str, btn_sel: str, source: str = "general") -> bool:
    result = solve_and_fill_captcha(
        page,
        input_selector=input_sel,
        button_selector=btn_sel,
        submit=True,
        max_retries=3,
        bot=bot,
        stop_event=getattr(bot, "_active_stop_event", None),
        step=resolve_captcha_step_name(source, input_sel),
    )
    return bool(result.filled and (result.submitted or not btn_sel))


def solve_captcha_wrapper(bot, page, input_sel, btn_sel, mode):
    del mode
    return solve_captcha_step(bot, page, input_sel, btn_sel, source="general")


def wait_for_manual_captcha_handoff(bot, page, input_sel, btn_sel, stop_event, source="manual", success_selectors=(), refresh_on_failure=False):
    del refresh_on_failure
    deadline = time.time() + 90
    while not (stop_event is not None and stop_event.is_set()) and time.time() < deadline:
        for selector in success_selectors or ():
            try:
                loc = page.locator(selector)
                if loc.count() > 0 and loc.first.is_visible():
                    return True
            except Exception:
                continue
        result = solve_and_fill_captcha(
            page,
            input_selector=input_sel,
            button_selector=btn_sel,
            submit=True,
            max_retries=1,
            bot=bot,
            stop_event=stop_event,
            step=source,
        )
        if result.submitted:
            return True
        time.sleep(0.8)
    return False
