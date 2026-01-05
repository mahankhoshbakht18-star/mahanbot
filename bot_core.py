import time
import random
import os
import json
import requests
from playwright.sync_api import sync_playwright
from database import DBHandler
from captcha_service import CaptchaService
from browser_actions import BrowserActions

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
]

class BotCore:
    def __init__(self, nid, settings, log_callback=None, captcha_service=None):
        self.nid = nid
        self.settings = settings
        self.log_callback = log_callback
        self.captcha_service = captcha_service or CaptchaService()
        self.user_data = self._load_user_data()
        self.retry_limit = int(self.settings.get('retry_count', 1000))
        self.api_base_url = "https://python-ke7tg2.chbk.dev"
        self.user_dir = os.path.join(os.getcwd(), "chrome_profiles", nid)
        if not os.path.exists(self.user_dir): os.makedirs(self.user_dir)

    def log(self, message, level="info", page=None):
        if self.log_callback: self.log_callback(self.nid, message, level)
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

    def setup_browser(self, playwright):
        headless = self.settings.get('headless', False)
        ua = random.choice(USER_AGENTS)
        args = ["--start-maximized", "--no-sandbox", "--disable-blink-features=AutomationControlled"]
        if self.settings.get('clear_cookies'):
            browser = playwright.chromium.launch(headless=headless, args=args)
            context = browser.new_context(user_agent=ua, viewport={'width':1366,'height':768})
        else:
            context = playwright.chromium.launch_persistent_context(self.user_dir, headless=headless, args=args, user_agent=ua, viewport={'width':1366,'height':768})
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(30000)
        browser_instance = browser if self.settings.get('clear_cookies') else None
        return browser_instance, context, page

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
