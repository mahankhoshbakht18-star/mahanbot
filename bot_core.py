from typing import Optional
import time
import random
import os
import sys
import json
import re
import threading
import requests
import traceback
from database import DBHandler
from api_solver import CaptchaService
from network_diagnostics import NavigationEvidence, check_rejection, SiteRejectedError
from browser_actions import BrowserActions
from browser_launcher import (
    BrowserLaunchError,
    close_browser,
    launch_browser,
    PROFILE_ROOT,
)
from event_logger import emit_structured_event, sanitize_log_text


class BotCore:
    def __init__(self, nid, settings, log_callback=None, captcha_service=None, browser_profile=None):
        self.nid = nid
        self.settings = settings or {}
        self.log_callback = log_callback
        self.captcha_service = captcha_service or CaptchaService()
        self.user_data = self._load_user_data()
        self.retry_limit = max(1, min(20, int(self.settings.get('retry_count', 5))))
        self.local_api_base = os.getenv("MAHANBOT_LOCAL_API", f"http://127.0.0.1:{os.getenv('MAHANBOT_PORT', '8000')}").rstrip("/")
        self.api_base_url = (os.getenv("MAHANBOT_OTP_API") or self.local_api_base).rstrip("/")
        self.user_dir = str(PROFILE_ROOT / str(nid))
        if not os.path.exists(self.user_dir):
            os.makedirs(self.user_dir, exist_ok=True)
        self.browser_profile = browser_profile or {}
        self._cancel_watcher_thread = None
        self._cancel_watcher_stop_event = None
        self._dialog_handlers_attached = set()
        self._recovery_flags = {
            "otp_expired": False,
            "captcha_invalid": False,
            "bank_capacity_full": False,
            "session_expired": False,
            "waf_blocked": False,
            "generic_error": False,
        }
        self._captcha_retry = 0
        self._captcha_retry_limit = int(self.settings.get("captcha_max_retries", 3))
        self._otp_retry = 0
        self._otp_retry_limit = int(self.settings.get("otp_retry_limit", 5))
        self._otp_hard_stop = bool(self.settings.get("otp_hard_stop", False))
        self._otp_retry_exceeded = False
        self._last_progress_time = time.time()
        self._watchdog_timeout = max(20, int(self.settings.get("watchdog_timeout", 45)))
        self._watchdog_suspended_until = 0.0
        self._last_watchdog_dump = 0.0
        self._last_dialog_message = ""
        self._last_dialog_action = None
        self._last_dialog_ts = 0.0
        self._otp_entry_time = 0.0
        self._firewall_consecutive_hits = 0
        self._last_browser_preview_ts = 0.0
        self._run_started_at = 0.0
        self._state_started_at = 0.0
        self._current_step = "IDLE"
        self._recovery_lock = threading.RLock()
        self._recovery_in_progress = False
        self._submit_mutex = threading.Lock()
        self._error_signatures = {}

    @staticmethod
    def _mask(value, visible=2):
        text = str(value or "")
        if len(text) <= visible * 2:
            return "*" * len(text)
        return f"{text[:visible]}{'*' * (len(text) - visible * 2)}{text[-visible:]}"

    def reset_attempt_state(self, attempt=1):
        """Reset every ephemeral flag before a fresh browser attempt."""
        self._nid_filled = False
        self._otp_entry_time = 0.0
        self._needs_reload = False
        self._dialog_handling = False
        self._safe_goto_locked = False
        self._otp_wait_lock = False
        self._otp_guard_until = 0.0
        self._recovery_flags = {
            "otp_expired": False,
            "captcha_invalid": False,
            "bank_capacity_full": False,
            "session_expired": False,
            "waf_blocked": False,
            "generic_error": False,
        }
        self._captcha_retry = 0
        self._otp_retry = 0
        self._otp_retry_exceeded = False
        self._watchdog_suspended_until = 0.0
        self._firewall_consecutive_hits = 0
        self._last_dialog_message = ""
        self._last_dialog_action = None
        self._last_dialog_ts = 0.0
        self._recovery_in_progress = False
        self._error_signatures = {}
        self._bank_page_detected_emitted = False
        self._last_pref_loaded_sig = None
        self._verified_step3_signature = None
        self._last_progress_time = time.time()
        self._run_started_at = self._run_started_at or self._last_progress_time
        self.set_runtime_state("STARTING", "OPEN_BROWSER", browser_status="STARTING", retry_count=max(0, attempt - 1))

    def clear_otp_locks(self):
        """Explicitly release OTP waiting/navigation locks once past the OTP page."""
        self._otp_wait_lock = False
        self._otp_guard_until = 0.0
        self._safe_goto_locked = False

    def set_runtime_state(self, status, step, **extra):
        now = time.time()
        self._current_step = step
        self._state_started_at = now
        DBHandler.update_runtime(
            self.nid,
            status=status,
            current_step=step,
            last_activity_at=now,
            **extra,
        )

    def emit_event(self, event_code: str, *, step: str = "", reason: str = "", result: str = "", message: str = "", level: str = "info", page=None, extra=None):
        """Emit structured canonical event and update live preview if page provided."""
        step_name = step or getattr(self, "_current_step", "IDLE")
        meta = emit_structured_event(
            self.nid,
            event_code,
            step=step_name,
            reason=reason,
            result=result,
            message=message,
            level=level,
            extra=extra,
        )
        if page:
            self._update_browser_preview(page)
        return meta

    def record_metric(self, name, started_at):
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
        runtime = DBHandler.get_runtime(self.nid)
        metrics = dict(runtime.get("metrics") or {})
        metrics[name] = elapsed_ms
        DBHandler.update_runtime(self.nid, metrics=metrics)
        return elapsed_ms

    def log(self, message, level="info", page=None, meta=None):
        safe_message = sanitize_log_text(message)
        if getattr(self, "log_callback", None):
            try:
                getattr(self, "log_callback", None)(self.nid, safe_message, level, meta)
            except TypeError:
                getattr(self, "log_callback", None)(self.nid, safe_message, level)
        else:
            log_line = f"[{time.strftime('%H:%M:%S')}][{level.upper()}] {self._mask(self.nid)}: {safe_message}\n"
            try:
                sys.stdout.write(log_line)
                sys.stdout.flush()
            except (UnicodeEncodeError, UnicodeError):
                try:
                    sys.stdout.buffer.write(log_line.encode("utf-8", errors="replace"))
                    sys.stdout.buffer.flush()
                except Exception:
                    pass
        if page:
            self._update_browser_preview(page)
        show_on_page = os.getenv("MAHANBOT_SHOW_LOG_ON_PAGE", "false").lower() in {"1", "true", "yes"}
        if page and show_on_page:
            color = "red" if level == "error" else ("green" if level == "success" else "blue")
            BrowserActions.show_log_on_page(page, safe_message, color)

    def _update_browser_preview(self, page, force=False):
        """Capture a throttled live viewport for the embedded dashboard browser."""
        if getattr(self, "critical_path_active", False):
            return
        now = time.time()
        if not force and now - self._last_browser_preview_ts < 0.8:
            return
        self._last_browser_preview_ts = now
        try:
            safe_nid = "".join(ch for ch in str(self.nid) if ch.isalnum() or ch in {"-", "_"})
            project_dir = os.path.dirname(os.path.abspath(__file__))
            preview_dir = os.getenv("MAHANBOT_PREVIEW_DIR", os.path.join(project_dir, "static", "live_browser"))
            os.makedirs(preview_dir, exist_ok=True)
            final_path = os.path.join(preview_dir, f"{safe_nid}.jpg")
            temp_path = os.path.join(preview_dir, f".{safe_nid}.{threading.get_ident()}.jpg")
            page.screenshot(path=temp_path, type="jpeg", quality=58, full_page=False, timeout=2500,
                            mask=[page.locator('input:not([type="hidden"]), img[src*="aptcha"]')])
            os.replace(temp_path, final_path)
        except Exception:
            pass

    def _load_user_data(self):
        row = DBHandler.get_applicant(self.nid)
        if not row:
            return {}

        try:
            user_dict = dict(row)
        except Exception:
            user_dict = row if isinstance(row, dict) else {}

        data = user_dict.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}

        data.setdefault("full_name", user_dict.get("full_name"))
        data.setdefault("national_id", user_dict.get("national_id"))
        return dict(data)

    def save_checkpoint(self, step: str, page=None, **extra):
        """Persist recovery metadata independently of optional HTML/PNG snapshots."""
        if getattr(self, "critical_path_active", False):
            # Zero-Overhead Critical Path: bypass heavy synchronous screenshot capture
            page = None
        url = ""
        if page is not None:
            try:
                current_url = page.url
                url = current_url if isinstance(current_url, str) else ""
            except Exception:
                url = ""
        payload = {
            "last_verified_step": step,
            "last_valid_url": url or extra.get("last_valid_url", ""),
        }
        payload.update(extra)
        cp = DBHandler.save_checkpoint(self.nid, payload)
        self.emit_event(
            "CHECKPOINT_SAVED",
            step=step,
            reason=str(extra.get("reason") or "state_transition"),
            result="saved",
            message=f"Checkpoint ذخیره شد: مرحله {step}",
            level="info",
            page=page,
        )
        return cp

    def validate_otp_code(self, code: str, min_len: int = 4, max_len: int = 8) -> bool:
        """Verify OTP format, length, and digits."""
        if not code:
            return False
        clean = str(code).strip()
        return clean.isdigit() and (min_len <= len(clean) <= max_len)

    def get_otp_code(self, stop_event=None, timeout: int = 45):
        from otp_transport import poll_once
        return poll_once(self, stop_event, timeout)

    def wait_for_otp(self, stop_event=None, timeout: int = 45, min_ts: float = None):
        from otp_transport import poll_once
        if min_ts is not None and not self._otp_entry_time:
            self.mark_otp_entry_time(min_ts)
        deadline = time.monotonic() + max(1, timeout)
        while time.monotonic() < deadline:
            code = poll_once(self, stop_event, min(2, timeout))
            if code:
                return code
            if stop_event is not None:
                if stop_event.wait(0.2):
                    break
            else:
                time.sleep(0.2)
        return None

    def mark_otp_entry_time(self, ts: float = None) -> float:
        self._otp_entry_time = float(ts if ts is not None else time.time())
        from core.otp_queue import OTPQueueManager
        request = OTPQueueManager.create_request(self.nid,
                    session_id=getattr(getattr(self, 'live_owner', None), 'session_id', None),
                    ttl_seconds=int(self.settings.get('otp_validity_seconds', 120)))
        self._otp_request_id = request['otp_request_id']
        DBHandler.update_runtime(self.nid, otp_requested_at=self._otp_entry_time,
                                 otp_request_id=self._otp_request_id, otp_state="WAITING_FOR_SMS",
                                 otp_type=getattr(self, "OTP_TYPE", "REGISTRATION_OTP"))
        from relay_transport import queue_wait
        queue_wait(self)
        return self._otp_entry_time

    def clear_otp_backend(self):
        from relay_transport import finish
        finish(self)
        secret = os.getenv("MAHANBOT_OTP_SECRET") or os.getenv("MAHAN_OTP_SECRET")
        headers = {"X-OTP-Secret": secret} if secret else {}
        for base in dict.fromkeys((self.api_base_url, self.local_api_base)):
            try:
                requests.post(f"{base}/otp/clear/{self.nid}", headers=headers, timeout=3)
            except Exception:
                pass
        try:
            DBHandler.clear_otp(self.nid)
        except Exception:
            pass

    def clear_remote_otp(self):
        self.clear_otp_backend()

    def setup_browser(self, stop_event=None):
        self._closing_intentionally = False
        launch_started = time.perf_counter()
        self.log("🌐 در حال باز کردن مرورگر...", "info")
        profile = dict(self.browser_profile)
        if "user_data_dir" in profile and profile["user_data_dir"]:
            # Per-applicant profile ownership; never reuse a dashboard or shared Chrome profile.
            profile["user_data_dir"] = self.user_dir
        # Apply compact window settings from bot.settings unless explicitly overridden in browser_profile
        if "window_width" not in profile:
            profile["window_width"] = int(self.settings.get("loan_browser_width", self.settings.get("browser_window_width", 620)))
        if "window_height" not in profile:
            profile["window_height"] = int(self.settings.get("loan_browser_height", self.settings.get("browser_window_height", 730)))
        if "window_position" not in profile and self.settings.get("browser_window_position"):
            profile["window_position"] = str(self.settings["browser_window_position"])
        if "start_maximized" not in profile and "browser_start_maximized" in self.settings:
            profile["start_maximized"] = bool(self.settings["browser_start_maximized"])
        if "always_on_top" not in profile and "browser_always_on_top" in self.settings:
            profile["always_on_top"] = bool(self.settings["browser_always_on_top"])
        if "remember_position" not in profile and "browser_remember_position" in self.settings:
            profile["remember_position"] = bool(self.settings["browser_remember_position"])
        if "page_zoom" not in profile:
            profile["page_zoom"] = float(self.settings.get("browser_page_zoom", 1.25))
        if "focus_view_enabled" not in profile:
            profile["focus_view_enabled"] = bool(self.settings.get("focus_view_enabled", True))
        if not bool(self.settings.get("clear_cookies", False)) and not profile.get("user_data_dir"):
            profile["user_data_dir"] = self.user_dir
        # Clear orphaned Chromium locks first so the window can open immediately
        try:
            from core.browser_profile_health import BrowserProfileHealthManager
            if profile.get("user_data_dir"):
                BrowserProfileHealthManager.clear_stale_locks(profile["user_data_dir"])
            BrowserProfileHealthManager.clear_stale_locks(self.user_dir)
        except Exception:
            pass
        # Intelligent session hygiene: purge stale CBI session cookies before Step 1, preserve for Step 3
        try:
            from core.browser_profile_recovery import BrowserProfileRecoveryManager
            resumed_stage = getattr(self, "_resumed_stage", None)
            BrowserProfileRecoveryManager.prepare_session_for_launch(
                self.nid,
                stage=resumed_stage or "STEP_1_ENTRY_FORM"
            )
        except Exception as hy_err:
            self.log(f"⚠️ ارزیابی پیش‌از‌راه‌اندازی نشست مرورگر: {hy_err}", "warning")

        try:
            playwright, browser, context, page = launch_browser(profile)
        except BrowserLaunchError as exc:
            detail = ""
            try:
                detail = (exc.details or {}).get("error") or ""
            except Exception:
                detail = ""
            msg = exc.message if not detail or detail in str(exc.message) else f"{exc.message} ({detail})"
            self.log(f"❌ خطا در راه‌اندازی مرورگر: {msg}", "error")
            raise

        # Only clear cookies if explicitly requested by configuration
        if bool(self.settings.get("clear_cookies", False)):
            try:
                context.clear_cookies()
            except Exception:
                pass
        self.attach_dialog_handler(page)
        self.navigation_evidence = NavigationEvidence(page)
        context.on("page", self.attach_dialog_handler)
        session_done = threading.Event()
        if str(self.settings.get("browser_display_mode", "external")).lower() == "embedded":
            self._update_browser_preview(page, force=True)
        if stop_event is not None:
            self._attach_manual_close_watchers(stop_event, browser, context, page, session_done)
            self._start_cancel_watcher(stop_event, playwright, browser, context, page, session_done)
        self.log(
            f"Launching browser: {profile.get('browser', 'chromium')} (headless={profile.get('headless', False)})",
            "info",
            page,
        )
        self.log("Browser context created", "info", page)
        self.record_metric("browser_startup_ms", launch_started)
        self.set_runtime_state("RUNNING", "LOAD_FORM", browser_status="OPEN")
        return playwright, browser, context, page

    def _attach_manual_close_watchers(self, stop_event, browser, context, page, session_done=None):
        if stop_event is None:
            return

        state = {"fired": False}

        def _on_manual_close(reason: str):
            if session_done is not None:
                session_done.set()
            if state["fired"] or stop_event.is_set():
                return
            if (
                getattr(self, "_closing_intentionally", False)
                or getattr(page, "_mahan_closing_intentionally", False)
                or getattr(context, "_mahan_closing_intentionally", False)
                or getattr(browser, "_mahan_closing_intentionally", False)
            ):
                return
            state["fired"] = True
            if getattr(self, "reopen_browser_on_close", False):
                try:
                    self.log(f"مرورگر بسته شد ({reason})؛ بازیابی خودکار فعال است.", "warning")
                except Exception:
                    pass
                return
            try:
                self.log(f"🛑 مرورگر توسط کاربر بسته شد ({reason})؛ توقف بات.", "warning")
            except Exception:
                pass
            try:
                from database import DBHandler
                DBHandler.save_checkpoint(self.nid, {"session_active": False, "browser_closed_by_user": True})
                DBHandler.release_final_submission_claim(self.nid)
            except Exception:
                pass
            try:
                stop_event.set()
            except Exception:
                pass

        try:
            page.on("close", lambda: _on_manual_close("page.close"))
        except Exception:
            pass
        try:
            context.on("close", lambda: _on_manual_close("context.close"))
        except Exception:
            pass
        if browser is not None:
            try:
                browser.on("disconnected", lambda: _on_manual_close("browser.disconnected"))
            except Exception:
                pass

    def _start_cancel_watcher(self, stop_event, playwright, browser, context, page, session_done=None):
        def _watch():
            while not stop_event.is_set():
                if session_done is not None and session_done.wait(0.1):
                    return
                if session_done is None:
                    stop_event.wait(0.1)
            if session_done is not None:
                session_done.set()
            try:
                self.log("🛑 لغو عملیات ثبت شد؛ Thread مالک در حال بستن مرورگر است.", "warning")
            except Exception:
                pass

        thread = threading.Thread(target=_watch, daemon=True)
        thread.start()
        self._cancel_watcher_thread = thread
        self._cancel_watcher_stop_event = stop_event

    def close_browser_on_stop(self, stop_event, playwright, browser, context, page) -> bool:
        if stop_event is None or not stop_event.is_set():
            return False
        self._closing_intentionally = True
        try:
            self.log("🛑 Stop detected - closing browser now.", "warning")
        except Exception:
            pass
        close_browser(playwright, browser, context, page)
        return True

    def solve_firewall(self, page):
        """Security challenges must be completed by the user in the browser."""
        check_rejection(page)
        return False

    def _capture_captcha_image(self, captcha_element, source):
        return captcha_element.screenshot()

    def solve_captcha_with_model(
        self,
        page,
        input_selector,
        button_selector,
        stop_event=None,
        source="general",
        captcha_element=None,
        captcha_image_selector=".BDC_CaptchaImage",
    ):
        """Single entry into captcha_engine for register, status, and select."""
        del captcha_element, captcha_image_selector
        if stop_event is not None and stop_event.is_set():
            return False
        try:
            from captcha_engine import solve_and_fill_captcha
        except ImportError:
            return False
        try:
            self.set_runtime_state("PROCESSING", "SOLVE_CAPTCHA_LOCAL", browser_status="OPEN")
        except Exception:
            pass
        res = solve_and_fill_captcha(
            page,
            input_selector=input_selector if isinstance(input_selector, str) else "",
            button_selector=button_selector if isinstance(button_selector, str) else "",
            submit=True,
            max_retries=3,
            bot=self,
            stop_event=stop_event,
            step=source or "captcha",
        )
        if not res.filled:
            return False
        try:
            if res.code:
                DBHandler.append_captcha_attempt(self.nid, res.code, "submitted", source=source or "captcha")
            self.mark_progress("captcha_model_submit")
            self.set_runtime_state("PROCESSING", "CHECK_RESPONSE", browser_status="OPEN")
        except Exception:
            pass
        return True

    def wait_for_nid_input_priority(self, page, timeout_ms: int = 3000):
        selectors = ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name$='tbIDNo']"]
        for selector in selectors:
            try:
                page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
                return page.locator(selector).first
            except Exception:
                continue
        return None

    def mark_progress(self, reason=None):
        self._last_progress_time = time.time()
        DBHandler.update_runtime(self.nid, last_activity_at=self._last_progress_time)

    def _set_recovery_flag(self, key):
        if key in self._recovery_flags:
            self._recovery_flags[key] = True

    def consume_recovery_flag(self, key):
        if self._recovery_flags.get(key):
            self._recovery_flags[key] = False
            return True
        return False

    def last_dialog_indicates_otp_invalid(self, max_age: float = 6.0) -> bool:
        if not self._last_dialog_message:
            return False
        if time.time() - self._last_dialog_ts > max_age:
            return False
        return self._last_dialog_action == "otp_expired"

    def register_otp_failure(self, reason: str = "otp_invalid") -> bool:
        self._otp_retry += 1
        if self._otp_hard_stop and self._otp_retry > self._otp_retry_limit:
            if not self._otp_retry_exceeded:
                self._otp_retry_exceeded = True
                self.log(
                    f"RECOVERY otp_retry_exceeded ({self._otp_retry}/{self._otp_retry_limit}) reason={reason}",
                    "error",
                )
            return False
        self.log(
            f"RECOVERY otp_failure ({self._otp_retry}) reason={reason}",
            "warning",
        )
        return True

    def otp_retry_exceeded(self) -> bool:
        return self._otp_hard_stop and self._otp_retry_exceeded

    def _classify_dialog(self, msg: str) -> Optional[str]:
        """Classify JavaScript Alert/Confirm dialog text accurately before accepting/dismissing."""
        raw = msg or ""
        msg_norm = raw.replace("ي", "ی").replace("ك", "ک").strip()
        msg_clean = re.sub(r'[\u064B-\u065F\u0670\s]+', ' ', msg_norm).strip()
        if not msg_clean:
            return None

        # 0) System capacity updating notice (temporary gateway notice, not a permanent lack of capacity)
        if any(u in msg_clean for u in ("در حال بروزرسانی ظرفیت", "بروزرسانی ظرفیت", "عملیات بروزرسانی", "تا پایان عملیات بروزرسانی")):
            return "system_capacity_updating"

        # 1) Bank capacity full (e.g., Bank Saderat non-in-person capacity full in province)
        capacity_markers = (
            "ظرفیت ثبت نام",
            "ظرفیت ثبت‌نام",
            "ظرفیت غیرحضوری",
            "ظرفیت غیر حضوری",
            "ظرفیت پذیرش",
            "ظرفیت تکمیل",
            "تکمیل شده است",
            "تکمیل گردیده",
            "تکمیل می باشد",
            "تکمیل می‌باشد",
            "تکمیل است",
            "فاقد ظرفیت",
            "امکان انتخاب روش غیرحضوری",
            "امکان انتخاب روش غیر حضوری",
            "امکان ثبت نام غیرحضوری",
            "امکان ثبت نام غیر حضوری",
            "امکان ثبت نام به صورت غیرحضوری",
            "امکان ثبت نام به صورت غیر حضوری",
            "امکان ثبت نام در این بانک وجود ندارد",
            "امکان انتخاب این بانک وجود ندارد",
            "تکمیل ظرفیت",
            "عدم وجود ظرفیت",
            "سقف مجاز",
            "اتمام رسیده",
            "فاقد سهمیه",
            "سهمیه تکمیل",
        )
        if any(m in msg_clean for m in capacity_markers) or (
            ("غیرحضوری" in msg_clean or "غیر حضوری" in msg_clean) and ("تکمیل" in msg_clean or "اتمام" in msg_clean or "عدم" in msg_clean or "وجود ندارد" in msg_clean)
        ) or (
            "ظرفیت" in msg_clean and ("تکمیل" in msg_clean or "اتمام" in msg_clean or "فاقد" in msg_clean or "پایان" in msg_clean or "وجود ندارد" in msg_clean)
        ):
            return "bank_capacity_full"

        # 2) SMS rate limit / cooldown (CBI throttles SMS resending to once every 2 minutes)
        if any(w in msg_clean for w in (
            "امکان ارسال کد تایید تا دو دقیقه وجود ندارد",
            "امکان ارسال مجدد کد تا دو دقیقه وجود ندارد",
            "تا دو دقیقه وجود ندارد",
        )) or (("ارسال" in msg_clean or "کد تایید" in msg_clean) and ("دو دقیقه" in msg_clean or "۲ دقیقه" in msg_clean)):
            return "sms_cooldown"

        # 2.5) OTP Sent / Info (announcements that code was sent to phone - must NOT be treated as expired)
        otp_sent_markers = (
            "ارسال شد",
            "ارسال گردید",
            "پیامک شد",
            "پیامک گردید",
            "با موفقیت ارسال",
        )
        if any(s in msg_clean for s in otp_sent_markers) and not any(k in msg_clean for k in ("منقضی", "نامعتبر", "اشتباه")):
            return "otp_sent"        # 3) CAPTCHA invalid (Checked BEFORE OTP to prevent misclassifying captcha error alerts as expired OTP!)
        if any(token in msg_clean.lower() for token in ("کد امنیتی", "کپچا", "تصویر امنیتی", "کد تصویر", "captcha")) and any(
            token in msg_clean.lower() for token in (
                "اشتباه", "نادرست", "نامعتبر", "نا معتبر", "صحیح نیست", "صحیح نمی",
                "incorrect", "invalid", "wrong",
            )
        ):
            return "captcha_invalid"

        # 4) OTP expired / invalid (strictly genuine OTP error messages)
        otp_phrases = (
            "کد تایید تلفن همراه",
            "کد تأیید تلفن همراه",
            "کد تایید",
            "کد تأیید",
            "کد پیامکی",
            "کد تایید پیامک",
            "رمز یکبار مصرف",
            "تلفن همراه",
        )
        otp_keywords = ("منقضی", "نامعتبر", "نا معتبر", "اشتباه", "صحیح نمی باشد", "صحیح نمی‌باشد", "پایان یافته", "اعتبار ندارد")
        if any(k in msg_clean for k in otp_keywords) and any(p in msg_clean for p in otp_phrases) and "ظرفیت" not in msg_clean and "امنیتی" not in msg_clean and "تصویر" not in msg_clean:
            return "otp_expired"

        # 5) Session expired (strictly genuine session expiration only, never generic timing or capacity alerts)
        session_markers = (
            "نشست شما منقضی",
            "انقضای نشست",
            "مدت زمان نشست",
            "مهلت زمانی نشست",
            "زمان نشست شما به پایان رسیده",
            "نشست شما به پایان رسیده",
            "مجددا وارد سامانه شوید",
            "مجددا وارد شوید",
            "لطفا مجددا وارد",
            "session expired",
        )
        if any(m in msg_clean.lower() for m in session_markers) or (
            "نشست" in msg_clean and ("پایان رسیده" in msg_clean or "منقضی" in msg_clean or "وارد شوید" in msg_clean or "باطل" in msg_clean)
        ):
            return "session_expired"

        # 6) WAF / Security / Rate Limit
        waf_markers = (
            "مسدود",
            "تعداد دفعات",
            "محدودیت دسترسی",
            "درخواست شما رد شد",
            "خطای امنیتی",
            "access denied",
            "rejected",
        )
        if any(m in msg_clean.lower() for m in waf_markers):
            return "waf_blocked"

        return "generic_error"

    def attach_dialog_handler(self, page):
        if not page:
            return
        page_id = id(page)
        if page_id in self._dialog_handlers_attached:
            return
        self._dialog_handlers_attached.add(page_id)

        def _handler(dialog):
            msg = ""
            dtype = "unknown"
            if hasattr(self, "_dialog_handling"):
                self._dialog_handling = True
            try:
                dtype = dialog.type() if callable(getattr(dialog, "type", None)) else getattr(dialog, "type", "alert")
            except Exception:
                pass
            try:
                msg = (dialog.message() if callable(getattr(dialog, "message", None)) else getattr(dialog, "message", "")) or ""
            except Exception:
                pass

            # Accept the dialog IMMEDIATELY so Chromium is never frozen on modal alerts
            try:
                dialog.accept()
            except Exception:
                try:
                    dialog.dismiss()
                except Exception:
                    pass

            # Record dialog message and classification
            normalized_msg = (msg or "").strip()
            action = self._classify_dialog(normalized_msg)

            # Check if this dialog occurred on Step 2 (OTP stage)
            is_otp_stage = False
            try:
                if page:
                    otp_loc = page.locator("#ctl00_ContentPlaceHolder1_tbMobileConfCode, input[name$='tbMobileConfCode']")
                    if otp_loc.count() > 0 and otp_loc.first.is_visible():
                        is_otp_stage = True
            except Exception:
                pass
            if not is_otp_stage:
                is_otp_stage = (getattr(self, "current_step", "") == "STEP_2_OTP" or getattr(self, "state", "") == "OTP_FORM")

            if is_otp_stage and action in ("bank_capacity_full", "system_capacity_updating", "generic_error"):
                action = "otp_step_capacity_retry"

            self._last_dialog_message = normalized_msg
            self._last_dialog_action = action
            self._last_dialog_ts = time.time()

            try:
                self.log(
                    f"🔔 DIALOG_CAPTURED type={dtype} category={action or 'none'} msg={normalized_msg}",
                    "warning",
                    page,
                    meta={"dialog_type": dtype, "dialog_category": action, "dialog_message": normalized_msg},
                )
            except Exception:
                pass

            try:
                if action == "otp_step_capacity_retry":
                    self.log(
                        f"⚠️ پیام موقت در مرحله پیامک ({normalized_msg})؛ تصویر کپچا حفظ می‌شود.",
                        "warning",
                        page,
                    )
                elif action == "bank_capacity_full":
                    self._set_recovery_flag("bank_capacity_full")
                    self.emit_event(
                        "BANK_CAPACITY_FULL",
                        step="STEP_3_BANK_SELECT",
                        reason=normalized_msg,
                        result="capacity_full_detected",
                        message=f"ظرفیت بانک تکمیل است: {normalized_msg}",
                        level="warning",
                        page=page,
                    )
                    DBHandler.update_status(self.nid, "ظرفیت تکمیل", normalized_msg[:200])
                    self.set_runtime_state(
                        "BANK_CAPACITY_FULL",
                        "BANK_CAPACITY_FULL",
                        browser_status="OPEN",
                        last_error=normalized_msg[:250],
                    )
                elif action == "otp_expired":
                    if getattr(self, "live_owner", None):
                        self.live_owner.bank_page_active = False
                    self.clear_otp_locks()
                    self.register_otp_failure("dialog_otp_expired")
                    self._set_recovery_flag("otp_expired")
                    try:
                        DBHandler.clear_otp(self.nid)
                    except Exception:
                        pass
                    DBHandler.update_status(self.nid, "کد منقضی - شروع مجدد", "کد تایید منقضی شد؛ بازگشت خودکار به مرحله ۱ جهت دریافت کد جدید")
                    # The owner starts a new challenge; no delayed clear may erase its SMS.
                    self.emit_event(
                        "OTP_EXPIRED",
                        step="STEP_2_OTP",
                        reason=normalized_msg,
                        result="restart_step1",
                        message=f"الارم انقضای کد پیامک: {normalized_msg}؛ بازگشت خودکار به مرحله ۱ و شروع مجدد متقاضی جهت صدور کد جدید.",
                        level="warning",
                        page=page,
                    )
                elif action == "sms_cooldown":
                    # Soft cooldown only — never hard-block Step1→Page3 reentry for the full 120s.
                    self._sms_cooldown_until = time.time() + 15.0
                    DBHandler.update_status(self.nid, "انتظار سهمیه پیامک", "محدودیت ارسال پیامک؛ تلاش سریع مجدد بدون توقف طولانی...")
                    self.log("⏳ سامانه محدودیت ارسال پیامک اعلام کرد؛ بدون انتظار ۱۲۰ثانیه، کپچا و ادامه به‌سرعت تکرار می‌شود...", "warning", page)
                elif action == "captcha_invalid":
                    # CAPTCHA solve belongs to Step1/Step2 only — never to Page3 bank monitoring.
                    on_bank_page = False
                    try:
                        if page and page.locator("#ctl00_ContentPlaceHolder1_ddlBankName, select[name$='ddlBankName']").count() > 0:
                            bank_loc = page.locator("#ctl00_ContentPlaceHolder1_ddlBankName, select[name$='ddlBankName']").first
                            on_bank_page = bool(bank_loc.is_visible())
                    except Exception:
                        on_bank_page = False
                    if on_bank_page or bool(getattr(getattr(self, "live_owner", None), "bank_page_active", False)):
                        self._captcha_retry = 0
                        self.log("CAPTCHA alert روی صفحه بانک نادیده گرفته شد (پایش بانک کپچا حل نمی‌کند).", "info", page)
                    else:
                        # Soft counter only for diagnostics — never a hard stop.
                        self._captcha_retry = 0
                        self._set_recovery_flag("captcha_invalid")
                        DBHandler.update_runtime(
                            self.nid,
                            status="RETRYING",
                            current_step="CAPTCHA_ERROR",
                            retry_count=0,
                            last_error="captcha_invalid",
                        )
                        self.log("CAPTCHA نامعتبر بود؛ فقط state کپچا بازنشانی می‌شود.", "warning", page)
                elif action == "session_expired":
                    if getattr(self, "live_owner", None):
                        self.live_owner.bank_page_active = False
                    self._set_recovery_flag("session_expired")
                    self.emit_event(
                        "OTP_EXPIRED",
                        step=self._current_step,
                        reason=normalized_msg,
                        result="session_expired",
                        message=f"نشست منقضی شد: {normalized_msg}",
                        level="warning",
                        page=page,
                    )
                elif action == "waf_blocked":
                    self._set_recovery_flag("waf_blocked")
                    self.emit_event(
                        "WAF_BLOCKED",
                        step=self._current_step,
                        reason=normalized_msg,
                        result="paused",
                        message=f"خطای محدودیت/امنیتی سرور: {normalized_msg}",
                        level="error",
                        page=page,
                    )
                elif action == "generic_error":
                    self._set_recovery_flag("generic_error")
                    self.log(f"هشدار سامانه: {normalized_msg}", "warning", page)
            except Exception:
                pass

            if hasattr(self, "_dialog_handling"):
                self._dialog_handling = False

        page.on("dialog", _handler)

    def dismiss_modals(self, page):
        if not page:
            return False
        modal_selectors = [
            ".modal.show",
            ".swal2-container",
            "#dlg",
            ".ui-dialog",
        ]
        for selector in modal_selectors:
            try:
                modal = page.locator(selector)
                if modal.count() == 0:
                    continue
                target = modal.first
                if not target.is_visible():
                    continue
                modal_text = ""
                try:
                    modal_text = (target.inner_text() or "").strip()
                except Exception:
                    pass
                if modal_text:
                    action = self._classify_dialog(modal_text)
                    is_otp_stage = False
                    try:
                        if page:
                            otp_loc = page.locator("#ctl00_ContentPlaceHolder1_tbMobileConfCode, input[name$='tbMobileConfCode']")
                            if otp_loc.count() > 0 and otp_loc.first.is_visible():
                                is_otp_stage = True
                    except Exception:
                        pass
                    if not is_otp_stage:
                        is_otp_stage = (getattr(self, "current_step", "") == "STEP_2_OTP" or getattr(self, "state", "") == "OTP_FORM")

                    if is_otp_stage and action in ("bank_capacity_full", "system_capacity_updating", "generic_error"):
                        action = "otp_step_capacity_retry"

                    self._last_dialog_message = modal_text
                    self._last_dialog_action = action
                    self._last_dialog_ts = time.time()
                    if action == "otp_step_capacity_retry":
                        self.log(
                            f"⚠️ پیام موقت در مدال مرحله پیامک ({modal_text})؛ تلاش مجدد تا پایان اعتبار پیامک...",
                            "warning",
                            page,
                        )
                    elif action == "bank_capacity_full":
                        self._set_recovery_flag("bank_capacity_full")
                        self.emit_event(
                            "BANK_CAPACITY_FULL",
                            step="STEP_3_BANK_SELECT",
                            reason=modal_text,
                            result="capacity_full_modal",
                            message=f"ظرفیت بانک تکمیل است: {modal_text}",
                            level="warning",
                            page=page,
                        )
                    elif action == "otp_expired":
                        self._set_recovery_flag("otp_expired")
                        if getattr(self, "live_owner", None):
                            self.live_owner.bank_page_active = False
                        self.clear_otp_locks()
                        try:
                            DBHandler.clear_otp(self.nid)
                        except Exception:
                            pass
                        from otp_transport import invalidate_otp
                        invalidate_otp(self)
                    elif action == "session_expired":
                        self._set_recovery_flag("session_expired")
                        if getattr(self, "live_owner", None):
                            self.live_owner.bank_page_active = False
                btns = target.locator(
                    "button:has-text('OK'), button:has-text('تایید'), "
                    "button:has-text('تأیید'), button:has-text('بستن'), "
                    "button:has-text('باشه'), button:has-text('قبول'), "
                    ".swal2-confirm, .ui-dialog-buttonset button"
                )
                if btns.count() > 0:
                    btns.first.click(timeout=500)
                    self.log("RECOVERY modal_dismiss -> clicked", "warning", page)
                    return True
                close_btn = target.locator("[data-bs-dismiss='modal'], .btn-close, .close")
                if close_btn.count() > 0:
                    close_btn.first.click(timeout=500)
                    self.log("RECOVERY modal_dismiss -> closed", "warning", page)
                    return True
            except Exception:
                continue
        return False

    def detect_actual_page_stage(self, page) -> str:
        """
        Legacy-compatible stage string from the unified live-DOM detector (SSOT).
        URL alone is NEVER sufficient because ASP.NET keeps the same URL across wizard steps.
        """
        from core.stage_detector import detect_current_stage
        return detect_current_stage(page, bot=self).legacy

    def wait_for_aspnet_postback(self, page, timeout_ms: int = 8000) -> bool:
        """Wait for ASP.NET PageRequestManager / waitDiv / UpdatePanel postback to finish."""
        if not page:
            return False
        deadline = time.time() + (timeout_ms / 1000.0)
        try:
            page.wait_for_function(
                """() => {
                    try {
                        if (window.Sys && Sys.WebForms && Sys.WebForms.PageRequestManager) {
                            const prm = Sys.WebForms.PageRequestManager.getInstance();
                            if (prm && prm.get_isInAsyncPostBack()) return false;
                        }
                        const up = document.getElementById('ctl00_ContentPlaceHolder1_updateProgress');
                        if (up && up.style && up.style.display !== 'none' && up.offsetParent !== null) return false;
                        const wd = document.getElementById('waitDiv');
                        if (wd && wd.style && wd.style.visibility === 'visible') return false;
                        return document.readyState === 'complete' || document.readyState === 'interactive';
                    } catch (e) {
                        return true;
                    }
                }""",
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return time.time() < deadline

    def _dump_state(self, page, selectors=None, reason="state_dump"):
        if not bool(self.settings.get("diagnostic_logging_enabled", True)):
            return
        try:
            url = page.url
        except Exception:
            url = "unknown"
        try:
            title = page.title()
        except Exception:
            title = "unknown"
        counts = {}
        if selectors:
            for selector in selectors:
                try:
                    counts[selector] = page.locator(selector).count()
                except Exception:
                    counts[selector] = -1
        safe_id = self._mask(self.nid)
        try:
            self.log(
                f"WATCHDOG dump reason={reason} url={url} title={title} counts={counts}",
                "warning",
                page,
                meta={"reason": reason, "url": url, "counts": counts, "request_id": safe_id},
            )
        except Exception:
            pass
        try:
            os.makedirs("screenshots", exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            page.screenshot(path=f"screenshots/watchdog_{safe_id.replace('*', 'x')}_{timestamp}.png", full_page=True,
                            mask=[page.locator('input:not([type="hidden"]), img[src*="aptcha"]')])
        except Exception:
            pass

    def register_repeatable_error(self, error_key: str, max_repeats: int = 3) -> bool:
        """Track repeated error occurrences to prevent infinite reload loops."""
        count = self._error_signatures.get(error_key, 0) + 1
        self._error_signatures[error_key] = count
        return count <= max_repeats

    def watchdog_check(self, page, stop_event=None, selectors=None):
        if getattr(getattr(self, "live_owner", None), "bank_page_active", False):
            self.clear_otp_locks()
            self.consume_recovery_flag("otp_expired")
            return False
        """Synchronized Watchdog check that coordinates with Recovery and yields during manual input."""
        if stop_event is not None and stop_event.is_set():
            return False
        check_rejection(page)
        now = time.time()
        if now < self._watchdog_suspended_until:
            return False
        if self._recovery_in_progress or getattr(self, "_dialog_handling", False):
            return False
        # Check actual stage first to prevent stale OTP step on Step 3
        try:
            stage_probe = self.detect_actual_page_stage(page)
            if stage_probe == "STEP_3_BANK_SELECT":
                self.clear_otp_locks()
                if self._current_step in {"OTP_MANUAL_REQUIRED", "STEP_2_OTP", "WAITING_SMS"}:
                    self._current_step = "STEP_3_BANK_SELECT"
        except Exception:
            pass

        # Do not trigger watchdog while waiting for user manual input (CAPTCHA / OTP / Capacity decision)
        if getattr(self, "runtime_state", {}).get("status") == "WAITING_USER_INPUT" or self._current_step in {
            "CAPTCHA_MANUAL_REQUIRED", "OTP_MANUAL_REQUIRED", "WAITING_USER_INPUT", "MANUAL_REQUIRED",
            "BANK_CAPACITY_FULL", "FINAL_SUBMIT_WAIT", "RESERVE_PENDING", "STEP2_CAPTCHA_REQUIRED"
        }:
            self._last_progress_time = now
            return False

        # Active User Typing & Focus Guard: Never reload if user is focused on or typing into inputs!
        try:
            if page and hasattr(page, "evaluate") and not page.is_closed():
                is_user_active = page.evaluate("""() => {
                    const el = document.activeElement;
                    if (el && (el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA')) {
                        return true;
                    }
                    const captchaInp = document.querySelector("input[name*='tbCaptcha'], #ans");
                    if (captchaInp && captchaInp.value && captchaInp.value.trim().length > 0) {
                        return true;
                    }
                    const otpInp = document.querySelector("#ctl00_ContentPlaceHolder1_tbMobileConfCode, input[name$='tbMobileConfCode']");
                    if (otpInp && otpInp.value && otpInp.value.trim().length > 0) {
                        return true;
                    }
                    return false;
                }""")
                if is_user_active:
                    self._last_progress_time = now
                    return False
        except Exception:
            pass

        if now - self._last_progress_time < self._watchdog_timeout:
            return False

        if not self._recovery_lock.acquire(blocking=False):
            return False
        try:
            self._recovery_in_progress = True
            self._last_progress_time = now
            self.emit_event(
                "RECOVERY_STARTED",
                step=self._current_step,
                reason="watchdog_timeout",
                result="inspecting_dom",
                message="شروع بازیابی هوشمند صفحه (Watchdog)...",
                level="warning",
                page=page,
            )
            self._dump_state(page, selectors=selectors, reason="watchdog")
            self.dismiss_modals(page)
            if stop_event is not None and stop_event.is_set():
                return True

            # Recalculate actual stage before deciding whether to reload
            actual_stage = self.detect_actual_page_stage(page)
            if actual_stage == "STEP_3_BANK_SELECT":
                self.clear_otp_locks()
                self.emit_event(
                    "RECOVERY_COMPLETED",
                    step="STEP_3_BANK_SELECT",
                    reason="stage_resynced_without_reload",
                    result="bank_select_active",
                    message="صفحه انتخاب بانک فعال است؛ همگام‌سازی وضعیت بدون رفرش مخرب انجام شد.",
                    level="info",
                    page=page,
                )
                return True

            if actual_stage == "STEP_2_OTP":
                self.emit_event(
                    "RECOVERY_COMPLETED",
                    step="STEP_2_OTP",
                    reason="otp_stage_preserved",
                    result="waiting_otp",
                    message="صفحه پیامک فعال است؛ از رفرش صفحه خودداری شد.",
                    level="info",
                    page=page,
                )
                return True

            # chrome-error / blank / dead document: hard recover by GET re-entry (not page.reload).
            cur_url = ""
            try:
                cur_url = str(getattr(page, "url", "") or "")
            except Exception:
                cur_url = ""
            url_l = cur_url.lower()
            is_chrome_error = (
                "chrome-error://" in url_l
                or "chromewebdata" in url_l
                or url_l in {"", "about:blank", "chrome://newtab/"}
            )
            if is_chrome_error:
                target = getattr(self, "_workflow_entry_path", None)
                if target and str(target).startswith("/"):
                    target = "https://ve.cbi.ir" + str(target)
                if not target or "cbi.ir" not in str(target):
                    target = "https://ve.cbi.ir/SelectBnkShb.aspx"
                self.log(f"🛠️ بازیابی chrome-error: باز کردن مجدد {target}", "warning", page)
                try:
                    self._safe_goto_guarded(
                        page,
                        target,
                        force=True,
                        timeout=int(self.settings.get("navigation_timeout_ms", 35000)),
                        wait_until="domcontentloaded",
                        log_callback=self.log,
                    )
                    self._last_progress_time = time.time()
                    self.emit_event(
                        "RECOVERY_COMPLETED",
                        step=actual_stage,
                        reason="chrome_error_goto_select",
                        result="navigated",
                        message="صفحه خطای کروم با باز کردن مجدد SelectBnkShb بازیابی شد.",
                        level="success",
                        page=page,
                    )
                    self.suspend_watchdog(8)
                    return True
                except Exception as nav_exc:
                    self.log(f"بازیابی chrome-error ناموفق: {nav_exc}", "error", page)

            # Non-error UNKNOWN: wait for ASP.NET postback; do not page.reload().
            try:
                self.wait_for_aspnet_postback(page, timeout_ms=2500)
            except Exception:
                pass
            self.emit_event(
                "RECOVERY_COMPLETED",
                step=actual_stage,
                reason="wait_postback",
                result="wait_dom",
                message=f"وضعیت {actual_stage}: انتظار کوتاه postback/DOM (بدون ریلود کامل).",
                level="warning",
                page=page,
            )
            self.suspend_watchdog(12)
            return True
        finally:
            self._recovery_in_progress = False
            self._recovery_lock.release()

    def suspend_watchdog(self, seconds: float = 20.0):
        seconds = max(0.0, float(seconds))
        self._watchdog_suspended_until = max(self._watchdog_suspended_until, time.time() + seconds)

    def is_firewall_challenge(self, page) -> bool:
        selectors = ["#ans", "#jar", "text=در حال بررسی مرورگر شما"]
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if locator.count() > 0 and locator.first.is_visible():
                    return True
            except Exception:
                continue

        challenge_markers = ["human visitor", "support id", "cloudflare", "firewall"]
        try:
            body_text = (page.inner_text("body") or "").lower()
            if "the requested url was rejected" in body_text:
                return False
            return any(marker in body_text for marker in challenge_markers)
        except Exception:
            return False


def classify_page_error(message: str) -> str:
    """Classify dialog or page alerts (e.g. bank_capacity_full, session_expired, otp_expired, etc.)."""
    bot = BotCore.__new__(BotCore)
    res = bot._classify_dialog(message)
    if res == "bank_capacity_full":
        return "capacity_full"
    return res or "generic_error"
