# bot_select.py
import time
import json
import random
from playwright.sync_api import Error as PlaywrightError
from bot_core import BotCore
from browser_launcher import BrowserLaunchError, close_browser, safe_goto
from browser_actions import BrowserActions
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
                def handle_partial_navigation_error(exc):
                    if "net::ERR_CONNECTION_CLOSED" in str(exc) and page.url.startswith(TARGET_URL):
                        self.log("⚠️ Connection closed but page appears loaded; continuing.", "warning", page)
                        return True
                    return False

                try:
                    safe_goto(page, TARGET_URL, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)
                except Exception as exc:
                    if handle_partial_navigation_error(exc):
                        pass
                    else:
                        if sleep_with_stop(stop_event, 1.0):
                            return
                        try:
                            safe_goto(
                                page,
                                TARGET_URL,
                                timeout=60000,
                                wait_until="domcontentloaded",
                                log_callback=self.log,
                            )
                        except Exception as exc_retry:
                            if handle_partial_navigation_error(exc_retry):
                                pass
                            else:
                                return
                else:
                    if sleep_with_stop(stop_event, 0.2):
                        return

                def wait_for_state(selector, label, timeout=2000):
                    if stop_event.is_set():
                        return False
                    self.log(f"🔍 Checking for {label}...", "info", page)
                    try:
                        page.wait_for_selector(selector, timeout=timeout, state="visible")
                        return True
                    except Exception:
                        return False

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
                    if wait_for_state("input[name$='tbIDNo']", "Login Form"):
                        if stop_event.is_set():
                            break
                        nid_locator = page.locator("input[name$='tbIDNo']")
                        if not nid_locator.input_value():
                            try:
                                nid_locator.fill(self.nid)
                            except Exception:
                                pass
                            if nid_locator.input_value() != self.nid:
                                BrowserActions.force_fill(nid_locator, self.nid)

                        self._solve_captcha_wrapper(
                            page,
                            "input[name$='tbCaptcha1']",
                            "input[name$='btnSendConfirmCode']",
                            captcha_mode,
                        )
                        try:
                            page.wait_for_selector("input[name$='tbMobileConfCode']", timeout=3000)
                        except Exception:
                            pass

                    # مرحله ۲: OTP
                    elif wait_for_state("input[name$='tbMobileConfCode']", "OTP Form"):
                        if stop_event.is_set():
                            break

                        otp_filled = False
                        while not stop_event.is_set():
                            if not page.locator("input[name$='tbMobileConfCode']").is_visible():
                                break
                            otp_code = self._get_otp_from_db_fresh()
                            if otp_code:
                                current_val = page.locator("input[name$='tbMobileConfCode']").input_value()
                                if current_val != otp_code:
                                    self.log(f"✅ دریافت کد پیامک: {otp_code}", "success", page)
                                    page.locator("input[name$='tbMobileConfCode']").fill(otp_code)
                                    if sleep_with_stop(stop_event, 0.5):
                                        break
                                otp_filled = True
                                break

                            self.log("📩 منتظر دریافت پیامک...", "waiting sms", page)
                            if sleep_with_stop(stop_event, 2):
                                break

                        if otp_filled:
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

                    # مرحله ۳: انتخاب بانک
                    elif wait_for_state("#ctl00_ContentPlaceHolder1_ddlBankName", "Bank Selection"):
                        if stop_event.is_set():
                            break
                        result = self._process_bank_selection_v2(page)
                        if result == "selected":
                            self.log("✅ بانک انتخاب شد.", "success", page)
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
                    elif wait_for_state("#ctl00_ContentPlaceHolder1_ddlBranch", "Branch Selection"):
                        if stop_event.is_set():
                            break
                        self._process_branch_selection(page, stop_event)

                    # اگر به لاگین پرت شد
                    elif wait_for_state("#ctl00_ContentPlaceHolder1_btnLogin", "Login Redirect"):
                        if stop_event.is_set():
                            break
                        self._perform_login_standard(page, captcha_mode)

                    # موفقیت نهایی
                    elif wait_for_state("#ctl00_ContentPlaceHolder1_lblTrackingCode", "Tracking Code"):
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
            with DBHandler._connect(row_factory=True) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT data FROM applicants WHERE national_id=?", (self.nid,))
                row = cursor.fetchone()
                if row and row["data"]:
                    data = json.loads(row["data"])
                    otp_code = data.get("otp_code")
                    return str(otp_code).strip() if otp_code else None
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

            if not available_banks:
                self.log("⚠️ لیست بانک‌ها خالی است. رفرش صفحه...", "warning", page)
                try:
                    page.reload()
                except Exception:
                    pass
                return "waiting"

            user_priorities = self.user_data.get("banks", [])
            if not user_priorities:
                self.log("⚠️ لیست اولویت بانک خالی است!", "error")
                return "error"

            for priority in user_priorities:
                target_name = priority["name"] if isinstance(priority, dict) else priority

                found_val = None
                for b_text, b_val in available_banks.items():
                    if target_name and target_name in b_text:
                        found_val = b_val
                        break

                if found_val:
                    self.log(f"🎯 بانک یافت شد: {target_name}", "selecting", page)
                    page.select_option(dropdown_id, value=found_val)
                    self.log("⏳ در حال بارگذاری شعب...", "info", page)
                    self._wait_for_branch_ready(page)
                    return "selected"

            self.log("⚠️ بانک موردنظر پیدا نشد. رفرش صفحه...", "warning", page)
            try:
                page.reload()
            except Exception:
                pass
            return "no_match"
        except Exception:
            return "error"

    def _process_branch_selection(self, page, stop_event):
        try:
            branch_ddl = "#ctl00_ContentPlaceHolder1_ddlBranch"
            branch_index = self.settings.get("branch_index", 1)
            try:
                branch_index = int(branch_index)
            except (TypeError, ValueError):
                branch_index = 1
            if branch_index < 1:
                branch_index = 1

            if page.locator(branch_ddl).input_value() == "0":
                page.locator(branch_ddl).select_option(index=branch_index)
                if self.settings.get("final_submit", False):
                    self.log("🔥 ثبت نهایی...", "success", page)
                    page.click("#ctl00_ContentPlaceHolder1_btnSave")
                else:
                    submit_btn = page.locator("#ctl00_ContentPlaceHolder1_btnSave")
                    try:
                        submit_btn.scroll_into_view_if_needed()
                        page.evaluate(
                            "btn => btn.style.border = '3px solid red'",
                            submit_btn.element_handle(),
                        )
                    except Exception:
                        pass
                    self.log("🛑 توقف قبل از ثبت نهایی (حالت تست)", "warning", page)
                    while not stop_event.is_set():
                        time.sleep(1)
        except Exception:
            pass

    def _wait_for_branch_ready(self, page):
        branch_ddl = "#ctl00_ContentPlaceHolder1_ddlBranch"
        try:
            page.wait_for_selector(branch_ddl, timeout=10000)
            page.wait_for_function(
                "(selector) => {"
                "const el = document.querySelector(selector);"
                "return el && !el.disabled && el.offsetParent !== null;"
                "}",
                branch_ddl,
                timeout=10000,
            )
        except Exception:
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                time.sleep(2)

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
