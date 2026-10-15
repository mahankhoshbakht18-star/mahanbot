# bot_select.py
import time
import os
import json
import random
import re
import threading
import hashlib

from browser_launcher import BrowserLaunchError, PlaywrightError, close_browser, safe_goto
from bot_core import BotCore
from bot_helpers import sleep_with_stop
from network_diagnostics import SiteRejectedError, check_rejection
from browser_actions import BrowserActions
from database import DBHandler
from event_logger import EVENT_BROADCASTER, build_event, emit_structured_event
from bot_select_firewall import firewall_gate, firewall_input_visible
from bot_select_captcha import (
    refresh_captcha_or_reload,
    detect_captcha_failure,
    solve_captcha_wrapper,
    solve_captcha_step,
    wait_for_manual_captcha_handoff,
)
from bot_select_otp import (
    get_otp_from_db_fresh,
    fast_otp_sync_and_inject,
    handle_otp_dialog,
    notify_favorite_banks,
    detect_otp_failure,
    otp_input_visible,
    resend_enabled,
    can_navigate_from_otp,
)
from bot_select_bank import (
    process_bank_selection_v2,
    return_to_bank_field,
    select_first_available_branch,
    process_branch_selection,
    wait_for_branch_fully_loaded,
    load_runtime_data,
)

TARGET_URL = "https://ve.cbi.ir/SelectBnkShb.aspx"
CHILD_SELECT_URL = "https://ve.cbi.ir/ChildSelectBnkShb.aspx"


class LoanTypeAdapter:
    """Adapter separating Marriage Loan (وام ازدواج) and Childbearing Loan (وام فرزندآوری) differences."""

    def __init__(self, loan_type: str = "marriage", user_data: dict = None):
        raw = str(loan_type or (user_data or {}).get("loan_type") or "marriage").strip().lower()
        if raw in {"child", "childbearing", "farzand", "فرزند", "فرزندآوری"}:
            self.kind = "childbearing"
            self.radio_selector = "#ctl00_ContentPlaceHolder1_rbtnChild, input[value*='Child'], input[id*='Child']"
            self.loan_option_selector = None
        else:
            self.kind = "marriage"
            self.radio_selector = f"#ctl00_ContentPlaceHolder1_{loan_type}" if str(loan_type).startswith("rbtn") else "#ctl00_ContentPlaceHolder1_rbtnNaghdi"
            self.loan_option_selector = self.radio_selector

    @property
    def target_select_url(self) -> str:
        # Both marriage and childbearing bank selection on ve.cbi.ir use SelectBnkShb.aspx (or ChildSelectBnkShb.aspx if configured)
        return TARGET_URL

    def validate_applicant(self, nid: str, user_data: dict) -> tuple[bool, str]:
        clean_nid = str(nid or "").strip()
        if not clean_nid.isdigit() or len(clean_nid) != 10:
            return False, "کد ملی متقاضی باید دقیقاً ۱۰ رقم باشد."
        if self.kind == "childbearing":
            child_nid = str((user_data or {}).get("child_national_id") or "").strip()
            if child_nid and (not child_nid.isdigit() or len(child_nid) != 10):
                return False, "کد ملی فرزند باید دقیقاً ۱۰ رقم باشد."
        return True, "ok"

    def fill_entry_extras(self, bot, page, user_data: dict):
        """Fill any loan-type-specific controls ONLY if they actually exist and are visible in DOM."""
        if self.loan_option_selector:
            try:
                loc = page.locator(self.loan_option_selector)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.check()
            except Exception:
                pass
        if self.kind == "childbearing":
            child_nid = str((user_data or {}).get("child_national_id") or "").strip()
            if child_nid:
                for sel in ("#ctl00_ContentPlaceHolder1_tbChildIDNo", "input[name$='tbChildIDNo']"):
                    try:
                        loc = page.locator(sel)
                        if loc.count() > 0 and loc.first.is_visible():
                            bot._fill_input_with_verification(page, loc.first, child_nid, "Child National ID")
                            break
                    except Exception:
                        continue


