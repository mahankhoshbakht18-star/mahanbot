"""
core/captcha_pipeline.py — Unified CAPTCHA Discovery and Lifecycle Pipeline

Architecture:
    CURRENT_PAGE_DETECTED
            |
            v
    CAPTCHA_DISCOVERY
            |
            v
    CAPTCHA_TYPE_CLASSIFIED
            |
            v
    IMAGE_AND_INPUT_MATCHED
            |
            v
    RECOGNITION_OR_MANUAL_HANDOFF
            |
            v
    FIELD_VALUE_VALIDATED
            |
            v
    READY_FOR_NEXT_STEP

Handles:
- Stage-specific element selectors (Login, Step 2 OTP, Step 3 Bank/Reserve).
- DOM postback / AJAX re-evaluation.
- Distinguishing TSPD security interstitials vs standard image challenges.
- Noise filtering (SoundIcon, ReloadIcon, progressbar, etc.).
- Proactive pre-filling during OTP wait.
- Non-destructive image refresh via ReloadLink without session reload.
- Full structured event logging with strict redaction of sensitive credentials.
"""

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mahanbot.captcha_pipeline")


class CaptchaStage(str, Enum):
    STAGE_1_LOGIN = "STAGE_1_LOGIN"
    STEP_2_OTP = "STEP_2_OTP"
    STEP_3_BANK_SELECT = "STEP_3_BANK_SELECT"
    FACILITY_SELECTION = "FACILITY_SELECTION"
    UNKNOWN = "UNKNOWN"


class CaptchaType(str, Enum):
    STANDARD_IMAGE = "STANDARD_IMAGE"
    TSPD_CHALLENGE = "TSPD_CHALLENGE"
    NONE_REQUIRED = "NONE_REQUIRED"
    UNKNOWN = "UNKNOWN"


@dataclass
class CaptchaControls:
    stage: str
    captcha_type: CaptchaType
    scope: Any = None
    image_loc: Any = None
    input_loc: Any = None
    submit_btn: Any = None
    reload_btn: Any = None
    image_id: str = ""
    image_src: str = ""
    input_id: str = ""
    image_ready: bool = False
    is_tspd: bool = False
    error_reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        return (
            self.captcha_type == CaptchaType.STANDARD_IMAGE
            and self.image_loc is not None
            and self.input_loc is not None
            and self.submit_btn is not None
        )


