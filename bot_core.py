import time
import os
import json
import threading
import requests
from database import DBHandler
from captcha_service import CaptchaService
from browser_actions import BrowserActions
from browser_launcher import (
    BrowserLaunchError,
    close_browser,
    open_healthcheck_page,
    launch_browser,
)

class BotCore:
    def __init__(self, nid, settings, log_callback=None, captcha_service=None, browser_profile=None):
        self.nid = nid
        self.settings = settings
        self.log_callback = log_callback
        self.captcha_service = captcha_service or CaptchaService()
        self.user_data = self._load_user_data()
        self.retry_limit = int(self.settings.get('retry_count', 1000))
        self.api_base_url = "https://python-ke7tg2.chbk.dev"
        self.local_api_base = os.getenv("MAHANBOT_LOCAL_API", "http://127.0.0.1:8000")
        self.user_dir = os.path.join(os.getcwd(), "chrome_profiles", nid)
        if not os.path.exists(self.user_dir): os.makedirs(self.user_dir)
        self.browser_profile = browser_profile or {}
        self._cancel_watcher_thread = None
        self._cancel_watcher_stop_event = None
        self._dialog_handlers_attached = set()
        self._recovery_flags = {"otp_expired": False, "captcha_invalid": False, "generic_error": False}
        self._captcha_retry = 0
        self._captcha_retry_limit = int(self.settings.get("captcha_retry_limit", 5))
        self._otp_retry = 0
        self._otp_retry_limit = int(self.settings.get("otp_retry_limit", 5))
        self._otp_retry_exceeded = False
        self._last_progress_time = time.time()
        self._watchdog_timeout = int(self.settings.get("watchdog_timeout", 45))
        self._last_watchdog_dump = 0.0
        self._last_dialog_message = ""
        self._last_dialog_action = None
        self._last_dialog_ts = 0.0

    def log(self, message, level="info", page=None, meta=None):
        if self.log_callback:
            try:
                self.log_callback(self.nid, message, level, meta)
            except TypeError:
                self.log_callback(self.nid, message, level)
        else: print(f"[{level.upper()}] {self.nid}: {message}")
        show_on_page = os.getenv("MAHANBOT_SHOW_LOG_ON_PAGE", "false").lower() in {"1", "true", "yes"}
        if page and show_on_page:
            color = "red" if level == "error" else ("green" if level == "success" else "blue")
            BrowserActions.show_log_on_page(page, message, color)

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

    def get_otp_code(self, stop_event=None, timeout: int = 45):
        """Retrieve OTP primarily from the local wait endpoint for event-driven flow."""

        # 1) Local wait endpoint (/wait_otp) for zero-polling behavior
        try:
            code = self.wait_for_otp(stop_event, timeout=timeout)
            if code:
                DBHandler.save_otp(self.nid, code)
                self.log(f"✅ OTP received from /wait_otp: {code}", "success")
                return str(code).strip()
        except Exception:
            pass

        # 2) Remote API fallback only when local endpoint is unavailable
        try:
            response = requests.get(f"{self.api_base_url}/get_otp/{self.nid}", timeout=3)
            if response.status_code == 200:
                data = response.json()
                code = data.get("otp")
                if code is not None:
                    code = str(code).strip()
                if code:
                    DBHandler.save_otp(self.nid, code)
                    self.log(f"[NID: {self.nid}] ?? OTP successfully retrieved from API and synced to DB: {code}", "success")
                    return code
        except Exception:
            pass

        # 3) DB fallback as last resort
        try:
            user = DBHandler.get_applicant(self.nid)
            if user:
                raw_data = user.get("data")
                if isinstance(raw_data, str):
                    data = json.loads(raw_data)
                elif isinstance(raw_data, dict):
                    data = raw_data
                else:
                    data = {}
                code = data.get("otp_code")
                if code and str(code).strip():
                    return str(code).strip()
        except Exception:
            pass

        return None

    def wait_for_otp(self, stop_event=None, timeout: int = 45):
        if stop_event is not None and stop_event.is_set():
            return None
        timeout = max(1, int(timeout))
        try:
            response = requests.get(
                f"{self.local_api_base}/wait_otp/{self.nid}",
                params={"timeout": timeout},
                timeout=timeout + 5,
            )
            if response.status_code == 204:
                return None
            if response.status_code == 200:
                data = response.json()
                code = data.get("otp")
                if code and str(code).strip():
                    return str(code).strip()
        except Exception:
            pass
        return None

    def clear_otp_backend(self):
        try:
            requests.post(f"{self.local_api_base}/otp/clear/{self.nid}", timeout=3)
            return
        except Exception:
            pass
        try:
            DBHandler.clear_otp(self.nid)
        except Exception:
            pass

    def clear_remote_otp(self):
        self.clear_otp_backend()

    def setup_browser(self, stop_event=None):
        profile = dict(self.browser_profile)
        if not self.settings.get("clear_cookies", True) and not profile.get("user_data_dir"):
            profile["user_data_dir"] = self.user_dir
        try:
            playwright, browser, context, page = launch_browser(profile)
        except BrowserLaunchError as exc:
            self.log(f"❌ خطا در راه‌اندازی مرورگر: {exc.message}", "error")
            raise
        self.attach_dialog_handler(page)
        context.on("page", self.attach_dialog_handler)
        if stop_event is not None:
            self._start_cancel_watcher(stop_event, playwright, browser, context, page)
        open_healthcheck_page(page, log_callback=self.log)
        self.log(
            f"Launching browser: {profile.get('browser', 'chromium')} (headless={profile.get('headless', False)})",
            "info",
            page,
        )
        self.log("Browser context created", "info", page)
        return playwright, browser, context, page

    def _start_cancel_watcher(self, stop_event, playwright, browser, context, page):
        if self._cancel_watcher_stop_event is stop_event and self._cancel_watcher_thread:
            if self._cancel_watcher_thread.is_alive():
                return

        def _watch():
            stop_event.wait()
            try:
                self.log("🛑 لغو عملیات و بستن مرورگر...", "warning")
            except Exception:
                pass
            close_browser(playwright, browser, context, page)

        thread = threading.Thread(target=_watch, daemon=True)
        thread.start()
        self._cancel_watcher_thread = thread
        self._cancel_watcher_stop_event = stop_event

    def close_browser_on_stop(self, stop_event, playwright, browser, context, page) -> bool:
        if stop_event is None or not stop_event.is_set():
            return False
        try:
            self.log("🛑 Stop detected - closing browser now.", "warning")
        except Exception:
            pass
        close_browser(playwright, browser, context, page)
        return True

    def solve_firewall(self, page):
        self.log("🛡 solve_firewall: entered", "info", page)
        try:
            detected = "none"
            action = "none"

            firewall_input = page.locator("#ans")
            if firewall_input.count() > 0 and firewall_input.first.is_visible():
                detected = "#ans_visible"
                self.log("🛡️ حل فایروال...", "warning", page)
                imgs = page.locator("img[src^='data:image']").all()
                if imgs:
                    code = self.captcha_service.solve(imgs[0].screenshot(), mode='firewall')
                    if code:
                        firewall_input.first.fill(code)
                        page.locator("#jar").first.click()
                        action = "captcha_submitted"
                        time.sleep(1.5)
                        self.log(
                            f"🛡 solve_firewall: detected={detected}, action={action}, returning=True",
                            "info",
                            page,
                        )
                        return True

            self.log(
                f"🛡 solve_firewall: detected={detected}, action={action}, returning=False",
                "info",
                page,
            )
            return False
        except Exception as exc:
            self.log(f"🛡 solve_firewall: detected=error, action=exception:{exc}, returning=False", "warning", page)
            return False


    def mark_progress(self, reason=None):
        self._last_progress_time = time.time()
        if self._captcha_retry:
            self._captcha_retry = 0

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
        msg = self._last_dialog_message
        return "منقضی" in msg or "نامعتبر" in msg

    def register_otp_failure(self, reason: str = "otp_invalid") -> bool:
        self._otp_retry += 1
        if self._otp_retry > self._otp_retry_limit:
            if not self._otp_retry_exceeded:
                self._otp_retry_exceeded = True
                self.log(
                    f"RECOVERY otp_retry_exceeded ({self._otp_retry}/{self._otp_retry_limit}) reason={reason}",
                    "error",
                )
            return False
        self.log(
            f"RECOVERY otp_failure ({self._otp_retry}/{self._otp_retry_limit}) reason={reason}",
            "warning",
        )
        return True

    def otp_retry_exceeded(self) -> bool:
        return self._otp_retry_exceeded

    def _classify_dialog(self, msg):
        msg = msg or ""
        otp_phrases = [
            "\u06a9\u062f \u062a\u0627\u06cc\u06cc\u062f \u062a\u0644\u0641\u0646 \u0647\u0645\u0631\u0627\u0647",
            "\u06a9\u062f \u062a\u0627\u06cc\u06cc\u062f",
            "\u06a9\u062f \u062a\u0623\u06cc\u06cc\u062f",
        ]
        otp_keywords = ["\u0645\u0646\u0642\u0636\u06cc", "\u0646\u0627\u0645\u0639\u062a\u0628\u0631"]
        if any(p in msg for p in otp_phrases) or any(k in msg for k in otp_keywords):
            return "otp_expired"
        if msg:
            return "generic_error"
        return None

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
                dtype = dialog.type()
            except Exception:
                pass
            try:
                msg = dialog.message() or ""
            except Exception:
                pass

            try:
                action = self._classify_dialog(msg)
                self._last_dialog_message = msg
                self._last_dialog_action = action
                self._last_dialog_ts = time.time()
                if action == "otp_expired":
                    self.clear_remote_otp()
                    if self.register_otp_failure("dialog_otp_expired"):
                        self._set_recovery_flag("otp_expired")
                        self.log("RECOVERY otp_expired -> restart_otp", "warning", page)
                    try:
                        page.reload(timeout=10000)
                    except Exception:
                        pass
                elif action == "generic_error":
                    self._set_recovery_flag("generic_error")
                    self.log("RECOVERY generic_error -> soft_reload", "warning", page)
            except Exception:
                pass

            try:
                self.log(f"DIALOG_ACCEPTED type={dtype} msg={msg}", "warning", page)
            except Exception:
                pass
            try:
                dialog.accept()
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
                btns = target.locator(
                    "button:has-text('OK'), button:has-text('\u062a\u0627\u06cc\u06cc\u062f'), "
                    "button:has-text('\u062a\u0623\u06cc\u06cc\u062f'), button:has-text('\u0628\u0633\u062a\u0646'), "
                    "button:has-text('\u0628\u0627\u0634\u0647'), button:has-text('\u0642\u0628\u0648\u0644'), "
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

    def _dump_state(self, page, selectors=None, reason="state_dump"):
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
        try:
            self.log(
                f"WATCHDOG dump reason={reason} url={url} title={title} counts={counts}",
                "warning",
                page,
            )
        except Exception:
            pass
        try:
            os.makedirs("screenshots", exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            page.screenshot(path=f"screenshots/watchdog_{self.nid}_{timestamp}.png", full_page=True)
        except Exception:
            pass

    def watchdog_check(self, page, stop_event=None, selectors=None):
        if stop_event is not None and stop_event.is_set():
            return False
        now = time.time()
        if now - self._last_progress_time < self._watchdog_timeout:
            return False
        self._last_progress_time = now
        try:
            self._dump_state(page, selectors=selectors, reason="watchdog")
        except Exception:
            pass
        try:
            self.log("WATCHDOG triggered -> recovery step dismiss_modals", "warning", page)
            self.dismiss_modals(page)
        except Exception:
            pass
        if stop_event is not None and stop_event.is_set():
            return True
        try:
            self.log("WATCHDOG triggered -> recovery step reload", "warning", page)
            page.reload()
        except Exception:
            pass
        return True

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
            return any(marker in body_text for marker in challenge_markers)
        except Exception:
            return False
