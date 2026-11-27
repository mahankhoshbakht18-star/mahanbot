# bot_register.py
import time
import random
import re
from bot_core import BotCore
from bot_helpers import sleep_with_stop, detect_captcha_failure
from browser_actions import BrowserActions
from browser_launcher import BrowserLaunchError, close_browser, safe_goto
from core.helpers import (
    _normalize_nid,
    normalize_applicant_payload,
    validate_applicant_save,
    validate_registration_readiness,
)
from core.persian_normalizer import normalize_compact_key, normalize_location_key, to_ascii_digits
from database import DBHandler
try:
    from bot_select_captcha import wait_for_manual_captcha_handoff
except ImportError:
    wait_for_manual_captcha_handoff = None

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

    def validate_data(self, nid: str, data: dict, *, strict: bool = False) -> tuple[bool, str]:
        if strict:
            applicant_view = {
                "national_id": nid,
                "full_name": (data or {}).get("full_name", ""),
                "data": dict(data or {}),
            }
            ok, errors, _ = validate_registration_readiness(
                applicant_view, expected_nid=nid, loan_type_override=self.kind
            )
            return (True, "ok") if ok else (False, "؛ ".join(errors))

        clean_nid = _normalize_nid(nid) if to_ascii_digits(str(nid or "")).strip() else ""
        _, _, norm = normalize_applicant_payload((data or {}).get("full_name", "applicant"), clean_nid, data or {})
        if not (clean_nid.isdigit() and len(clean_nid) == 10):
            return False, "کد ملی متقاضی باید دقیقاً ۱۰ رقم باشد."
        zip_code = norm.get("postal_code", "")
        if zip_code and (not zip_code.isdigit() or len(zip_code) != 10):
            return False, "کد پستی باید دقیقاً ۱۰ رقم باشد."
        if self.kind == "marriage":
            spouse = norm.get("spouse_national_id", "")
            if spouse and (not spouse.isdigit() or len(spouse) != 10):
                return False, "کد ملی همسر باید دقیقاً ۱۰ رقم باشد."
        elif self.kind == "childbearing":
            child_nid = norm.get("child_national_id", "")
            if child_nid and (not child_nid.isdigit() or len(child_nid) != 10):
                return False, "کد ملی فرزند باید دقیقاً ۱۰ رقم باشد."
        errs = validate_applicant_save("placeholder", clean_nid, norm)
        if errs:
            return False, errs[0]
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

        # Always reload fresh applicant record before validation/execution.
        self.nid = _normalize_nid(self.nid) or str(self.nid or "").strip()
        user_rec = DBHandler.get_applicant(self.nid)
        user_st = str(dict(user_rec).get("status") if user_rec else "").lower()
        if user_st in {"success", "completed", "registered"}:
            existing_tracking = DBHandler.has_final_submission(self.nid)
            if existing_tracking:
                self.log(f"این متقاضی با کد پیگیری {self._mask(existing_tracking)} قبلاً در سامانه ثبت شده است. برای ثبت مجدد، پاکسازی کد رهگیری را انجام دهید.", "info")
                return

        self.user_data = self._load_user_data()
        adapter = RegistrationLoanAdapter(loan_type=loan_type, user_data=self.user_data)
        valid, reg_errors, prepared_data = validate_registration_readiness(
            dict(user_rec) if user_rec else None,
            expected_nid=self.nid,
            loan_type_override=adapter.kind,
        )
        if not valid:
            err_msg = "اطلاعات ثبت‌نام اولیه ناقص یا نامعتبر است: " + "؛ ".join(reg_errors)
            self.set_runtime_state("ERROR", "VALIDATION_ERROR", browser_status="CLOSED", last_error=err_msg)
            DBHandler.update_status(self.nid, "خطای اطلاعات", err_msg)
            self.log(f"❌ {err_msg}", "error")
            return
        self.user_data = prepared_data
        loaded_fields = [
            label for key, label in (
                ("full_name", "نام"),
                ("mobile", "موبایل"),
                ("spouse_national_id", "کد ملی همسر"),
                ("child_national_id", "کد ملی فرزند"),
                ("birth_y", "تاریخ تولد"),
                ("marriage_y", "تاریخ ازدواج"),
                ("child_birth_y", "تاریخ تولد فرزند"),
                ("state", "استان"),
                ("city", "شهر"),
                ("postal_code", "کد پستی"),
                ("phone", "تلفن ثابت"),
            )
            if str(prepared_data.get(key) or "").strip()
        ]
        self.log("اطلاعات ذخیره‌شدهٔ همین متقاضی خوانده شد: " + "، ".join(loaded_fields), "info")

        target_url = adapter.target_url
        self.target_url = target_url

        try:
            playwright, browser, context, page = self.setup_browser(stop_event)
            if not page or stop_event.is_set():
                return

            nav_ok = False
            for attempt_nav in range(1, 6):
                if stop_event.is_set() or getattr(page, "is_closed", lambda: False)():
                    break
                try:
                    self.log(f"در حال بارگذاری سامانه ثبت‌نام ({adapter.kind} - تلاش {attempt_nav} از 5)...", "info", page)
                    safe_goto(page, target_url, timeout=45000, wait_until="commit", log_callback=self.log)
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=12000)
                    except Exception:
                        pass
                    nav_ok = True
                    break
                except Exception as exc_nav:
                    try:
                        if page.locator("#ctl00_ContentPlaceHolder1_tbIDNo, input[name$='tbIDNo'], .BDC_CaptchaImage").count() > 0:
                            nav_ok = True
                            break
                    except Exception:
                        pass
                    self.log(f"⚠️ تاخیر در پاسخ سامانه ثبت‌نام ({exc_nav})؛ تلاش مجدد بارگذاری بدون بستن مرورگر...", "warning", page)
                    if sleep_with_stop(stop_event, 2.0):
                        break

            if not nav_ok:
                try:
                    if page.locator("#ctl00_ContentPlaceHolder1_tbIDNo, input[name$='tbIDNo']").count() > 0:
                        nav_ok = True
                except Exception:
                    pass
            if not nav_ok:
                self.log("سامانه هنوز جواب نداده؛ مرورگر باز می‌ماند و بارگذاری دوباره تلاش می‌شود.", "warning", page)

            self.log(f"🚀 ربات ثبت‌نام ({adapter.kind}) شروع شد.", "info", page)
            captcha_mode = "model"
            allow_final_submit = bool(self.settings.get("auto_final_submit", self.settings.get("final_submit", False)))

            while not stop_event.is_set():

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
                        self.log("تکرار کد تایید به سقف رسید؛ مرورگر باز می‌ماند و تلاش ادامه دارد.", "warning", page)
                        self._otp_retry = 0
                        self._otp_retry_exceeded = False
                        continue

                    if self.consume_recovery_flag("otp_expired"):
                        self.clear_otp_locks()
                        self.clear_otp_backend()
                        self.clear_remote_otp()
                        self._last_dialog_action = None
                        if hasattr(self, "_consumed_otp_digests"):
                            self._consumed_otp_digests.clear()
                        self._register_otp_announced = False
                        self._otp_entry_time = 0
                        self.log("🔄 کد تایید منقضی شد؛ بازگشت به صفحه اول ثبت‌نام جهت صدور کد جدید...", "warning", page)
                        DBHandler.update_status(self.nid, "کد منقضی - شروع مجدد", "کد تایید منقضی شد؛ بازگشت به مرحله ۱")
                        # Explicit otp_expired flag only: navigate to entry form. Never page.reload().
                        try:
                            self._safe_goto(page, getattr(self, "target_url", "https://ve.cbi.ir/TasRequest.aspx"))
                        except Exception:
                            self.log("بازگشت به مرحله ۱ بدون ریلود کامل ناموفق بود؛ انتظار DOM...", "warning", page)
                            try:
                                self.wait_for_aspnet_postback(page, timeout_ms=3500)
                            except Exception:
                                pass
                        continue

                    if self.consume_recovery_flag("captcha_invalid"):
                        if self._captcha_retry <= self._captcha_retry_limit:
                            self._refresh_captcha_in_place(page, captcha_rejected=True)
                        else:
                            if bool(self.settings.get("pause_for_manual_captcha", False)):
                                wait_for_manual_captcha_handoff(
                                    self,
                                    page,
                                    "#ctl00_ContentPlaceHolder1_tbCaptcha1",
                                    "#ctl00_ContentPlaceHolder1_btnSendConfirmCode",
                                    stop_event,
                                    source="register_captcha",
                                )
                            else:
                                # Soft reset retry counter and refresh ONLY the captcha image (no page.reload).
                                self._captcha_retry = 0
                                self._refresh_captcha_in_place(page, captcha_rejected=True)
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
                            if stop_event.is_set():
                                break
                            continue
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
                        self.log("📝 مرحله ۱: بررسی و پر کردن فرم هویتی", "registering", page)
                        self._ensure_text(page, ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name='ctl00$ContentPlaceHolder1$tbIDNo']"], self.nid)
                        # Register.aspx has no first/last-name inputs. Only fill them when that control exists.
                        self._ensure_text(page, ["#ctl00_ContentPlaceHolder1_tbFName", "input[name='ctl00$ContentPlaceHolder1$tbFName']"], first_name)
                        self._ensure_text(page, ["#ctl00_ContentPlaceHolder1_tbLName", "input[name='ctl00$ContentPlaceHolder1$tbLName']"], last_name)

                        if adapter.kind == "marriage":
                            spouse = to_ascii_digits(str(d.get("spouse_national_id") or "")).strip()
                            if spouse.isdigit() and len(spouse) == 10:
                                self._ensure_text(page, ["#ctl00_ContentPlaceHolder1_tbIDNo2", "input[name='ctl00$ContentPlaceHolder1$tbIDNo2']"], spouse)
                        elif adapter.kind == "childbearing":
                            child_nid = to_ascii_digits(str(d.get("child_national_id") or "")).strip()
                            if child_nid.isdigit() and len(child_nid) == 10:
                                self._ensure_text(page, ["#ctl00_ContentPlaceHolder1_tbChildIDNo", "input[name*='tbChildIDNo']"], child_nid)
                            child_by, child_bm, child_bd = (
                                d.get("child_birth_y") or d.get("child_birth_year"),
                                d.get("child_birth_m") or d.get("child_birth_month"),
                                d.get("child_birth_d") or d.get("child_birth_day"),
                            )
                            if child_by and self._is_visible(page, "#ctl00_ContentPlaceHolder1_tbChildBrYear"):
                                self._fill_date_triple(
                                    page,
                                    ["#ctl00_ContentPlaceHolder1_tbChildBrYear"],
                                    "#ctl00_ContentPlaceHolder1_ddlChildBrMonth",
                                    "#ctl00_ContentPlaceHolder1_ddlChildBrDay",
                                    child_by,
                                    child_bm,
                                    child_bd,
                                )

                        birth_y, birth_m, birth_d = self._get_birth_parts(d)
                        self._fill_date_triple(
                            page,
                            ["#ctl00_ContentPlaceHolder1_tbBrYear", "input[name='ctl00$ContentPlaceHolder1$tbBrYear']"],
                            "#ctl00_ContentPlaceHolder1_ddlBrMonth",
                            "#ctl00_ContentPlaceHolder1_ddlBrDay",
                            birth_y,
                            birth_m,
                            birth_d,
                        )

                        if adapter.kind == "marriage":
                            marriage_y, marriage_m, marriage_d = self._get_marriage_parts(d)
                            self._fill_date_triple(
                                page,
                                ["#ctl00_ContentPlaceHolder1_tbMarrYear", "input[name='ctl00$ContentPlaceHolder1$tbMarrYear']"],
                                "#ctl00_ContentPlaceHolder1_ddlMarryMonth",
                                "#ctl00_ContentPlaceHolder1_ddlMarryDay",
                                marriage_y,
                                marriage_m,
                                marriage_d,
                            )

                        self._ensure_text(
                            page,
                            ["#ctl00_ContentPlaceHolder1_tbMobileNo", "input[name='ctl00$ContentPlaceHolder1$tbMobileNo']"],
                            to_ascii_digits(str(d.get("mobile") or "")).strip(),
                        )
                        missing = self._step1_unfilled_labels(page, adapter.kind, d)
                        if missing:
                            self.log("این فیلدهای فرم سامانه هنوز خالی مانده‌اند: " + "، ".join(missing), "warning", page)
                            if stop_event.is_set():
                                break
                            continue

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
                        # Each pass of this loop is one retry. Waiting for SMS must not use that budget.
                        attempt -= 1
                        if stop_event.is_set():
                            break

                        if not getattr(self, "_register_otp_announced", False):
                            self._register_otp_announced = True
                            if not getattr(self, "_otp_entry_time", 0):
                                try:
                                    self.mark_otp_entry_time()
                                except Exception:
                                    pass
                            self.log("سامانه کد تایید خواست. مرورگر باز می‌ماند تا پیامک برسد.", "info", page)
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
                                self.log("کد تایید رد شد؛ مرورگر باز می‌ماند و همان مرحله دوباره تلاش می‌شود.", "warning", page)
                                self._otp_retry = 0
                                self._otp_retry_exceeded = False
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
                                mil_status = to_ascii_digits(str(d.get("military_status") or "1")).strip() or "1"
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

                        phone_val = to_ascii_digits(str(d.get("phone") or "")).strip()
                        if phone_val:
                            self._ensure_text(page, ["#ctl00_ContentPlaceHolder1_tbTel", "input[name='ctl00$ContentPlaceHolder1$tbTel']"], phone_val)
                        zip_val = to_ascii_digits(str(d.get("postal_code") or d.get("zip_code") or "")).strip()
                        if zip_val:
                            if zip_val.isdigit() and len(zip_val) < 10:
                                zip_val = zip_val.zfill(10)
                            self._ensure_text(page, ["#ctl00_ContentPlaceHolder1_tbZipCD", "input[name='ctl00$ContentPlaceHolder1$tbZipCD']"], zip_val)

                        if d.get("address") and self._is_visible(page, "#ctl00_ContentPlaceHolder1_tbAddress"):
                            self._ensure_text(page, ["#ctl00_ContentPlaceHolder1_tbAddress"], str(d.get("address")))

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
                            self.log("ثبت نهایی خودکار خاموش است. مرورگر باز می‌ماند تا خودتان آن را ببندید.", "warning", page)
                            self._hold_until_user_closes(stop_event, page)
                            break

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
                        self._hold_until_user_closes(stop_event, page)
                        break

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

    def _hold_until_user_closes(self, stop_event, page):
        """Keep the registration window open until the user stops the job or closes the browser."""
        self.log("مرورگر ثبت‌نام باز می‌ماند تا خودتان آن را ببندید.", "info", page)
        while stop_event is not None and not stop_event.is_set():
            try:
                if page is None or page.is_closed():
                    stop_event.set()
                    return
            except Exception:
                return
            if sleep_with_stop(stop_event, 1.0):
                return

    def _refresh_captcha_in_place(self, page, captcha_rejected=False):
        from bot_select_captcha import refresh_captcha_or_reload
        return refresh_captcha_or_reload(
            self, page, input_sel="input[name*='tbCaptcha']",
            allow_reload=False, captcha_rejected=captcha_rejected,
        )

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
            try:
                locator = page.locator(selector).first
                if locator.count() > 0:
                    return locator
            except Exception:
                continue
        return None

    def _ensure_text(self, page, selectors, value):
        """Fill a text field only when empty or different from the normalized expected value."""
        if value is None or str(value) == "":
            return
        clean_val = str(value).strip()
        locator = self._find_locator(page, selectors)
        if locator is None:
            return
        try:
            current = to_ascii_digits(str(locator.input_value() or "")).strip()
            if current == to_ascii_digits(clean_val).strip():
                return
        except Exception:
            pass
        self._fill_text(page, selectors, clean_val)

    def _fill_text(self, page, selectors, value):
        if value is None or value == "":
            return
        clean_val = str(value)
        locator = self._find_locator(page, selectors)
        if locator is None:
            return
        try:
            locator.wait_for(state="visible", timeout=3000)
        except Exception:
            return
        try:
            ok = BrowserActions.human_fill(locator, clean_val)
            if ok is False:
                BrowserActions.force_fill(locator, clean_val)
        except Exception:
            try:
                BrowserActions.force_fill(locator, clean_val)
            except Exception:
                pass

    def _select_option(self, page, selector, value=None):
        if not value:
            return
        clean = to_ascii_digits(str(value)).strip()
        if not clean:
            return
        candidates = []
        for cand in (clean, clean.zfill(2), clean.lstrip("0") or clean):
            if cand and cand not in candidates:
                candidates.append(cand)
        try:
            field = page.locator(selector).first
            if field.count() == 0:
                return
            field.wait_for(state="visible", timeout=3000)
        except Exception:
            return
        for cand in candidates:
            try:
                page.select_option(selector, value=cand)
                return
            except Exception:
                continue
        self.log(f"⚠️ مقدار انتخابی در فهرست کشویی پیدا نشد ({selector})", "warning", page)

    def _fill_date_triple(self, page, year_selectors, month_selector, day_selector, year, month, day):
        """Fill Jalali year textbox and select month/day dropdowns with ASCII digit normalization."""
        clean_y = to_ascii_digits(str(year or "")).strip()
        clean_m = to_ascii_digits(str(month or "")).strip()
        clean_d = to_ascii_digits(str(day or "")).strip()
        if clean_y:
            self._ensure_text(page, year_selectors, clean_y)
        if clean_m:
            self._select_option(page, month_selector, value=clean_m.zfill(2))
        if clean_d:
            self._select_option(page, day_selector, value=clean_d.zfill(2))

    def _step1_unfilled_labels(self, page, kind, data):
        """Return Persian labels of step-1 controls that exist on the page but are still empty."""
        missing = []

        def text_empty(selector):
            locator = self._find_locator(page, [selector])
            if locator is None:
                return False
            try:
                return not to_ascii_digits(str(locator.input_value() or "")).strip()
            except Exception:
                return True

        def select_empty(selector):
            locator = self._find_locator(page, [selector])
            if locator is None:
                return False
            try:
                current = to_ascii_digits(str(locator.input_value() or "")).strip()
                return current in {"", "0", "-1"}
            except Exception:
                return True

        if text_empty("#ctl00_ContentPlaceHolder1_tbIDNo"):
            missing.append("کد ملی متقاضی")
        if kind == "marriage" and str(data.get("spouse_national_id") or "").strip() and text_empty("#ctl00_ContentPlaceHolder1_tbIDNo2"):
            missing.append("کد ملی همسر")
        if kind == "childbearing" and str(data.get("child_national_id") or "").strip() and text_empty("#ctl00_ContentPlaceHolder1_tbChildIDNo"):
            missing.append("کد ملی فرزند")
        if str(data.get("birth_y") or data.get("birth_year") or "").strip() and (
            text_empty("#ctl00_ContentPlaceHolder1_tbBrYear")
            or select_empty("#ctl00_ContentPlaceHolder1_ddlBrMonth")
            or select_empty("#ctl00_ContentPlaceHolder1_ddlBrDay")
        ):
            missing.append("تاریخ تولد")
        if kind == "marriage" and str(data.get("marriage_y") or data.get("marriage_year") or "").strip() and (
            text_empty("#ctl00_ContentPlaceHolder1_tbMarrYear")
            or select_empty("#ctl00_ContentPlaceHolder1_ddlMarryMonth")
            or select_empty("#ctl00_ContentPlaceHolder1_ddlMarryDay")
        ):
            missing.append("تاریخ ازدواج")
        if str(data.get("mobile") or "").strip() and text_empty("#ctl00_ContentPlaceHolder1_tbMobileNo"):
            missing.append("تلفن همراه")
        return missing

    def _get_birth_parts(self, data):
        return (
            to_ascii_digits(str(data.get("birth_y") or data.get("birth_year") or "")).strip(),
            to_ascii_digits(str(data.get("birth_m") or data.get("birth_month") or "")).strip(),
            to_ascii_digits(str(data.get("birth_d") or data.get("birth_day") or "")).strip(),
        )

    def _get_marriage_parts(self, data):
        return (
            to_ascii_digits(str(data.get("marriage_y") or data.get("marriage_year") or "")).strip(),
            to_ascii_digits(str(data.get("marriage_m") or data.get("marriage_month") or "")).strip(),
            to_ascii_digits(str(data.get("marriage_d") or data.get("marriage_day") or "")).strip(),
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
        max_retries = max(1, int(self.settings.get("captcha_max_retries", 10)))
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
                self.wait_for_aspnet_postback(page, timeout_ms=4000)
                if not detect_captcha_failure(page) and getattr(self, "_last_dialog_action", None) != "captcha_invalid":
                    return True
                self._refresh_captcha_in_place(page, captcha_rejected=True)
            else:
                break

        if bool(self.settings.get("pause_for_manual_captcha", False)):
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
            target = normalize_location_key(text_to_match)
            target_compact = normalize_compact_key(text_to_match)
            options = page.locator(f"{selector} option").all()
            partial_match = None
            for opt in options:
                opt_raw = opt.text_content()
                opt_norm = normalize_location_key(opt_raw)
                opt_compact = normalize_compact_key(opt_raw)
                if opt_norm == target or (target_compact and opt_compact == target_compact):
                    return opt.get_attribute("value")
                if len(target_compact) > 3 and target_compact in opt_compact and partial_match is None:
                    if "سوران" in target_compact and "سیب" not in target_compact and "سیب" in opt_compact:
                        continue
                    partial_match = opt.get_attribute("value")
            return partial_match
        except Exception:
            pass
        return None
