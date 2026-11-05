import json
import hashlib
import re
import time
from database import DBHandler
from event_logger import emit_structured_event
from browser_actions import BrowserActions
try:
    from bot_select_captcha import (
        CAPTCHA_IMAGE_SELECTORS,
        CAPTCHA_RELOAD_SELECTORS,
        refresh_captcha_or_reload,
        detect_captcha_failure,
        wait_for_captcha_image_ready,
    )
except ImportError:
    from bot_select_captcha import (
        refresh_captcha_or_reload,
        detect_captcha_failure,
    )
    CAPTCHA_IMAGE_SELECTORS = ("img.BDC_CaptchaImage", "#captchaImage", "#captchaImg")
    CAPTCHA_RELOAD_SELECTORS = ("a[title*='CAPTCHA' i]", "a[title*='کد' i]")
    wait_for_captcha_image_ready = None


def _log_fa(bot, message: str, level: str = "info", page=None):
    try:
        bot.log(message, level, page)
    except Exception:
        try:
            bot.log(message, level)
        except Exception:
            pass


def _iter_scopes(page):
    yield "page", page
    try:
        for idx, frame in enumerate(page.frames):
            frame_name = (frame.name or "").lower()
            frame_url = (frame.url or "").lower()
            if "tspd" in frame_name or "tspd" in frame_url:
                continue
            yield f"frame[{idx}]", frame
    except Exception:
        return


def _locate_otp_input(page, timeout_ms: int = 100):
    """شناسایی سریع فیلد OTP"""
    from otp_form_detector import detect_otp_form
    form = detect_otp_form(page)
    control = form.resolve('otp')
    if control is not None:
        return control, form.scope
    if form.inspected:
        return None, None
    if form.reason.startswith('ambiguous') or form.reason == 'postback_pending':
        return None, None
    selectors = ["#ctl00_ContentPlaceHolder1_tbMobileConfCode", "input[name$='tbMobileConfCode']"]
    for _, scope in _iter_scopes(page):
        for selector in selectors:
            try:
                locator = scope.locator(selector)
                if locator.count() > 0 and locator.first.is_visible() and locator.first.is_enabled():
                    return locator.first, scope
            except Exception:
                continue
    return None, None


def _bank_select_visible_now(page) -> bool:
    """بررسی ورود قطعی به مرحله سوم (انتخاب بانک)"""
    selectors = [
        "#ctl00_ContentPlaceHolder1_ddlBankName",
        "select[name$='ddlBankName']",
    ]
    for _, scope in _iter_scopes(page):
        for selector in selectors:
            try:
                locator = scope.locator(selector)
                if locator.count() > 0 and locator.first.is_visible():
                    return True
            except Exception:
                continue
    return False


def _entry_form_visible_now(page) -> bool:
    """بررسی بازگشت به مرحله اول (کد ملی)"""
    otp_loc, _ = _locate_otp_input(page)
    if otp_loc is not None or _bank_select_visible_now(page):
        return False
    selectors = ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name$='tbIDNo']"]
    for _, scope in _iter_scopes(page):
        for selector in selectors:
            try:
                locator = scope.locator(selector)
                if locator.count() > 0 and locator.first.is_visible():
                    return True
            except Exception:
                continue
    return False


def _locate_otp_captcha_controls(page, wait_ms: int = 3500):
    """
    Locate CAPTCHA image, input field, and submit button on the OTP page.
    Waits up to `wait_ms` for the CAPTCHA image to render and finish loading (`naturalWidth > 0`).
    If the image hasn't appeared yet, triggers the reload icon so it renders and can be solved by the model.
    """
    from otp_form_detector import detect_otp_form
    image, captcha_input, submit_btn = None, None, None
    form = detect_otp_form(page, timeout_ms=wait_ms)
    if form.fields:
        if 'image' in form.fields:
            image = form.scope.locator(form.fields['image']['selector'])
        captcha_input = form.resolve('captcha')
        submit_btn = form.resolve('continue')
        if image is not None and captcha_input is not None and submit_btn is not None:
            return image, captcha_input, submit_btn

    btn_selectors = [
        "#ctl00_ContentPlaceHolder1_btnContinue1",
        "input[name$='btnContinue1']",
        "#ctl00_ContentPlaceHolder1_btnContinue",
        "input[name*='btnContinue']",
        "input[type='submit'][value*='ادامه']",
        "input[type='submit'][value*='تایید']",
        "input[type='submit'][value*='تأیید']",
        "button:has-text('ادامه')",
    ]

    if image is None:
        image, _ = wait_for_captcha_image_ready(page, timeout_ms=wait_ms, trigger_refresh_if_missing=True)

    if not image:
        for _, scope in _iter_scopes(page):
            for selector in CAPTCHA_IMAGE_SELECTORS:
                try:
                    loc = scope.locator(selector)
                    if loc.count() > 0 and loc.first.is_visible():
                        image = loc.first
                        break
                except Exception:
                    continue
            if image:
                break

    if captcha_input is None:
        for _, scope in _iter_scopes(page):
            captcha_input = BrowserActions.find_captcha_input(
                scope, "#ctl00_ContentPlaceHolder1_tbCaptcha2"
            )
            if not captcha_input:
                captcha_input = BrowserActions.find_captcha_input(
                    scope, "input[name$='tbCaptcha2']"
                )
            if not captcha_input:
                captcha_input = BrowserActions.find_captcha_input(
                    scope, "input[name*='tbCaptcha']"
                )
            if captcha_input:
                break

    if submit_btn is None:
        for _, scope in _iter_scopes(page):
            for selector in btn_selectors:
                try:
                    loc = scope.locator(selector)
                    if loc.count() > 0 and loc.first.is_visible():
                        submit_btn = loc.first
                        break
                except Exception:
                    continue
            if submit_btn:
                break

    return image, captcha_input, submit_btn


