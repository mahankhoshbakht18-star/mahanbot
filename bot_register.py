# bot_register.py
import time
import random
import re
from bot_core import BotCore
from bot_helpers import sleep_with_stop, detect_captcha_failure
from browser_actions import BrowserActions
from browser_launcher import BrowserLaunchError, close_browser, safe_goto
from database import DBHandler
from bot_select_captcha import wait_for_manual_captcha_handoff

TARGET_URL = "https://ve.cbi.ir/Register.aspx"
CHILD_REGISTER_URL = "https://ve.cbi.ir/ChildRegister.aspx"


class RegistrationLoanAdapter:
    """Adapter separating initial Registration forms for Marriage Loan (وام ازدواج) vs Childbearing Loan (وام فرزندآوری)."""

    def __init__(self, loan_type: str = "marriage", user_data: dict = None):
        raw = str(loan_type or (user_data or {}).get("loan_type") or "marriage").strip().lower()
        if raw in {"child", "childbearing", "farzand", "فرزند", "فرزندآوری"}:
            self.kind = "childbearing"
            self.target_url = str((user_data or {}).get("register_url") or CHILD_REGISTER_URL)
        else:
            self.kind = "marriage"
            self.target_url = TARGET_URL

    def validate_data(self, nid: str, data: dict) -> tuple[bool, str]:
        clean_nid = str(nid or "").strip()
        if not clean_nid.isdigit() or len(clean_nid) != 10:
            return False, "کد ملی متقاضی باید دقیقاً ۱۰ رقم باشد."
        zip_code = str(data.get("postal_code") or data.get("zip_code") or "").strip()
        if zip_code and (not zip_code.isdigit() or len(zip_code) != 10):
            return False, "کد پستی باید دقیقاً ۱۰ رقم باشد."
        if self.kind == "marriage":
            spouse = str(data.get("spouse_national_id") or "").strip()
            if spouse and (not spouse.isdigit() or len(spouse) != 10):
                return False, "کد ملی همسر باید دقیقاً ۱۰ رقم باشد."
        elif self.kind == "childbearing":
            child_nid = str(data.get("child_national_id") or "").strip()
            if child_nid and (not child_nid.isdigit() or len(child_nid) != 10):
                return False, "کد ملی فرزند باید دقیقاً ۱۰ رقم باشد."
        return True, "ok"


