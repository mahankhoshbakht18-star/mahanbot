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
    def __init__(self, nid, settings, log_callback=None, captcha_service=None, browser_profile=None):
        super().__init__(
            nid,
            settings,
            log_callback=log_callback,
            captcha_service=captcha_service,
            browser_profile=browser_profile,
        )

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
                    full_name = str(d.get("full_name") or "").strip()
                    first_name, last_name = self._split_full_name(d, full_name)

                    # ==========================
                    # STEP 1: فرم هویتی
                    # ==========================
                    if self._is_visible(page, "#ctl00_ContentPlaceHolder1_btnSendConfirmCode"):
                        if stop_event.is_set():
                            break

                        self._wait_for_ready(page)
                        if not self._input_has_value(page, ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name='ctl00$ContentPlaceHolder1$tbIDNo']"]):
                            self.log("📝 مرحله ۱: پر کردن فرم", "registering", page)
                            self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name='ctl00$ContentPlaceHolder1$tbIDNo']"], self.nid)
                            self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbFName", "input[name='ctl00$ContentPlaceHolder1$tbFName']"], first_name)
                            self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbLName", "input[name='ctl00$ContentPlaceHolder1$tbLName']"], last_name)

                            spouse = d.get("spouse")
                            if spouse:
                                self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbIDNo2", "input[name='ctl00$ContentPlaceHolder1$tbIDNo2']"], spouse)

                            birth_y, birth_m, birth_d = self._get_birth_parts(d)
                            if birth_y:
                                self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbBrYear", "input[name='ctl00$ContentPlaceHolder1$tbBrYear']"], birth_y)
                                self._select_option(page, "#ctl00_ContentPlaceHolder1_ddlBrMonth", value=str(birth_m).zfill(2) if birth_m else None)
                                self._select_option(page, "#ctl00_ContentPlaceHolder1_ddlBrDay", value=str(birth_d).zfill(2) if birth_d else None)

                            marriage_y, marriage_m, marriage_d = self._get_marriage_parts(d)
                            if marriage_y:
                                self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbMarrYear", "input[name='ctl00$ContentPlaceHolder1$tbMarrYear']"], marriage_y)
                                self._select_option(page, "#ctl00_ContentPlaceHolder1_ddlMarryMonth", value=str(marriage_m).zfill(2) if marriage_m else None)
                                self._select_option(page, "#ctl00_ContentPlaceHolder1_ddlMarryDay", value=str(marriage_d).zfill(2) if marriage_d else None)

                            self._fill_text(
                                page,
                                ["#ctl00_ContentPlaceHolder1_tbMobileNo", "input[name='ctl00$ContentPlaceHolder1$tbMobileNo']"],
                                d.get("mobile", ""),
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
                            page.wait_for_selector("#ctl00_ContentPlaceHolder1_tbMobileConfCode", timeout=10000, state="visible")
                        except Exception:
                            pass
                        continue

                    # ==========================
                    # STEP 2: کد تایید
                    # ==========================
                    if self._is_visible(page, "#ctl00_ContentPlaceHolder1_btnContinue1"):
                        if stop_event.is_set():
                            break

                        otp = self.wait_for_otp(stop_event, timeout=65)
                        if otp:
                            self.log(f"? ???? ??: {otp}", "success", page)
                            self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbMobileConfCode", "input[name='ctl00$ContentPlaceHolder1$tbMobileConfCode']"], otp)
                            try:
                                page.wait_for_timeout(500)
                            except Exception:
                                pass
                            success = self._solve_captcha_wrapper(
                                page,
                                "#ctl00_ContentPlaceHolder1_tbCaptcha2",
                                "#ctl00_ContentPlaceHolder1_btnContinue1",
                                captcha_mode,
                            )
                            if success and sleep_with_stop(stop_event, 2):
                                break
                        else:
                            self.log("?? ????? ?? ?????...", "waiting sms", page)
                        continue
                        continue

                    # ==========================
                    # STEP 3: اطلاعات سکونت
                    # ==========================
                    if self._is_visible(page, "#ctl00_ContentPlaceHolder1_btnContinue2"):
                        if stop_event.is_set():
                            break

                        self.log("📍 مرحله ۳: اطلاعات تکمیلی", "registering", page)
                        self._wait_for_ready(page)

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
                            self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbTel", "input[name='ctl00$ContentPlaceHolder1$tbTel']"], d.get("phone"))
                        if d.get("zip_code"):
                            self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbZipCD", "input[name='ctl00$ContentPlaceHolder1$tbZipCD']"], d.get("zip_code"))

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
                    if self._is_visible(page, "#ctl00_ContentPlaceHolder1_btnSave"):
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
                    if self.close_browser_on_stop(stop_event, playwright, browser, context, page):
                        return
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
    def _wait_for_ready(self, page):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass

    def _is_visible(self, page, selector):
        try:
            return page.locator(selector).first.is_visible()
        except Exception:
            return False

    def _input_has_value(self, page, selectors):
        locator = self._find_locator(page, selectors)
        try:
            return bool(locator and locator.input_value())
        except Exception:
            return False

    def _find_locator(self, page, selectors):
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.count() > 0:
                    return locator
            except Exception:
                continue
        return page.locator(selectors[0]).first

    def _fill_text(self, page, selectors, value):
        if value is None or value == "":
            return
        locator = self._find_locator(page, selectors)
        try:
            locator.wait_for(state="visible", timeout=10000)
        except Exception:
            return
        try:
            BrowserActions.force_fill(locator, value)
        except Exception:
            try:
                locator.fill(str(value))
            except Exception:
                pass

    def _select_option(self, page, selector, value=None):
        if not value:
            return
        try:
            page.locator(selector).wait_for(state="visible", timeout=10000)
            page.select_option(selector, value=value)
        except Exception:
            pass

    def _get_birth_parts(self, data):
        return (
            data.get("birth_y") or data.get("birth_year"),
            data.get("birth_m") or data.get("birth_month"),
            data.get("birth_d") or data.get("birth_day"),
        )

    def _get_marriage_parts(self, data):
        return (
            data.get("marriage_y") or data.get("marriage_year"),
            data.get("marriage_m") or data.get("marriage_month"),
            data.get("marriage_d") or data.get("marriage_day"),
        )

    def _split_full_name(self, data, fallback):
        first = data.get("first_name") or data.get("fname")
        last = data.get("last_name") or data.get("lname")
        if first or last:
            return (first or "").strip(), (last or "").strip()
        name = fallback.strip()
        if not name:
            return "", ""
        parts = name.split()
        if len(parts) == 1:
            return parts[0], ""
        return " ".join(parts[:-1]), parts[-1]
    def _solve_captcha_wrapper(self, page, input_sel, btn_sel, mode):
        if mode == "robot":
            return self._handle_captcha_robot(page, input_sel, btn_sel)
        else:
            return self._handle_captcha_human(page, input_sel, btn_sel)

    def _detect_captcha_failure(self, page) -> bool:
        phrases = [
            "کد امنیتی اشتباه",
            "کد امنیتی صحیح",
            "کد امنیتی نادرست",
            "security code",
        ]
        try:
            content = page.content()
        except Exception:
            return False
        return any(phrase in content for phrase in phrases)

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
                time.sleep(1)
                result = "failed" if self._detect_captcha_failure(page) else "success"
                DBHandler.append_captcha_attempt(self.nid, code, result, source="register")
                return True
            else:
                page.locator(".BDC_ReloadLink").first.click()
                time.sleep(1.5)
                if code:
                    DBHandler.append_captcha_attempt(self.nid, code, "failed", source="register")
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
                time.sleep(1)
                result = "failed" if self._detect_captcha_failure(page) else "success"
                DBHandler.append_captcha_attempt(self.nid, code, result, source="register")
                return True
            else:
                page.locator(".BDC_ReloadLink").first.click()
                time.sleep(1)
                if code:
                    DBHandler.append_captcha_attempt(self.nid, code, "failed", source="register")
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