def _refresh_captcha_image(bot, page, captcha_rejected=False, **kwargs):
    return refresh_captcha_or_reload(bot, page, allow_reload=False, captcha_rejected=captcha_rejected, **kwargs)


def ensure_otp_captcha_prefilled(bot, page, stop_event=None, force_refresh=False) -> bool:
    """
    Proactively locates and solves the OTP-stage CAPTCHA before OTP arrives or during OTP wait.
    Emits required lifecycle events and fills tbCaptcha2 without submitting the form prematurely.
    Uses the unified solve_step_captcha engine.
    """
    if stop_event is not None and stop_event.is_set():
        return False

    if bot.settings.get('pause_for_manual_captcha', False):
        bot.emit_event(
            "CAPTCHA_MANUAL_REQUIRED",
            step="STEP_2_OTP",
            reason="settings_pause_for_manual",
            message="ورود دستی کپچا فعال است؛ منتظر اقدام کاربر.",
            level="warning",
            page=page,
        )
        DBHandler.update_runtime(bot.nid, otp_state='CAPTCHA_REQUIRED')
        return False

    from bot_select_captcha import solve_step_captcha
    res = solve_step_captcha(
        page=page,
        step_name="STEP_2_OTP",
        force_refresh=force_refresh,
        max_attempts=1,
        bot=bot,
        stop_event=stop_event,
    )
    if res.success:
        bot._otp_prefilled_captcha = res.code
        return True
    return False

    bot.emit_event(
        "CAPTCHA_ELEMENT_SEARCH",
        step="STEP_2_OTP",
        message="در حال جستجوی المان‌های کپچای مرحله پیامک...",
        page=page,
    )

    image, captcha_input, submit_btn = _locate_otp_captcha_controls(page, wait_ms=2500)

    if image is None and force_refresh:
        _refresh_captcha_image(bot, page, captcha_rejected=True)
        image, captcha_input, submit_btn = _locate_otp_captcha_controls(page, wait_ms=2500)

    if image is not None:
        bot.emit_event(
            "CAPTCHA_IMAGE_FOUND",
            step="STEP_2_OTP",
            result="found",
            message="تصویر کپچای مرحله پیامک شناسایی شد.",
            page=page,
        )
    else:
        _log_fa(bot, "تصویر کپچای مرحله پیامک یافت نشد.", "warning", page)

    if captcha_input is not None:
        bot.emit_event(
            "CAPTCHA_INPUT_FOUND",
            step="STEP_2_OTP",
            result="found",
            message="فیلد ورودی کپچای مرحله پیامک شناسایی شد.",
            page=page,
        )
    else:
        _log_fa(bot, "فیلد ورودی کپچای مرحله پیامک یافت نشد.", "warning", page)

    if not image or not captcha_input:
        return False

    # Respect user typing
    try:
        if captcha_input.evaluate("el => el === document.activeElement"):
            return False
    except Exception:
        pass

    min_length = max(1, int(bot.settings.get("captcha_min_length", 4)))
    max_length = max(min_length, int(bot.settings.get("captcha_max_length", 12)))

    # Check if already filled and valid (unless force_refresh is requested)
    current_val = (captcha_input.input_value() or "").strip().upper()
    if not force_refresh and current_val and min_length <= len(current_val) <= max_length and current_val.isalnum():
        bot._otp_prefilled_captcha = current_val
        return True

    # If manual captcha is configured
    if bot.settings.get('pause_for_manual_captcha', False):
        bot.emit_event(
            "CAPTCHA_MANUAL_REQUIRED",
            step="STEP_2_OTP",
            reason="settings_pause_for_manual",
            message="ورود دستی کپچا فعال است؛ منتظر اقدام کاربر.",
            level="warning",
            page=page,
        )
        DBHandler.update_runtime(bot.nid, otp_state='CAPTCHA_REQUIRED')
        return False

    # Start solving with local model / captcha service
    bot.emit_event(
        "CAPTCHA_RECOGNITION_STARTED",
        step="STEP_2_OTP",
        message="آغاز پردازش و استخراج خودکار کد کپچا با مدل هوشمند...",
        page=page,
    )

    try:
        # Clear field before solving
        try:
            captcha_input.fill("")
        except Exception:
            pass

        # Capture image bytes or solve via element
        source_bytes = bot._capture_captcha_image(image, "otp") if hasattr(bot, "_capture_captcha_image") else None
        if source_bytes and len(source_bytes) < 600:
            source_bytes = None

        code = str((bot.captcha_service.solve(source_bytes) if source_bytes else bot.captcha_service.solve_element(image)) or "").strip().upper()

        from bot_select_captcha import validate_captcha_prediction
        is_valid = bool(re.fullmatch(rf"[0-9A-Z]{{{min_length},{max_length}}}", code)) and validate_captcha_prediction(code, min_length, max_length)

        bot.emit_event(
            "CAPTCHA_RECOGNITION_RESULT",
            step="STEP_2_OTP",
            result=f"code={code}" if is_valid else "invalid",
            message=f"نتیجه تشخیص مدل کپچا: {code}" if is_valid else f"پاسخ کپچا نامعتبر است: {code}",
            level="info" if is_valid else "warning",
            page=page,
        )

        if not is_valid:
            DBHandler.append_captcha_attempt(bot.nid, code, "failed", source="otp")
            # If invalid, trigger a refresh for the next attempt
            _refresh_captcha_image(bot, page, captcha_rejected=True)
            return False

        # Fill the input
        try:
            BrowserActions.human_fill(captcha_input, code)
        except Exception:
            captcha_input.fill(code, timeout=1500)

        try:
            captcha_input.dispatch_event("change")
        except Exception:
            pass

        filled_val = (captcha_input.input_value() or "").strip().upper()
        if filled_val == code:
            bot.emit_event(
                "CAPTCHA_FILLED",
                step="STEP_2_OTP",
                result="success",
                message=f"کپچای مرحله پیامک با مقدار {code} با موفقیت پر شد.",
                page=page,
            )
            bot._otp_prefilled_captcha = code
            return True
        else:
            DBHandler.append_captcha_attempt(bot.nid, code, "fill_failed", source="otp")
            return False

    except Exception as exc:
        _log_fa(bot, f"خطا در پردازش خودکار کپچا: {exc}", "warning", page)
        return False


