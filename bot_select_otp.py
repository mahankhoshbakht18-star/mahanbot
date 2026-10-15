import json
import re
import time
from database import DBHandler
from event_logger import emit_structured_event
from browser_actions import BrowserActions
from bot_select_captcha import (
    CAPTCHA_IMAGE_SELECTORS,
    CAPTCHA_RELOAD_SELECTORS,
    wait_for_captcha_image_ready,
)


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
        "#ctl00_ContentPlaceHolder1_ddlCity",
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

    image, _ = wait_for_captcha_image_ready(page, timeout_ms=wait_ms, trigger_refresh_if_missing=True)
    captcha_input, submit_btn = None, None

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

    for _, scope in _iter_scopes(page):
        captcha_input = BrowserActions.find_captcha_input(
            scope, "#ctl00_ContentPlaceHolder1_tbCaptcha2"
        )
        if not captcha_input:
            captcha_input = BrowserActions.find_captcha_input(
                scope, "input[name$='tbCaptcha2']"
            )
        if captcha_input:
            break

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


def _refresh_captcha_image(bot, page):
    image, _, _ = _locate_otp_captcha_controls(page, wait_ms=1200)
    if not image:
        # Even if image is not visible yet, try clicking any reload link on the page
        for _, scope in _iter_scopes(page):
            for rsel in CAPTCHA_RELOAD_SELECTORS:
                try:
                    rloc = scope.locator(rsel).first
                    if rloc.count() > 0 and rloc.is_visible():
                        rloc.click(timeout=700)
                        _log_fa(bot, "🔄 درخواست بارگذاری تصویر کپچا ارسال شد.", "info", page)
                        wait_for_captcha_image_ready(page, timeout_ms=2500, trigger_refresh_if_missing=False)
                        return True
                except Exception:
                    continue
        _log_fa(bot, "ℹ️ تصویر کپچا هنوز بارگذاری نشده است؛ در حال انتظار برای نمایش تصویر...", "info", page)
        return False
    try:
        previous = image.screenshot(type="png")
        clicked = False
        for _, scope in _iter_scopes(page):
            for rsel in CAPTCHA_RELOAD_SELECTORS:
                try:
                    reload_link = scope.locator(rsel).first
                    if reload_link.count() > 0 and reload_link.is_visible():
                        reload_link.click(timeout=700)
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                break
        if not clicked:
            image.click(timeout=700)

        deadline = time.time() + 2.5
        while time.time() < deadline:
            page.wait_for_timeout(60)
            try:
                current_shot = image.screenshot(type="png")
                if current_shot and current_shot != previous:
                    _log_fa(bot, "🔄 تصویر تازه کپچا دریافت شد.", "info", page)
                    return True
            except Exception:
                break
        return True
    except Exception as exc:
        _log_fa(bot, f"⚠️ رفرش کپچا ناموفق بود: {exc}", "warning", page)
        return False


def _solve_otp_captcha_fast(bot, page):
    """Wait for the OTP-stage CAPTCHA to appear/render and solve it with the local model."""
    image, captcha_input, submit_btn = _locate_otp_captcha_controls(page, wait_ms=4000)
    if not image:
        _refresh_captcha_image(bot, page)
        image, captcha_input, submit_btn = _locate_otp_captcha_controls(page, wait_ms=3000)

    if not captcha_input or not image or not submit_btn:
        missing = []
        if not image:
            missing.append("تصویر کپچا")
        if not captcha_input:
            missing.append("فیلد کپچا")
        if not submit_btn:
            missing.append("دکمه ادامه")
        _log_fa(bot, f"⏳ منتظر ظاهر شدن المان‌های کپچا در صفحه ({'، '.join(missing)})...", "warning", page)
        return False

    stop_event = getattr(bot, "_active_stop_event", None)
    if stop_event is None:
        return False
    return bot.solve_captcha_with_model(
        page,
        captcha_input,
        submit_btn,
        stop_event=stop_event,
        source="otp",
        captcha_element=image,
    )


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
            if raw_code.isdigit() and 4 <= len(raw_code) <= 8:
                if rec_ts == 0.0 or (rec_ts >= min_ts and (now - rec_ts) <= validity_sec):
                    return raw_code, "local_db"

        # 2) Throttle remote server HTTP polling to once every 3.0s so manual browser entry is never blocked
        last_poll = float(getattr(bot, "_last_remote_otp_poll_ts", 0.0) or 0.0)
        if now - last_poll >= 3.0:
            bot._last_remote_otp_poll_ts = now
            code = bot.get_otp_code(timeout=1)
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
    try:
        if not otp_locator.is_visible():
            otp_locator.wait_for(state="visible", timeout=1000)

        try:
            BrowserActions.human_fill(otp_locator, code, timeout=1500)
        except Exception:
            BrowserActions.fast_fill(otp_locator, code)

        if otp_locator.input_value() == code:
            _log_fa(bot, "📥 کد جدید OTP در فرم وارد شد.", "success", page)
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


