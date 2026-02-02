# bot_register.py
import time
import random
import re
from bot_core import BotCore
from browser_actions import BrowserActions
from browser_launcher import BrowserLaunchError, close_browser, safe_goto
from database import DBHandler

TARGET_URL = "https://ve.cbi.ir/Register.aspx"


def sleep_with_stop(stop_event, seconds: float, step: float = 0.2) -> bool:
    """
    Returns True if stop_event was set during sleep.
    """
    if seconds <= 0:
        return stop_event.is_set()
    end = time.time() + seconds
    while time.time() < end:
        if stop_event.is_set():
            return True
        time.sleep(min(step, end - time.time()))
    return stop_event.is_set()


class RegistrationBot(BotCore):
    def run(self, stop_event):
        attempt = 0
        playwright = None
        browser = None
        context = None
        page = None

        try:
            playwright, browser, context, page = self.setup_browser(stop_event)
            if not page or stop_event.is_set():
                return

            # ============================================================
            # Dialog handler (dashboard-only logging)
            # ============================================================
            def handle_dialog(dialog):
                try:
                    msg = dialog.message()
                    self.log(f"❌ پیام سایت: {msg}", "error")
                    if any(x in msg for x in ["منقضی", "نامعتبر", "اشتباه", "صحیح نمی باشد"]):
                        DBHandler.clear_otp(self.nid)
                        self.log("♻️ کد نامعتبر از دیتابیس پاک شد.", "warning")
                    dialog.accept()
                except Exception as e:
                    print(f"Dialog Error: {e}")

            page.on("dialog", handle_dialog)

            # ✅ DIRECT NAVIGATION (must happen immediately)
            try:
                safe_goto(page, TARGET_URL, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)
            except Exception:
                # If first nav fails, retry quickly but respect stop
                if sleep_with_stop(stop_event, 1.0):
                    return
                try:
                    safe_goto(page, TARGET_URL, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)
                except Exception:
                    return

            self.log("🚀 ربات رجیستر شروع شد (Direct Navigation فعال است).", "info", page)
            captcha_mode = self.settings.get("captcha_mode", "human")
            allow_final_submit = self.settings.get("final_submit", False)

            while attempt < self.retry_limit:
                if stop_event.is_set():
                    break

                attempt += 1

                try:
                    # Ensure we stay on the right page (no manual navigation required)
                    if stop_event.is_set():
                        break

                    current_url = page.url or ""
                    if current_url == "about:blank" or "Register.aspx" not in current_url:
                        try:
                            safe_goto(page, TARGET_URL, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)
                        except Exception:
                            if sleep_with_stop(stop_event, 0.8):
                                break
                            continue

                    if stop_event.is_set():
                        break

                    self.solve_firewall(page)
                    if stop_event.is_set():
                        break

                    d = self.user_data

                    # ==========================
                    # STEP 1: فرم هویتی
                    # ==========================
                    if page.locator("#ctl00_ContentPlaceHolder1_btnSendConfirmCode").is_visible():
                        if stop_event.is_set():
                            break

                        if not page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").input_value():
                            self.log("📝 مرحله ۱: پر کردن فرم", "registering", page)
                            BrowserActions.force_fill(page.locator("#ctl00_ContentPlaceHolder1_tbIDNo"), self.nid)
                            if d.get("spouse"):
                                BrowserActions.force_fill(page.locator("#ctl00_ContentPlaceHolder1_tbIDNo2"), d.get("spouse"))

                            if d.get("birth_y"):
                                page.locator("#ctl00_ContentPlaceHolder1_tbBrYear").fill(str(d.get("birth_y")))
                                page.select_option(
                                    "#ctl00_ContentPlaceHolder1_ddlBrMonth", value=str(d.get("birth_m")).zfill(2)
                                )
                                page.select_option(
                                    "#ctl00_ContentPlaceHolder1_ddlBrDay", value=str(d.get("birth_d")).zfill(2)
                                )

                            if d.get("marriage_y"):
                                page.locator("#ctl00_ContentPlaceHolder1_tbMarrYear").fill(str(d.get("marriage_y")))
                                page.select_option(
                                    "#ctl00_ContentPlaceHolder1_ddlMarryMonth", value=str(d.get("marriage_m")).zfill(2)
                                )
                                page.select_option(
                                    "#ctl00_ContentPlaceHolder1_ddlMarryDay", value=str(d.get("marriage_d")).zfill(2)
                                )

                            BrowserActions.force_fill(
                                page.locator("#ctl00_ContentPlaceHolder1_tbMobileNo"), d.get("mobile", "")
                            )

                        if stop_event.is_set():
                            break

                        self._solve_captcha_wrapper(
                            page,
                            "#ctl00_ContentPlaceHolder1_tbCaptcha1",
                            "#ctl00_ContentPlaceHolder1_btnSendConfirmCode",
                            captcha_mode,
                        )
                        try:
                            page.wait_for_selector("#ctl00_ContentPlaceHolder1_tbMobileConfCode", timeout=5000)
                        except Exception:
                            pass
                        continue

                    # ==========================
                    # STEP 2: کد تایید
                    # ==========================
                    if page.locator("#ctl00_ContentPlaceHolder1_btnContinue1").is_visible():
                        if stop_event.is_set():
                            break

                        otp = self.get_otp_code()
                        if otp:
                            self.log(f"✅ ورود کد: {otp}", "success", page)
                            page.locator("#ctl00_ContentPlaceHolder1_tbMobileConfCode").fill(otp)
                            self._solve_captcha_wrapper(
                                page,
                                "#ctl00_ContentPlaceHolder1_tbCaptcha2",
                                "#ctl00_ContentPlaceHolder1_btnContinue1",
                                captcha_mode,
                            )
                            if sleep_with_stop(stop_event, 4):
                                break
                        else:
                            self.log("📩 منتظر کد پیامک...", "waiting sms", page)
                            if sleep_with_stop(stop_event, 3):
                                break
                        continue

                    # ==========================
                    # STEP 3: اطلاعات سکونت
                    # ==========================
                    if page.locator("#ctl00_ContentPlaceHolder1_btnContinue2").is_visible():
                        if stop_event.is_set():
                            break

                        self.log("📍 مرحله ۳: اطلاعات تکمیلی", "registering", page)

                        try:
                            page.select_option("#ctl00_ContentPlaceHolder1_ddlIsarST", value="0")
                        except Exception:
                            pass

                        try:
                            if page.locator("#ctl00_ContentPlaceHolder1_ddlSarbasiST").is_visible():
                                mil_status = str(d.get("military_status", "1"))
                                page.select_option("#ctl00_ContentPlaceHolder1_ddlSarbasiST", value=mil_status)
                        except Exception:
                            pass

                        user_state = d.get("state")
                        state_val = self._find_select_value(page, "#ctl00_ContentPlaceHolder1_ddlState", user_state)
                        if state_val:
                            if page.locator("#ctl00_ContentPlaceHolder1_ddlState").input_value() != state_val:
                                self.log(f"استان: {user_state}", "info", page)
                                page.select_option("#ctl00_ContentPlaceHolder1_ddlState", value=state_val)
                                self.log("⏳ صبر برای رفرش شهرها...", "info", page)
                                try:
                                    page.wait_for_function(
                                        "document.getElementById('ctl00_ContentPlaceHolder1_ddlCity').options.length > 1",
                                        timeout=15000,
                                    )
                                except Exception:
                                    if sleep_with_stop(stop_event, 5):
                                        break
                        else:
                            self.log(f"⚠️ استان '{user_state}' پیدا نشد", "error", page)

                        user_city = d.get("city")
                        if user_city:
                            city_val = self._find_select_value(page, "#ctl00_ContentPlaceHolder1_ddlCity", user_city)
                            if city_val:
                                self.log(f"شهر: {user_city}", "info", page)
                                page.select_option("#ctl00_ContentPlaceHolder1_ddlCity", value=city_val)
                            else:
                                self.log(f"⚠️ شهر '{user_city}' پیدا نشد", "warning", page)

                        if d.get("phone"):
                            page.locator("#ctl00_ContentPlaceHolder1_tbTel").fill(str(d.get("phone")), force=True)
                        if d.get("zip_code"):
                            page.locator("#ctl00_ContentPlaceHolder1_tbZipCD").fill(str(d.get("zip_code")), force=True)

                        if stop_event.is_set():
                            break

                        self._solve_captcha_wrapper(
                            page,
                            "#ctl00_ContentPlaceHolder1_tbCaptcha3",
                            "#ctl00_ContentPlaceHolder1_btnContinue2",
                            captcha_mode,
                        )
                        continue

                    # ==========================
                    # STEP 4: تایید نهایی
                    # ==========================
                    if page.locator("#ctl00_ContentPlaceHolder1_btnSave").is_visible():
                        if stop_event.is_set():
                            break

                        self.log("🏁 مرحله نهایی", "registering", page)
                        chk = page.locator("#ctl00_ContentPlaceHolder1_chkBoxWarning")
                        if not chk.is_checked():
                            chk.click(force=True)

                        if not allow_final_submit:
                            self.log("🛑 توقف (ثبت نهایی خاموش)", "stop", page)
                            if sleep_with_stop(stop_event, 10):
                                break
                            return

                        page.locator("#ctl00_ContentPlaceHolder1_btnSave").click()
                        self.log("💾 ثبت نهایی شد", "info", page)
                        if sleep_with_stop(stop_event, 10):
                            break
                        continue

                    # بررسی موفقیت
                    if "ShowTrackingCode" in page.url or page.locator("span:has-text('کد رهگیری')").is_visible():
                        try:
                            full_text = page.locator("body").text_content()
                            match = re.search(r"کد رهگیری\s*[:\-\s]*(\d{10})", full_text or "")
                            code = match.group(1) if match else "---"
                            DBHandler.save_success_data(self.nid, code)
                            self.log(f"🎉 ثبت موفق! کد: {code}", "success", page)
                        except Exception:
                            self.log("🎉 ثبت نام موفق!", "success", page)
                        return

                except Exception:
                    # strict stop checks around sleeps / retries
                    if sleep_with_stop(stop_event, 1):
                        break

        except BrowserLaunchError as exc:
            self.log(f"Fatal: {exc.message}", "error")
        except Exception as e:
            self.log(f"Fatal: {e}", "error")
        finally:
            if stop_event.is_set():
                try:
                    self.log("🛑 Stop detected - closing browser now.", "warning")
                except Exception:
                    pass
            close_browser(playwright, browser, context, page)
            self.log("مرورگر بسته شد.", "stop")

    # --- توابع کمکی ---
    def _solve_captcha_wrapper(self, page, input_sel, btn_sel, mode):
        if mode == "robot":
            return self._handle_captcha_robot(page, input_sel, btn_sel)
        else:
            return self._handle_captcha_human(page, input_sel, btn_sel)

    def _handle_captcha_human(self, page, input_selector, btn_selector):
        try:
            captcha_img = page.locator(".BDC_CaptchaImage").first
            if not captcha_img.is_visible():
                return False
            code = self.captcha_service.solve(captcha_img.screenshot(), mode="general")
            if code and len(code) >= 4:
                page.locator(input_selector).clear()
                page.locator(input_selector).type(code, delay=random.randint(100, 200))
                time.sleep(0.5)
                page.locator(btn_selector).click(delay=150)
                return True
            else:
                page.locator(".BDC_ReloadLink").first.click()
                time.sleep(1.5)
                return False
        except Exception:
            return False

    def _handle_captcha_robot(self, page, input_selector, btn_selector):
        try:
            captcha_img = page.locator(".BDC_CaptchaImage").first
            if not captcha_img.is_visible():
                return False
            code = self.captcha_service.solve(captcha_img.screenshot(), mode="general")
            if code and len(code) >= 4:
                page.locator(input_selector).fill(code)
                page.locator(btn_selector).click()
                return True
            else:
                page.locator(".BDC_ReloadLink").first.click()
                time.sleep(1)
                return False
        except Exception:
            return False

    def _normalize_text(self, text):
        if not text:
            return ""
        text = str(text)
        text = text.replace("ي", "ی").replace("ك", "ک")
        text = re.sub(r"\s+", "", text)
        text = text.replace("\u200c", "")
        return text

    def _find_select_value(self, page, selector, text_to_match):
        if not text_to_match:
            return None
        try:
            target = self._normalize_text(text_to_match)
            options = page.locator(f"{selector} option").all()
            for opt in options:
                opt_raw = opt.text_content()
                opt_norm = self._normalize_text(opt_raw)
                if opt_norm == target:
                    return opt.get_attribute("value")
                if len(target) > 3 and target in opt_norm:
                    if "سوران" in target and "سیب" not in target and "سیب" in opt_norm:
                        continue
                    return opt.get_attribute("value")
        except Exception:
            pass
        return None