def _solve_otp_captcha_fast(bot, page):
    """Wait for the OTP-stage CAPTCHA to appear/render and solve it with the unified solver."""
    from otp_form_detector import detect_otp_form
    form = detect_otp_form(page, timeout_ms=1500)
    button, otp, captcha = None, None, None

    if form.fields:
        button = form.resolve('continue')
        otp = form.resolve('otp')
        captcha = form.resolve('captcha')
        if button is None or otp is None or not otp.input_value().strip():
            return False
        if 'captcha' not in form.fields:
            button.click(timeout=1500)
            return True

    if button is None or captcha is None:
        _, captcha_inp, submit_btn = _locate_otp_captcha_controls(page, wait_ms=2000)
        button = button or submit_btn
        captcha = captcha or captcha_inp

    captcha_rejected = False
    if getattr(bot, "_last_dialog_action", None) == "captcha_invalid" or getattr(bot, "_recovery_flags", {}).get("captcha_invalid"):
        captcha_rejected = True
        if hasattr(bot, "consume_recovery_flag"):
            bot.consume_recovery_flag("captcha_invalid")
        bot._last_dialog_action = None

    min_length = max(1, int(bot.settings.get("captcha_min_length", 4))) if hasattr(bot, "settings") else 4
    max_length = max(min_length, int(bot.settings.get("captcha_max_length", 12))) if hasattr(bot, "settings") else 12

    current_val = (captcha.input_value() or "").strip().upper() if captcha is not None else ""
    if not captcha_rejected and current_val and min_length <= len(current_val) <= max_length and current_val.isalnum():
        if button is not None:
            button.click(timeout=1500)
            return True

    if bot.settings.get('pause_for_manual_captcha', False):
        bot.emit_event(
            "CAPTCHA_MANUAL_REQUIRED",
            step="STEP_2_OTP",
            reason="settings_pause_for_manual",
            message="ورود دستی کپچا طبق تنظیمات فعال است.",
            page=page,
        )
        DBHandler.update_runtime(bot.nid, otp_state='CAPTCHA_REQUIRED')
        return False

    from bot_select_captcha import solve_step_captcha
    res = solve_step_captcha(
        page=page,
        step_name="STEP_2_OTP",
        force_refresh=captcha_rejected,
        max_attempts=1,
        bot=bot,
        stop_event=getattr(bot, "_active_stop_event", None),
    )
    if res.success:
        bot._otp_prefilled_captcha = res.code
        if button is not None:
            button.click(timeout=1500)
            return True
        return False
    return False


def get_otp_from_db_fresh(bot):
    """استعلام سریع کد OTP از دیتابیس محلی و نظرسنجی غیردرگیرکننده (Throttled) از سرور"""
    validity_sec = float(bot.settings.get("otp_validity_seconds", 120))
    now = time.time()
    min_ts = max(float(getattr(bot, "_otp_entry_time", 0.0) or 0.0), now - validity_sec)
    try:
        # 1) Check local DB immediately (0ms) so manual UI / cached OTP is picked up without delay
        record = DBHandler.get_otp_record(bot.nid)
        if record:
            raw_code = str(record.get("otp") or "").strip()
            rec_ts = float(record.get("ts") or 0.0)
            from otp_transport import fresh_record
            valid = fresh_record(bot, record, now)
            if valid:
                return valid, "local_db"

        # 2) Throttle remote server HTTP polling to once every 3.0s so manual browser entry is never blocked
        last_poll = float(getattr(bot, "_last_remote_otp_poll_ts", 0.0) or 0.0)
        if now - last_poll >= 3.0:
            bot._last_remote_otp_poll_ts = now
            code = bot.get_otp_code(stop_event=getattr(bot, "_active_stop_event", None), timeout=1)
            if code and str(code).strip():
                clean = str(code).strip()
                if clean.isdigit() and 4 <= len(clean) <= 8:
                    return clean, "received"
    except Exception:
        pass

    return None, None


def _safe_input_value(locator):
    try:
        return locator.input_value()
    except Exception:
        return ""


def _inject_otp_code(bot, page, otp_locator, code: str):
    from otp_form_detector import detect_otp_form, fill_otp_form
    form = detect_otp_form(page)
    if form.fields:
        started = time.perf_counter()
        ok = fill_otp_form(page, code)
        DBHandler.update_runtime(bot.nid, otp_form_detection_ms=form.elapsed_ms,
                                  otp_form_fill_ms=round((time.perf_counter() - started) * 1000, 3))
        if ok:
            bot._last_retained_otp = code
        return ok
    if form.reason.startswith('ambiguous'):
        return False
    if form.inspected:
        return False
    try:
        if not otp_locator.is_visible():
            otp_locator.wait_for(state="visible", timeout=1000)

        try:
            BrowserActions.human_fill(otp_locator, code, timeout=1500)
        except Exception:
            BrowserActions.fast_fill(otp_locator, code)

        if otp_locator.input_value() == code:
            _log_fa(bot, "📥 کد جدید OTP در فرم وارد شد.", "success", page)
            bot._last_retained_otp = code
            return True
    except Exception:
        pass

    try:
        BrowserActions.force_fill(otp_locator, code)
        _log_fa(bot, "📥 کد جدید OTP با روش جایگزین در فرم وارد شد.", "success", page)
        return True
    except Exception as exc:
        _log_fa(bot, f"⚠️ خطا در تزریق OTP: {exc}", "warning", page)
        return False


