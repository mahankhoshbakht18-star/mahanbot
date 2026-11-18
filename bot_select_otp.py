import json
import hashlib
import re
import time
from database import DBHandler
from event_logger import emit_structured_event
from browser_actions import BrowserActions

OTP_TTL_HARD_CAP_SEC = 115
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


def _otp_input_visible_now(page) -> bool:
    loc, _ = _locate_otp_input(page)
    return loc is not None


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
        image, _ = wait_for_captcha_image_ready(page, timeout_ms=wait_ms, trigger_refresh_if_missing=False)

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
    Only ensure OTP captcha IMAGE is visible — never OCR/fill early.
    Actual solve is shared via solve_step_captcha (same path as status/register/select).
    """
    del force_refresh
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
    try:
        image, captcha_input, _ = _locate_otp_captcha_controls(page, wait_ms=2000)
        if image is None and callable(wait_for_captcha_image_ready):
            image, _ = wait_for_captcha_image_ready(page, timeout_ms=2500, trigger_refresh_if_missing=False)
        if captcha_input is not None:
            try:
                captcha_input.fill("")
            except Exception:
                pass
        bot._otp_prefilled_captcha = None
        return image is not None
    except Exception:
        return False


def _otp_captcha_length_bounds(bot, captcha_el) -> tuple:
    """CBI BotDetect on OTP is 4–5 chars. Never accept 6+ predictions (burn TTL on rejects)."""
    min_length = 4
    max_length = 5
    try:
        raw = captcha_el.get_attribute("maxlength") if captcha_el is not None else None
        if raw and str(raw).isdigit():
            ml = int(raw)
            if 4 <= ml <= 5:
                max_length = ml
                min_length = 4
            elif ml == 3:
                min_length = max_length = 3
    except Exception:
        pass
    # Settings may widen Step1 captcha bounds — do NOT let them expand OTP beyond 5.
    return min_length, min(5, max(min_length, max_length))


def _remembered_otp(bot) -> str:
    code = str(getattr(bot, "_last_retained_otp", "") or "").strip()
    if code:
        return code
    try:
        record = DBHandler.get_otp_record(bot.nid) or {}
        status = str(record.get("status") or "")
        if status not in {"expired", "rejected", "consumed"}:
            return str(record.get("otp") or "").strip()
    except Exception:
        return ""
    return ""


def _restore_otp_field(bot, page) -> bool:
    """Captcha alerts clear tbMobileConfCode. Put the code we already have back before submit."""
    code = _remembered_otp(bot)
    if not code:
        return False
    otp_locator, _ = _locate_otp_input(page)
    if otp_locator is None:
        return False
    current = str(_safe_input_value(otp_locator) or "").strip()
    if current == code:
        return True
    return bool(_inject_otp_code(bot, page, otp_locator, code))


def _solve_otp_captcha_fast(bot, page):
    """Fresh-solve OTP CAPTCHA immediately before submit — never reuse a stale prefilled value."""
    _restore_otp_field(bot, page)
    from otp_form_detector import detect_otp_form
    form = detect_otp_form(page, timeout_ms=400)
    button, otp, captcha = None, None, None

    if form.fields:
        button = form.resolve('continue')
        otp = form.resolve('otp')
        captcha = form.resolve('captcha')
        if otp is not None and not str(otp.input_value() or "").strip():
            _restore_otp_field(bot, page)
        otp_ready = otp is not None and bool(str(otp.input_value() or "").strip())
        if button is not None and otp_ready and "captcha" not in form.fields:
            button.click(timeout=800)
            return True

    if button is None or captcha is None:
        _, captcha_inp, submit_btn = _locate_otp_captcha_controls(page, wait_ms=400)
        button = button or submit_btn
        captcha = captcha or captcha_inp

    # Only treat explicit True as force-refresh (MagicMock attrs are truthy otherwise).
    captcha_rejected = (
        getattr(bot, "_last_dialog_action", None) == "captcha_invalid"
        or bool((getattr(bot, "_recovery_flags", {}) or {}).get("captcha_invalid"))
        or (getattr(bot, "_otp_force_captcha_refresh", False) is True)
    )
    if getattr(bot, "_last_dialog_action", None) == "captcha_invalid" or getattr(bot, "_recovery_flags", {}).get("captcha_invalid"):
        if hasattr(bot, "consume_recovery_flag"):
            bot.consume_recovery_flag("captcha_invalid")
        bot._last_dialog_action = None

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

    # Clear field only — do NOT refresh unless server rejected previous attempt.
    # force_refresh=True every time made BotDetect rotate images mid-solve.
    try:
        if captcha is not None:
            captcha.fill("")
    except Exception:
        pass
    bot._otp_prefilled_captcha = None

    min_length, max_length = _otp_captcha_length_bounds(bot, captcha)
    bot._otp_captcha_min_length = min_length
    bot._otp_captcha_max_length = max_length

    from captcha_engine import solve_and_fill_captcha
    res = solve_and_fill_captcha(
        page,
        input_selector="#ctl00_ContentPlaceHolder1_tbCaptcha2, input[id*='tbCaptcha2' i]",
        button_selector="",
        submit=False,
        max_retries=2,
        bot=bot,
        stop_event=getattr(bot, "_active_stop_event", None),
        step="STEP_2_OTP",
    )
    bot._otp_force_captcha_refresh = False
    if res.filled:
        code = str(res.code or "").strip().upper()
        if not (min_length <= len(code) <= max_length) or len(code) > 5:
            _log_fa(
                bot,
                f"طول کپچا ({len(code)}:{code}) خارج از بازه ۴–۵؛ ارسال نمی‌شود تا TTL نسوزد.",
                "warning",
                page,
            )
            return False
        bot._otp_prefilled_captcha = code
        if not _restore_otp_field(bot, page):
            _log_fa(bot, "فیلد پیامک بعد از کپچا خالی بود و کد ذخیره‌شده برنگشت؛ ارسال نمی‌شود.", "warning", page)
            return False
        if button is not None:
            try:
                button.click(timeout=1200, no_wait_after=True)
                return True
            except TypeError:
                try:
                    button.click(timeout=1200)
                    return True
                except Exception:
                    pass
            except Exception:
                pass
            try:
                if captcha is not None:
                    captcha.press("Enter", timeout=400)
                    return True
            except Exception:
                pass
            return False
        return False
    return False


def get_otp_from_db_fresh(bot):
    """استعلام سریع کد OTP از دیتابیس محلی و نظرسنجی کوتاه از سرور — آماده پذیرش فوری پیامک."""
    validity_sec = min(float(bot.settings.get("otp_validity_seconds", OTP_TTL_HARD_CAP_SEC)), float(OTP_TTL_HARD_CAP_SEC))
    now = time.time()
    try:
        # 1) Local DB first (0ms) — UI / Android forwarder / relay writes land here.
        record = DBHandler.get_otp_record(bot.nid)
        if record:
            from otp_transport import fresh_record
            valid = fresh_record(bot, record, now)
            if valid:
                return valid, "local_db"

        # 2) Remote poll: keep short so SMS is accepted as soon as the server has it.
        poll_every = float(bot.settings.get("otp_remote_poll_interval_sec", 0.5) or 0.5)
        poll_every = max(0.25, min(poll_every, 2.0))
        last_poll = float(getattr(bot, "_last_remote_otp_poll_ts", 0.0) or 0.0)
        if now - last_poll >= poll_every:
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


def _detect_site_otp_ttl(page, default_seconds: int = 115) -> int:
    """Parse site TTL text, then hard-cap at 115 seconds."""
    parsed = None
    try:
        body = page.inner_text("body") or ""
        m = re.search(r"(\d{2,3})\s*ثانیه", body)
        if m:
            val = int(m.group(1))
            if 20 <= val <= 600:
                parsed = val
    except Exception:
        pass
    base = parsed if parsed is not None else int(default_seconds or OTP_TTL_HARD_CAP_SEC)
    return max(20, min(int(base), OTP_TTL_HARD_CAP_SEC))


def _post_submit_watch(
    bot,
    page,
    stop_event,
    sleep_with_stop_fn,
    last_code: str,
    wait_seconds: float = None,
    otp_issued_at: float = None,
    effective_ttl: float = None,
):
    """بعد از Submit، تا باقیمانده TTL پایش می‌کند و SESSION_EXPIRED را از OTP_REJECTED جدا می‌کند."""
    ttl = float(effective_ttl if effective_ttl is not None else OTP_TTL_HARD_CAP_SEC)
    ttl = min(ttl, float(OTP_TTL_HARD_CAP_SEC))
    if wait_seconds is None:
        issued = float(otp_issued_at or getattr(bot, "_otp_entry_time", 0) or time.time())
        wait_seconds = max(1.0, (issued + ttl) - time.time())
    deadline = time.time() + float(wait_seconds)
    _log_fa(
        bot,
        f"⏱️ پایش پس از ارسال کد پیامک تا {int(wait_seconds)}ث باقیمانده TTL: بررسی بانک / انقضا / رد کد...",
        "info",
        page,
    )

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

        fail_type = _detect_failure_type(page, bot=bot)
        if fail_type in ("otp", "otp_expired") or getattr(bot, "_last_dialog_action", None) == "otp_expired" or getattr(bot, "_recovery_flags", {}).get("otp_expired"):
            rejected = getattr(bot, "_consumed_otp_digests", set())
            rejected.add(hashlib.sha256(str(last_code).encode()).hexdigest())
            bot._consumed_otp_digests = rejected
            event_name = "OTP_INVALID" if fail_type == "otp" else "OTP_EXPIRED"
            bot.emit_event(
                event_name,
                step="STEP_2_OTP",
                reason="site_rejected_otp",
                result="OTP_REJECTED",
                message="کد پیامک توسط سامانه رد یا منقضی اعلام شد (OTP_REJECTED).",
                level="warning",
                page=page,
            )
            from otp_transport import invalidate_otp
            invalidate_otp(bot, 'expired' if fail_type == 'otp_expired' else 'rejected')
            DBHandler.update_runtime(bot.nid, otp_state='OTP_EXPIRED' if fail_type == 'otp_expired' else 'OTP_INVALID')
            if hasattr(bot, "clear_otp_locks"):
                bot.clear_otp_locks()
            bot._set_recovery_flag("otp_expired")
            DBHandler.update_status(bot.nid, "کد منقضی - شروع مجدد", "کد تایید منقضی/رد شد؛ در حال بازگشت به مرحله ۱ جهت اخذ کد جدید")
            return "otp_expired", None, None
        elif fail_type == "captcha" or getattr(bot, "_last_dialog_action", None) == "captcha_invalid":
            bot._last_dialog_action = None
            return "retry", None, None
        elif getattr(bot, "_last_dialog_action", None) in ("otp_step_capacity_retry", "system_capacity_updating", "bank_capacity_full", "generic_error"):
            dialog_msg = getattr(bot, "_last_dialog_message", "")
            bot._last_dialog_action = None
            _log_fa(bot, f"⚠️ سامانه پیام موقت اعلام کرد ({dialog_msg})؛ تصویر کپچا حفظ می‌شود.", "warning", page)
            return "timeout", None, None

        if _entry_form_visible_now(page):
            # Distinguish session regression from OTP rejection (already handled above).
            from core.stage_detector import detect_current_stage, SESSION_EXPIRED as SSOT_SESSION_EXPIRED
            det = detect_current_stage(page, bot=bot)
            if det.stage == SSOT_SESSION_EXPIRED or "session" in (det.reason or ""):
                bot.emit_event(
                    "SESSION_EXPIRED",
                    step="STEP_2_OTP",
                    reason="regressed_to_entry_after_submit",
                    result="SESSION_EXPIRED",
                    message="نشست پس از ارسال OTP منقضی شد (SESSION_EXPIRED).",
                    level="warning",
                    page=page,
                )
                if hasattr(bot, "clear_otp_locks"):
                    bot.clear_otp_locks()
                bot._set_recovery_flag("session_expired")
                DBHandler.update_runtime(bot.nid, otp_state="SESSION_EXPIRED")
                return "session_expired", None, None
            _log_fa(bot, "↩️ بازگشت به فرم ورود بدون پیام رد OTP؛ SESSION_EXPIRED تلقی شد.", "warning", page)
            if hasattr(bot, "clear_otp_locks"):
                bot.clear_otp_locks()
            bot._set_recovery_flag("session_expired")
            DBHandler.update_runtime(bot.nid, otp_state="SESSION_EXPIRED")
            return "session_expired", None, None

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
    default_ttl = min(int(bot.settings.get("otp_validity_seconds", OTP_TTL_HARD_CAP_SEC)), OTP_TTL_HARD_CAP_SEC)
    effective_ttl = min(_detect_site_otp_ttl(page, default_seconds=default_ttl), OTP_TTL_HARD_CAP_SEC)
    now_ts = time.time()
    existing_entry = float(getattr(bot, "_otp_entry_time", 0) or 0)
    hot_reentry = bool(getattr(bot, "_page3_hot_reentry", False))
    retained_ready = False
    ctx0 = getattr(bot, "ctx", None)
    restore_code = None
    if hot_reentry and ctx0 is not None and ctx0.is_otp_valid():
        restore_code = ctx0.cached_otp_code
        retained_ready = True
    elif hot_reentry and getattr(bot, "_last_retained_otp", None):
        restore_code = bot._last_retained_otp
        retained_ready = True
    if hot_reentry and restore_code:
        # Page3↔OTP: same SMS code must stay injectable (never treat as final-consumed yet).
        digest = hashlib.sha256(str(restore_code).encode()).hexdigest()
        bot._consumed_otp_digests = set(getattr(bot, "_consumed_otp_digests", set()) or set()) - {digest}
        bot._last_retained_otp = restore_code
        try:
            old = DBHandler.get_otp_record(bot.nid) or {}
            ts = old.get("ts") or getattr(ctx0, "otp_received_at", None) or time.time()
            DBHandler.set_otp(
                bot.nid,
                restore_code,
                ts=ts,
                status="bank_ready",
                request_id=getattr(bot, "_otp_request_id", None) or old.get("request_id"),
            )
        except Exception:
            pass
    if not hot_reentry:
        # First/normal OTP wait: discard stale cached codes so we actually wait for SMS.
        bot._last_retained_otp = None
        bot._page3_hot_reentry = False
        if ctx0 is not None:
            try:
                ctx0.invalidate_otp()
            except Exception:
                pass
        try:
            # Drop leftover bank_ready/submitted codes from prior cycles.
            old = DBHandler.get_otp_record(bot.nid) or {}
            if old.get("otp") and str(old.get("status") or "") in {"bank_ready", "submitted", "received"}:
                DBHandler.set_otp(bot.nid, old["otp"], ts=old.get("ts"), status="expired",
                                  request_id=old.get("request_id"))
        except Exception:
            pass
    # Hot Page3→OTP: never remint OTP request / never reset TTL clock.
    if retained_ready and existing_entry:
        otp_issued_at = existing_entry
    elif not existing_entry or (now_ts - existing_entry >= effective_ttl):
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
        result="waiting_for_sms" if not retained_ready else "hot_reentry_with_cached_otp",
        message=(
            f"بازگشت سریع به تایید پیامک با کد ذخیره‌شده؛ ارسال فوری به صفحه بانک..."
            if retained_ready else
            f"صفحه تایید پیامکی فعال شد (مهلت اعتبار: {effective_ttl} ثانیه)."
        ),
        level="info",
        page=page,
    )
    DBHandler.update_status(
        bot.nid,
        "ورود مجدد به بانک" if retained_ready else "منتظر پیامک",
        "ارسال فوری OTP ذخیره‌شده به صفحه ۳" if retained_ready else "در انتظار دریافت کد تایید پیامکی",
    )
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
    elif retained_ready:
        # Reuse OTP, but always fresh-solve CAPTCHA on the submit path.
        bot._otp_force_captcha_refresh = True
        bot._otp_prefilled_captcha = None
        _log_fa(bot, "⚡ ورود مجدد از صفحه ۳؛ حل کپچا و ارسال فوری OTP ذخیره‌شده...", "info", page)
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
    # User request: no hard captcha attempt ceiling — keep retrying until Page3 / stop.
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
        # Retained OTP فقط وقتی از صفحه ۳ برگشته‌ایم — ورود اول باید منتظر پیامک جدید بماند.
        if not code and hot_reentry:
            ctx = getattr(bot, "ctx", None)
            if ctx is not None and ctx.is_otp_valid():
                code, source = ctx.cached_otp_code, "context_cache"
            elif getattr(bot, "_last_retained_otp", None):
                code, source = bot._last_retained_otp, "retained_cache"
        if not code and dom_val.isascii() and dom_val.isdigit() and manual_length_ok:
            if hashlib.sha256(dom_val.encode()).hexdigest() not in getattr(bot, "_consumed_otp_digests", set()):
                code, source = dom_val, "browser_manual"
                DBHandler.set_otp(bot.nid, dom_val, ts=time.time(), status="manual",
                                  request_id=getattr(bot, '_otp_request_id', None))
                _log_fa(bot, "کد تایید دستی دریافت شد؛ در حال بررسی.", "info")

        if code and not submission_pending:
            digest = hashlib.sha256(str(code).encode()).hexdigest()
            if digest in getattr(bot, '_consumed_otp_digests', set()):
                if hot_reentry:
                    # Same code is intentionally reused until final tracking / true expiry.
                    bot._consumed_otp_digests = set(getattr(bot, '_consumed_otp_digests', set()) or set()) - {digest}
                else:
                    code = None

        if (time.monotonic() >= receive_deadline or time.time() - otp_issued_at >= effective_ttl) and not code and not manual_required:
            DBHandler.update_runtime(bot.nid, otp_state="SMS_TIMEOUT")
            bot.emit_event("SMS_TIMEOUT", step="STEP_2_OTP", result="MANUAL_OTP_REQUIRED",
                           message="پیامک در مهلت تعیین‌شده نرسید؛ ورود دستی در همین صفحه فعال است.", page=page)
            DBHandler.update_runtime(bot.nid, otp_state="MANUAL_OTP_REQUIRED")
            manual_required = True
        if not code:
            # Stay hot for inbound SMS / manual paste — poll often, don't sleep away TTL.
            if sleep_with_stop_fn(stop_event, 0.12):
                break
            continue

        if not submission_pending:
            DBHandler.update_runtime(bot.nid, otp_state="CODE_VALIDATED")

            if code != last_injected_code:
                record = DBHandler.get_otp_record(bot.nid) or {}
                otp_received_at = float(record.get("ts") or time.time()) if record.get("otp") == code else time.time()
                captcha_failures = 0

            # While OTP page is still open, keep accepting the same code (user request).
            # Only expire if page regressed away from OTP or stop requested.
            if otp_received_at and time.time() - otp_received_at >= effective_ttl:
                if _otp_input_visible_now(page) or getattr(bot, "_page3_hot_reentry", False):
                    _log_fa(bot, "⏱️ TTL اسمی گذشته ولی صفحه OTP باز است؛ همان کد برای ارسال حفظ می‌شود.", "info", page)
                else:
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
            # Re-check SMS right before captcha OCR — code may have just arrived.
            newer, newer_src = get_otp_from_db_fresh(bot)
            if newer and newer != code:
                code, source = newer, newer_src
                last_injected_code = None
                continue
            if not submission_pending and not _solve_otp_captcha_fast(bot, page):
                captcha_failures += 1
                # The page keeps the current image. The local model reads it again.
                retry_pause = 0.05
                if sleep_with_stop_fn(stop_event, retry_pause):
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
            otp_issued_at=otp_issued_at,
            effective_ttl=effective_ttl,
        )

        if status == "otp_expired":
            _log_fa(bot, "🔄 کد تایید رد/منقضی شد (OTP_REJECTED)؛ بازگشت خودکار به مرحله ۱ برای صدور کد جدید (سقف ۱۱۵ث)...", "warning", page)
            bot._set_recovery_flag("otp_expired")
            return "otp_expired"

        if status == "session_expired":
            _log_fa(bot, "⚪ نشست پس از ارسال OTP منقضی شد (SESSION_EXPIRED)؛ نیاز به احراز مجدد.", "warning", page)
            bot._set_recovery_flag("session_expired")
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
            _log_fa(bot, f"🔄 کپچا رد شد؛ حل مجدد با مدل محلی (#{captcha_failures})...", "warning", page)
            try:
                _, captcha_inp, _ = _locate_otp_captcha_controls(page, wait_ms=1000)
                if captcha_inp is not None:
                    captcha_inp.fill('')
            except Exception:
                pass
            bot._otp_prefilled_captcha = None
            bot._otp_force_captcha_refresh = False
            bot._captcha_retry = 0  # never accumulate toward a hard stop
            if sleep_with_stop_fn(stop_event, 0.2 if getattr(bot, "_page3_hot_reentry", False) else 0.35):
                break
            continue

        if status == "bank":
            # Retain OTP for Step 3 duration; only mark final consumed upon tracking code
            record = DBHandler.get_otp_record(bot.nid) or {}
            DBHandler.set_otp(bot.nid, code, ts=record.get("ts"), status="bank_ready",
                              request_id=getattr(bot, '_otp_request_id', None))
            DBHandler.update_runtime(bot.nid, otp_state="OTP_ACCEPTED")
            bot._last_retained_otp = code
            bot._page3_hot_reentry = False
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
            if has_expired and not (_otp_input_visible_now(page) or getattr(bot, "_page3_hot_reentry", False)):
                _log_fa(bot, "🔄 مهلت زمانی کد پیامک به پایان رسید؛ خروج جهت بازگشت به مرحله ۱ و درخواست کد جدید...", "warning", page)
                bot._set_recovery_flag("otp_expired")
                return "otp_expired"

            # Already submitted once: keep watching briefly; do not auto-resubmit unless we had a code.
            if last_injected_code and _otp_input_visible_now(page):
                submission_pending = False
                bot._otp_force_captcha_refresh = True
                _log_fa(bot, "⏱️ پاسخ قطعی نیامد؛ همان OTP حفظ و کپچای تازه ارسال می‌شود...", "info", page)
                if sleep_with_stop_fn(stop_event, 0.35):
                    break
                continue
            elapsed = int(time.time() - (otp_received_at or otp_issued_at))
            remaining = max(1, effective_ttl - elapsed)
            _log_fa(bot, f"⏱️ نتیجه ارسال هنوز مشخص نیست (مهلت باقی‌مانده پیامک: {remaining} ثانیه)؛ انتظار بدون ارسال مجدد...", "info", page)
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
        if form.reason.startswith('ambiguous') and not _otp_input_visible_now(page):
            flow.transition('PAUSED', form.reason)
            return 'manual_required'
        record = DBHandler.get_otp_record(self.bot.nid) or {}
        same_request = not record.get('request_id') or record['request_id'] == getattr(self.bot, '_otp_request_id', None)
        if record.get('status') == 'submitted' and same_request and getattr(self.bot, '_page3_hot_reentry', False):
            flow.transition('PAUSED', 'previous_otp_submission_unresolved')
            return 'manual_required'
        if record.get('status') == 'submitted' and not getattr(self.bot, '_page3_hot_reentry', False):
            try:
                DBHandler.set_otp(
                    self.bot.nid,
                    record.get('otp') or '',
                    ts=record.get('ts'),
                    status='expired',
                    request_id=record.get('request_id'),
                )
            except Exception:
                pass
        if record.get('status') in {'consumed', 'expired', 'rejected'} and same_request:
            self.bot._last_retained_otp = None
            otp_on_page = form.resolve('otp') is not None or _otp_input_visible_now(page)
            if otp_on_page and flow.http_status != 401:
                self.bot.consume_recovery_flag('otp_expired')
                if getattr(self.bot, '_last_dialog_action', None) == 'otp_expired':
                    self.bot._last_dialog_action = None
            else:
                DBHandler.update_runtime(self.bot.nid, otp_state='OTP_EXPIRED')
                flow.transition('OTP_REAUTH_REQUIRED', 'previous_code_not_reusable')
                return 'otp_expired'
        flow.transition('PAGE_REDETECTING', reason)
        flow.transition('STEP2_OTP_REQUIRED', reason)
        result = _run_otp_step(self.bot, page, stop_event, sleep_with_stop_fn)
        if result == 'bank_ready':
            # Keep OTP reusable for Page3↔OTP hot loop until final tracking or true expiry.
            record = DBHandler.get_otp_record(self.bot.nid) or {}
            if record.get('otp') and record.get('status') != 'bank_ready':
                DBHandler.set_otp(
                    self.bot.nid,
                    record['otp'],
                    ts=record.get('ts'),
                    status='bank_ready',
                    request_id=getattr(self.bot, '_otp_request_id', None),
                )
            if record.get('otp'):
                self.bot._last_retained_otp = record['otp']
            if flow.last_step3_visit:
                flow.successful_reentries += 1
            flow.last_step3_visit = time.time()
            flow.transition('STEP3_ACTIVE', 'server_verified_bank_dom')
        elif result == 'otp_expired':
            # Always restart from Step 1 — do not park on MANUAL_REQUIRED.
            self.bot._page3_hot_reentry = False
            self.bot._last_retained_otp = None
            ctx = getattr(self.bot, 'ctx', None)
            if ctx is not None:
                try:
                    ctx.invalidate_otp()
                except Exception:
                    pass
            if DBHandler.get_runtime(self.bot.nid).get('otp_state') != 'OTP_INVALID':
                DBHandler.update_runtime(self.bot.nid, otp_state='OTP_EXPIRED')
            try:
                stale = DBHandler.get_otp_record(self.bot.nid) or {}
                if stale.get('otp') and str(stale.get('status') or '') == 'submitted':
                    DBHandler.set_otp(
                        self.bot.nid,
                        stale.get('otp') or '',
                        ts=stale.get('ts'),
                        status='expired',
                        request_id=stale.get('request_id'),
                    )
            except Exception:
                pass
            self.bot.emit_event(
                'OTP_EXPIRED',
                step='STEP_2_OTP',
                reason='otp_expired_restart_step1',
                result='reauth_required',
                message='کد پیامک منقضی شد؛ بازگشت خودکار به مرحله ۱ برای صدور کد جدید.',
                level='warning',
                page=page,
            )
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
