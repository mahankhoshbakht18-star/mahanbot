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

    def get_otp_code(self):
        """???????????? ???? ?????????? (?????????? ??????????/?????????? ?????? ???????? ????????????)"""

        # 1) Local DB cache (otp_code in data JSON)
        try:
            user = DBHandler.get_applicant(self.nid)
            if user:
                raw_data = user.get("data")
                if isinstance(raw_data, str):
                    d = json.loads(raw_data)
                elif isinstance(raw_data, dict):
                    d = raw_data
                else:
                    d = {}
                code = d.get("otp_code")
                if code and str(code).strip():
                    self.log(f"??? ?????????????? ???? ???? ?????????? ???? ??????????????: {code}", "success")
                    return str(code).strip()
        except Exception:
            pass

        # 2) Remote API (map `otp` -> otp_code, persist immediately)
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

        return None

    def wait_for_otp(self, stop_event=None, timeout: int = 65):
        if stop_event is not None and stop_event.is_set():
            return None
        try:
            response = requests.get(f"{self.local_api_base}/wait_otp/{self.nid}", timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                code = data.get("otp")
                if code and str(code).strip():
                    return str(code).strip()
        except Exception:
            pass
        return None

    def setup_browser(self, stop_event=None):
        profile = dict(self.browser_profile)
        if not self.settings.get("clear_cookies", True) and not profile.get("user_data_dir"):
            profile["user_data_dir"] = self.user_dir
        try:
            playwright, browser, context, page = launch_browser(profile)
        except BrowserLaunchError as exc:
            self.log(f"❌ خطا در راه‌اندازی مرورگر: {exc.message}", "error")
            raise
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