def _detect_failure_type(page, bot=None):
    """Classify explicit current error evidence, separately from form labels."""
    from otp_form_detector import normalize_text
    action = getattr(bot, '_last_dialog_action', None) if bot else None
    if action == 'captcha_invalid':
        return 'captcha'
    if action == 'otp_expired':
        return 'otp_expired'
    try:
        alerts = page.locator('[role="alert"]:visible, [id$="labErrMsg"]:visible, [id$="lblError"]:visible, .ValidationSummary:visible').all_text_contents()
        explicit = ' '.join(t for t in alerts if t.strip()) if isinstance(alerts, list) else ''
        text = normalize_text(explicit or page.inner_text('body'))
        failure = r'(?:اشتباه|نادرست|نامعتبر|صحیح نیست|صحیح نمی|منقضی)'
        if re.search(r'(?:کد امنیتی|کد تصویر|کپچا|عبارت امنیتی).{0,35}' + failure, text):
            return 'captcha'
        if re.search(r'(?:کد تایید|کد تأیید|کد پیامک|رمز یکبار مصرف|رمز یک بار مصرف).{0,45}' + failure, text):
            return 'otp_expired' if 'منقضی' in text else 'otp'
    except Exception:
        pass
    return None


def _detect_site_otp_ttl(page, default_seconds: int = 120) -> int:
    try:
        body = page.inner_text("body") or ""
        m = re.search(r"(\d{2,3})\s*ثانیه", body)
        if m:
            val = int(m.group(1))
            if 20 <= val <= 600:
                return val
    except Exception:
        pass
    return default_seconds


def _post_submit_watch(bot, page, stop_event, sleep_with_stop_fn, last_code: str, wait_seconds: float = 10.0):
    """بعد از Submit تا ۱۰ ثانیه همزمان بانک و کد جدید را پایش می‌کند."""
    deadline = time.time() + wait_seconds
    _log_fa(bot, "⏱️ پایش پس از ارسال کد پیامک: بررسی انتقال به صفحه انتخاب بانک...", "info", page)

    while time.time() < deadline:
        if stop_event.is_set():
            return "stopped", None, None

        if _bank_select_visible_now(page):
            if hasattr(bot, "clear_otp_locks"):
                bot.clear_otp_locks()
            DBHandler.update_status(bot.nid, "انتخاب بانک", "ورود به مرحله انتخاب بانک")
            if hasattr(bot, "set_runtime_state"):
                bot.set_runtime_state("MONITORING", "BANK_SELECTION", browser_status="OPEN")
            bot.save_checkpoint(
                "STEP_3_BANK_SELECT",
                page=page,
                expected_next_step="SELECT_BANK",
                last_operation_result="OTP_ACCEPTED",
            )
            _log_fa(bot, "🎯 انتقال به صفحه انتخاب بانک تایید شد.", "success", page)
            return "bank", None, None

        if _entry_form_visible_now(page):
            _log_fa(bot, "↩️ بازگشت به فرم ورود تشخیص داده شد.", "warning", page)
            return "stopped", None, None

        fail_type = _detect_failure_type(page, bot=bot)
        if fail_type in ("otp", "otp_expired") or getattr(bot, "_last_dialog_action", None) == "otp_expired" or getattr(bot, "_recovery_flags", {}).get("otp_expired"):
            rejected = getattr(bot, "_consumed_otp_digests", set())
            rejected.add(hashlib.sha256(str(last_code).encode()).hexdigest())
            bot._consumed_otp_digests = rejected
            bot.emit_event(
                "OTP_INVALID" if fail_type == 'otp' else "OTP_EXPIRED",
                step="STEP_2_OTP",
                reason="site_rejected_otp",
                result="restart_step1",
                message="الارم انقضای کد پیامک از سامانه دریافت شد؛ بازگشت خودکار به مرحله ۱ جهت دریافت کد جدید ۱۲۰ ثانیه‌ای.",
                level="warning",
                page=page,
            )
            from otp_transport import invalidate_otp
            invalidate_otp(bot, 'expired' if fail_type == 'otp_expired' else 'rejected')
            DBHandler.update_runtime(bot.nid, otp_state='OTP_EXPIRED' if fail_type == 'otp_expired' else 'OTP_INVALID')
            if hasattr(bot, "clear_otp_locks"):
                bot.clear_otp_locks()
            bot._set_recovery_flag("otp_expired")
            DBHandler.update_status(bot.nid, "کد منقضی - شروع مجدد", "کد تایید منقضی شد؛ در حال بازگشت به مرحله ۱ جهت اخذ کد جدید")
            return "otp_expired", None, None
        elif fail_type == "captcha" or getattr(bot, "_last_dialog_action", None) == "captcha_invalid":
            bot._last_dialog_action = None
            return "retry", None, None
        elif getattr(bot, "_last_dialog_action", None) in ("otp_step_capacity_retry", "system_capacity_updating", "bank_capacity_full", "generic_error"):
            dialog_msg = getattr(bot, "_last_dialog_message", "")
            bot._last_dialog_action = None
            _log_fa(bot, f"⚠️ سامانه پیام موقت اعلام کرد ({dialog_msg})؛ تصویر کپچا حفظ می‌شود.", "warning", page)
            return "timeout", None, None

        if sleep_with_stop_fn(stop_event, 0.3):
            return "stopped", None, None

    return "timeout", None, None