def _detect_failure_type(page) -> str:
    """تشخیص نوع خطا: کپچا یا OTP"""
    try:
        body = (page.inner_text("body") or "").lower()
        if "کد امنیتی" in body and ("اشتباه" in body or "نادرست" in body or "صحیح" in body or "نامعتبر" in body):
            return "captcha"
        if ("کد تایید" in body or "کد تأیید" in body) and ("نامعتبر" in body or "منقضی" in body or "اشتباه" in body):
            return "otp"
        if "نامعتبر" in body or "منقضی" in body:
            return "otp"
        return None
    except Exception:
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

        new_code, source = get_otp_from_db_fresh(bot)
        if new_code and new_code != last_code:
            _log_fa(bot, f"⚡ کد جدید در زمان انتظار از منبع {source} رسید.", "warning", page)
            return "new_code", new_code, source

        fail_type = _detect_failure_type(page)
        if fail_type == "otp" or getattr(bot, "_last_dialog_action", None) == "otp_expired":
            bot.emit_event(
                "OTP_EXPIRED",
                step="STEP_2_OTP",
                reason="site_rejected_otp",
                result="waiting_new_otp",
                message="کد پیامک نامعتبر یا منقضی تشخیص داده شد؛ پاکسازی و انتظار برای کد جدید.",
                level="warning",
                page=page,
            )
            bot.clear_otp_backend()
            bot.mark_otp_entry_time()
            DBHandler.update_status(bot.nid, "منتظر پیامک", "OTP expired; waiting for fresh code")
            return "retry", None, None
        elif fail_type == "captcha" or getattr(bot, "_last_dialog_action", None) == "captcha_invalid":
            bot._last_dialog_action = None
            _log_fa(bot, "❌ کپچای مرحله پیامک اشتباه بود؛ دریافت تصویر جدید و حل مجدد با مدل...", "warning", page)
            _refresh_captcha_image(bot, page)
            return "retry", None, None

        if sleep_with_stop_fn(stop_event, 0.3):
            return "stopped", None, None

    return "timeout", None, None