class BankSelectionBot(BotCore):
    def __init__(self, nid, settings, log_callback=None, captcha_service=None, browser_profile=None):
        super().__init__(
            nid,
            settings,
            log_callback=log_callback,
            captcha_service=captcha_service,
            browser_profile=browser_profile,
        )
        self._favorite_notified = set()
        self._nid_filled = False
        self._needs_reload = False
        self._dialog_handling = False
        self._safe_goto_locked = False
        self._otp_wait_lock = False
        self._otp_guard_until = 0.0
        self.reopen_browser_on_close = True
        self._bank_monitor_lock = threading.Lock()
        self._last_state_sweep_sig = None

    def fetch_otp_fast(self, stop_event=None, timeout: int = 120, min_ts: float = 0.0):
        """Fetch OTP from wait endpoint."""
        otp_code = self.wait_for_otp(stop_event=stop_event, timeout=timeout, min_ts=min_ts)
        if otp_code:
            return str(otp_code).strip()
        return None

    def _restore_from_checkpoint_if_available(self, page) -> str:
        """
        Check if a valid Checkpoint exists and compare it with actual DOM stage.
        Prevents duplicate final submissions and restores prior bank/city preferences.
        """
        existing_tracking = DBHandler.has_final_submission(self.nid)
        if existing_tracking:
            self.emit_event(
                "FINAL_SUBMIT_CONFIRMED",
                step="DONE",
                reason="already_submitted_in_checkpoint",
                result=f"tracking={self._mask(existing_tracking)}",
                message=f"درخواست این متقاضی قبلاً ثبت نهایی شده است (کد پیگیری: {self._mask(existing_tracking)}).",
                level="success",
                page=page,
            )
            return "DONE"

        if not bool(self.settings.get("resume_from_checkpoint", True)):
            return "NONE"

        cp = DBHandler.get_checkpoint(self.nid)
        if not cp or not cp.get("can_resume", True):
            return "NONE"

        actual_stage = self.detect_actual_page_stage(page)
        saved_step = cp.get("last_verified_step") or "IDLE"

        # Restore saved preferences into user_data if missing
        for k in ("state", "city", "priority_banks", "bank_mode"):
            if cp.get(k) and not getattr(self, "user_data", {}).get(k):
                getattr(self, "user_data", {})[k] = cp[k]

        if actual_stage == "STEP_3_BANK_SELECT":
            self.clear_otp_locks()
            self.emit_event(
                "CHECKPOINT_RESTORED",
                step="STEP_3_BANK_SELECT",
                reason=f"saved={saved_step} actual={actual_stage}",
                result="resumed_at_bank_select",
                message="نشست فعال شناسایی شد؛ ادامه عملیات مستقیماً از مرحله انتخاب بانک.",
                level="success",
                page=page,
            )
            return "STEP_3_BANK_SELECT"

        if actual_stage == "STEP_2_OTP":
            self.emit_event(
                "CHECKPOINT_RESTORED",
                step="STEP_2_OTP",
                reason=f"saved={saved_step} actual={actual_stage}",
                result="resumed_at_otp",
                message="ادامه عملیات از مرحله تایید پیامکی.",
                level="info",
                page=page,
            )
            return "STEP_2_OTP"

        if saved_step in {"STEP_3_BANK_SELECT", "STEP_2_OTP"} and actual_stage == "STEP_1_ENTRY_FORM":
            self.emit_event(
                "CHECKPOINT_RESTORED",
                step="STEP_1_ENTRY_FORM",
                reason=f"session_expired_from_{saved_step}",
                result="reauth_required",
                message="نشست قبلی منقضی شده است؛ بازیابی اطلاعات متقاضی از Checkpoint و ورود مجدد از مرحله اول.",
                level="warning",
                page=page,
            )
            return "STEP_1_ENTRY_FORM"

        return actual_stage

    def run(self, stop_event, loan_type="rbtnNaghdi"):
        attempt = 0
        self._active_stop_event = stop_event
        self._run_started_at = time.time()
        self._run_perf_started = time.perf_counter()

        # 0. Pre-flight check: prevent duplicate submission before even launching browser
        existing_tracking = DBHandler.has_final_submission(self.nid)
        if existing_tracking:
            self.set_runtime_state("SUCCESS", "DONE", browser_status="CLOSED")
            DBHandler.update_status(self.nid, "ثبت نهایی موفق", f"کد رهگیری: {self._mask(existing_tracking)}")
            self.emit_event(
                "FINAL_SUBMIT_CONFIRMED",
                step="DONE",
                reason="duplicate_submit_guard",
                result=f"tracking={self._mask(existing_tracking)}",
                message=f"ثبت نهایی قبلاً انجام شده است ({self._mask(existing_tracking)})؛ از اجرای تکراری جلوگیری شد.",
                level="success",
            )
            return

        adapter = LoanTypeAdapter(loan_type=loan_type, user_data=getattr(self, "user_data", {}))
        valid_app, err_msg = adapter.validate_applicant(self.nid, getattr(self, "user_data", {}))
        if not valid_app:
            self.set_runtime_state("ERROR", "VALIDATION_ERROR", browser_status="CLOSED", last_error=err_msg)
            DBHandler.update_status(self.nid, "خطای اطلاعات", err_msg)
            self.log(f"❌ {err_msg}", "error")
            return

        while attempt < self.retry_limit:
            if stop_event.is_set():
                break
            attempt += 1
            self.reset_attempt_state(attempt)

            playwright = None
            browser = None
            context = None
            page = None
            try:
                playwright, browser, context, page = self.setup_browser(stop_event)
                if stop_event.is_set():
                    return

                otp_attempted = False
                capacity_recheck_count = 0
                list_refresh_count = 0

                self.log(f"🚀 شروع عملیات ({adapter.kind} - دور {attempt})...", "info", page)
                automation_mode = str(self.settings.get("automation_mode", "normal")).lower()
                timing_profiles = {
                    "fast": {"step1": 450, "otp": 200, "probe": 80, "retry": 0.1},
                    "normal": {"step1": 750, "otp": 350, "probe": 150, "retry": 0.25},
                    "safe": {"step1": 1200, "otp": 600, "probe": 300, "retry": 0.5},
                }
                timing = timing_profiles.get(automation_mode, timing_profiles["normal"])
                step1_timeout = timing["step1"]
                otp_timeout = timing["otp"]
                state_probe_timeout = timing["probe"]

                entry_time = 0.0
                firewall_solved_count = 0
                max_firewall_solves = int(self.settings.get("max_firewall_solves_per_attempt", 6))
                next_state_sweep_at = 0.0

                state = "ENTRY_FORM"
                state_since = time.time()
                page_loaded_ts = state_since

                def set_state(new_state: str):
                    nonlocal state, state_since, entry_time
                    # CRITICAL FIX: Whenever entering BANK_SELECT / BANK_SELECTION, BRANCH_SELECT, ENTRY_FORM, or DONE,
                    # immediately clear any lingering OTP waiting/navigation guard and stale Step 1/2 flags!
                    if new_state in {"BANK_SELECT", "BANK_SELECTION", "BRANCH_SELECT", "ENTRY_FORM", "DONE", "BANK_CAPACITY_FULL"}:
                        self.clear_otp_locks()
                    if new_state in {"BANK_SELECT", "BANK_SELECTION", "BANK_CAPACITY_FULL"}:
                        self.consume_recovery_flag("captcha_invalid")
                        self.consume_recovery_flag("otp_expired")
                        if not getattr(self, "_bank_page_detected_emitted", False):
                            self._bank_page_detected_emitted = True
                            self.emit_event(
                                "BANK_PAGE_DETECTED",
                                step="BANK_SELECTION",
                                reason=f"url={getattr(page, 'url', '')}",
                                result="ddlBankName_ready",
                                message="ورود قطعی به صفحه سوم (انتخاب بانک و شعبه) تایید شد؛ قفل پیامک آزاد گردید.",
                                level="success",
                                page=page,
                            )
                    elif new_state in {"ENTRY_FORM", "OTP_FORM"}:
                        self._bank_page_detected_emitted = False
                        self._verified_step3_signature = None

                    if state != new_state:
                        state = new_state
                        state_since = time.time()
                        self.mark_progress(f"state:{new_state}")
                        status_map = {
                            "ENTRY_FORM": "RUNNING",
                            "OTP_FORM": "WAITING_USER_INPUT",
                            "BANK_SELECT": "MONITORING",
                            "BANK_SELECTION": "MONITORING",
                            "BANK_CAPACITY_FULL": "BANK_CAPACITY_FULL",
                            "BRANCH_SELECT": "PROCESSING",
                            "DONE": "SUCCESS",
                        }
                        panel_label_map = {
                            "ENTRY_FORM": "در حال ورود اطلاعات",
                            "OTP_FORM": "منتظر پیامک",
                            "BANK_SELECT": "انتخاب بانک",
                            "BANK_SELECTION": "انتخاب بانک",
                            "BANK_CAPACITY_FULL": "ظرفیت تکمیل",
                            "BRANCH_SELECT": "انتخاب شعبه",
                            "DONE": "ثبت نهایی موفق",
                        }
                        self.set_runtime_state(status_map.get(new_state, "RUNNING"), new_state, browser_status="OPEN")
                        if new_state in panel_label_map:
                            DBHandler.update_status(self.nid, panel_label_map[new_state], f"Stage: {new_state}")
                        if new_state == "OTP_FORM":
                            entry_time = self.mark_otp_entry_time(max(state_since, page_loaded_ts))
                            otp_ttl = float(self.settings.get("otp_validity_seconds", 120))
                            self._otp_guard_until = time.time() + otp_ttl
                            self._otp_wait_lock = True

                def state_timed_out(seconds: float) -> bool:
                    return (time.time() - state_since) > seconds

                def handle_partial_navigation_error(exc):
                    if "net::ERR_CONNECTION_CLOSED" in str(exc) and page.url.startswith(adapter.target_select_url):
                        self.log("⚠️ Connection closed but page appears loaded; continuing.", "warning", page)
                        return True
                    return False

                def page_has_target_selectors():
                    selectors = [
                        "#ctl00_ContentPlaceHolder1_tbIDNo",
                        "input[name$='tbIDNo']",
                        "input[name$='tbMobileConfCode']",
                        "#ctl00_ContentPlaceHolder1_ddlBankName",
                    ]
                    for selector in selectors:
                        try:
                            page.wait_for_selector(selector, timeout=2000, state="attached")
                            return True
                        except Exception:
                            continue
                    return False

                try:
                    navigation_started = time.perf_counter()
                    self._safe_goto_guarded(
                        page,
                        adapter.target_select_url,
                        timeout=int(self.settings.get("navigation_timeout_ms", 15000)),
                        wait_until="domcontentloaded",
                        log_callback=self.log,
                    )
                except SiteRejectedError:
                    raise
                except Exception as exc:
                    if handle_partial_navigation_error(exc) and page_has_target_selectors():
                        pass
                    else:
                        if sleep_with_stop(stop_event, 3.0, step=0.05):
                            return
                        try:
                            self._safe_goto_guarded(
                                page,
                                adapter.target_select_url,
                                timeout=int(self.settings.get("navigation_timeout_ms", 15000)),
                                wait_until="domcontentloaded",
                                log_callback=self.log,
                            )
                        except SiteRejectedError:
                            raise
                        except Exception as exc_retry:
                            if handle_partial_navigation_error(exc_retry) and page_has_target_selectors():
                                pass
                            else:
                                return
                page_loaded_ts = time.time()
                self.record_metric("page_load_ms", navigation_started)

                # Check Checkpoint & Resume synchronization right after initial page load
                resumed_stage = self._restore_from_checkpoint_if_available(page)
                if resumed_stage == "DONE":
                    return
                elif resumed_stage == "STEP_3_BANK_SELECT":
                    set_state("BANK_SELECT")
                elif resumed_stage == "STEP_2_OTP":
                    set_state("OTP_FORM")

                def wait_for_state(selector, label, timeout=250):
                    if stop_event.is_set():
                        return False
                    if self.is_firewall_challenge(page):
                        return False
                    try:
                        page.wait_for_selector(selector, timeout=timeout, state="visible")
                        return True
                    except Exception:
                        return False

                # Fast initial Step 1 check ONLY if not already on Step 2 or Step 3
                if state == "ENTRY_FORM" and not self._otp_input_visible(page):
                    fast_field_started = time.perf_counter()
                    try:
                        page.wait_for_selector(
                            "#ctl00_ContentPlaceHolder1_tbIDNo, input[name$='tbIDNo'], #ctl00_ContentPlaceHolder1_ddlBankName, #ans",
                            state="visible",
                            timeout=10000,
                        )
                    except Exception:
                        pass
                    fast_nid_locator = None
                    if not self._firewall_input_visible(page):
                        fast_nid_locator = self.wait_for_nid_input_priority(page, timeout_ms=250)
                    if fast_nid_locator is not None and not stop_event.is_set():
                        self.record_metric("nid_field_ready_ms", fast_field_started)
                        fill_started = time.perf_counter()
                        if self._fill_input_with_verification(
                            page, fast_nid_locator, self.nid, "National ID"
                        ):
                            self._nid_filled = True
                            adapter.fill_entry_extras(self, page, getattr(self, "user_data", {}))
                            self.record_metric("nid_fill_ms", fill_started)
                            set_state("ENTRY_FORM")
                            self.save_checkpoint("STEP_1_ENTRY_FORM", page=page, loan_type=adapter.kind, expected_next_step="STEP_2_OTP")
                            if not self._firewall_gate(page, stop_event):
                                self._solve_primary_captcha_fast(page, stop_event)

                while not stop_event.is_set():
                    if stop_event.is_set():
                        break

                    if page.is_closed():
                        self.set_runtime_state("RETRYING", "OPEN_BROWSER", browser_status="CLOSED", last_error="browser_closed")
                        self.log("مرورگر بسته/متوقف شد؛ بازکردن فوری مرورگر جدید...", "warning")
                        break

                    check_rejection(page)

                    # 1. Firewall challenge gate
                    if self._firewall_input_visible(page) or self.is_firewall_challenge(page):
                        solved = self.handle_firewall_challenge(page, stop_event)
                        if not solved:
                            return
                        firewall_solved_count += 1
                        DBHandler.update_status(self.nid, "Ready", "Firewall challenge cleared")
                        if firewall_solved_count >= max_firewall_solves:
                            self.log("⛔ Firewall solve limit reached; stopping job.", "error", page)
                            DBHandler.update_status(self.nid, "Stopped", "Firewall solve limit reached")
                            stop_event.set()
                            break
                        continue

                    # 2. Synchronized Watchdog check
                    if self.watchdog_check(
                        page,
                        stop_event,
                        selectors=[
                            "#ctl00_ContentPlaceHolder1_tbIDNo",
                            "input[name$='tbMobileConfCode']",
                            "#ctl00_ContentPlaceHolder1_ddlBankName",
                        ],
                    ):
                        continue

                    # 3. Handle classified recovery flags
                    if self.consume_recovery_flag("waf_blocked"):
                        self.set_runtime_state("ERROR", "WAF_BLOCKED", browser_status="OPEN", last_error=self._last_dialog_message or "waf_blocked")
                        DBHandler.update_status(self.nid, "محدودیت امنیتی سرور", "بررسی خودکار متوقف شد؛ نیازمند بررسی کاربر")
                        return

                    if self.consume_recovery_flag("session_expired"):
                        self.clear_otp_locks()
                        self.clear_otp_backend()
                        otp_attempted = False
                        DBHandler.update_status(self.nid, "نیازمند ورود مجدد", "نشست منقضی شد؛ بازگشت به مرحله تایید هویت")
                        self.save_checkpoint(
                            "SESSION_EXPIRED",
                            page=page,
                            session_active=False,
                            expected_next_step="STEP_1_ENTRY_FORM",
                            last_error_reason="session_expired",
                        )
                        self._safe_goto_guarded(
                            page,
                            adapter.target_select_url,
                            timeout=int(self.settings.get("navigation_timeout_ms", 15000)),
                            wait_until="domcontentloaded",
                            log_callback=self.log,
                        )
                        set_state("ENTRY_FORM")
                        continue

                    # If already on Step 3 (SelectBnkShb.aspx), discard stale OTP/CAPTCHA flags from Step 1/2
                    if self.detect_actual_page_stage(page) == "STEP_3_BANK_SELECT":
                        self.clear_otp_locks()
                        self.consume_recovery_flag("otp_expired")
                        self.consume_recovery_flag("captcha_invalid")

                    if self.consume_recovery_flag("otp_expired"):
                        otp_attempted = False
                        entry_time = self.mark_otp_entry_time()
                        set_state("OTP_FORM")
                        DBHandler.update_status(self.nid, "منتظر پیامک", "OTP expired; waiting for a fresh server code")
                        continue

                    if self.consume_recovery_flag("captcha_invalid"):
                        if self._captcha_retry <= self._captcha_retry_limit:
                            retry_delay = max(0.0, float(self.settings.get("captcha_retry_delay", 0.25)))
                            if sleep_with_stop(stop_event, retry_delay, step=0.05):
                                break
                            self._refresh_captcha_or_reload(page, allow_reload=False)
                            if self.detect_actual_page_stage(page) == "STEP_1_ENTRY_FORM":
                                self._solve_primary_captcha_fast(page, stop_event)
                        else:
                            if bool(self.settings.get("pause_for_manual_captcha", True)):
                                wait_for_manual_captcha_handoff(
                                    self,
                                    page,
                                    "input[name*='tbCaptcha']",
                                    "input[type='submit']",
                                    stop_event,
                                    source="dialog_captcha_invalid",
                                )
                            else:
                                self.set_runtime_state("ERROR", "CAPTCHA_ERROR", browser_status="OPEN", last_error="captcha_retry_limit")
                                self.log("حداکثر تلاش کپچا تمام شد؛ عملیات متوقف شد.", "error", page)
                                return
                        continue

                    if self.consume_recovery_flag("generic_error"):
                        # Do NOT blindly reload on SelectBnkShb.aspx if bank dropdown is still active!
                        actual_stage = self.detect_actual_page_stage(page)
                        if actual_stage == "STEP_3_BANK_SELECT":
                            self.clear_otp_locks()
                            self.log(f"ℹ️ پیام سامانه در صفحه انتخاب بانک دریافت شد ({self._last_dialog_message})؛ صفحه فعلی حفظ می‌شود.", "warning", page)
                        elif self._can_navigate_from_otp(page):
                            self._dump_state(
                                page,
                                selectors=[
                                    "#ctl00_ContentPlaceHolder1_tbIDNo",
                                    "input[name$='tbMobileConfCode']",
                                    "#ctl00_ContentPlaceHolder1_ddlBankName",
                                ],
                                reason="dialog_generic",
                            )
                            self._reload_guarded(page, reason="generic_error")
                        continue

                    self.dismiss_modals(page)

                    now = time.time()
                    if now >= next_state_sweep_at:
                        self._log_state_sweep(page)
                        next_state_sweep_at = now + 5.0

                    # Soft WAF / blocked message on page
                    try:
                        if wait_for_state("text=درخواست شما رد شد", "CHECK_BLOCKERS/SOFT_WAF", timeout=state_probe_timeout):
                            self.emit_event(
                                "WAF_BLOCKED",
                                step=state,
                                reason="soft_waf_text",
                                result="stopped",
                                message="پیام مسدودیت/رد درخواست از سمت سامانه مشاهده شد؛ توقف بررسی خودکار.",
                                level="error",
                                page=page,
                            )
                            self.set_runtime_state("ERROR", "WAF_BLOCKED", browser_status="OPEN", last_error="soft_waf")
                            DBHandler.update_status(self.nid, "محدودیت امنیتی سرور", "درخواست توسط سرور رد شد")
                            return
                    except Exception:
                        pass

                    # ==========================================================
                    # Determine Actual Stage from DOM (Step 3, Step 2, Step 1, Done)
                    # Note: Check Step 3 and Step 2 BEFORE probing Step 1 so we don't
                    # waste 1.5s probing missing Step 1 selectors when already on Step 3!
                    # ==========================================================
                    actual_stage = self.detect_actual_page_stage(page)

                    if actual_stage == "DONE":
                        set_state("DONE")
                        code = ""
                        try:
                            code = page.locator("#ctl00_ContentPlaceHolder1_lblTrackingCode").inner_text()
                        except Exception:
                            code = "CONFIRMED"
                        DBHandler.save_success_data(self.nid, code)
                        self.set_runtime_state("SUCCESS", "DONE", browser_status="OPEN")
                        self.emit_event(
                            "FINAL_SUBMIT_CONFIRMED",
                            step="DONE",
                            reason="tracking_code_visible",
                            result=f"tracking={self._mask(code)}",
                            message=f"✅ ثبت نهایی تایید شد. کد رهگیری: {self._mask(code)}",
                            level="success",
                            page=page,
                        )
                        return

                    if actual_stage == "STEP_3_BANK_SELECT":
                        # Ensure OTP wait lock is strictly cleared on Step 3!
                        self.clear_otp_locks()
                        if state != "BANK_CAPACITY_FULL":
                            set_state("BANK_SELECT")
                        if stop_event.is_set():
                            break
                        if self._firewall_gate(page, stop_event):
                            continue

                        result = self._process_bank_selection_v2(page, stop_event)

                        if result in {"submitted_confirmed", "already_submitted"}:
                            set_state("DONE")
                            return

                        elif result == "option_selected_waiting_user":
                            # Bank & branch/mode are selected and verified. Wait quietly without re-running selection in a loop
                            # unless the user clicks Save manually, enables auto_final_submit, or the selection in DOM is reset.
                            while not stop_event.is_set():
                                self.suspend_watchdog(60)
                                self.mark_progress("waiting_user_final_submit")
                                if sleep_with_stop(stop_event, 1.0, step=0.2):
                                    break
                                cur_stage = self.detect_actual_page_stage(page)
                                if cur_stage != "STEP_3_BANK_SELECT":
                                    break
                                # Check if user enabled auto_final_submit dynamically
                                latest_cfg = DBHandler.get_config()
                                if bool(latest_cfg.get("auto_final_submit", latest_cfg.get("final_submit", False))):
                                    self.settings["auto_final_submit"] = True
                                    self.settings["final_submit"] = True
                                    break
                                # Check if bank dropdown was reset to 0 on the page
                                try:
                                    cur_val = page.locator("#ctl00_ContentPlaceHolder1_ddlBankName").first.input_value()
                                    if cur_val in ("", "0"):
                                        break
                                except Exception:
                                    pass
                            continue

                        elif result == "capacity_full":
                            set_state("BANK_CAPACITY_FULL")
                            capacity_recheck_count += 1
                            base_recheck = max(5.0, float(self.settings.get("bank_capacity_recheck_seconds", 20.0)))
                            # Apply gentle backoff if repeated capacity full (capped at 120s)
                            backoff_factor = min(3.0, 1.0 + (capacity_recheck_count - 1) * 0.25)
                            wait_sec = min(120.0, round(base_recheck * backoff_factor, 1))

                            if not bool(self.settings.get("bank_monitor_enabled", True)):
                                self.log("⏸️ ظرفیت بانک تکمیل است و پایش خودکار غیرفعال است؛ منتظر اقدام کاربر.", "warning", page)
                                self.suspend_watchdog(300)
                                if sleep_with_stop(stop_event, 5.0, step=0.2):
                                    break
                                continue

                            DBHandler.update_runtime(
                                self.nid,
                                status="BANK_CAPACITY_FULL",
                                current_step="BANK_CAPACITY_FULL",
                                next_refresh=time.time() + wait_sec,
                                retry_count=capacity_recheck_count,
                            )
                            self.suspend_watchdog(wait_sec + 15.0)
                            self.log(
                                f"⏳ ظرفیت ثبت‌نام بانک تکمیل است؛ حفظ صفحه فعلی و بررسی مجدد تا {wait_sec} ثانیه دیگر (بدون تغییر روش غیرحضوری)...",
                                "warning",
                                page,
                            )
                            if sleep_with_stop(stop_event, wait_sec, step=0.2):
                                break
                            # Re-trigger official dropdown check without returning to Step 1
                            try:
                                return_to_bank_field(page, "#ctl00_ContentPlaceHolder1_ddlBankName")
                                self.wait_for_aspnet_postback(page, timeout_ms=5000)
                            except Exception:
                                pass
                            continue

                        elif result == "no_match":
                            list_refresh_count += 1
                            if not bool(self.settings.get("bank_monitor_enabled", True)):
                                self.set_runtime_state("STOPPED", "BANK_OPTION_NOT_FOUND", browser_status="OPEN")
                                self.log("بانک هدف در فهرست موجود نیست و پایش خودکار غیرفعال است.", "warning", page)
                                return

                            base_refresh = max(1.0, float(self.settings.get("bank_list_refresh_seconds", self.settings.get("bank_refresh_interval", 5.0))))
                            # Gentle backoff after every 10 consecutive refreshes to respect server rate limits
                            backoff = min(4.0, 1.0 + (list_refresh_count // 10) * 0.5)
                            refresh_interval = round(base_refresh * backoff, 1)

                            DBHandler.update_runtime(
                                self.nid,
                                status="MONITORING",
                                current_step="MONITOR_BANKS",
                                next_refresh=time.time() + refresh_interval,
                                retry_count=list_refresh_count,
                            )
                            self.suspend_watchdog(refresh_interval + 15.0)
                            if sleep_with_stop(stop_event, refresh_interval, step=0.1):
                                break
                            # Refresh bank list via official city dropdown postback first (avoids losing session or returning to Step 1)
                            refreshed_via_postback = False
                            try:
                                city_loc = page.locator("#ctl00_ContentPlaceHolder1_ddlCity")
                                if city_loc.count() > 0 and city_loc.first.is_visible():
                                    cur_city = city_loc.first.input_value()
                                    if cur_city and cur_city != "0":
                                        page.evaluate(
                                            "() => { if (typeof __doPostBack === 'function') __doPostBack('ctl00$ContentPlaceHolder1$ddlCity', ''); }"
                                        )
                                        self.wait_for_aspnet_postback(page, timeout_ms=8000)
                                        refreshed_via_postback = True
                                        self.mark_progress("bank_list_postback_refresh")
                            except Exception:
                                refreshed_via_postback = False

                            if not refreshed_via_postback:
                                try:
                                    page.reload(wait_until="domcontentloaded", timeout=int(self.settings.get("navigation_timeout_ms", 15000)))
                                    self.mark_progress("bank_page_refresh")
                                except Exception:
                                    pass
                            continue

                        elif result == "waiting":
                            refresh_interval = max(1.0, float(self.settings.get("bank_list_refresh_seconds", 5.0)))
                            if sleep_with_stop(stop_event, refresh_interval, step=0.1):
                                break
                            continue

                        elif result == "submit_rejected":
                            self.suspend_watchdog(120)
                            if sleep_with_stop(stop_event, 5.0, step=0.2):
                                break
                            continue

                    elif actual_stage == "STEP_2_OTP" or self._otp_input_visible(page):
                        set_state("OTP_FORM")
                        if stop_event.is_set():
                            break

                        result = self.fast_otp_sync_and_inject(page, stop_event)
                        if stop_event.is_set():
                            break
                        if result == "bank_ready":
                            otp_attempted = True
                            self.clear_otp_locks()
                            set_state("BANK_SELECT")
                        elif result == "otp_expired":
                            otp_attempted = False
                            self.clear_otp_locks()
                            # Re-verify if still on OTP page or needs official re-send
                            if resend_enabled(page):
                                try:
                                    page.locator("#ctl00_ContentPlaceHolder1_btnSendConfirmCode, input[name$='btnSendConfirmCode']").first.click()
                                    self.log("📲 درخواست ارسال مجدد کد پیامک از مسیر رسمی سامانه ثبت شد.", "info", page)
                                except Exception:
                                    pass
                        continue

                    elif actual_stage == "STEP_1_ENTRY_FORM":
                        set_state("ENTRY_FORM")
                        if stop_event.is_set():
                            break
                        if otp_attempted:
                            self.clear_otp_backend()
                            self.log("♻️ بازگشت به مرحله اول؛ کد قبلی پاک شد.", "warning", page)
                            otp_attempted = False

                        nid_locator, _ = self._select_editable_input(
                            page,
                            ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name$='tbIDNo']"],
                            "National ID",
                        )
                        if not nid_locator:
                            self.log("❌ Failed to locate National ID input.", "error", page)
                            self._debug_step1_dump(page, "nid_not_found")
                            if sleep_with_stop(stop_event, 0.5):
                                break
                            continue
                        try:
                            current_val = nid_locator.input_value()
                        except Exception:
                            current_val = ""
                        if current_val != self.nid:
                            self._nid_filled = False

                        if not self._nid_filled:
                            filled = self._fill_input_with_verification(page, nid_locator, self.nid, "National ID")
                            if not filled:
                                self._debug_step1_dump(page, "nid_fill_failed")
                                if sleep_with_stop(stop_event, 0.5):
                                    break
                                continue
                            self._nid_filled = True
                            adapter.fill_entry_extras(self, page, getattr(self, "user_data", {}))

                        if self._firewall_gate(page, stop_event):
                            continue

                        self._solve_primary_captcha_fast(page, stop_event)

                        try:
                            page.wait_for_selector("input[name$='tbMobileConfCode'], #ctl00_ContentPlaceHolder1_ddlBankName", timeout=otp_timeout)
                        except Exception:
                            pass
                        continue

                    # Fallback for login redirect
                    elif wait_for_state("#ctl00_ContentPlaceHolder1_btnLogin", "CHECK_STEP_LOGIN_REDIRECT", timeout=state_probe_timeout):
                        if stop_event.is_set():
                            break
                        if self._firewall_gate(page, stop_event):
                            continue
                        if otp_attempted:
                            self.clear_otp_backend()
                            self.clear_otp_locks()
                            self.log("♻️ نشست منقضی شد؛ بازگشت به مرحله اول و انتظار پیامک جدید.", "warning", page)
                            otp_attempted = False
                        self._perform_login_standard(page, "model")

                    if sleep_with_stop(stop_event, 0.2):
                        break

            except SiteRejectedError as exc:
                self.emit_event(
                    "WAF_BLOCKED",
                    step=getattr(self, "_current_step", "IDLE"),
                    reason=f"support_id={exc.support_id or 'unknown'}",
                    result="site_rejected",
                    message="سامانه درخواست را رد کرد (WAF/Firewall)؛ اجرای خودکار متوقف شد.",
                    level="error",
                    extra={"support_id": exc.support_id, "navigation": self.navigation_evidence.snapshot(page) if hasattr(self, "navigation_evidence") else {}},
                )
                self.set_runtime_state("ERROR", "SITE_REJECTED", browser_status="CLOSED", last_error="site_rejected")
                DBHandler.update_status(self.nid, "SITE_REJECTED", f"Support ID: {exc.support_id or 'unknown'}")
                return
            except PlaywrightError as pe:
                if self.close_browser_on_stop(stop_event, playwright, browser, context, page):
                    return
                if "Target closed" in str(pe):
                    self.set_runtime_state("RETRYING", "OPEN_BROWSER", browser_status="CLOSED", last_error="target_closed")
                    self.log("مرورگر بسته/کرش کرد؛ نشست جدید بلافاصله ساخته می‌شود.", "warning")
                    continue
                if sleep_with_stop(stop_event, float(self.settings.get("retry_delay", 0.25)), step=0.05):
                    break
            except BrowserLaunchError as exc:
                if self.close_browser_on_stop(stop_event, playwright, browser, context, page):
                    return
                self.log(f"خطا در مرورگر: {exc.message}", "error")
                self.set_runtime_state("RETRYING", "OPEN_BROWSER", browser_status="ERROR", last_error=exc.code)
                if sleep_with_stop(stop_event, float(self.settings.get("retry_delay", 0.25)), step=0.05):
                    break
            except Exception as exc:
                if self.close_browser_on_stop(stop_event, playwright, browser, context, page):
                    return
                error_text = str(exc).lower()
                if "target closed" in error_text or "browser has been closed" in error_text:
                    self.log("مرورگر از دسترس خارج شد؛ تلاش فوری با مرورگر جدید.", "warning")
                    continue
                if sleep_with_stop(stop_event, float(self.settings.get("retry_delay", 0.25)), step=0.05):
                    break
            finally:
                if stop_event.is_set():
                    try:
                        self.log("🛑 Stop detected - closing browser now.", "warning")
                    except Exception:
                        pass
                close_browser(playwright, browser, context, page)

            if not stop_event.is_set():
                sleep_with_stop(stop_event, 0)

    def is_firewall_challenge(self, page) -> bool:
        selectors = ["#ans", "#jar"]
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if locator.count() > 0 and locator.first.is_visible():
                    return True
            except Exception:
                continue

        challenge_markers = ["human visitor", "support id"]
        try:
            body_text = (page.inner_text("body") or "").lower()
            if "the requested url was rejected" in body_text:
                return False
            return any(marker in body_text for marker in challenge_markers)
        except Exception:
            return False

    def handle_firewall_challenge(self, page, stop_event):
        check_rejection(page)
        self.emit_event(
            "CAPTCHA_MANUAL_REQUIRED",
            step="FIREWALL_CHALLENGE",
            reason="firewall_gate",
            result="waiting_user_input",
            message="🛡️ چالش فایروال شناسایی شد؛ منتظر ورود دستی کپچا توسط کاربر هستم.",
            level="warning",
            page=page,
        )
        timeout_seconds = int(self.settings.get("firewall_manual_timeout", 300))
        self.suspend_watchdog(timeout_seconds + 15)
        deadline = time.time() + timeout_seconds

        support_id = None
        try:
            body_text = page.inner_text("body") or ""
            match = re.search(r"support id(?: is)?[:\s]*([A-Za-z0-9-]+)", body_text, re.IGNORECASE)
            if match:
                support_id = match.group(1)
        except Exception:
            pass

        if support_id:
            self.log(f"🧾 Firewall Support ID: {support_id}", "info", page)
            DBHandler.update_status(self.nid, "منتظر CAPTCHA", f"Support ID: {support_id}")
        else:
            DBHandler.update_status(self.nid, "منتظر CAPTCHA", "Manual firewall CAPTCHA entry required")

        next_wait_log_at = 0.0
        while not stop_event.is_set():
            check_rejection(page)
            if time.time() > deadline:
                self.set_runtime_state("ERROR", "FIREWALL_TIMEOUT", last_error="manual_challenge_timeout")
                return False
            if self._firewall_input_visible(page):
                if sleep_with_stop(stop_event, 0.5):
                    return False
                continue

            try:
                stage = self.detect_actual_page_stage(page)
                if stage in {"STEP_1_ENTRY_FORM", "STEP_2_OTP", "STEP_3_BANK_SELECT", "DONE"}:
                    self.log("✅ چالش فایروال پس از ورود کاربر رفع شد؛ ادامهٔ عملیات.", "success", page)
                    return True
            except Exception:
                pass

            now = time.time()
            if now >= next_wait_log_at:
                self.log("Waiting for firewall resolution...", "warning", page)
                next_wait_log_at = now + 15.0

            if sleep_with_stop(stop_event, 0.8):
                return False

        return False

    def _firewall_gate(self, page, stop_event) -> bool:
        return firewall_gate(self, page, stop_event)

    def _firewall_input_visible(self, page) -> bool:
        return firewall_input_visible(page)

    def _reload_guarded(self, page, reason: str = "") -> bool:
        # CRITICAL FIX: Only suppress reload when the OTP input is ACTUALLY present on the page!
        # If the bot is on Step 3 (SelectBnkShb.aspx with ddlBankName), clear stale OTP locks first.
        actual_stage = self.detect_actual_page_stage(page)
        if actual_stage == "STEP_3_BANK_SELECT":
            self.clear_otp_locks()

        otp_visible = self._otp_input_visible(page)
        otp_guard_active = (time.time() < self._otp_guard_until) and otp_visible
        if (self._otp_wait_lock and otp_visible) or otp_guard_active or otp_visible:
            self._safe_goto_locked = True
            msg = "🔒 Reload suppressed: waiting on OTP input."
            if reason:
                msg = f"{msg} ({reason})"
            self.log(msg, "warning", page)
            return False
        try:
            page.reload()
            return True
        except Exception:
            return False

    def _safe_goto_guarded(self, page, url, **kwargs):
        actual_stage = self.detect_actual_page_stage(page)
        if actual_stage == "STEP_3_BANK_SELECT":
            self.clear_otp_locks()

        otp_visible = self._otp_input_visible(page)
        otp_guard_active = (time.time() < self._otp_guard_until) and otp_visible
        if (self._otp_wait_lock and otp_visible) or otp_guard_active or (self._safe_goto_locked and otp_visible) or otp_visible:
            self._safe_goto_locked = True
            self.log("🔒 safe_goto locked: waiting on OTP form; navigation suppressed.", "warning", page)
            return False
        safe_goto(page, url, **kwargs)
        return True

    def _refresh_captcha_or_reload(self, page, input_sel="#ctl00_ContentPlaceHolder1_tbCaptcha2", timeout=12000, allow_reload=True):
        return refresh_captcha_or_reload(self, page, input_sel=input_sel, timeout=timeout, allow_reload=allow_reload)

    def _ensure_nid_after_reload(self, page):
        selectors = ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name$='tbIDNo']"]
        for selector in selectors:
            try:
                field = page.locator(selector)
                if field.count() == 0 or not field.first.is_visible():
                    continue
                current = ""
                try:
                    current = field.first.input_value().strip()
                except Exception:
                    current = ""
                if not current:
                    try:
                        BrowserActions.human_fill(field.first, self.nid, timeout=750)
                    except Exception:
                        BrowserActions.fast_fill(field.first, self.nid, timeout=750)
                    self._nid_filled = True
                return True
            except Exception:
                continue
        return False

    def _solve_primary_captcha_fast(self, page, stop_event) -> bool:
        """
        Solve Step 1 CAPTCHA up to captcha_max_retries times.
        Waits for the CAPTCHA image to appear and finish loading (`naturalWidth > 0`) before passing to the model.
        """
        from bot_select_captcha import wait_for_captcha_image_ready
        input_sel = "input[name$='tbCaptcha1']"
        btn_sel = "input[name$='btnSendConfirmCode']"
        max_attempts = max(1, int(self.settings.get("captcha_max_retries", 5)))
        attempts = 0

        while not stop_event.is_set() and attempts < max_attempts:
            attempts += 1
            if self._firewall_gate(page, stop_event):
                return False

            captcha_img, _ = wait_for_captcha_image_ready(page, timeout_ms=4500, trigger_refresh_if_missing=True)
            if captcha_img is None:
                try:
                    captcha_img = page.locator(".BDC_CaptchaImage").first
                    if captcha_img.count() == 0 or not captcha_img.is_visible():
                        return False
                except Exception:
                    return False

            # Clear any leftover invalid dialog flag before submitting a fresh prediction
            self.consume_recovery_flag("captcha_invalid")

            try:
                if hasattr(self, "_run_perf_started"):
                    self.record_metric("captcha_handoff_ms", self._run_perf_started)
                if not self.solve_captcha_with_model(
                    page, input_sel, btn_sel, stop_event=stop_event, source="primary", captcha_element=captcha_img
                ):
                    self._refresh_captcha_or_reload(page, input_sel=input_sel, allow_reload=False)
                    continue
            except Exception:
                self._refresh_captcha_or_reload(page, input_sel=input_sel, allow_reload=False)
                continue

            try:
                page.wait_for_selector("input[name$='tbMobileConfCode']", timeout=1200, state="visible")
                return True
            except Exception:
                pass

            try:
                page.wait_for_selector("#ctl00_ContentPlaceHolder1_ddlBankName", timeout=800, state="visible")
                self.clear_otp_locks()
                return True
            except Exception:
                pass

            if self._detect_captcha_failure(page):
                self._refresh_captcha_or_reload(page, input_sel=input_sel, allow_reload=False)
                continue

            self._refresh_captcha_or_reload(page, input_sel=input_sel, allow_reload=False)

        # Manual CAPTCHA handoff after bounded retries
        if not stop_event.is_set() and bool(self.settings.get("pause_for_manual_captcha", True)):
            return wait_for_manual_captcha_handoff(
                self,
                page,
                input_sel,
                btn_sel,
                stop_event,
                source="step1_primary",
            )
        return False

    def _get_otp_from_db_fresh(self):
        return get_otp_from_db_fresh(self)

    def fast_otp_sync_and_inject(self, page, stop_event):
        return fast_otp_sync_and_inject(self, page, stop_event, sleep_with_stop)

    def _select_editable_input(self, page, selectors, label):
        scopes = [("page", page)] + [(f"frame[{idx}]", frame) for idx, frame in enumerate(page.frames)]
        for scope_name, scope in scopes:
            for selector in selectors:
                try:
                    locator = scope.locator(selector)
                    count = locator.count()
                except Exception:
                    continue
                for index in range(count):
                    candidate = locator.nth(index)
                    try:
                        is_visible = candidate.is_visible()
                        is_enabled = candidate.is_enabled()
                        readonly = candidate.get_attribute("readonly")
                        disabled = candidate.get_attribute("disabled")
                        handle = candidate.element_handle()
                        bounding_box = handle.bounding_box() if handle else None
                        if (
                            is_visible
                            and is_enabled
                            and not readonly
                            and not disabled
                            and bounding_box
                        ):
                            return candidate, scope
                    except Exception:
                        continue
        return None, None

    def _wait_for_state_any_scope(self, page, selector, label, timeout=2000):
        if self.is_firewall_challenge(page):
            return False
        scopes = [("page", page)] + [(f"frame[{idx}]", frame) for idx, frame in enumerate(page.frames)]
        for scope_name, scope in scopes:
            try:
                scope.wait_for_selector(selector, timeout=timeout, state="visible")
                return True
            except Exception:
                continue
        return False

    def _has_firewall_signal(self, page):
        selectors = ["#ans", "#jar", "text=در حال بررسی مرورگر شما", "text=cloudflare", "text=firewall"]
        for selector in selectors:
            try:
                loc = page.locator(selector)
                if loc.count() > 0 and loc.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _log_state_sweep(self, page):
        if not bool(self.settings.get("diagnostic_logging_enabled", True)):
            return
        selectors = [
            "#ctl00_ContentPlaceHolder1_tbIDNo",
            "input[name$='tbIDNo']",
            "input[name$='tbMobileConfCode']",
            "#ctl00_ContentPlaceHolder1_ddlBankName",
        ]
        counts = {}
        for selector in selectors:
            try:
                counts[selector] = page.locator(selector).count()
            except Exception:
                counts[selector] = -1
        frame_urls = []
        try:
            for frame in page.frames:
                try:
                    frame_urls.append(frame.url)
                except Exception:
                    frame_urls.append("<unavailable>")
        except Exception:
            frame_urls = ["<unavailable>"]

        try:
            sig = f"{page.url}|{counts}"
            # Suppress identical consecutive STATE_SWEEP logs unless state changed
            if sig == self._last_state_sweep_sig:
                return
            self._last_state_sweep_sig = sig
            self.log(
                f"🧭 STATE_SWEEP url={page.url} title={page.title()} counts={counts} frames={len(frame_urls)} frame_urls={frame_urls}",
                "info",
                page,
            )
        except Exception:
            pass

    def _fill_input_with_verification(self, page, locator, value, label):
        try:
            BrowserActions.human_fill(locator, value, timeout=1500)
        except Exception:
            pass
        try:
            current_val = locator.input_value()
        except Exception:
            current_val = ""
        if current_val == value:
            self.log(f"✅ Filled {label} successfully.", "success")
            return True

        try:
            BrowserActions.fast_fill(locator, value, timeout=750)
        except Exception:
            pass
        try:
            current_val = locator.input_value()
        except Exception:
            current_val = ""
        if current_val == value:
            self.log(f"✅ Filled {label} successfully (fast_fill).", "success")
            return True

        try:
            handle = locator.element_handle()
            if handle:
                page.evaluate(
                    "(el, val) => {"
                    "el.value = val;"
                    "el.dispatchEvent(new Event('input', { bubbles: true }));"
                    "el.dispatchEvent(new Event('change', { bubbles: true }));"
                    "}",
                    handle,
                    value,
                )
        except Exception:
            pass
        try:
            current_val = locator.input_value()
        except Exception:
            current_val = ""
        if current_val == value:
            self.log(f"✅ Filled {label} successfully (js injection).", "success")
            return True

        self.log(f"❌ Failed to fill {label}.", "error", page)
        return False

    def _collect_selector_debug(self, page, selector):
        info = []
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            return info
        for index in range(count):
            candidate = locator.nth(index)
            try:
                handle = candidate.element_handle()
                bounding_box = handle.bounding_box() if handle else None
                entry = {
                    "selector": selector,
                    "index": index,
                    "id": candidate.get_attribute("id"),
                    "name": candidate.get_attribute("name"),
                    "type": candidate.get_attribute("type"),
                    "disabled": candidate.get_attribute("disabled"),
                    "readonly": candidate.get_attribute("readonly"),
                    "visible": candidate.is_visible(),
                    "enabled": candidate.is_enabled(),
                    "bounding_box": bounding_box,
                    "value": self._mask(candidate.input_value()),
                }
                info.append(entry)
            except Exception:
                continue
        return info

    def _debug_step1_dump(self, page, reason):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        screenshot_dir = "screenshots"
        safe_contact_id = hashlib.sha256(str(self.nid).encode("utf-8")).hexdigest()[:10]
        screenshot_path = f"{screenshot_dir}/select_{safe_contact_id}_{timestamp}_{reason}.png"
        try:
            os.makedirs(screenshot_dir, exist_ok=True)
        except Exception:
            pass
        try:
            page.screenshot(path=screenshot_path, full_page=True)
        except Exception:
            screenshot_path = "screenshot_failed"

        debug_data = {
            "reason": reason,
            "url": page.url,
            "selectors": [],
            "screenshot": screenshot_path,
        }
        selectors = ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name$='tbIDNo']", "input[name$='tbCaptcha1']"]
        for selector in selectors:
            debug_data["selectors"].extend(self._collect_selector_debug(page, selector))

        try:
            self.log(f"🧾 Step1 debug dump: {json.dumps(debug_data, ensure_ascii=False)}", "error", page)
        except Exception:
            pass

    def _process_bank_selection_v2(self, page, stop_event):
        return process_bank_selection_v2(self, page, stop_event)

    def _return_to_bank_field(self, page, dropdown_id):
        return_to_bank_field(page, dropdown_id)

    def _select_first_available_branch(self, page):
        return select_first_available_branch(page)

    def _process_branch_selection(self, page, stop_event):
        return process_branch_selection(self, page, stop_event)

    def _wait_for_branch_fully_loaded(self, page, branch_ddl):
        return wait_for_branch_fully_loaded(page, branch_ddl)

    def _load_runtime_data(self):
        return load_runtime_data(self)

    def _handle_otp_dialog(self, dialog):
        return handle_otp_dialog(self, dialog)

    def _notify_favorite_banks(self, available_banks, favorite_banks, user_priorities):
        return notify_favorite_banks(self, available_banks, favorite_banks, user_priorities)

    def _detect_otp_failure(self, page) -> bool:
        return detect_otp_failure(self, page)

    def _otp_input_visible(self, page) -> bool:
        return otp_input_visible(self, page)

    def _resend_enabled(self, page) -> bool:
        return resend_enabled(page)

    def _can_navigate_from_otp(self, page) -> bool:
        return can_navigate_from_otp(self, page)

    def _detect_captcha_failure(self, page) -> bool:
        return detect_captcha_failure(page)

    def _solve_captcha_wrapper(self, page, input_sel, btn_sel, mode):
        return solve_captcha_wrapper(self, page, input_sel, btn_sel, mode)

    def solve_captcha_step(self, page, input_sel: str, btn_sel: str, source: str = "general") -> bool:
        return solve_captcha_step(self, page, input_sel, btn_sel, source=source)

    def _perform_login_standard(self, page, mode):
        try:
            if not page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").input_value():
                try:
                    BrowserActions.human_fill(page.locator("#ctl00_ContentPlaceHolder1_tbIDNo"), self.nid, timeout=750)
                except Exception:
                    BrowserActions.fast_fill(page.locator("#ctl00_ContentPlaceHolder1_tbIDNo"), self.nid, timeout=750)
            self.solve_captcha_with_model(
                page,
                "#ctl00_ContentPlaceHolder1_tbCaptcha",
                "#ctl00_ContentPlaceHolder1_btnLogin",
                stop_event=self._active_stop_event,
                source="login_redirect",
            )
        except Exception:
            pass
