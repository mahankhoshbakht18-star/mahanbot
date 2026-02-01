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
    safe_goto,
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
        self.user_dir = os.path.join(os.getcwd(), "chrome_profiles", nid)
        if not os.path.exists(self.user_dir): os.makedirs(self.user_dir)
        self.browser_profile = browser_profile or {}

    def log(self, message, level="info", page=None, meta=None):
        if self.log_callback:
            try:
                self.log_callback(self.nid, message, level, meta)
            except TypeError:
                self.log_callback(self.nid, message, level)
        else: print(f"[{level.upper()}] {self.nid}: {message}")
        if page:
            color = "red" if level == "error" else ("green" if level == "success" else "blue")
            BrowserActions.show_log_on_page(page, message, color)

    def _load_user_data(self):
        row = DBHandler.get_applicant(self.nid)
        if row and row['data']:
            if isinstance(row['data'], str): return json.loads(row['data'])
            return row['data']
        return {}

    def get_otp_code(self):
        """دریافت کد تایید (ابتدا لوکال/دستی، سپس سرور چابکان)"""
        
        # 1. اولویت اول: چک کردن دیتابیس داخلی (ورود دستی یا دریافت قبلی)
        try:
            user = DBHandler.get_applicant(self.nid)
            if user:
                d = json.loads(user['data'])
                if 'otp_code' in d and d['otp_code']:
                    self.log(f"✅ استفاده از کد موجود در دیتابیس: {d['otp_code']}", "success")
                    return str(d['otp_code']).strip()
        except: pass

        # 2. اولویت دوم: استعلام از سرور چابکان
        try:
            response = requests.get(f"{self.api_base_url}/get_otp/{self.nid}", timeout=3)
            if response.status_code == 200:
                data = response.json()
                code = data.get('otp')
                if code and str(code).strip():
                    self.log(f"☁️ دریافت کد از سرور آنلاین: {code}", "success")
                    # ذخیره در دیتابیس لوکال برای استفاده‌های بعدی
                    DBHandler.save_otp(self.nid, code)
                    return str(code).strip()
        except: pass
        
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
        self._wrap_page_navigation(page)
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

    def _wrap_page_navigation(self, page):
        original_goto = page.goto

        def guarded_goto(url, **kwargs):
            return safe_goto(page, url, log_callback=self.log, **kwargs)

        page.goto = guarded_goto  # type: ignore[assignment]
        page._original_goto = original_goto  # type: ignore[attr-defined]

    def _start_cancel_watcher(self, stop_event, playwright, browser, context, page):
        def _watch():
            stop_event.wait()
            try:
                self.log("🛑 لغو عملیات و بستن مرورگر...", "warning")
            except Exception:
                pass
            close_browser(playwright, browser, context, page)

        thread = threading.Thread(target=_watch, daemon=True)
        thread.start()

    def solve_firewall(self, page):
        try:
            if page.locator("#ans").is_visible():
                self.log("🛡️ حل فایروال...", "warning", page)
                imgs = page.locator("img[src^='data:image']").all()
                if imgs:
                    code = self.captcha_service.solve(imgs[0].screenshot(), mode='firewall')
                    if code:
                        page.locator("#ans").fill(code)
                        page.locator("#jar").click()
                        time.sleep(1.5)
                        return True
            return True
        except: return False