def fast_otp_sync_and_inject(bot, page, stop_event, sleep_with_stop_fn):
    """
    Step 2: OTP Confirmation State Handler.
    - Activates OTP_REQUIRED state and records issuance time.
    - Proactively ensures the Step 2 CAPTCHA image is rendered in DOM.
    - Injects OTP once per code, then uses the local CRNN model to solve CAPTCHA.
    - If CAPTCHA image hasn't appeared yet, waits/triggers reload and hands it to the model as soon as it appears.
    """
    default_ttl = int(bot.settings.get("otp_validity_seconds", 120))
    effective_ttl = _detect_site_otp_ttl(page, default_seconds=default_ttl)
    otp_issued_at = bot.mark_otp_entry_time()

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

    try:
        bot.suspend_watchdog(max(180, effective_ttl + 60))
    except Exception:
        pass

    last_injected_code = None
    otp_received_at = 0.0
    otp_not_found_ts = 0.0
    captcha_failures = 0
    max_captcha_retries = max(3, int(bot.settings.get("captcha_max_retries", 5)))

    while not stop_event.is_set():
        bot.mark_progress("waiting_for_otp_loop")

        if _bank_select_visible_now(page):
            if hasattr(bot, "clear_otp_locks"):
                bot.clear_otp_locks()
            _log_fa(bot, "✅ مرحله انتخاب بانک فعال شد؛ خروج از سیکل OTP.", "success", page)
            return "bank_ready"

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
                return None
            if sleep_with_stop_fn(stop_event, 0.3):
                break
            continue

        otp_not_found_ts = 0.0

        dom_val = (_safe_input_value(otp_locator) or "").strip()
        code, source = get_otp_from_db_fresh(bot)
        if not code and dom_val.isdigit():
            if len(dom_val) >= 6:
                code, source = dom_val, "browser_manual"
            elif len(dom_val) == 5:
                # Wait briefly to make sure the user is not still typing the 6th digit
                if getattr(bot, "_partial_dom_otp_val", "") != dom_val:
                    bot._partial_dom_otp_val = dom_val
                    bot._partial_dom_otp_ts = time.time()
                elif (time.time() - getattr(bot, "_partial_dom_otp_ts", 0.0)) >= 0.75:
                    code, source = dom_val, "browser_manual"

        if time.time() - otp_issued_at >= effective_ttl and not code:
            bot.emit_event(
                "OTP_EXPIRED",
                step="STEP_2_OTP",
                reason=f"elapsed>{effective_ttl}s",
                result="expired",
                message=f"مهلت {effective_ttl} ثانیه‌ای کد پیامک به پایان رسید.",
                level="warning",
                page=page,
            )
            bot.clear_otp_backend()
            if not bot.register_otp_failure("otp_ttl_expired"):
                DBHandler.update_status(bot.nid, "نیازمند ورود مجدد", "تعداد دفعات انقضای پیامک به سقف مجاز رسید")
                return "otp_expired"
            otp_issued_at = bot.mark_otp_entry_time()
            return "otp_expired"

        if not code:
            if sleep_with_stop_fn(stop_event, 0.3):
                break
            continue

        if code != last_injected_code:
            otp_received_at = time.time()
            captcha_failures = 0

        if otp_received_at and time.time() - otp_received_at >= effective_ttl:
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
            last_injected_code = None
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

        if not _solve_otp_captcha_fast(bot, page):
            captcha_failures += 1
            if captcha_failures >= max_captcha_retries and bool(bot.settings.get("pause_for_manual_captcha", True)):
                from bot_select_captcha import wait_for_manual_captcha_handoff
                if wait_for_manual_captcha_handoff(
                    bot,
                    page,
                    "#ctl00_ContentPlaceHolder1_tbCaptcha2",
                    "#ctl00_ContentPlaceHolder1_btnContinue1",
                    stop_event,
                    source="otp_stage",
                    success_selectors=("#ctl00_ContentPlaceHolder1_ddlBankName", "select[name$='ddlBankName']"),
                ):
                    if hasattr(bot, "clear_otp_locks"):
                        bot.clear_otp_locks()
                    return "bank_ready"
                return None
            _refresh_captcha_image(bot, page)
            if sleep_with_stop_fn(stop_event, 0.4):
                break
            continue

        status, new_code, _ = _post_submit_watch(
            bot=bot,
            page=page,
            stop_event=stop_event,
            sleep_with_stop_fn=sleep_with_stop_fn,
            last_code=last_injected_code or code,
            wait_seconds=10.0,
        )

        if status == "bank":
            if hasattr(bot, "clear_otp_locks"):
                bot.clear_otp_locks()
            return "bank_ready"

        if status == "new_code" and new_code:
            continue

        if status in {"retry", "timeout"}:
            continue

        if status == "stopped":
            break

        if sleep_with_stop_fn(stop_event, 0.3):
            break

    return None


def detect_otp_failure(bot, page) -> bool:
    try:
        body = (page.inner_text("body") or "").lower()
        if "ظرفیت" in body:
            return False
        return any(x in body for x in ["کد تایید نامعتبر", "کد تأیید نامعتبر", "منقضی", "کد وارد شده اشتباه"])
    except Exception:
        return False


def handle_otp_dialog(bot, dialog):
    msg = dialog.message() if callable(getattr(dialog, "message", None)) else getattr(dialog, "message", "")
    if msg and any(x in msg for x in ["نامعتبر", "منقضی"]) and "ظرفیت" not in msg:
        _log_fa(bot, f"🚨 هشدار دیالوگ OTP: {msg}", "error")
        dialog.accept()
        return True
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