def _run_otp_step(bot, page, stop_event, sleep_with_stop_fn):
    """
    Step 2: OTP Confirmation State Handler.
    - Activates OTP_REQUIRED state and records issuance time.
    - Proactively ensures the Step 2 CAPTCHA image is rendered in DOM.
    - Injects OTP once per code, then uses the local CRNN model to solve CAPTCHA.
    - If CAPTCHA image hasn't appeared yet, waits/triggers reload and hands it to the model as soon as it appears.
    """
    default_ttl = int(bot.settings.get("otp_validity_seconds", 120))
    effective_ttl = _detect_site_otp_ttl(page, default_seconds=default_ttl)
    now_ts = time.time()
    existing_entry = float(getattr(bot, "_otp_entry_time", 0) or 0)
    # If uninitialized or older than effective TTL, mark a fresh challenge start time & new request ID
    if not existing_entry or (now_ts - existing_entry >= effective_ttl):
        otp_issued_at = bot.mark_otp_entry_time(now_ts)
    else:
        otp_issued_at = existing_entry
    receive_deadline = time.monotonic() + max(1, int(bot.settings.get("otp_receive_timeout", effective_ttl)))
    manual_required = False

    from core.loan_workflow import workflow_for
    workflow_for(bot).transition('WAITING_FOR_NEW_OTP', 'current_challenge_waiting_for_code')

    bot.emit_event(
        "OTP_REQUIRED",
        step="STEP_2_OTP",
        reason=f"ttl={effective_ttl}s",
        result="waiting_for_sms",
        message=f"صفحه تایید پیامکی فعال شد (مهلت اعتبار: {effective_ttl} ثانیه).",
        level="info",
        page=page,
    )
    DBHandler.update_status(bot.nid, "منتظر پیامک", "در انتظار دریافت کد تایید پیامکی")
    bot.save_checkpoint(
        "STEP_2_OTP",
        page=page,
        expected_next_step="STEP_3_BANK_SELECT",
        last_operation_result="OTP_REQUIRED",
    )
    bot.emit_event(
        "OTP_PAGE_DETECTED",
        step="STEP_2_OTP",
        result="detected",
        message="صفحه تأیید پیامک شناسایی شد.",
        page=page,
    )
    manual_captcha = False

    def begin_manual_captcha(reason):
        DBHandler.update_runtime(bot.nid, otp_state="MANUAL_OTP_REQUIRED")
        bot.set_runtime_state("WAITING_USER_INPUT", "MANUAL_CAPTCHA_OR_OTP", browser_status="OPEN")
        flow = workflow_for(bot)
        flow.transition("STEP2_CAPTCHA_REQUIRED", reason)
        for event in ("CAPTCHA_MANUAL_REQUIRED", "MANUAL_REQUIRED", "MANUAL_WAITING"):
            bot.emit_event(event, step="STEP_2_OTP", reason=reason, result="waiting_for_user", page=page)
        _log_fa(bot, "منتظر اقدام دستی کاربر (کپچا یا پیامک)؛ مرورگر باز و نشست حفظ می‌شود.", "info", page)

    if bot.settings.get("pause_for_manual_captcha", False):
        begin_manual_captcha("settings_pause_for_manual")
        manual_captcha = True
    else:
        try:
            ensure_otp_captcha_prefilled(bot, page, stop_event=stop_event)
        except Exception as exc:
            _log_fa(bot, f"پیش‌بارگذاری اولیه کپچا با خطا مواجه شد: {exc}", "warning", page)

    try:
        bot.suspend_watchdog(max(180, effective_ttl + 60))
    except Exception:
        pass

    # Retained values never establish permission to reuse an OTP after navigation.
    last_injected_code = None
    submission_pending = False
    otp_received_at = 0.0
    otp_not_found_ts = 0.0
    captcha_failures = 0
    max_captcha_retries = max(1, int(bot.settings.get("otp_captcha_max_retries", bot.settings.get("captcha_max_retries", 10))))
    form_wait_started = 0.0

    while not stop_event.is_set():
        bot.mark_progress("waiting_for_otp_loop")
        if page.is_closed() is True:
            return 'target_closed'
        from core.loan_workflow import workflow_for
        flow = workflow_for(bot)
        if flow.pump_commands():
            if sleep_with_stop_fn(stop_event, .2):
                break
            continue
        if flow.http_status in {401, 403, 429} or flow.http_status >= 500:
            DBHandler.update_runtime(bot.nid, otp_state='MANUAL_OTP_REQUIRED')
            return 'otp_expired' if flow.http_status == 401 else 'manual_required'

        if getattr(bot, "_recovery_flags", {}).get("otp_expired") or getattr(bot, "_last_dialog_action", None) == "otp_expired":
            bot._set_recovery_flag("otp_expired")
            _log_fa(bot, "🔄 دریافت الارم انقضای کد تایید؛ خروج فوری جهت بازگشت به مرحله ۱...", "warning", page)
            return "otp_expired"

        if _bank_select_visible_now(page):
            DBHandler.update_runtime(bot.nid, otp_state="OTP_ACCEPTED")
            if hasattr(bot, "clear_otp_locks"):
                bot.clear_otp_locks()
            DBHandler.update_status(bot.nid, "انتخاب بانک", "ورود به مرحله انتخاب بانک")
            if hasattr(bot, "set_runtime_state"):
                bot.set_runtime_state("MONITORING", "BANK_SELECTION", browser_status="OPEN")
            bot.save_checkpoint(
                "STEP_3_BANK_SELECT",
                page=page,
                expected_next_step="SELECT_BANK",
                last_operation_result="OTP_ACCEPTED",
            )
            _log_fa(bot, "✅ مرحله انتخاب بانک فعال شد؛ خروج از سیکل OTP.", "success", page)
            if manual_captcha:
                from core.otp_queue import OTPQueueManager
                OTPQueueManager.mark_verified(bot.nid, getattr(bot, "_otp_request_id", None))
                bot.emit_event("MANUAL_ACCEPTED", step="STEP_2_OTP", result="bank_dom_confirmed", page=page)
                bot.emit_event("PAGE3_READY", step="STEP_3_BANK_SELECT", result="bank_dom_confirmed", page=page)
            return "bank_ready"

        if manual_captcha:
            if (getattr(bot, "_recovery_flags", {}).get("session_expired")
                    or _entry_form_visible_now(page) or time.time() - otp_issued_at >= effective_ttl):
                return "otp_expired"
            if sleep_with_stop_fn(stop_event, 0.3):
                break
            continue

        if _entry_form_visible_now(page):
            if hasattr(bot, "clear_otp_locks"):
                bot.clear_otp_locks()
            _log_fa(bot, "↩️ بازگشت به فرم ورود (کد ملی) شناسایی شد؛ خروج از سیکل OTP.", "warning", page)
            return None

        try:
            if bot._firewall_gate(page, stop_event):
                if sleep_with_stop_fn(stop_event, 0.3):
                    break
                continue
        except Exception:
            pass

        otp_locator, _ = _locate_otp_input(page)
        if not otp_locator:
            if otp_not_found_ts == 0.0:
                otp_not_found_ts = time.time()
            if time.time() - otp_not_found_ts > 5.0:
                if hasattr(bot, "clear_otp_locks"):
                    bot.clear_otp_locks()
                return 'manual_required'
            if sleep_with_stop_fn(stop_event, 0.3):
                break
            continue

        otp_not_found_ts = 0.0

        from otp_transport import normalized_code
        dom_val = normalized_code(_safe_input_value(otp_locator))
        from otp_form_detector import detect_otp_form
        current_form = detect_otp_form(page)
        if current_form.fields and current_form.resolve('continue') is None and not submission_pending:
            form_wait_started = form_wait_started or time.monotonic()
            if time.monotonic() - form_wait_started >= float(bot.settings.get('otp_form_ready_timeout_seconds', 10)):
                return 'manual_required'
            if sleep_with_stop_fn(stop_event, .2):
                break
            continue
        form_wait_started = 0.0
        limit = current_form.fields.get('otp', {}).get('maxlength', 0)
        manual_length_ok = len(dom_val) == limit if 4 <= limit <= 8 else 4 <= len(dom_val) <= 8
        code, source = (last_injected_code, "pending_submission") if submission_pending else get_otp_from_db_fresh(bot)
        if not code and dom_val.isascii() and dom_val.isdigit() and manual_length_ok:
            if hashlib.sha256(dom_val.encode()).hexdigest() not in getattr(bot, "_consumed_otp_digests", set()):
                code, source = dom_val, "browser_manual"
                DBHandler.set_otp(bot.nid, dom_val, ts=time.time(), status="manual",
                                  request_id=getattr(bot, '_otp_request_id', None))
                _log_fa(bot, "کد تایید دستی دریافت شد؛ در حال بررسی.", "info")

        if not code:
            ctx = getattr(bot, "ctx", None)
            if ctx is not None and ctx.is_otp_valid():
                code = ctx.cached_otp_code
                source = "context_cache"
            elif getattr(bot, "_last_retained_otp", None):
                code = bot._last_retained_otp
                source = "retained_cache"

        if code and not submission_pending:
            digest = hashlib.sha256(str(code).encode()).hexdigest()
            if digest in getattr(bot, '_consumed_otp_digests', set()):
                code = None

        if (time.monotonic() >= receive_deadline or time.time() - otp_issued_at >= effective_ttl) and not code and not manual_required:
            DBHandler.update_runtime(bot.nid, otp_state="SMS_TIMEOUT")
            bot.emit_event("SMS_TIMEOUT", step="STEP_2_OTP", result="MANUAL_OTP_REQUIRED",
                           message="پیامک در مهلت تعیین‌شده نرسید؛ ورود دستی در همین صفحه فعال است.", page=page)
            DBHandler.update_runtime(bot.nid, otp_state="MANUAL_OTP_REQUIRED")
            manual_required = True
        if not code:
            if sleep_with_stop_fn(stop_event, 0.3):
                break
            continue

        if not submission_pending:
            DBHandler.update_runtime(bot.nid, otp_state="CODE_VALIDATED")

            if code != last_injected_code:
                record = DBHandler.get_otp_record(bot.nid) or {}
                otp_received_at = float(record.get("ts") or time.time()) if record.get("otp") == code else time.time()
                captcha_failures = 0

            if otp_received_at and time.time() - otp_received_at >= effective_ttl:
                rejected = getattr(bot, "_consumed_otp_digests", set())
                rejected.add(hashlib.sha256(str(code).encode()).hexdigest())
                bot._consumed_otp_digests = rejected
                bot.emit_event(
                    "OTP_EXPIRED",
                    step="STEP_2_OTP",
                    reason="injected_otp_ttl_expired",
                    result="cleared",
                    message="اعتبار کد پیامک جاری به پایان رسید؛ منتظر کد جدید.",
                    level="warning",
                    page=page,
                )
                bot.clear_otp_backend()
                bot.mark_otp_entry_time()
                DBHandler.update_status(bot.nid, "منتظر پیامک", "OTP expired; waiting for fresh code")
                last_injected_code = getattr(bot, '_last_retained_otp', None)
                otp_received_at = 0.0
                continue

            current_val = _safe_input_value(otp_locator)
            if current_val != code:
                if not _inject_otp_code(bot, page, otp_locator, code):
                    if sleep_with_stop_fn(stop_event, 0.3):
                        break
                    continue
                last_injected_code = code
            else:
                last_injected_code = code

            DBHandler.update_runtime(bot.nid, otp_state="VERIFYING_OTP")
            if not submission_pending and not _solve_otp_captcha_fast(bot, page):
                captcha_failures += 1
                if captcha_failures >= max_captcha_retries:
                    begin_manual_captcha("captcha_attempt_limit")
                    manual_captcha = True
                    continue
                # Auto-retry with model: refresh image and continue
                if _detect_failure_type(page, bot) == 'captcha':
                    _refresh_captcha_image(bot, page, captcha_rejected=True)
                if sleep_with_stop_fn(stop_event, 0.4):
                    break
                continue

        if not submission_pending:
            record = DBHandler.get_otp_record(bot.nid) or {}
            DBHandler.set_otp(bot.nid, last_injected_code or code, ts=record.get('ts'), status='submitted',
                              request_id=getattr(bot, '_otp_request_id', None))
            DBHandler.update_runtime(bot.nid, otp_state='OTP_SUBMITTED')
        submission_pending = True
        status, new_code, _ = _post_submit_watch(
            bot=bot,
            page=page,
            stop_event=stop_event,
            sleep_with_stop_fn=sleep_with_stop_fn,
            last_code=last_injected_code or code,
            wait_seconds=10.0,
        )

        if status == "otp_expired":
            _log_fa(bot, "🔄 کد تایید منقضی شد؛ بازگشت خودکار به مرحله ۱ متقاضی برای صدور کد جدید ۱۲۰ ثانیه‌ای...", "warning", page)
            bot._set_recovery_flag("otp_expired")
            return "otp_expired"

        if status == "retry":
            submission_pending = False
            captcha_failures += 1
            record = DBHandler.get_otp_record(bot.nid) or {}
            if record.get("otp"):
                DBHandler.set_otp(bot.nid, record["otp"], ts=record.get("ts"), status="received",
                                  request_id=getattr(bot, "_otp_request_id", None))
            DBHandler.update_runtime(bot.nid, otp_state="CAPTCHA_RETRY")
            bot.emit_event("CAPTCHA_REJECTED_BY_SERVER", step="STEP_2_OTP", result="rejected", page=page)
            _log_fa(bot, f"🔄 کپچای مرحله پیامک نامعتبر بود؛ پاکسازی فیلد و تلاش مجدد روی کپچای جدید ({captcha_failures}/{max_captcha_retries})...", "warning", page)
            try:
                _, captcha_inp, _ = _locate_otp_captcha_controls(page, wait_ms=1000)
                if captcha_inp is not None:
                    captcha_inp.fill('')
            except Exception:
                pass
            bot._otp_prefilled_captcha = None
            if captcha_failures >= max_captcha_retries:
                begin_manual_captcha("captcha_rejection_limit")
                manual_captcha = True
                continue
            _refresh_captcha_image(bot, page, captcha_rejected=True)
            if sleep_with_stop_fn(stop_event, 0.3):
                break
            continue

        if status == "bank":
            # Retain OTP for Step 3 duration; only mark final consumed upon tracking code
            record = DBHandler.get_otp_record(bot.nid) or {}
            DBHandler.set_otp(bot.nid, code, ts=record.get("ts"), status="bank_ready",
                              request_id=getattr(bot, '_otp_request_id', None))
            DBHandler.update_runtime(bot.nid, otp_state="OTP_ACCEPTED")
            bot._last_retained_otp = code
            ctx = getattr(bot, "ctx", None)
            if ctx is not None:
                ctx.cached_otp_code = code
                if not ctx.otp_received_at:
                    ctx.otp_received_at = time.time()
                ctx.current_state = "S3_BANK_SELECTION"
            from core.otp_queue import OTPQueueManager
            OTPQueueManager.mark_verified(bot.nid, getattr(bot, '_otp_request_id', None))
            if hasattr(bot, "clear_otp_locks"):
                bot.clear_otp_locks()
            return "bank_ready" 

        if status == "retry":
            submission_pending = False
            record = DBHandler.get_otp_record(bot.nid) or {}
            DBHandler.set_otp(bot.nid, last_injected_code or code, ts=record.get('ts'), status='received',
                              request_id=getattr(bot, '_otp_request_id', None))
            continue
        if status == "timeout":
            DBHandler.update_runtime(bot.nid, otp_state="OTP_RESULT_PENDING")
            has_expired = (
                (otp_received_at and (time.time() - otp_received_at >= effective_ttl)) or
                (otp_issued_at and (time.time() - otp_issued_at >= effective_ttl + 10)) or
                getattr(bot, "_recovery_flags", {}).get("otp_expired") or
                getattr(bot, "_last_dialog_action", None) == "otp_expired" or
                detect_otp_failure(bot, page) or
                _detect_failure_type(page) in ("otp", "otp_expired")
            )
            if has_expired:
                _log_fa(bot, "🔄 مهلت زمانی کد پیامک به پایان رسید؛ خروج جهت بازگشت به مرحله ۱ و درخواست کد جدید...", "warning", page)
                bot._set_recovery_flag("otp_expired")
                return "otp_expired"

            elapsed = int(time.time() - (otp_received_at or otp_issued_at))
            remaining = max(1, effective_ttl - elapsed)
            _log_fa(bot, f"⏱️ نتیجه ارسال هنوز مشخص نیست (مهلت باقی‌مانده پیامک: {remaining} ثانیه)؛ انتظار بدون ارسال مجدد...", "info", page)
            # The submitted request is still unconfirmed; keep observing it.
            continue

        if status == "stopped":
            break

        if sleep_with_stop_fn(stop_event, 0.3):
            break

    return None