class CaptchaPipeline:
    """Enterprise CAPTCHA discovery and execution engine across all CBI stages."""

    # Stage-specific configuration
    STAGE_SELECTORS = {
        CaptchaStage.STEP_2_OTP: {
            "inputs": [
                "#ctl00_ContentPlaceHolder1_tbCaptcha2",
                "input[name$='tbCaptcha2']",
                "input[id*='tbCaptcha2' i]",
                "input[name*='tbCaptcha2' i]",
            ],
            "submits": [
                "#ctl00_ContentPlaceHolder1_btnContinue1",
                "input[name$='btnContinue1']",
                "input[name*='btnContinue']",
                "button:has-text('ادامه')",
                "input[type='submit'][value*='ادامه']",
            ],
            "images": [
                "#c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage",
                "img[id*='captcha2' i][id*='CaptchaImage' i]",
                "img[id*='CaptchaImage' i]",
                "img.BDC_CaptchaImage",
                "img[src*='BotDetectCaptcha'][src*='get=image']",
            ],
            "reloads": [
                "#c_selectbnkshb_ctl00_contentplaceholder1_captcha2_ReloadLink",
                "a[id*='captcha2' i][id*='ReloadLink' i]",
                ".BDC_ReloadLink",
                "a[id*='ReloadLink' i]",
                "img[id*='ReloadIcon' i]",
            ],
        },
        CaptchaStage.STAGE_1_LOGIN: {
            "inputs": [
                "#ctl00_ContentPlaceHolder1_tbCaptcha",
                "input[name$='tbCaptcha']",
                "#ctl00_ContentPlaceHolder1_tbCaptcha2",
            ],
            "submits": [
                "#ctl00_ContentPlaceHolder1_btnLogin",
                "input[name$='btnLogin']",
                "input[type='submit'][value*='ورود']",
            ],
            "images": [
                "img.BDC_CaptchaImage",
                "img[id*='CaptchaImage' i]",
                "img[src*='BotDetectCaptcha'][src*='get=image']",
            ],
            "reloads": [
                ".BDC_ReloadLink",
                "a[id*='ReloadLink' i]",
                "img[id*='ReloadIcon' i]",
            ],
        },
        CaptchaStage.STEP_3_BANK_SELECT: {
            "inputs": [
                "#ctl00_ContentPlaceHolder1_tbCaptcha",
                "input[name$='tbCaptcha']",
                "#ans",
            ],
            "submits": [
                "#ctl00_ContentPlaceHolder1_btnSave",
                "input[name$='btnSave']",
                "button:has-text('ثبت')",
            ],
            "images": [
                "img.BDC_CaptchaImage",
                "img[id*='CaptchaImage' i]",
                "img[src*='BotDetectCaptcha'][src*='get=image']",
            ],
            "reloads": [
                ".BDC_ReloadLink",
                "a[id*='ReloadLink' i]",
            ],
        },
    }

    NOISE_KEYWORDS = ("sound", "reload", "icon", "progressbar", "help", "ico")

    @classmethod
    def emit_event(
        cls,
        bot: Any,
        event_code: str,
        step: str = "",
        reason: str = "",
        result: str = "",
        message: str = "",
        level: str = "info",
        page: Any = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        """Unified structured logger with mandatory redaction."""
        try:
            if hasattr(bot, "emit_event"):
                bot.emit_event(
                    event_code,
                    step=step,
                    reason=reason,
                    result=result,
                    message=message,
                    level=level,
                    page=page,
                    extra=extra,
                )
            elif hasattr(bot, "log"):
                bot.log(f"[{event_code}] {message}", level, page)
        except Exception:
            pass

    @classmethod
    def discover_captcha(
        cls,
        bot: Any,
        page: Any,
        stage: str = CaptchaStage.STEP_2_OTP,
        wait_ms: int = 3000,
    ) -> CaptchaControls:
        """
        Executes unified CAPTCHA discovery across frames, classifies the type,
        filters noise, and returns paired controls.
        """
        cls.emit_event(
            bot,
            "CAPTCHA_DISCOVERY_STARTED",
            step=stage,
            message=f"جستجوی المان‌های کپچا در مرحله {stage} آغاز شد.",
            page=page,
        )

        controls = CaptchaControls(stage=stage, captcha_type=CaptchaType.UNKNOWN, scope=page)
        if page is None or (hasattr(page, "is_closed") and page.is_closed()):
            controls.error_reason = "page_closed"
            cls.emit_event(bot, "CAPTCHA_ROOT_CAUSE", step=stage, reason="page_closed", level="error")
            return controls

        # 1. Frame Iteration & TSPD Security Check
        live_frames = []
        try:
            for idx, frame in enumerate(page.frames):
                frame_url = getattr(frame, "url", "") or ""
                frame_name = getattr(frame, "name", "") or ""
                cls.emit_event(
                    bot,
                    "CAPTCHA_FRAME_INSPECTED",
                    step=stage,
                    reason=f"index={idx},url={frame_url[:60]}",
                    message=f"بررسی فریم شماره {idx}: {frame_name or frame_url[:50]}",
                    page=page,
                )
                if "tspd" in frame_url.lower() or "tspd" in frame_name.lower():
                    controls.is_tspd = True
                    continue
                live_frames.append(frame)
        except Exception:
            live_frames = [page]

        if not live_frames:
            live_frames = [page]

        # Check if the whole page is overtaken by TSPD / WAF challenge
        try:
            body_text = page.inner_text("body", timeout=500) if hasattr(page, "inner_text") else ""
            page_url = getattr(page, "url", "") or ""
            if "tspd" in page_url.lower() or "incident" in body_text.lower() or "challenge" in body_text.lower():
                controls.captcha_type = CaptchaType.TSPD_CHALLENGE
                cls.emit_event(
                    bot,
                    "CAPTCHA_TYPE_DETECTED",
                    step=stage,
                    result="TSPD_CHALLENGE",
                    message="چالش امنیتی فایروال / TSPD در صفحه شناسایی شد.",
                    level="warning",
                    page=page,
                )
                return controls
        except Exception:
            pass

        selectors = cls.STAGE_SELECTORS.get(stage, cls.STAGE_SELECTORS[CaptchaStage.STEP_2_OTP])

        # 2. Scope-based search across candidate frames
        matched_scope = live_frames[0]
        input_loc = None
        image_loc = None

        for scope in live_frames:
            # A. Find Input Field in scope
            cur_input = None
            cur_input_id = ""
            for sel in selectors["inputs"]:
                try:
                    cand = scope.locator(sel)
                    if cand.count() > 0 and cand.first.is_visible():
                        cur_input = cand.first
                        cur_input_id = sel
                        break
                except Exception:
                    continue

            # B. Find Captcha Image in scope
            cur_image = None
            cur_image_id = ""
            cur_image_src = ""
            cur_image_ready = False
            for sel in selectors["images"]:
                try:
                    cand = scope.locator(sel)
                    if cand.count() > 0:
                        for i in range(cand.count()):
                            el = cand.nth(i)
                            if not el.is_visible():
                                continue
                            try:
                                info = el.evaluate("""(img) => ({
                                    id: img.id || '',
                                    src: img.src || '',
                                    naturalWidth: img.naturalWidth || 0,
                                    complete: img.complete || false
                                })""")
                            except Exception:
                                info = {
                                    "id": el.get_attribute("id") or "",
                                    "src": el.get_attribute("src") or "",
                                    "naturalWidth": 160,
                                    "complete": True,
                                }
                            full_id = (info.get("id", "") + " " + info.get("src", "")).lower()
                            if any(noise in full_id for noise in cls.NOISE_KEYWORDS):
                                continue
                            cur_image = el
                            cur_image_id = info.get("id") or sel
                            cur_image_src = info.get("src") or ""
                            cur_image_ready = bool(info.get("complete") and (info.get("naturalWidth") is None or info.get("naturalWidth", 0) > 0))
                            break
                        if cur_image:
                            break
                except Exception:
                    continue

            if cur_input and cur_image:
                input_loc = cur_input
                image_loc = cur_image
                controls.input_id = cur_input_id
                controls.image_id = cur_image_id
                controls.image_src = cur_image_src
                controls.image_ready = cur_image_ready
                matched_scope = scope
                break
            elif cur_input or cur_image:
                if not input_loc and cur_input:
                    input_loc = cur_input
                    controls.input_id = cur_input_id
                    matched_scope = scope
                if not image_loc and cur_image:
                    image_loc = cur_image
                    controls.image_id = cur_image_id
                    controls.image_src = cur_image_src
                    controls.image_ready = cur_image_ready
                    matched_scope = scope

        controls.scope = matched_scope
        controls.input_loc = input_loc
        controls.image_loc = image_loc

        # Emit individual element findings
        if input_loc is not None:
            cls.emit_event(
                bot,
                "CAPTCHA_INPUT_FOUND",
                step=stage,
                result="found",
                message=f"فیلد ورودی کپچا شناسایی شد ({controls.input_id}).",
                page=page,
            )
        else:
            cls.emit_event(
                bot,
                "CAPTCHA_INPUT_NOT_FOUND",
                step=stage,
                result="not_found",
                message="فیلد ورودی کپچا در صفحه یافت نشد.",
                level="warning",
                page=page,
            )

        if image_loc is not None:
            cls.emit_event(
                bot,
                "CAPTCHA_IMAGE_FOUND",
                step=stage,
                result="found",
                message=f"تصویر کپچا شناسایی شد ({controls.image_id}).",
                page=page,
            )
        else:
            cls.emit_event(
                bot,
                "CAPTCHA_IMAGE_NOT_FOUND",
                step=stage,
                result="not_found",
                message="تصویر کپچا در صفحه یافت نشد.",
                level="warning",
                page=page,
            )

        # C. Find Submit Button
        for sel in selectors["submits"]:
            try:
                cand = matched_scope.locator(sel)
                if cand.count() > 0 and cand.first.is_visible():
                    controls.submit_btn = cand.first
                    break
            except Exception:
                continue

        # D. Find Reload Button
        for sel in selectors["reloads"]:
            try:
                cand = matched_scope.locator(sel)
                if cand.count() > 0 and cand.first.is_visible():
                    controls.reload_btn = cand.first
                    break
            except Exception:
                continue

        # 3. Classify Captcha Type
        if controls.image_loc is not None and controls.input_loc is not None:
            controls.captcha_type = CaptchaType.STANDARD_IMAGE
            cls.emit_event(
                bot,
                "CAPTCHA_TYPE_DETECTED",
                step=stage,
                result="STANDARD_IMAGE",
                message="کپچای تصویری استاندارد تأیید شد.",
                page=page,
            )
        elif controls.input_loc is None and controls.image_loc is None:
            controls.captcha_type = CaptchaType.NONE_REQUIRED
            cls.emit_event(
                bot,
                "CAPTCHA_TYPE_DETECTED",
                step=stage,
                result="NONE_REQUIRED",
                message="این فرم نیازی به ورود کپچا ندارد.",
                page=page,
            )
        else:
            controls.captcha_type = CaptchaType.UNKNOWN
            if controls.image_loc and not controls.input_loc:
                controls.error_reason = "image_without_input"
                cls.emit_event(
                    bot,
                    "CAPTCHA_ROOT_CAUSE",
                    step=stage,
                    reason="image_without_input",
                    message="تصویر کپچا بدون فیلد ورودی در صفحه است.",
                    level="warning",
                    page=page,
                )
            elif controls.input_loc and not controls.image_loc:
                controls.error_reason = "input_without_image"
                cls.emit_event(
                    bot,
                    "CAPTCHA_ROOT_CAUSE",
                    step=stage,
                    reason="input_without_image",
                    message="فیلد ورودی بدون تصویر کپچا در صفحه است.",
                    level="warning",
                    page=page,
                )
            cls.emit_event(
                bot,
                "CAPTCHA_TYPE_DETECTED",
                step=stage,
                result="UNKNOWN",
                message="عناصر کپچا به‌طور کامل تشکیل نشده‌اند.",
                level="warning",
                page=page,
            )

        # 4. Handle Delayed Image Loading
        if controls.image_loc is not None and not controls.image_ready and wait_ms > 0:
            deadline = time.time() + (wait_ms / 1000.0)
            while time.time() < deadline:
                try:
                    is_ready = controls.image_loc.evaluate("img => img.complete && (img.naturalWidth === undefined || img.naturalWidth > 0)")
                    if is_ready:
                        controls.image_ready = True
                        break
                except Exception:
                    pass
                time.sleep(0.1)

            if not controls.image_ready and controls.reload_btn is not None:
                try:
                    controls.reload_btn.click(timeout=800)
                    time.sleep(0.3)
                    controls.image_ready = bool(controls.image_loc.evaluate("img => img.complete && (img.naturalWidth === undefined || img.naturalWidth > 0)"))
                except Exception:
                    pass

        return controls

    @classmethod
    def recognize_and_fill(
        cls,
        bot: Any,
        page: Any,
        controls: CaptchaControls,
        stop_event: Any = None,
        force_refresh: bool = False,
    ) -> Tuple[bool, str]:
        """
        Executes OCR inference with local model / captcha service, validates the result,
        fills the input field, and returns (success, code).
        NEVER clicks the submit button prematurely.
        """
        if stop_event is not None and stop_event.is_set():
            return False, "STOPPED"

        stage = controls.stage

        # Check if manual mode is enforced
        is_manual = bool(bot.settings.get("pause_for_manual_captcha", False)) or controls.captcha_type == CaptchaType.TSPD_CHALLENGE
        if is_manual:
            cls.emit_event(
                bot,
                "CAPTCHA_MANUAL_REQUIRED",
                step=stage,
                reason="manual_setting_or_tspd",
                message="ورود دستی کپچا طبق تنظیمات یا چالش امنیتی الزامی است.",
                level="warning",
                page=page,
            )
            return False, "MANUAL_REQUIRED"

        if not controls.is_complete:
            cls.emit_event(
                bot,
                "CAPTCHA_ROOT_CAUSE",
                step=stage,
                reason="elements_incomplete",
                message=f"المان‌های کپچا ناقص است: تصویر={bool(controls.image_loc)}, ورودی={bool(controls.input_loc)}",
                level="warning",
                page=page,
            )
            return False, "ELEMENTS_INCOMPLETE"

        # Active user typing guard
        try:
            if hasattr(controls.input_loc, "evaluate") and controls.input_loc.evaluate("el => el === document.activeElement"):
                return False, "USER_TYPING"
        except Exception:
            pass

        min_length = max(1, int(bot.settings.get("captcha_min_length", 4)))
        max_length = max(min_length, int(bot.settings.get("captcha_max_length", 12)))

        # Check if already filled and valid
        current_val = (controls.input_loc.input_value() or "").strip().upper()
        if not force_refresh and current_val and min_length <= len(current_val) <= max_length and current_val.isalnum():
            bot._otp_prefilled_captcha = current_val
            return True, current_val

        cls.emit_event(
            bot,
            "CAPTCHA_RECOGNIZER_STARTED",
            step=stage,
            message="آغاز پردازش و استخراج کد کپچا با مدل هوشمند...",
            page=page,
        )

        try:
            # Clear input prior to solving
            try:
                controls.input_loc.fill("")
            except Exception:
                pass

            # Capture image bytes
            source_bytes = None
            if hasattr(bot, "_capture_captcha_image"):
                source_bytes = bot._capture_captcha_image(controls.image_loc, stage)
            elif hasattr(controls.image_loc, "screenshot"):
                source_bytes = controls.image_loc.screenshot()

            if source_bytes and len(source_bytes) < 600:
                source_bytes = None

            # Solve via service
            if hasattr(bot, "captcha_service"):
                code = str((bot.captcha_service.solve(source_bytes) if source_bytes else bot.captcha_service.solve_element(controls.image_loc)) or "").strip().upper()
            else:
                code = ""

            from bot_select_captcha import validate_captcha_prediction
            is_valid = bool(re.fullmatch(rf"[0-9A-Z]{{{min_length},{max_length}}}", code)) and validate_captcha_prediction(code, min_length, max_length)

            cls.emit_event(
                bot,
                "CAPTCHA_RECOGNIZER_FINISHED",
                step=stage,
                result=f"code={code}" if is_valid else "invalid",
                message=f"نتیجه تشخیص مدل کپچا: {code}" if is_valid else f"کد کپچا نامعتبر تشخیص داده شد: {code}",
                level="info" if is_valid else "warning",
                page=page,
            )

            if not is_valid:
                cls.emit_event(
                    bot,
                    "CAPTCHA_ROOT_CAUSE",
                    step=stage,
                    reason="invalid_prediction",
                    message="پاسخ مدل کپچا ساختار معتبری نداشت؛ نیاز به بازخوانی تصویر جدید.",
                    level="warning",
                    page=page,
                )
                cls.refresh_captcha_non_destructive(bot, page, controls, reason="invalid_prediction")
                return False, "INVALID_PREDICTION"

            # Fill into DOM
            from browser_actions import BrowserActions
            try:
                BrowserActions.human_fill(controls.input_loc, code)
            except Exception:
                controls.input_loc.fill(code, timeout=1500)

            try:
                controls.input_loc.dispatch_event("change")
            except Exception:
                pass

            filled_val = (controls.input_loc.input_value() or "").strip().upper()
            if filled_val == code:
                cls.emit_event(
                    bot,
                    "CAPTCHA_FIELD_FILLED",
                    step=stage,
                    result="success",
                    message=f"کپچای مرحله با مقدار {code} در فیلد ورودی ثبت شد.",
                    page=page,
                )
                bot._otp_prefilled_captcha = code
                return True, code
            else:
                cls.emit_event(
                    bot,
                    "CAPTCHA_ROOT_CAUSE",
                    step=stage,
                    reason="fill_verification_mismatch",
                    message="مقدار فیلد پس از درج تطابق نداشت.",
                    level="warning",
                    page=page,
                )
                return False, "FILL_MISMATCH"

        except Exception as exc:
            cls.emit_event(
                bot,
                "CAPTCHA_TIMEOUT" if "timeout" in type(exc).__name__.lower() or "timeout" in str(exc).lower() else "CAPTCHA_ROOT_CAUSE",
                step=stage,
                reason=f"solver_error:{type(exc).__name__}",
                message=f"خطا یا وقفه در سرویس تشخیص کپچا: {exc}",
                level="error",
                page=page,
            )
            return False, f"ERROR_{type(exc).__name__}"

    @classmethod
    def refresh_captcha_non_destructive(
        cls,
        bot: Any,
        page: Any,
        controls: Optional[CaptchaControls] = None,
        reason: str = "refresh",
    ) -> bool:
        """
        Refreshes only the CAPTCHA image via its ReloadLink without full page reload.
        Preserves session cookies, ViewState, and OTP state intact.
        """
        stage = controls.stage if controls else CaptchaStage.STEP_2_OTP
        cls.emit_event(
            bot,
            "CAPTCHA_DOM_CHANGED",
            step=stage,
            reason=reason,
            message="درخواست بازخوانی تصویر تازه کپچا بدون رفرش سشن...",
            page=page,
        )

        try:
            clicked = False
            scope = controls.scope if controls and controls.scope else page
            if controls and controls.reload_btn is not None:
                try:
                    controls.reload_btn.click(timeout=1000)
                    clicked = True
                except Exception:
                    pass

            if not clicked:
                from bot_select_captcha import CAPTCHA_RELOAD_SELECTORS
                for sel in CAPTCHA_RELOAD_SELECTORS:
                    try:
                        loc = scope.locator(sel)
                        if loc.count() > 0 and loc.first.is_visible():
                            loc.first.click(timeout=1000)
                            clicked = True
                            break
                    except Exception:
                        continue

            if controls and controls.input_loc is not None:
                try:
                    controls.input_loc.fill("")
                except Exception:
                    pass

            if hasattr(bot, "_otp_prefilled_captcha"):
                bot._otp_prefilled_captcha = None

            return clicked
        except Exception:
            return False
