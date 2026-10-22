"""
MAHANBOT - Delete Request Bot (TasReqDelete.aspx)
Automates deletion/cancellation of existing loan registrations on https://ve.cbi.ir/TasReqDelete.aspx
with real DOM selectors, BotDetect Captcha solving, dialog confirmation, and SMS OTP fallback.
"""
import re
import time
from typing import Optional, List, Tuple

from bot_core import BotCore
from bot_helpers import sleep_with_stop, detect_captcha_failure
from browser_actions import BrowserActions
from browser_launcher import BrowserLaunchError, close_browser, safe_goto
from database import DBHandler

TARGET_URL = "https://ve.cbi.ir/TasReqDelete.aspx"


class DeleteRequestBot(BotCore):
    def __init__(self, nid: str, settings: dict, log_callback=None, captcha_service=None, browser_profile=None):
        super().__init__(
            nid,
            settings,
            log_callback=log_callback,
            captcha_service=captcha_service,
            browser_profile=browser_profile,
        )
        self._last_missing_log_ts = 0.0
        self._last_busy_log_ts = 0.0

    def run(self, stop_event) -> bool:
        self._active_stop_event = stop_event
        playwright = None
        browser = None
        context = None
        page = None

        # 1. Verify tracking code existence
        data = self.user_data or {}
        tracking_code = str(data.get("tracking_code") or "").strip()
        if not tracking_code or tracking_code == "-":
            cp = DBHandler.get_checkpoint(self.nid) or {}
            tracking_code = str(cp.get("tracking_code") or "").strip()

        if not tracking_code or tracking_code == "-":
            msg = "کد رهگیری برای متقاضی یافت نشد. برای حذف از سامانه مرکزی داشتن کد رهگیری الزامی است؛ ابتدا بازیابی کنید."
            self.log(msg, "error")
            DBHandler.update_status(self.nid, "Stopped", msg)
            return False

        try:
            playwright, browser, context, page = self.setup_browser(stop_event)
            if not page or stop_event.is_set():
                return False

            # Auto-accept all browser confirmation dialogs (e.g., "آیا از حذف مطمئن هستید؟")
            page.on("dialog", lambda dialog: self._handle_dialog(dialog))

            self.log(f"شروع فرایند حذف ثبت‌نام در سامانه مرکزی (TasReqDelete.aspx) برای کد ملی {self._mask(self.nid)}", "info", page)
            safe_goto(page, TARGET_URL, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)

            while not stop_event.is_set():
                if self._page_closed(page):
                    return False
                if self._form_ready(page):
                    break
                if self.is_firewall_challenge(page):
                    if not self._solve_firewall_loop(page, stop_event):
                        return False
                    continue
                if sleep_with_stop(stop_event, 0.3):
                    return False

            if stop_event.is_set():
                return False

            success = self._main_delete_loop(page, tracking_code, stop_event)
            if success:
                DBHandler.clear_request_data(self.nid, "ثبت‌نام متقاضی در سامانه ve.cbi.ir با موفقیت حذف شد")
                self.log("ثبت‌نام متقاضی با موفقیت از سامانه بانک مرکزی حذف شد. پرونده برای ثبت‌نام مجدد آماده است.", "success", page)
                return True
            return False

        except BrowserLaunchError as exc:
            self.log(f"خطای راه‌اندازی مرورگر: {exc.message}", "error")
            return False
        except Exception as exc:
            self.log(f"خطا در فرایند حذف درخواست: {exc}", "error")
            return False
        finally:
            close_browser(playwright, browser, context, page)

    def _handle_dialog(self, dialog):
        try:
            self.log(f"پیام دیالوگ سامانه: {dialog.message}", "info")
            dialog.accept()
        except Exception:
            pass

    def _page_closed(self, page) -> bool:
        try:
            return page.is_closed()
        except Exception:
            return True

    def _iter_scopes(self, page):
        yield page
        try:
            for frame in page.frames:
                yield frame
        except Exception:
            return

    def _first_visible_locator(self, page, selectors: List[str]):
        for scope in self._iter_scopes(page):
            for selector in selectors:
                try:
                    locator = scope.locator(selector)
                    if locator.count() > 0 and locator.first.is_visible():
                        return locator.first
                except Exception:
                    continue
        return None

    def _form_ready(self, page) -> bool:
        nid_selectors = ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name*='tbIDNo']", "input[name*='IDNo']"]
        track_selectors = ["#ctl00_ContentPlaceHolder1_tbTraceCD", "input[name*='tbTraceCD']", "input[name*='TraceCD']"]
        btn_selectors = [
            "#ctl00_ContentPlaceHolder1_btnDelete",
            "#ctl00_ContentPlaceHolder1_btnSubmit",
            "#ctl00_ContentPlaceHolder1_btnConfirm",
            "input[type='submit']",
        ]
        return (
            self._first_visible_locator(page, nid_selectors) is not None
            and self._first_visible_locator(page, track_selectors) is not None
            and self._first_visible_locator(page, btn_selectors) is not None
        )

    def _fill_stage1_form(self, page, tracking_code: str):
        data = self.user_data or {}
        mobile = str(data.get("mobile") or data.get("phone") or data.get("cell_phone") or "").strip()

        # Fill National ID
        nid_input = self._first_visible_locator(page, ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name*='tbIDNo']", "input[name*='IDNo']"])
        if nid_input:
            BrowserActions.human_fill(nid_input, self.nid)

        # Fill Tracking Code
        track_input = self._first_visible_locator(page, ["#ctl00_ContentPlaceHolder1_tbTraceCD", "input[name*='tbTraceCD']", "input[name*='TraceCD']"])
        if track_input:
            BrowserActions.human_fill(track_input, tracking_code)

        # Fill Mobile (if present on form)
        mob_input = self._first_visible_locator(page, ["#ctl00_ContentPlaceHolder1_tbMobile", "#ctl00_ContentPlaceHolder1_tbCellPhone", "input[name*='tbMobile']"])
        if mob_input and mobile:
            BrowserActions.human_fill(mob_input, mobile)

        self.log(f"فرم حذف درخواست با کد ملی و کد رهگیری {self._mask(tracking_code)} تکمیل شد.", "info", page)

    def _refresh_captcha(self, page):
        reload_btn = self._first_visible_locator(page, [".BDC_ReloadLink", "a[title*='کد جدید']"])
        if reload_btn is not None:
            try:
                reload_btn.click(timeout=600)
                return True
            except Exception:
                pass
        captcha_img = self._first_visible_locator(page, [".BDC_CaptchaImage", "img[id*='CaptchaImage']"])
        if captcha_img is not None:
            try:
                captcha_img.click(timeout=600)
                return True
            except Exception:
                pass
        return False

    def _solve_firewall_loop(self, page, stop_event) -> bool:
        self.log("فایروال شناسایی شد؛ در حال حل چالش امنیتی...", "warning", page)
        deadline = time.time() + int(self.settings.get("firewall_manual_timeout", 180))
        while not stop_event.is_set():
            if self.solve_firewall(page):
                delay = 0.1 if self.settings.get("firewall_solve_fast") else 0.3
                if sleep_with_stop(stop_event, delay):
                    return False
            if not self.is_firewall_challenge(page):
                self.log("فایروال برطرف شد.", "success", page)
                return True
            if time.time() > deadline:
                self.log("مهلت حل فایروال به پایان رسید.", "error", page)
                return False
            if sleep_with_stop(stop_event, 0.5):
                return False
        return False

    def _second_stage_visible(self, page) -> bool:
        otp_selectors = ["#ctl00_ContentPlaceHolder1_tbMobConfCode", "input[name*='tbMobConfCode']", "input[name*='ConfCode']"]
        continue_selectors = ["#ctl00_ContentPlaceHolder1_btnContinue2", "#ctl00_ContentPlaceHolder1_btnDelete", "input[type='submit']"]
        return self._first_visible_locator(page, otp_selectors) is not None and self._first_visible_locator(page, continue_selectors) is not None

    def _check_result(self, page) -> Tuple[Optional[bool], str]:
        """
        Inspects page text for success or error notices.
        Returns (True/False/None, message).
        """
        try:
            body = page.inner_text("body") or ""
        except Exception:
            body = ""

        # Success patterns
        success_phrases = [
            "ثبت نام شما با موفقیت حذف گردید",
            "با موفقیت حذف گردید",
            "با موفقیت حذف شد",
            "درخواست شما با موفقیت حذف گردید",
            "اطلاعات شما با موفقیت حذف شد",
            "درخواست شما حذف شد",
            "ثبت نام حذف شد",
        ]
        for phrase in success_phrases:
            if phrase in body:
                return True, phrase

        # Error patterns
        error_phrases = [
            "تعیین شعبه شده است",
            "کد رهگیری یا شماره ملی اشتباه است",
            "کد رهگیری اشتباه است",
            "اطلاعات وارد شده صحیح نمی باشد",
            "بیش از حد مجاز",
            "امکان حذف ثبت نام وجود ندارد",
            "پرونده ای با این مشخصات یافت نشد",
        ]
        for phrase in error_phrases:
            if phrase in body:
                return False, phrase

        # Check notification elements
        lbl = self._first_visible_locator(page, [
            "#ctl00_ContentPlaceHolder1_lblMessage",
            "#ctl00_ContentPlaceHolder1_lblError",
            "span[id*='lblMessage']",
            "span[id*='lblError']",
            ".alert-danger",
            ".alert-success",
        ])
        if lbl:
            try:
                txt = (lbl.inner_text() or "").strip()
                if any(p in txt for p in success_phrases):
                    return True, txt
                if any(p in txt for p in error_phrases):
                    return False, txt
            except Exception:
                pass

        return None, ""

    def _submit_stage1(self, page, tracking_code: str) -> bool:
        if self._page_closed(page):
            return False

        captcha_img = self._first_visible_locator(page, [".BDC_CaptchaImage", "img[id*='CaptchaImage']"])
        captcha_inp = BrowserActions.find_captcha_input(page, "#ctl00_ContentPlaceHolder1_tbCaptcha1")
        submit_btn = self._first_visible_locator(page, [
            "#ctl00_ContentPlaceHolder1_btnDelete",
            "#ctl00_ContentPlaceHolder1_btnSubmit",
            "#ctl00_ContentPlaceHolder1_btnConfirm",
            "input[type='submit']",
        ])

        if captcha_img is None or captcha_inp is None or submit_btn is None:
            now = time.time()
            if now - self._last_missing_log_ts > 2.0:
                self.log("المان‌های کپچا یا دکمه ارسال حذف یافت نشد.", "warning", page)
                self._last_missing_log_ts = now
            return False

        self._fill_stage1_form(page, tracking_code)

        return self.solve_captcha_with_model(
            page,
            captcha_inp,
            submit_btn,
            stop_event=self._active_stop_event,
            source="delete_step1",
            captcha_element=captcha_img,
        )

    def _run_stage2_otp(self, page, stop_event) -> bool:
        self.log("مرحله دوم حذف (تأیید پیامکی) شناسایی شد؛ منتظر دریافت کد تأیید...", "info", page)
        otp_selectors = ["#ctl00_ContentPlaceHolder1_tbMobConfCode", "input[name*='tbMobConfCode']", "input[name*='ConfCode']"]
        captcha_selectors = ["#ctl00_ContentPlaceHolder1_tbCaptcha2", "input[name*='tbCaptcha']"]
        btn_selectors = ["#ctl00_ContentPlaceHolder1_btnContinue2", "#ctl00_ContentPlaceHolder1_btnDelete", "input[type='submit']"]

        deadline = time.time() + 130.0
        while time.time() < deadline and not stop_event.is_set():
            if self._page_closed(page):
                return False

            otp_inp = self._first_visible_locator(page, otp_selectors)
            if not otp_inp:
                if sleep_with_stop(stop_event, 0.3):
                    return False
                continue

            # Fetch OTP code
            code = None
            try:
                server_code = self.get_otp_code(timeout=1)
                if server_code and len(str(server_code).strip()) >= 5:
                    code = str(server_code).strip()
            except Exception:
                pass

            if not code:
                try:
                    db_code = DBHandler.get_otp(self.nid)
                    if db_code and len(str(db_code).strip()) >= 5:
                        code = str(db_code).strip()
                except Exception:
                    pass

            if not code:
                if sleep_with_stop(stop_event, 0.5):
                    return False
                continue

            # Fill OTP
            BrowserActions.human_fill(otp_inp, code)
            self.log(f"کد تأیید پیامکی {code[:2]}**{code[-2:]} در فیلد مرحله دوم وارد شد.", "success", page)

            # Solve stage 2 captcha and submit
            captcha_img = self._first_visible_locator(page, [".BDC_CaptchaImage", "img[id*='CaptchaImage']"])
            captcha_inp = self._first_visible_locator(page, captcha_selectors)
            submit_btn = self._first_visible_locator(page, btn_selectors)

            if captcha_inp and submit_btn and captcha_img:
                self.solve_captcha_with_model(
                    page,
                    captcha_inp,
                    submit_btn,
                    stop_event=stop_event,
                    source="delete_step2",
                    captcha_element=captcha_img,
                )

            # Wait and check result
            check_deadline = time.time() + 10.0
            while time.time() < check_deadline and not stop_event.is_set():
                is_success, msg = self._check_result(page)
                if is_success is True:
                    return True
                if is_success is False:
                    self.log(f"خطای سامانه در حذف ثبت‌نام: {msg}", "error", page)
                    return False
                if sleep_with_stop(stop_event, 0.5):
                    return False

        return False

    def _main_delete_loop(self, page, tracking_code: str, stop_event) -> bool:
        self.log("در حال ارسال درخواست لغو/حذف ثبت‌نام...", "info", page)
        attempts = 0
        max_attempts = int(self.settings.get("delete_max_attempts", 10))

        while attempts < max_attempts and not stop_event.is_set():
            if self._page_closed(page):
                return False

            if self.is_firewall_challenge(page):
                if not self._solve_firewall_loop(page, stop_event):
                    return False
                continue

            # Check if stage 2 is already active
            if self._second_stage_visible(page):
                return self._run_stage2_otp(page, stop_event)

            # Check if result already visible
            is_success, msg = self._check_result(page)
            if is_success is True:
                return True
            if is_success is False:
                self.log(f"پاسخ سامانه بانک مرکزی: {msg}", "error", page)
                return False

            # Submit stage 1
            attempts += 1
            self.log(f"تلاش شماره {attempts} برای ارسال فرم حذف...", "info", page)
            if not self._submit_stage1(page, tracking_code):
                if sleep_with_stop(stop_event, 0.5):
                    return False
                continue

            # Wait after submit
            wait_deadline = time.time() + 10.0
            while time.time() < wait_deadline and not stop_event.is_set():
                if self._page_closed(page):
                    return False

                if self._second_stage_visible(page):
                    return self._run_stage2_otp(page, stop_event)

                is_success, msg = self._check_result(page)
                if is_success is True:
                    return True
                if is_success is False:
                    self.log(f"پاسخ منفی سامانه: {msg}", "error", page)
                    return False

                if detect_captcha_failure(page):
                    self.log("کپچا اشتباه بود؛ بارگذاری مجدد و تلاش دوباره...", "warning", page)
                    self._refresh_captcha(page)
                    break

                if sleep_with_stop(stop_event, 0.4):
                    return False

        return False