class OTPWorkflowController:
    """Same bounded handler on first entry, postback, reconnect, and reauthentication."""
    def __init__(self, bot):
        self.bot = bot

    def run(self, page, stop_event, sleep_with_stop_fn, reason='otp_form_detected'):
        from core.loan_workflow import workflow_for
        flow = workflow_for(self.bot)
        flow.bind(page)
        owner = getattr(self.bot, 'live_owner', None)
        if owner is not None:
            owner.assert_owner()
            if owner.page is not page:
                raise RuntimeError('OTP page does not belong to the applicant owner')
        from database import DBHandler
        if DBHandler.get_checkpoint(self.bot.nid).get('final_submit_status') in {'pending', 'unknown'}:
            flow.transition('PAUSED', 'previous_submission_requires_reconciliation')
            return 'manual_required'
        form = __import__('otp_form_detector').detect_otp_form(page)
        if form.reason.startswith('ambiguous'):
            flow.transition('PAUSED', form.reason)
            return 'manual_required'
        record = DBHandler.get_otp_record(self.bot.nid) or {}
        same_request = not record.get('request_id') or record['request_id'] == getattr(self.bot, '_otp_request_id', None)
        if record.get('status') == 'submitted' and same_request:
            flow.transition('PAUSED', 'previous_otp_submission_unresolved')
            return 'manual_required'
        if record.get('status') in {'consumed', 'expired', 'rejected'} and same_request:
            self.bot._last_retained_otp = None
            DBHandler.update_runtime(self.bot.nid, otp_state='OTP_EXPIRED')
            flow.transition('OTP_REAUTH_REQUIRED', 'previous_code_not_reusable')
            return 'otp_expired'
        flow.transition('PAGE_REDETECTING', reason)
        flow.transition('STEP2_OTP_REQUIRED', reason)
        result = _run_otp_step(self.bot, page, stop_event, sleep_with_stop_fn)
        if result == 'bank_ready':
            record = DBHandler.get_otp_record(self.bot.nid) or {}
            if record.get('otp') and record.get('status') != 'consumed':
                self.bot._consumed_otp_digests = getattr(self.bot, '_consumed_otp_digests', set()) | {
                    hashlib.sha256(record['otp'].encode()).hexdigest()}
                DBHandler.set_otp(self.bot.nid, record['otp'], ts=record.get('ts'), status='consumed',
                                  request_id=getattr(self.bot, '_otp_request_id', None))
            self.bot._last_retained_otp = None
            if flow.last_step3_visit:
                flow.successful_reentries += 1
            flow.last_step3_visit = time.time()
            flow.transition('STEP3_ACTIVE', 'server_verified_bank_dom')
        elif result == 'otp_expired':
            if DBHandler.get_runtime(self.bot.nid).get('otp_state') != 'OTP_INVALID':
                DBHandler.update_runtime(self.bot.nid, otp_state='OTP_EXPIRED')
            flow.transition('OTP_REAUTH_REQUIRED', 'site_or_code_expiry')
        elif result == 'target_closed':
            flow.transition('FAILED', 'target_closed')
        elif result == 'manual_required':
            flow.transition('PAUSED', 'unresolved_form_or_response')
        return result