class RegistrationBot(BotCore):
    def __init__(self, nid, settings, log_callback=None, captcha_service=None, browser_profile=None):
        super().__init__(
            nid,
            settings,
            log_callback=log_callback,
            captcha_service=captcha_service,
            browser_profile=browser_profile,
        )

    def run(self, stop_event, loan_type: str = None):
        self._active_stop_event = stop_event
        attempt = 0
        playwright = None
        browser = None
        context = None
        page = None

        # Prevent duplicate registration if already confirmed
        existing_tracking = DBHandler.has_final_submission(self.nid)
        if existing_tracking:
            self.emit_event(
                "FINAL_SUBMIT_CONFIRMED",
                step="DONE",
                reason="already_registered",
                result=f"tracking={self._mask(existing_tracking)}",
                message=f"ثبت‌نام این متقاضی قبلاً انجام شده است (کد پیگیری: {self._mask(existing_tracking)}).",
                level="success",
            )
            return

        adapter = RegistrationLoanAdapter(loan_type=loan_type, user_data=getattr(self, "user_data", {}))
        valid, err_msg = adapter.validate_data(self.nid, getattr(self, "user_data", {}))
        if not valid:
            self.set_runtime_state("ERROR", "VALIDATION_ERROR", browser_status="CLOSED", last_error=err_msg)
            DBHandler.update_status(self.nid, "خطای اطلاعات", err_msg)
            self.log(f"❌ {err_msg}", "error")
            return

        target_url = adapter.target_url

        try:
            playwright, browser, context, page = self.setup_browser(stop_event)
            if not page or stop_event.is_set():
                return

            try:
                safe_goto(page, target_url, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)
            except Exception:
                if sleep_with_stop(stop_event, 1.0):
                    return
                try:
                    safe_goto(page, target_url, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)
                except Exception:
                    return

            self.log(f"🚀 ربات ثبت‌نام ({adapter.kind}) شروع شد.", "info", page)
            captcha_mode = "model"
            allow_final_submit = bool(self.settings.get("auto_final_submit", self.settings.get("final_submit", False)))

            while attempt < self.retry_limit:
                if stop_event.is_set():
                    break

                attempt += 1

                try:
                    if stop_event.is_set():
                        break

                    current_url = page.url or ""
                    if current_url == "about:blank" or ("Register.aspx" not in current_url and "ChildRegister.aspx" not in current_url):
                        try:
                            safe_goto(page, target_url, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)
                        except Exception:
                            if sleep_with_stop(stop_event, 0.8):
                                break
                            continue

                    if stop_event.is_set():
                        break

                    if self.watchdog_check(
                        page,
                        stop_event,
                        selectors=[
                            "#ctl00_ContentPlaceHolder1_tbIDNo",
                            "#ctl00_ContentPlaceHolder1_tbMobileConfCode",
                            "#ctl00_ContentPlaceHolder1_btnContinue1",
                        ],
                    ):
                        continue

                    if self.otp_retry_exceeded():
                        self.log("⛔ OTP retry limit reached; stopping job.", "error", page)
                        DBHandler.update_status(self.nid, "Stopped", "OTP retry limit reached")
                        stop_event.set()
                        break

                    if self.consume_recovery_flag("otp_expired"):
                        if self.otp_retry_exceeded():
                            self.log("⛔ OTP retry limit reached; stopping job.", "error", page)
                            DBHandler.update_status(self.nid, "Stopped", "OTP retry limit reached")
                            stop_event.set()
                            break
                        try:
                            page.reload()
                        except Exception:
                            pass
                        continue

                    if self.consume_recovery_flag("captcha_invalid"):
                        if self._captcha_retry <= self._captcha_retry_limit:
                            self._refresh_captcha_in_place(page)
                        else:
                            if bool(self.settings.get("pause_for_manual_captcha", True)):
                                wait_for_manual_captcha_handoff(
                                    self,
                                    page,
                                    "#ctl00_ContentPlaceHolder1_tbCaptcha1",
                                    "#ctl00_ContentPlaceHolder1_btnSendConfirmCode",
                                    stop_event,
                                    source="register_captcha",
                                )
                            else:
                                self._captcha_retry = 0
                                try:
                                    page.reload()
                                except Exception:
                                    pass
                        continue

                    if self.consume_recovery_flag("generic_error"):
                        try:
                            self._dump_state(
                                page,
                                selectors=[
                                    "#ctl00_ContentPlaceHolder1_tbIDNo",
                                    "#ctl00_ContentPlaceHolder1_tbMobileConfCode",
                                    "#ctl00_ContentPlaceHolder1_btnContinue1",
                                ],
                                reason="dialog_generic",
                            )
                        except Exception:
                            pass
                        continue

                    self.dismiss_modals(page)

                    if self.is_firewall_challenge(page):
                        DBHandler.update_status(self.nid, "منتظر CAPTCHA", "Manual firewall CAPTCHA entry required")
                        if not self._handle_firewall_challenge(page, stop_event):
                            break
                        DBHandler.update_status(self.nid, "Ready", "Firewall challenge cleared")
                        continue

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
                        self.mark_progress("step1_form")
                        if stop_event.is_set():
                            break

                        self._wait_for_ready(page)
                        if not self._input_has_value(page, ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name='ctl00$ContentPlaceHolder1$tbIDNo']"]):
                            self.log("📝 مرحله ۱: پر کردن فرم هویتی", "registering", page)
                            self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name='ctl00$ContentPlaceHolder1$tbIDNo']"], self.nid)
                            self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbFName", "input[name='ctl00$ContentPlaceHolder1$tbFName']"], first_name)
                            self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbLName", "input[name='ctl00$ContentPlaceHolder1$tbLName']"], last_name)

                            if adapter.kind == "marriage":
                                spouse = str(d.get("spouse_national_id") or "").strip()
                                if spouse.isdigit() and len(spouse) == 10:
                                    self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbIDNo2", "input[name='ctl00$ContentPlaceHolder1$tbIDNo2']"], spouse)
                            elif adapter.kind == "childbearing":
                                child_nid = str(d.get("child_national_id") or "").strip()
                                if child_nid.isdigit() and len(child_nid) == 10:
                                    self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbChildIDNo", "input[name*='tbChildIDNo']"], child_nid)
                                child_by, child_bm, child_bd = (
                                    d.get("child_birth_y") or d.get("child_birth_year"),
                                    d.get("child_birth_m") or d.get("child_birth_month"),
                                    d.get("child_birth_d") or d.get("child_birth_day"),
                                )
                                if child_by and self._is_visible(page, "#ctl00_ContentPlaceHolder1_tbChildBrYear"):
                                    self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbChildBrYear"], str(child_by))
                                    self._select_option(page, "#ctl00_ContentPlaceHolder1_ddlChildBrMonth", value=str(child_bm).zfill(2) if child_bm else None)
                                    self._select_option(page, "#ctl00_ContentPlaceHolder1_ddlChildBrDay", value=str(child_bd).zfill(2) if child_bd else None)

                            birth_y, birth_m, birth_d = self._get_birth_parts(d)
                            if birth_y:
                                self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbBrYear", "input[name='ctl00$ContentPlaceHolder1$tbBrYear']"], birth_y)
                                self._select_option(page, "#ctl00_ContentPlaceHolder1_ddlBrMonth", value=str(birth_m).zfill(2) if birth_m else None)
                                self._select_option(page, "#ctl00_ContentPlaceHolder1_ddlBrDay", value=str(birth_d).zfill(2) if birth_d else None)

                            if adapter.kind == "marriage":
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

                        self.save_checkpoint("REGISTER_STEP_1", page=page, loan_type=adapter.kind, expected_next_step="REGISTER_STEP_2_OTP")
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
                        self.mark_progress("step2_otp")
                        if stop_event.is_set():
                            break

                        self.emit_event(
                            "OTP_REQUIRED",
                            step="REGISTER_STEP_2_OTP",
                            reason="otp_form_visible",
                            result="waiting_sms",
                            message="منتظر دریافت کد پیامکی تایید ثبت‌نام...",
                            level="info",
                            page=page,
                        )

                        if self._detect_otp_failure(page) or self.last_dialog_indicates_otp_invalid():
                            if not self.register_otp_failure("otp_invalid"):
                                self.log("⛔ OTP retry limit reached; stopping job.", "error", page)
                                DBHandler.update_status(self.nid, "Stopped", "OTP retry limit reached")
                                stop_event.set()
                                break
                            self.clear_otp_backend()
                            continue

                        otp = self.get_otp_code(stop_event=stop_event, timeout=int(self.settings.get("otp_validity_seconds", 120)))
                        if not otp:
                            try:
                                page.wait_for_timeout(random.randint(80, 160))
                            except Exception:
                                pass
                            continue

                        self.log("✅ کد پیامک دریافت شد و در فرم وارد می‌شود.", "success", page)
                        otp_selector = "#ctl00_ContentPlaceHolder1_tbMobileConfCode, input[name='ctl00$ContentPlaceHolder1$tbMobileConfCode']"
                        otp_value = str(otp).strip()
                        otp_locator = page.locator(otp_selector).first
                        try:
                            if otp_locator.count() > 0 and otp_locator.is_visible():
                                BrowserActions.human_fill(otp_locator, otp_value, timeout=1500)
                        except Exception:
                            pass

                        success = self._solve_captcha_wrapper(
                            page,
                            "#ctl00_ContentPlaceHolder1_tbCaptcha2",
                            "#ctl00_ContentPlaceHolder1_btnContinue1",
                            captcha_mode,
                        )
                        if not success:
                            continue
                        try:
                            page.wait_for_selector("#ctl00_ContentPlaceHolder1_btnContinue2, #ctl00_ContentPlaceHolder1_ddlBankName", timeout=5000, state="visible")
                        except Exception:
                            pass
                        continue

                    # ==========================
                    # STEP 3: اطلاعات سکونت و کد پستی
                    # ==========================
                    if self._is_visible(page, "#ctl00_ContentPlaceHolder1_btnContinue2"):
                        self.mark_progress("step3_residence")
                        if stop_event.is_set():
                            break

                        self.log("📍 مرحله ۳: اطلاعات تکمیلی و کد پستی", "registering", page)
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
                                self.wait_for_aspnet_postback(page, timeout_ms=10000)
                                try:
                                    page.wait_for_function(
                                        "document.getElementById('ctl00_ContentPlaceHolder1_ddlCity').options.length > 1",
                                        timeout=15000,
                                    )
                                except Exception:
                                    if sleep_with_stop(stop_event, 3):
                                        break
                        else:
                            self.log(f"⚠️ استان '{user_state}' پیدا نشد", "error", page)

                        user_city = d.get("city")
                        if user_city:
                            city_val = self._find_select_value(page, "#ctl00_ContentPlaceHolder1_ddlCity", user_city)
                            if city_val:
                                self.log(f"شهر: {user_city}", "info", page)
                                page.select_option("#ctl00_ContentPlaceHolder1_ddlCity", value=city_val)
                                self.wait_for_aspnet_postback(page, timeout_ms=8000)
                            else:
                                self.log(f"⚠️ شهر '{user_city}' پیدا نشد", "warning", page)

                        if d.get("phone"):
                            self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbTel", "input[name='ctl00$ContentPlaceHolder1$tbTel']"], d.get("phone"))
                        zip_val = str(d.get("postal_code") or d.get("zip_code") or "").strip()
                        if zip_val:
                            if zip_val.isdigit() and len(zip_val) < 10:
                                zip_val = zip_val.zfill(10)
                            self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbZipCD", "input[name='ctl00$ContentPlaceHolder1$tbZipCD']"], zip_val)

                        if d.get("address") and self._is_visible(page, "#ctl00_ContentPlaceHolder1_tbAddress"):
                            self._fill_text(page, ["#ctl00_ContentPlaceHolder1_tbAddress"], d.get("address"))

                        if stop_event.is_set():
                            break

                        self.save_checkpoint("REGISTER_STEP_3", page=page, state=user_state or "", city=user_city or "", expected_next_step="FINAL_SUBMIT")
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

                        self.log("🏁 مرحله نهایی ثبت‌نام", "registering", page)
                        chk = page.locator("#ctl00_ContentPlaceHolder1_chkBoxWarning")
                        if chk.count() > 0 and not chk.is_checked():
                            chk.click(force=True)

                        if not allow_final_submit:
                            self.set_runtime_state("WAITING_USER_INPUT", "FINAL_SUBMIT_WAIT", browser_status="OPEN")
                            DBHandler.update_status(self.nid, "منتظر تایید ثبت نهایی", "فرم آماده ثبت نهایی است (auto_final_submit=false)")
                            self.log("🛑 توقف قبل از ثبت نهایی (ثبت نهایی خودکار غیرفعال است).", "warning", page)
                            return

                        self.emit_event(
                            "FINAL_SUBMIT_STARTED",
                            step="REGISTER_FINAL_SUBMIT",
                            reason=f"loan_type={adapter.kind}",
                            result="submitting",
                            message="در حال ارسال نهایی فرم ثبت‌نام...",
                            level="info",
                            page=page,
                        )
                        page.locator("#ctl00_ContentPlaceHolder1_btnSave").click()
                        self.wait_for_aspnet_postback(page, timeout_ms=10000)
                        if sleep_with_stop(stop_event, 2):
                            break
                        continue

                    # بررسی موفقیت و کد رهگیری رسمی
                    if "ShowTrackingCode" in page.url or self._is_visible(page, "span:has-text('کد رهگیری')") or self._is_visible(page, "#ctl00_ContentPlaceHolder1_lblTrackingCode"):
                        try:
                            full_text = page.locator("body").text_content()
                            match = re.search(r"کد\s*رهگیری\s*[:\-\s]*(\d{6,20})", full_text or "")
                            code = match.group(1) if match else "CONFIRMED"
                            DBHandler.save_success_data(self.nid, code)
                            self.emit_event(
                                "FINAL_SUBMIT_CONFIRMED",
                                step="DONE",
                                reason="tracking_code_received",
                                result=f"tracking={self._mask(code)}",
                                message=f"🎉 ثبت‌نام موفق! کد رهگیری: {self._mask(code)}",
                                level="success",
                                page=page,
                            )
                        except Exception:
                            self.log("🎉 ثبت نام موفق!", "success", page)
                        return

                except Exception:
                    if self.close_browser_on_stop(stop_event, playwright, browser, context, page):
                        return
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

    def _refresh_captcha_in_place(self, page):
        try:
            reload_btn = page.locator(".BDC_ReloadLink").first
            if reload_btn.count() and reload_btn.is_visible():
                reload_btn.click(timeout=1000)
                return True
        except Exception:
            pass
        return False

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
            BrowserActions.human_fill(locator, value)
        except Exception:
            try:
                BrowserActions.force_fill(locator, value)
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

    def _handle_firewall_challenge(self, page, stop_event) -> bool:
        self.emit_event(
            "CAPTCHA_MANUAL_REQUIRED",
            step="FIREWALL_CHALLENGE",
            reason="firewall_detected",
            result="waiting_manual_input",
            message="🛡️ چالش فایروال شناسایی شد؛ منتظر ورود دستی کپچا هستم.",
            level="warning",
            page=page,
        )
        timeout_seconds = int(self.settings.get("firewall_manual_timeout", 300))
        self.suspend_watchdog(timeout_seconds + 15)
        deadline = time.time() + timeout_seconds
        next_wait_log_at = 0.0

        while not stop_event.is_set():
            try:
                nid_input = page.locator("#ctl00_ContentPlaceHolder1_tbIDNo")
                if nid_input.is_visible() and nid_input.is_enabled():
                    self.log("✅ چالش فایروال پس از ورود کاربر رفع شد؛ ادامهٔ عملیات.", "success", page)
                    return True
            except Exception:
                pass

            if time.time() > deadline:
                self.log("⛔ Firewall challenge timeout.", "error", page)
                DBHandler.update_status(self.nid, "FIREWALL_TIMEOUT", "Manual firewall CAPTCHA timeout")
                return False

            now = time.time()
            if now >= next_wait_log_at:
                self.log("Waiting...", "warning", page)
                next_wait_log_at = now + 15.0

            if sleep_with_stop(stop_event, 0.8):
                return False
        return False

    def _detect_otp_failure(self, page) -> bool:
        modal_selectors = [
            "[role='dialog']",
            "[role='alert']",
            ".alert",
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
                text = target.inner_text()
                if "منقضی" in text or "نامعتبر" in text:
                    self.dismiss_modals(page)
                    return True
            except Exception:
                continue
        return False

    def _solve_captcha_wrapper(self, page, input_sel, btn_sel, mode):
        del mode
        max_retries = max(1, int(self.settings.get("captcha_max_retries", 3)))
        for _ in range(max_retries):
            if self._active_stop_event and self._active_stop_event.is_set():
                return False
            if self.solve_captcha_with_model(
                page,
                input_sel,
                btn_sel,
                stop_event=self._active_stop_event,
                source="register",
            ):
                if not detect_captcha_failure(page):
                    return True
            self._refresh_captcha_in_place(page)

        if bool(self.settings.get("pause_for_manual_captcha", True)):
            return wait_for_manual_captcha_handoff(
                self,
                page,
                input_sel,
                btn_sel,
                self._active_stop_event,
                source="register_manual_fallback",
                success_selectors=(
                    "#ctl00_ContentPlaceHolder1_tbMobileConfCode",
                    "#ctl00_ContentPlaceHolder1_btnContinue2",
                    "#ctl00_ContentPlaceHolder1_btnSave",
                ),
            )
        return False

    def _handle_captcha_robot(self, page, input_selector, btn_selector):
        return self._solve_captcha_wrapper(page, input_selector, btn_selector, "model")

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
