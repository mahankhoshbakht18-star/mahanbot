"""
core/unified_bank_engine.py — Unified Bank Selection Engine for MAHANBOT.

Implements the complete, end-to-end marriage loan bank selection cycle on CBI
(ve.cbi.ir/SelectBnkShb.aspx and SelTasCase.aspx).

Operates exclusively with the isolated test database (cbi_test.db) and pure PyTorch CRNN
(my_captcha_model.pth). Zero reliance on ddddocr or external OCR services.
"""

import asyncio
import io
import logging
import re
import threading
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.pure_crnn_solver import PureCRNNSolver
from core.test_database_manager import TestDatabaseManager, TEST_DB_PATH

logger = logging.getLogger("mahanbot.unified_engine")


class EngineState(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    APPLICANT_SELECTED = "APPLICANT_SELECTED"
    STEP1_NAVIGATING = "STEP1_NAVIGATING"
    STEP1_FORM = "STEP1_FORM"
    STEP1_CAPTCHA = "STEP1_CAPTCHA"
    STEP2_OTP_DETECTED = "STEP2_OTP_DETECTED"
    STEP2_CAPTCHA = "STEP2_CAPTCHA"
    STEP2_OTP_PROCESSING = "STEP2_OTP_PROCESSING"
    STEP2_SUBMITTING = "STEP2_SUBMITTING"
    STEP2_MANUAL_WAIT = "STEP2_MANUAL_WAIT"
    PAGE3_DETECTED = "PAGE3_DETECTED"
    PAGE3_INSPECTING = "PAGE3_INSPECTING"
    PAGE3_SELECTING_CITY = "PAGE3_SELECTING_CITY"
    PAGE3_SELECTING_BANK = "PAGE3_SELECTING_BANK"
    PAGE3_SELECTING_BRANCH = "PAGE3_SELECTING_BRANCH"
    READY_FOR_MANUAL_CONFIRMATION = "READY_FOR_MANUAL_CONFIRMATION"
    FINAL_SUBMITTING = "FINAL_SUBMITTING"
    CONFIRMED = "CONFIRMED"
    PAGE3_RELOAD_HANDLING = "PAGE3_RELOAD_HANDLING"
    OTP_EXPIRED_RESTART = "OTP_EXPIRED_RESTART"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


def normalize_persian(text: Optional[str]) -> str:
    """Normalize Arabic and Persian characters."""
    if not text:
        return ""
    trans = str.maketrans("ييكك۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "ییكک01234567890123456789")
    s = text.translate(trans)
    s = re.sub(r"[\u200c\u200b\u00a0\s]+", " ", s).strip()
    return s.lower()


class UnifiedBankEngine:
    """
    Central controller managing the full bank selection workflow.
    """

    def __init__(
        self,
        db_path: str = TEST_DB_PATH,
        model_path: Optional[str] = None,
        mock_server_url: Optional[str] = None,
        headless: bool = True,
    ):
        self.db = TestDatabaseManager(db_path)
        self.solver = PureCRNNSolver.get_instance(model_path)
        self.mock_server_url = mock_server_url
        self.headless = headless

        self.state: EngineState = EngineState.IDLE
        self.active_applicant: Optional[Dict[str, Any]] = None
        self.last_error: Optional[str] = None
        self.current_step: str = ""
        self.logs: List[Dict[str, Any]] = []

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused by default
        self._manual_resume_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

        # Browser handles
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def log(self, message: str, level: str = "info"):
        """Append log message and output to python logger."""
        entry = {
            "timestamp": time.time(),
            "time_str": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "state": self.state.value,
            "nid": self.active_applicant.get("national_id") if self.active_applicant else None,
        }
        with self._lock:
            self.logs.append(entry)
            if len(self.logs) > 500:
                self.logs.pop(0)

        log_func = getattr(logger, level if hasattr(logger, level) else "info")
        log_func(f"[{self.state.value}] {message}")

    def get_status(self) -> Dict[str, Any]:
        """Return the current engine snapshot for the web UI."""
        with self._lock:
            return {
                "state": self.state.value,
                "is_running": self._thread is not None and self._thread.is_alive(),
                "is_paused": not self._pause_event.is_set(),
                "active_applicant": self.active_applicant,
                "last_error": self.last_error,
                "current_step": self.current_step,
                "logs": self.logs[-50:],
            }

    def start(self, applicant_nid: Optional[str] = None):
        """Start engine execution in a background thread."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self.log("موتور در حال اجراست.", "warning")
                return

            self._stop_event.clear()
            self._pause_event.set()
            self._manual_resume_event.clear()
            self.last_error = None
            self.state = EngineState.STARTING
            self._thread = threading.Thread(
                target=self._run_orchestrator,
                args=(applicant_nid,),
                daemon=True,
                name="UnifiedBankEngineThread",
            )
            self._thread.start()
            self.log("موتور یکپارچه انتخاب بانک راه‌اندازی شد.")

    def pause(self):
        """Pause the engine."""
        self._pause_event.clear()
        self.state = EngineState.PAUSED
        self.log("عملیات موقتاً متوقف (Pause) شد.")

    def resume(self):
        """Resume the engine from pause."""
        self._pause_event.set()
        self.log("عملیات از سر گرفته شد.")

    def stop(self):
        """Stop the engine and clean up browser resources."""
        self.log("دستور توقف موتور صادر شد.")
        self._stop_event.set()
        self._pause_event.set()
        self._manual_resume_event.set()
        if self._thread and self._thread.is_alive() and threading.current_thread() != self._thread:
            self._thread.join(timeout=5.0)
        self._close_browser()
        self.state = EngineState.STOPPED
        self.log("موتور متوقف شد.")

    def trigger_manual_confirmation(self):
        """Called when user confirms final submission manually in the UI."""
        self._manual_resume_event.set()

    # --------------------------------------------------------------------------
    # Browser Management
    # --------------------------------------------------------------------------
    def _init_browser(self):
        from playwright.sync_api import sync_playwright

        if self._playwright is None:
            self._playwright = sync_playwright().start()
        if self._browser is None:
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
        self._context = self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(15000)

    def _close_browser(self):
        try:
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception as e:
            logger.debug(f"Error during browser cleanup: {e}")
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None

    def _check_control_signals(self) -> bool:
        """Check if stopped or paused. Returns False if engine should terminate."""
        if self._stop_event.is_set():
            return False
        while not self._pause_event.is_set():
            if self._stop_event.is_set():
                return False
            time.sleep(0.5)
        return True

    # --------------------------------------------------------------------------
    # Orchestrator Loop
    # --------------------------------------------------------------------------
    def _run_orchestrator(self, specific_nid: Optional[str] = None):
        try:
            self.db.init_database()
            applicants = []
            if specific_nid:
                app = self.db.get_applicant(specific_nid)
                if app:
                    applicants = [app]
            else:
                applicants = self.db.get_applicants()

            if not applicants:
                self.log("هیچ متقاضی فعالی در دیتابیس یافت نشد.", "warning")
                self.state = EngineState.IDLE
                return

            self.log(f"تعداد {len(applicants)} متقاضی برای پردازش آماده است.")

            for applicant in applicants:
                if self._stop_event.is_set():
                    break
                self.active_applicant = applicant
                nid = applicant["national_id"]
                name = applicant["full_name"]

                # Skip already confirmed applicants
                if applicant.get("status") in ("ثبت قطعی", "رزرو شده"):
                    self.log(f"متقاضی {name} ({nid}) قبلاً ثبت نهایی شده است. رد شد.", "info")
                    continue

                self.state = EngineState.APPLICANT_SELECTED
                self.log(f"شروع پردازش متقاضی: {name} ({nid})")
                self.db.update_status(nid, "در حال بررسی", "شروع چرخه انتخاب بانک")

                success = self._process_single_applicant(applicant)
                if not success:
                    self.log(f"چرخه متقاضی {name} با خطا متوقف شد.", "error")
                else:
                    self.log(f"چرخه متقاضی {name} با موفقیت خاتمه یافت.", "info")

                self._close_browser()

            self.state = EngineState.STOPPED if self._stop_event.is_set() else EngineState.IDLE
            self.log("پردازش کلیه متقاضیان به پایان رسید.")

        except Exception as exc:
            self.last_error = str(exc)
            self.state = EngineState.ERROR
            self.log(f"خطای بحرانی در موتور: {exc}", "error")
        finally:
            self._close_browser()

    # --------------------------------------------------------------------------
    # Single Applicant Workflow
    # --------------------------------------------------------------------------
    def _process_single_applicant(self, applicant: Dict[str, Any]) -> bool:
        nid = applicant["national_id"]
        target_url = self.mock_server_url or "https://ve.cbi.ir/SelectBnkShb.aspx"

        self._init_browser()
        page = self._page

        # Phase 1: Navigation and Step 1
        self.state = EngineState.STEP1_NAVIGATING
        self.log(f"بارگذاری صفحه ورودی: {target_url}")
        try:
            page.goto(target_url, wait_until="domcontentloaded")
        except Exception as e:
            self.log(f"خطا در باز کردن آدرس {target_url}: {e}", "error")
            return False

        if not self._check_control_signals():
            return False

        # Phase 2: Fill NID and Solve Step 1 Captcha
        step1_done = self._execute_step1(applicant, page)
        if not step1_done:
            return False

        if not self._check_control_signals():
            return False

        # Phase 3: Step 2 OTP & Captcha
        step2_done = self._execute_step2(applicant, page)
        if not step2_done:
            return False

        if not self._check_control_signals():
            return False

        # Phase 4: Page 3 Selection & Final Submit
        page3_done = self._execute_page3(applicant, page)
        return page3_done

    # --------------------------------------------------------------------------
    # Step 1: NID + Captcha 1
    # --------------------------------------------------------------------------
    def _execute_step1(self, applicant: Dict[str, Any], page) -> bool:
        self.state = EngineState.STEP1_FORM
        nid = applicant["national_id"]
        self.log("تکمیل فرم ورود مرحله اول (کد ملی و کپچا)")

        # Disambiguated Step 1 selectors
        nid_sel = "#ctl00_ContentPlaceHolder1_tbIDNo"
        cap1_img_sel = "#ctl00_ContentPlaceHolder1_botdetectcaptcha_CaptchaImage"
        cap1_inp_sel = "#ctl00_ContentPlaceHolder1_tbCaptcha"
        btn_login_sel = "#ctl00_ContentPlaceHolder1_btnLogin"

        # Check if already passed Step 1
        if self._is_on_otp_page(page):
            self.log("نشست قبلاً مرحله ۱ را رد کرده و در مرحله OTP است.")
            return True
        if self._is_on_page3(page):
            self.log("نشست مستقیماً در صفحه سوم فعال است.")
            return True

        try:
            page.wait_for_selector(nid_sel, timeout=7000)
        except Exception:
            self.log(f"فیلد کد ملی ({nid_sel}) پیدا نشد.", "error")
            return False

        page.fill(nid_sel, nid)

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            if not self._check_control_signals():
                return False

            self.state = EngineState.STEP1_CAPTCHA
            self.log(f"حل کپچای مرحله ۱ (تلاش {attempt} از {max_retries})")

            solved_text = self._solve_captcha_element(page, cap1_img_sel)
            if not solved_text:
                self.log("مدل نتوانست کپچای مرحله ۱ را حل کند؛ رفرش تصویر...", "warning")
                self._reload_captcha(page, "#ctl00_ContentPlaceHolder1_botdetectcaptcha_ReloadIcon")
                time.sleep(1.0)
                continue

            self.log(f"کپچای مرحله ۱ با مدل پیش‌بینی شد: {solved_text}")
            page.fill(cap1_inp_sel, solved_text)
            page.click(btn_login_sel)

            # Wait for navigation or error
            time.sleep(1.5)

            # Check if moved to Step 2
            if self._is_on_otp_page(page) or self._is_on_page3(page):
                self.log("مرحله ۱ با موفقیت تکمیل شد و به مرحله بعد هدایت گردید.")
                self.db.record_checkpoint(nid, "STEP_1_ENTRY_FORM", "ورود موفق مرحله اول")
                return True

            # Check error message on page
            error_msg = self._extract_page_error(page)
            if error_msg:
                self.log(f"پیام خطا از سامانه در مرحله ۱: {error_msg}", "warning")
                # If error is invalid NID or serious, fail early
                if "کد ملی" in error_msg and "صحیح" not in error_msg:
                    self.db.update_status(nid, "خطای اطلاعات", error_msg)
                    return False
                self._reload_captcha(page, "#ctl00_ContentPlaceHolder1_botdetectcaptcha_ReloadIcon")
                time.sleep(1.0)

        self.log("تعداد تلاش‌های حل کپچای مرحله ۱ به سقف رسید.", "error")
        return False

    # --------------------------------------------------------------------------
    # Step 2: OTP + Captcha 2 (With Prefilled OTP & Manual Resume Fix)
    # --------------------------------------------------------------------------
    def _execute_step2(self, applicant: Dict[str, Any], page) -> bool:
        self.state = EngineState.STEP2_OTP_DETECTED
        nid = applicant["national_id"]
        self.log("شناسایی مرحله دوم: تأیید کد پیامکی و کپچا")

        # Disambiguated Step 2 selectors
        otp_inp_sel = "#ctl00_ContentPlaceHolder1_tbMobileConfCode"
        cap2_img_sel = "#ctl00_ContentPlaceHolder1_botdetectcaptcha2_CaptchaImage"
        cap2_inp_sel = "#ctl00_ContentPlaceHolder1_tbCaptcha2"
        btn_continue_sel = "#ctl00_ContentPlaceHolder1_btnContinue1"

        try:
            page.wait_for_selector(otp_inp_sel, timeout=8000)
        except Exception:
            # Maybe already on Page 3
            if self._is_on_page3(page):
                return True
            self.log("فرم مرحله ۲ (کد پیامکی) یافت نشد.", "error")
            return False

        # --- FIX 3: Check for Prefilled OTP (Page 3 Reload Behavior) ---
        prefilled_code = page.input_value(otp_inp_sel).strip()
        code_to_use = None

        if prefilled_code and len(prefilled_code) >= 4 and prefilled_code.isdigit():
            self.log("کد پیامک از قبل در فرم پر شده است (رفتار رفرش صفحه ۳). نگهداری کد بدون درخواست مجدد.")
            code_to_use = prefilled_code
            self.state = EngineState.PAGE3_RELOAD_HANDLING
        else:
            self.state = EngineState.STEP2_OTP_PROCESSING
            self.log("در انتظار دریافت کد پیامک از دیتابیس/رله...")
            self.db.update_status(nid, "منتظر پیامک", "در انتظار ورود کد تایید")

            # Poll for OTP from test DB with timeout
            deadline = time.time() + 45.0
            while time.time() < deadline:
                if not self._check_control_signals():
                    return False
                otp_rec = self.db.get_otp(nid)
                if otp_rec and otp_rec.get("code"):
                    code_to_use = otp_rec["code"]
                    break
                time.sleep(1.0)

            if not code_to_use:
                self.log("مهلت دریافت پیامک به پایان رسید. انتظار برای مداخله کاربر (حل دستی)...", "warning")
                return self._wait_for_manual_step2_handoff(page, nid)

            self.log("کد پیامک دریافت شد و در فرم درج می‌شود.")
            page.fill(otp_inp_sel, code_to_use)

        # Solve Step 2 Captcha
        max_cap_retries = 3
        for attempt in range(1, max_cap_retries + 1):
            if not self._check_control_signals():
                return False

            self.state = EngineState.STEP2_CAPTCHA
            self.log(f"حل کپچای مرحله ۲ (تلاش {attempt} از {max_cap_retries})")

            solved_cap = self._solve_captcha_element(page, cap2_img_sel)
            if not solved_cap:
                self.log("عدم موفقیت در حل کپچای ۲؛ درخواست تصویر جدید...", "warning")
                self._reload_captcha(page, "#ctl00_ContentPlaceHolder1_botdetectcaptcha2_ReloadIcon")
                time.sleep(1.0)
                continue

            self.log(f"کپچای مرحله ۲ با مدل پیش‌بینی شد: {solved_cap}")
            page.fill(cap2_inp_sel, solved_cap)

            self.state = EngineState.STEP2_SUBMITTING
            page.click(btn_continue_sel)
            time.sleep(1.5)

            # Check if advanced to Page 3
            if self._is_on_page3(page):
                self.log("ورود موفقیت‌آمیز به صفحه سوم (انتخاب بانک)!")
                self.db.record_checkpoint(nid, "STEP_2_OTP", "تأیید پیامک و کپچا موفق")
                return True

            # Check for OTP expiration
            error_msg = self._extract_page_error(page)
            if error_msg:
                self.log(f"پیام سامانه در مرحله ۲: {error_msg}", "warning")
                if "منقضی" in error_msg:
                    self.log("کد پیامک منقضی شده است؛ بازگشت به مرحله اول.", "warning")
                    self.state = EngineState.OTP_EXPIRED_RESTART
                    self.db.invalidate_otp(nid, status="expired")
                    return False

            # If still on Step 2, reload captcha and retry
            self._reload_captcha(page, "#ctl00_ContentPlaceHolder1_botdetectcaptcha2_ReloadIcon")
            time.sleep(1.0)

        # --- FIX 2: If automated captcha fails, do NOT freeze permanently!
        # Enter manual wait loop which continuously polls DOM and resumes when user advances.
        self.log("کپچای مرحله ۲ خودکار کامل نشد. انتقال به حالت مداخله دستی بدون توقف نشست...", "warning")
        return self._wait_for_manual_step2_handoff(page, nid)

    def _wait_for_manual_step2_handoff(self, page, nid: str) -> bool:
        """
        Fixes the freeze bug: Keeps the session active and polls the DOM.
        If the user inputs the code/captcha manually and submits, the engine detects
        navigation to Page 3 and seamlessly resumes the automated workflow.
        """
        self.state = EngineState.STEP2_MANUAL_WAIT
        self.db.update_status(nid, "نیاز به تأیید دستی", "لطفاً کپچا/کد را در مرورگر وارد کنید")
        self.log("در انتظار تکمیل دستی مرحله ۲ توسط کاربر (نظارت پیوسته DOM فعال است)...", "info")

        wait_deadline = time.time() + 180.0  # 3 minutes window
        while time.time() < wait_deadline:
            if not self._check_control_signals():
                return False

            if self._is_on_page3(page):
                self.log("مداخله دستی انجام شد و هدایت به صفحه ۳ تشخیص داده شد. از سرگیری خودکار!")
                self.db.record_checkpoint(nid, "STEP_2_OTP", "تکمیل دستی مرحله دوم")
                return True

            # Also check if redirected back to Step 1
            if page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").count() > 0 and page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").first.is_visible():
                self.log("نشست به مرحله اول بازگشت.", "warning")
                return False

            time.sleep(1.0)

        self.log("مهلت مداخله دستی در مرحله ۲ به پایان رسید.", "error")
        return False

    # --------------------------------------------------------------------------
    # Step 3: Page 3 Bank & Branch Selection
    # --------------------------------------------------------------------------
    def _execute_page3(self, applicant: Dict[str, Any], page) -> bool:
        self.state = EngineState.PAGE3_DETECTED
        nid = applicant["national_id"]
        name = applicant["full_name"]
        self.log(f"ورود به صفحه ۳ برای متقاضی {name}")

        bank_ddl = "#ctl00_ContentPlaceHolder1_ddlBankName"
        city_ddl = "#ctl00_ContentPlaceHolder1_ddlCity"
        branch_ddl = "#ctl00_ContentPlaceHolder1_ddlBranch"
        btn_submit = "#ctl00_ContentPlaceHolder1_btnSubmit"

        try:
            page.wait_for_selector(bank_ddl, timeout=10000)
        except Exception:
            # Check if site redirected back to OTP (prefilled reload)
            if self._is_on_otp_page(page):
                self.log("سامانه به صفحه OTP هدایت کرد (رفتار رفرش). پردازش مجدد مرحله ۲...")
                if self._execute_step2(applicant, page):
                    return self._execute_page3(applicant, page)
                return False
            self.log("فهرست بانک‌ها در صفحه ۳ بارگذاری نشد.", "error")
            return False

        # 1. City selection (No artificial rapid pulsing of ddlCity!)
        self.state = EngineState.PAGE3_SELECTING_CITY
        applicant_city = applicant.get("city", "")
        if applicant_city:
            self.log(f"بررسی و انتخاب شهر متقاضی: {applicant_city}")
            self._select_city_if_needed(page, city_ddl, applicant_city)

        # 2. Inspect Banks and Match Priorities
        self.state = EngineState.PAGE3_INSPECTING
        priority_banks = applicant.get("priority_banks", [])
        allow_reserve = applicant.get("allow_reserve", True)
        self.log(f"اولویت‌های بانک متقاضی: {priority_banks} (مجوز رزرو: {allow_reserve})")

        bank_options = self._read_dropdown_options(page, bank_ddl)
        self.log(f"تعداد {len(bank_options)} گزینه بانک در صفحه زنده یافت شد.")

        selected_bank_val, selected_bank_title, is_reserve = self._match_priority_bank(
            bank_options, priority_banks, allow_reserve
        )

        if not selected_bank_val:
            self.log("هیچ‌یک از بانک‌های اولویت‌دار متقاضی ظرفیت خالی (فعال یا رزرو) ندارند.", "warning")
            self.db.update_status(nid, "عدم ظرفیت بانک", "بانک‌های اولویت‌دار ظرفیت ندارند")
            return False

        capacity_kind = "رزرو" if is_reserve else "فعال"
        self.log(f"بانک انتخاب شد: {selected_bank_title} (نوع ظرفیت: {capacity_kind})")

        # Select the bank in dropdown with postback wait
        self.state = EngineState.PAGE3_SELECTING_BANK
        page.select_option(bank_ddl, selected_bank_val)
        time.sleep(2.0)  # Wait for ASP.NET postback

        # 3. Handle Mode: In-Person vs Online
        self.state = EngineState.PAGE3_SELECTING_BRANCH
        bank_mode = applicant.get("bank_mode", "online")
        self.log(f"روش مراجعه متقاضی: {bank_mode}")

        if bank_mode == "online":
            self.log("روش غیرحضوری انتخاب شده است؛ بدون نیاز به انتخاب شعبه.")
            # Select online radio if present
            rb_online = page.locator("#ctl00_ContentPlaceHolder1_rbOnline, input[value='online'], input[id*='rbOnline']")
            if rb_online.count() > 0 and rb_online.first.is_visible():
                rb_online.first.check()
                time.sleep(1.0)
        else:
            self.log("روش حضوری؛ انتظار برای بارگذاری فهرست شعب...")
            # Select in-person radio if present
            rb_in_person = page.locator("#ctl00_ContentPlaceHolder1_rbInPerson, input[value='in_person'], input[id*='rbInPerson']")
            if rb_in_person.count() > 0 and rb_in_person.first.is_visible():
                rb_in_person.first.check()
                time.sleep(1.5)

            # Select first available branch
            try:
                page.wait_for_selector(branch_ddl, timeout=6000)
                branch_options = self._read_dropdown_options(page, branch_ddl)
                valid_branches = [b for b in branch_options if b["value"] and b["value"] != "0"]
                if valid_branches:
                    preferred = applicant.get("preferred_branch", "")
                    target_branch = valid_branches[0]
                    if preferred:
                        for b in valid_branches:
                            if normalize_persian(preferred) in normalize_persian(b["text"]):
                                target_branch = b
                                break
                    self.log(f"انتخاب شعبه: {target_branch['text']}")
                    page.select_option(branch_ddl, target_branch["value"])
                    time.sleep(1.0)
            except Exception as e:
                self.log(f"فهرست شعب نمایش داده نشد یا خطا رخ داد: {e}", "warning")

        # 4. Final Submission Logic (Strict auto_final_submit enforcement)
        auto_final_submit = applicant.get("auto_final_submit", False)
        self.log(f"تنظیم ثبت نهایی خودکار (auto_final_submit): {auto_final_submit}")

        if not auto_final_submit:
            # STOP at this state and wait for manual user confirmation
            self.state = EngineState.READY_FOR_MANUAL_CONFIRMATION
            self.db.update_status(nid, "آماده تأیید نهایی", f"بانک {selected_bank_title} آماده تأیید است")
            self.log(
                f"✅ کلیه اطلاعات برای بانک '{selected_bank_title}' تنظیم شد. ثبت نهایی خودکار خاموش است. در انتظار تأیید کاربر...",
                "info",
            )

            # Wait for manual confirmation signal or stop
            while not self._manual_resume_event.is_set():
                if not self._check_control_signals():
                    return False
                time.sleep(0.2)

            if not self._check_control_signals() or self._stop_event.is_set():
                return False

            self._manual_resume_event.clear()
            self.log("تأیید دستی ثبت نهایی توسط کاربر دریافت شد. ادامه ارسال درخواست...")


        # Execute final submission
        self.state = EngineState.FINAL_SUBMITTING
        self.log("کلیک روی دکمه ثبت نهایی درخواست...")
        try:
            page.click(btn_submit)
            time.sleep(2.0)
        except Exception as e:
            self.log(f"خطا در ارسال فرم ثبت نهایی: {e}", "error")
            return False

        # Evaluate confirmation
        return self._evaluate_final_response(page, applicant, is_reserve)

    def _evaluate_final_response(self, page, applicant: Dict[str, Any], is_reserve: bool) -> bool:
        nid = applicant["national_id"]
        content = page.content()

        # Check for definitive confirmation and tracking code
        tracking_match = re.search(r"کد\s*(?:رهگیری|پیگیری)\s*[:：]\s*([0-9A-Za-z\-_]+)", content)
        if tracking_match:

            tracking_code = tracking_match.group(1)
            self.state = EngineState.CONFIRMED
            self.log(f"🎉 ثبت نهایی قطعی با موفقیت انجام شد! کد پیگیری: {tracking_code}", "info")
            self.db.save_tracking_code(nid, tracking_code)
            return True

        if is_reserve or "رزرو" in content:
            self.state = EngineState.CONFIRMED
            self.log("🎉 درخواست رزرو بانک با موفقیت ثبت شد!", "info")
            self.db.save_reserve_confirmation(nid)
            return True

        # Check for success message even without explicit tracking regex
        if "با موفقیت ثبت شد" in content or "ثبت نام شما با موفقیت" in content:
            self.state = EngineState.CONFIRMED
            self.log("🎉 پیام موفقیت ثبت از سامانه دریافت شد.", "info")
            self.db.save_tracking_code(nid, f"CONFIRMED_{int(time.time())}")
            return True

        error_msg = self._extract_page_error(page)
        self.log(f"نتیجه نهایی ثبت نامشخص یا رد شده است. پیام سامانه: {error_msg or 'نامشخص'}", "error")
        self.db.update_status(nid, "خطای ثبت نهایی", error_msg or "پاسخ نامشخص")
        return False

    # --------------------------------------------------------------------------
    # Helper Methods
    # --------------------------------------------------------------------------
    def _is_on_otp_page(self, page) -> bool:
        """Check if currently on the OTP stage via unified live-DOM detector."""
        from core.stage_detector import detect_current_stage, SMS_OTP_STAGE
        return detect_current_stage(page, bot=None).stage == SMS_OTP_STAGE

    def _is_on_page3(self, page) -> bool:
        """Check if currently on Page 3 via unified live-DOM detector."""
        from core.stage_detector import detect_current_stage, PAGE_3_BANK_STAGE
        return detect_current_stage(page, bot=None).stage == PAGE_3_BANK_STAGE

    def _solve_captcha_element(self, page, img_selector: str) -> Optional[str]:
        """Read the captcha through captcha_engine and return a 4–5 character code."""
        try:
            from captcha_engine import CaptchaModel, attach_captcha_listener, capture_captcha_bytes
            loc = page.locator(img_selector)
            if loc.count() == 0 or not loc.first.is_visible():
                return None
            attach_captcha_listener(page)
            image_bytes = capture_captcha_bytes(page, loc.first)
            if not image_bytes:
                return None
            return CaptchaModel.shared().predict(image_bytes) or None
        except Exception as e:
            logger.debug(f"Error solving captcha element {img_selector}: {e}")
            return None

    def _reload_captcha(self, page, reload_icon_selector: str):
        """Click reload icon to refresh captcha image."""
        try:
            icon = page.locator(reload_icon_selector)
            if icon.count() > 0 and icon.first.is_visible():
                icon.first.click()
                time.sleep(0.5)
        except Exception:
            pass

    def _extract_page_error(self, page) -> Optional[str]:
        """Read standard error banner labels."""
        error_selectors = [
            "#ctl00_ContentPlaceHolder1_labError",
            "#ctl00_ContentPlaceHolder1_lblMsg",
            ".alert-danger",
            ".errorMessage",
        ]
        for sel in error_selectors:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                text = loc.first.inner_text().strip()
                if text:
                    return text
        return None

    def _read_dropdown_options(self, page, selector: str) -> List[Dict[str, str]]:
        """Read options from an HTML select dropdown."""
        try:
            options = page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return [];
                    return Array.from(el.options || []).map(o => ({
                        value: o.value || '',
                        text: (o.textContent || '').trim()
                    }));
                }""",
                selector,
            )
            return options or []
        except Exception:
            return []

    def _select_city_if_needed(self, page, city_sel: str, applicant_city: str):
        """Select city solely from applicant data without rapid pulsing."""
        options = self._read_dropdown_options(page, city_sel)
        norm_city = normalize_persian(applicant_city)
        for opt in options:
            val = opt["value"]
            if not val or val == "0":
                continue
            if norm_city in normalize_persian(opt["text"]):
                page.select_option(city_sel, val)
                time.sleep(1.0)
                return

    def _match_priority_bank(
        self,
        bank_options: List[Dict[str, str]],
        priority_banks: List[str],
        allow_reserve: bool,
    ) -> Tuple[Optional[str], Optional[str], bool]:
        """
        Match available banks against applicant priority list.
        1. Checks active capacity first.
        2. If none and allow_reserve is True, checks reserve capacity.
        Returns: (selected_value, selected_title, is_reserve)
        """
        valid_options = [o for o in bank_options if o["value"] and o["value"] != "0"]

        # Step 1: Check active capacity
        for priority in priority_banks:
            p_norm = normalize_persian(priority)
            for opt in valid_options:
                text_norm = normalize_persian(opt["text"])
                if p_norm in text_norm and "رزرو" not in text_norm:
                    return opt["value"], opt["text"], False

        # Step 2: Check reserve capacity if allowed
        if allow_reserve:
            for priority in priority_banks:
                p_norm = normalize_persian(priority)
                for opt in valid_options:
                    text_norm = normalize_persian(opt["text"])
                    if p_norm in text_norm and "رزرو" in text_norm:
                        return opt["value"], opt["text"], True

        return None, None, False