def fast_otp_sync_and_inject(bot, page, stop_event, sleep_with_stop_fn):
    return OTPWorkflowController(bot).run(page, stop_event, sleep_with_stop_fn)


def detect_otp_failure(bot, page) -> bool:
    try:
        body = (page.inner_text("body") or "").lower()
        if "ظرفیت" in body or "امنیتی" in body or "کپچا" in body:
            return False
        otp_markers = ["کد تایید", "کد تأیید", "کد پیامک", "رمز یکبار مصرف", "تلفن همراه"]
        err_markers = ["نامعتبر", "منقضی", "اشتباه", "صحیح نمی‌باشد", "صحیح نمی باشد"]
        return any(om in body for om in otp_markers) and any(em in body for em in err_markers)
    except Exception:
        return False


def handle_otp_dialog(bot, dialog):
    msg = dialog.message() if callable(getattr(dialog, "message", None)) else getattr(dialog, "message", "")
    if not msg:
        dialog.accept()
        return False
    msg_clean = (msg or "").strip()
    if any(k in msg_clean for k in ("کد امنیتی", "کپچا", "تصویر امنیتی", "کد تصویر", "عبارت امنیتی")) and any(
        f in msg_clean for f in ("اشتباه", "نادرست", "نامعتبر", "نا معتبر", "صحیح نیست", "صحیح نمی", "incorrect", "invalid")
    ):
        _log_fa(bot, f"⚠️ هشدار دیالوگ: کپچا نادرست است ({msg_clean})؛ تصویر تازه دریافت و مجددا حل می‌شود.", "warning")
        bot._last_dialog_action = "captcha_invalid"
        bot._last_dialog_message = msg_clean
        bot._set_recovery_flag("captcha_invalid")
        dialog.accept()
        return True

    if any(ot in msg_clean for ot in ("کد تایید", "کد تأیید", "کد پیامک", "رمز یکبار مصرف", "تلفن همراه")) and any(
        f in msg_clean for f in ("منقضی", "نامعتبر", "نا معتبر", "اشتباه")
    ) and "امنیتی" not in msg_clean and "کپچا" not in msg_clean:
        _log_fa(bot, f"🚨 هشدار دیالوگ OTP: {msg_clean}؛ ثبت انقضا و بازگشت خودکار به مرحله ۱", "error")
        bot._last_dialog_action = "otp_expired"
        bot._last_dialog_message = msg_clean
        bot._set_recovery_flag("otp_expired")
        dialog.accept()
        return True

    dialog.accept()
    return False


def notify_favorite_banks(bot, *args, **kwargs):
    pass


def otp_input_visible(bot, page):
    locator, _ = _locate_otp_input(page)
    return locator is not None


def resend_enabled(page):
    selectors = [
        "#ctl00_ContentPlaceHolder1_btnSendConfirmCode",
        "input[name$='btnSendConfirmCode']",
        "input[value*='ارسال مجدد']",
        "button:has-text('ارسال مجدد')",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible() and loc.first.is_enabled():
                return True
        except Exception:
            continue
    return False


def can_navigate_from_otp(bot, page):
    return not otp_input_visible(bot, page)
