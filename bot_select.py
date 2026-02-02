# bot_select.py
import time
import json
import random
from playwright.sync_api import Error as PlaywrightError
from bot_core import BotCore
from browser_launcher import BrowserLaunchError, close_browser, safe_goto
from database import DBHandler

TARGET_URL = "https://ve.cbi.ir/SelectBnkShb.aspx"


def sleep_with_stop(stop_event, seconds: float, step: float = 0.2) -> bool:
    if seconds <= 0:
        return stop_event.is_set()
    end = time.time() + seconds
    while time.time() < end:
        if stop_event.is_set():
            return True
        time.sleep(min(step, end - time.time()))
    return stop_event.is_set()


class BankSelectionBot(BotCore):
    def __init__(self, nid, settings, log_callback=None, captcha_service=None, browser_profile=None):
        super().__init__(
            nid,
            settings,
            log_callback=log_callback,
            captcha_service=captcha_service,
            browser_profile=browser_profile,
        )

    def run(self, stop_event, loan_type="rbtnNaghdi"):
        attempt = 0

        while attempt < self.retry_limit:
            if stop_event.is_set():
                break
            attempt += 1

            playwright = None
            browser = None
            context = None
            page = None
            try:
                playwright, browser, context, page = self.setup_browser(stop_event)
                if stop_event.is_set():
                    return

                page.on("dialog", lambda dialog: dialog.accept())

                self.log(f"🚀 شروع عملیات (دور {attempt})...", "info", page)
                captcha_mode = self.settings.get("captcha_mode", "human")

                # ✅ DIRECT NAVIGATION (must happen immediately)
                try:
                    safe_goto(page, TARGET_URL, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)
                except Exception:
                    if sleep_with_stop(stop_event, 1.0):
                        return
                    try:
                        safe_goto(page, TARGET_URL, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)
                    except Exception:
                        return

                while not stop_event.is_set():
                    if page.is_closed():
                        self.log("🛑 مرورگر توسط کاربر بسته شد.", "stopped")
                        stop_event.set()
                        return

                    # Soft WAF / blocked message
                    try:
                        if page.locator("body").get_by_text("درخواست شما رد شد").is_visible():
                            self.log("⛔ مسدودی! رفرش...", "error", page)
                            if sleep_with_stop(stop_event, 2):
                                break
                            safe_goto(page, TARGET_URL, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)
                            continue
                    except Exception:
                        pass

                    # firewall
                    if self.solve_firewall(page):
                        if sleep_with_stop(stop_event, 0.8):
                            break
                        continue

                    if stop_event.is_set():
                        break

                    # مرحله ۱: ورود کد ملی
                    if page.locator("#ctl00_ContentPlaceHolder1_btnSendConfirmCode").is_visible():
                        if stop_event.is_set():
                            break
                        if not page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").input_value():
                            page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").fill(self.nid)

                        self._solve_captcha_wrapper(
                            page,
                            "#ctl00_ContentPlaceHolder1_tbCaptcha1",
                            "#ctl00_ContentPlaceHolder1_btnSendConfirmCode",
                            captcha_mode,
                        )
                        try:
                            page.wait_for_selector("#ctl00_ContentPlaceHolder1_tbMobileConfCode", timeout=3000)
                        except Exception:
                            pass

                    # مرحله ۲: OTP
                    elif page.locator("#ctl00_ContentPlaceHolder1_tbMobileConfCode").is_visible():
                        if stop_event.is_set():
                            break

                        otp_code = self._get_otp_from_db_fresh()
                        if otp_code:
                            current_val = page.locator("#ctl00_ContentPlaceHolder1_tbMobileConfCode").input_value()
                            if current_val != otp_code:
                                self.log(f"✅ دریافت کد پیامک: {otp_code}", "success", page)
                                page.locator("#ctl00_ContentPlaceHolder1_tbMobileConfCode").fill(otp_code)
                                if sleep_with_stop(stop_event, 0.5):
                                    break

                            success = self._solve_captcha_wrapper(
                                page,
                                "#ctl00_ContentPlaceHolder1_tbCaptcha2",
                                "#ctl00_ContentPlaceHolder1_btnContinue1",
                                captcha_mode,
                            )
                            if success:
                                self.log("👆 تایید کد پیامک...", "info", page)
                                if sleep_with_stop(stop_event, 3):
                                    break
                        else:
                            self.log("📩 منتظر دریافت پیامک...", "waiting sms", page)
                            if sleep_with_stop(stop_event, 2):
                                break

                    # مرحله ۳: انتخاب بانک
                    elif page.locator("#ctl00_ContentPlaceHolder1_ddlBankName").is_visible():
                        if stop_event.is_set():
                            break
                        result = self._process_bank_selection_v2(page)
                        if result == "success":
                            self.log("🎉 بانک رزرو شد! پایان عملیات.", "success", page)
                            return
                        elif result == "no_match":
                            self.log("❌ بانک مورد نظر یافت نشد. رفرش...", "warning", page)
                            try:
                                page.reload()
                            except Exception:
                                pass
                        elif result == "waiting":
                            if sleep_with_stop(stop_event, 2):
                                break

                    # انتخاب شعبه
                    elif page.locator("#ctl00_ContentPlaceHolder1_ddlBranch").is_visible():
                        if stop_event.is_set():
                            break
                        self._process_branch_selection(page)

                    # اگر به لاگین پرت شد
                    elif page.locator("#ctl00_ContentPlaceHolder1_btnLogin").is_visible():
                        if stop_event.is_set():
                            break
                        self._perform_login_standard(page, captcha_mode)

                    # موفقیت نهایی
                    elif page.locator("#ctl00_ContentPlaceHolder1_lblTrackingCode").is_visible():
                        code = page.locator("#ctl00_ContentPlaceHolder1_lblTrackingCode").inner_text()
                        self.log(f"✅ کد رهگیری: {code}", "success")
                        DBHandler.save_success_data(self.nid, code)
                        return

                    if sleep_with_stop(stop_event, 0.5):
                        break

            except PlaywrightError as pe:
                if self.close_browser_on_stop(stop_event, playwright, browser, context, page):
                    return
                if "Target closed" in str(pe):
                    self.log("🛑 مرورگر بسته شد.", "stopped")
                    stop_event.set()
                    return
                if sleep_with_stop(stop_event, 2):
                    break
            except BrowserLaunchError as exc:
                if self.close_browser_on_stop(stop_event, playwright, browser, context, page):
                    return
                self.log(f"خطا در مرورگر: {exc.message}", "error")
                if sleep_with_stop(stop_event, 2):
                    break
            except Exception:
                if self.close_browser_on_stop(stop_event, playwright, browser, context, page):
                    return
                if sleep_with_stop(stop_event, 2):
                    break
            finally:
                if stop_event.is_set():
                    try:
                        self.log("🛑 Stop detected - closing browser now.", "warning")
                    except Exception:
                        pass
                close_browser(playwright, browser, context, page)

            if not stop_event.is_set():
                sleep_with_stop(stop_event, 2)

    def _get_otp_from_db_fresh(self):
        try:
            row = DBHandler.get_applicant(self.nid)
            if row and row["data"]:
                data = json.loads(row["data"])
                return data.get("otp_code")
        except Exception:
            pass
        return None

    def _process_bank_selection_v2(self, page):
        try:
            dropdown_id = "#ctl00_ContentPlaceHolder1_ddlBankName"
            options = page.locator(f"{dropdown_id} option").all()
            available_banks = {}

            for opt in options:
                val = opt.get_attribute("value")
                txt = opt.inner_text().strip()
                if val and val != "0":
                    available_banks[txt] = val

            user_priorities = self.user_data.get("banks", [])
            if not user_priorities:
                self.log("⚠️ لیست اولویت بانک خالی است!", "error")
                return "error"

            for priority in user_priorities:
                target_name = priority["name"] if isinstance(priority, dict) else priority

                found_val = None
                for b_text, b_val in available_banks.items():
                    if target_name in b_text:
                        found_val = b_val
                        break

                if found_val:
                    self.log(f"🎯 بانک یافت شد: {target_name}", "selecting", page)
                    page.select_option(dropdown_id, value=found_val)
                    self.log("⏳ در حال بارگذاری شعب...", "info", page)
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        time.sleep(3)
                    return "success"

            return "no_match"
        except Exception:
            return "error"

    def _process_branch_selection(self, page):
        try:
            branch_ddl = "#ctl00_ContentPlaceHolder1_ddlBranch"
            if page.locator(branch_ddl).input_value() == "0":
                page.locator(branch_ddl).select_option(index=1)
                if self.settings.get("final_submit", False):
                    self.log("🔥 ثبت نهایی...", "success", page)
                    page.click("#ctl00_ContentPlaceHolder1_btnRegister")
                else:
                    self.log("🛑 توقف قبل از ثبت نهایی (حالت تست)", "warning", page)
                    time.sleep(5)
        except Exception:
            pass

    def _solve_captcha_wrapper(self, page, input_sel, btn_sel, mode):
        try:
            captcha_img = page.locator(".BDC_CaptchaImage").first
            if not captcha_img.is_visible():
                return False

            if page.locator(input_sel).input_value() and mode == "robot":
                page.locator(btn_sel).click()
                return True

            code = self.captcha_service.solve(captcha_img.screenshot(), mode="general")

            if code and len(code) >= 4:
                self.log(f"🧩 حل شد: {code}", "info", page)
                inp = page.locator(input_sel)
                inp.clear()

                if mode == "human":
                    inp.type(code, delay=random.randint(150, 300))
                    time.sleep(0.5)
                    page.locator(btn_sel).click(delay=random.randint(50, 150))
                else:
                    inp.fill(code)
                    page.locator(btn_sel).click()
                return True
            else:
                self.log("❌ خطا در خواندن. رفرش...", "warning", page)
                try:
                    page.locator(".BDC_ReloadLink").first.click()
                except Exception:
                    pass
                time.sleep(1.5)
                return False
        except Exception:
            return False

    def _perform_login_standard(self, page, mode):
        try:
            if not page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").input_value():
                page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").fill(self.nid)
            self._solve_captcha_wrapper(
                page,
                "#ctl00_ContentPlaceHolder1_tbCaptcha",
                "#ctl00_ContentPlaceHolder1_btnLogin",
                mode,
            )
        except Exception:
            pass
