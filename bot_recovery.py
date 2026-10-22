import re
import time
from typing import Optional, Tuple

from bot_core import BotCore
from bot_helpers import sleep_with_stop, detect_captcha_failure
from browser_actions import BrowserActions
from browser_launcher import BrowserLaunchError, close_browser, safe_goto
from database import DBHandler

TARGET_URL = "https://ve.cbi.ir/GetTraceCD.aspx"



class RecoveryBot(BotCore):
    def __init__(self, nid, settings, log_callback=None, captcha_service=None, browser_profile=None):
        super().__init__(
            nid,
            settings,
            log_callback=log_callback,
            captcha_service=captcha_service,
            browser_profile=browser_profile,
        )
        self._last_missing_controls_log_ts = 0.0
        self._last_busy_log_ts = 0.0
        self._last_step2_log_ts = 0.0

    def run(self, stop_event):
        self._active_stop_event = stop_event
        playwright = None
        browser = None
        context = None
        page = None
        try:
            playwright, browser, context, page = self.setup_browser(stop_event)
            if not page or stop_event.is_set():
                return None

            self.log("شروع بازیابی کد رهگیری.", "info", page)
            safe_goto(page, TARGET_URL, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)

            while not stop_event.is_set():
                if self._page_closed(page):
                    return None
                if self._tracking_page_ready(page):
                    break
                if self.is_firewall_challenge(page):
                    if not self._solve_firewall_loop(page, stop_event):
                        return None
                    continue
                if sleep_with_stop(stop_event, 0.3):
                    return None

            if stop_event.is_set():
                return None

            self._fill_recovery_form(page)
            code = self._main_recovery_loop(page, stop_event)
            if code:
                DBHandler.save_recovered_tracking_code(self.nid, code)
                self.log(f"کد رهگیری استخراج و ذخیره شد: {self._mask(code)}", "success", page)
                return code
            return None
        except BrowserLaunchError as exc:
            self.log(f"Fatal: {exc.message}", "error")
            return None
        except Exception as exc:
            self.log(f"Fatal: {exc}", "error")
            return None
        finally:
            close_browser(playwright, browser, context, page)

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

    def _first_visible_locator(self, page, selectors):
        for scope in self._iter_scopes(page):
            for selector in selectors:
                try:
                    locator = scope.locator(selector)
                    if locator.count() > 0 and locator.first.is_visible():
                        return locator.first
                except Exception:
                    continue
        return None

    def _tracking_page_ready(self, page) -> bool:
        selectors = [
            "#ctl00_ContentPlaceHolder1_tbIDNo",
            "#ctl00_ContentPlaceHolder1_tbCaptcha1",
            "#ctl00_ContentPlaceHolder1_btnGetTraceCD",
        ]
        for selector in selectors:
            locator = self._first_visible_locator(page, [selector])
            if locator is None:
                return False
        return True

    def _extract_initial(self, value: str) -> str:
        return str(value or "").strip()[:1]

    def _parse_date(self, raw_value: str, fallback_y=None, fallback_m=None, fallback_d=None) -> Tuple[str, str, str]:
        raw = str(raw_value or "").strip()
        y, m, d = "", "", ""
        if raw:
            parts = [p for p in re.split(r"\D+", raw) if p]
            if len(parts) >= 3:
                y, m, d = parts[0], parts[1], parts[2]
        if not y and fallback_y:
            y = str(fallback_y).strip()
        if not m and fallback_m:
            m = str(fallback_m).strip()
        if not d and fallback_d:
            d = str(fallback_d).strip()
        return y, m.zfill(2) if m else "", d.zfill(2) if d else ""

    def _fill_text(self, page, selector: str, value: str, maxlength: Optional[int] = None):
        if value is None:
            return
        text = str(value).strip()
        if maxlength is not None:
            text = text[:maxlength]
        try:
            locator = self._first_visible_locator(page, [selector])
            if locator is None:
                return
            BrowserActions.human_fill(locator, text)
        except Exception:
            pass

    def _select_option(self, page, selector: str, value: str):
        if not value:
            return
        try:
            for scope in self._iter_scopes(page):
                locator = scope.locator(selector)
                if locator.count() > 0 and locator.first.is_visible():
                    scope.select_option(selector, value=value)
                    return
        except Exception:
            pass

    def _fill_recovery_form(self, page):
        data = self.user_data or {}
        first_name = str(data.get("first_name") or data.get("fname") or "").strip()
        last_name = str(data.get("last_name") or data.get("lname") or "").strip()
        if not first_name or not last_name:
            full_name = str(data.get("full_name") or "").strip()
            if full_name:
                parts = full_name.split()
                if parts:
                    first_name = first_name or parts[0]
                    last_name = last_name or (parts[-1] if len(parts) > 1 else "")

        birth_y, birth_m, birth_d = self._parse_date(
            data.get("birth_date"),
            fallback_y=data.get("birth_y") or data.get("birth_year"),
            fallback_m=data.get("birth_m") or data.get("birth_month"),
            fallback_d=data.get("birth_d") or data.get("birth_day"),
        )
        marriage_y, marriage_m, marriage_d = self._parse_date(
            data.get("marriage_date"),
            fallback_y=data.get("marriage_y") or data.get("marriage_year"),
            fallback_m=data.get("marriage_m") or data.get("marriage_month"),
            fallback_d=data.get("marriage_d") or data.get("marriage_day"),
        )

        self.log("تزریق اطلاعات مرحله اول بازیابی.", "info", page)
        self._fill_text(page, "#ctl00_ContentPlaceHolder1_tbIDNo", self.nid)
        self._fill_text(page, "#ctl00_ContentPlaceHolder1_tbFName", self._extract_initial(first_name), maxlength=1)
        self._fill_text(page, "#ctl00_ContentPlaceHolder1_tbLName", self._extract_initial(last_name), maxlength=1)
        self._fill_text(page, "#ctl00_ContentPlaceHolder1_tbBrYear", birth_y)
        self._select_option(page, "#ctl00_ContentPlaceHolder1_ddlBrMonth", birth_m)
        self._select_option(page, "#ctl00_ContentPlaceHolder1_ddlBrDay", birth_d)
        self._fill_text(page, "#ctl00_ContentPlaceHolder1_tbMarrYear", marriage_y)
        self._select_option(page, "#ctl00_ContentPlaceHolder1_ddlMarrMonth", marriage_m)
        self._select_option(page, "#ctl00_ContentPlaceHolder1_ddlMarrDay", marriage_d)
        self.log("اطلاعات مرحله اول تکمیل شد.", "success", page)

    def _refresh_captcha(self, page):
        reload_btn = self._first_visible_locator(page, [".BDC_ReloadLink"])
        if reload_btn is not None:
            try:
                reload_btn.click(timeout=500)
                return True
            except Exception:
                pass
        captcha_img = self._first_visible_locator(page, [".BDC_CaptchaImage"])
        if captcha_img is not None:
            try:
                captcha_img.click(timeout=500)
                return True
            except Exception:
                pass
        return False

    def _detect_captcha_failure(self, page) -> bool:
        return detect_captcha_failure(page)

    def _detect_system_busy(self, page) -> bool:
        phrases = [
            "بروز وضعیت ناشناخته در سامانه ازدواج",
            "به علت ترافیک بالای سامانه",
            "لطفا چند دقیقه دیگر دوباره تلاش نمایید",
            "لطفاً چند دقیقه دیگر دوباره تلاش نمایید",
        ]
        try:
            body = page.inner_text("body") or ""
        except Exception:
            return False
        return any(p in body for p in phrases)

    def _handle_system_busy(self, page, stop_event) -> bool:
        now = time.time()
        if now - self._last_busy_log_ts > 10:
            self.log("سامانه پرترافیک است؛ توقف کوتاه و تلاش مجدد.", "warning", page)
            self._last_busy_log_ts = now
        DBHandler.update_status(self.nid, "BUSY_RETRY", "سامانه پرترافیک است؛ در حال تلاش مجدد")
        wait_seconds = int(self.settings.get("recovery_busy_wait_seconds", 120))
        if sleep_with_stop(stop_event, wait_seconds):
            return False
        try:
            safe_goto(page, TARGET_URL, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)
        except Exception:
            return False
        self._fill_recovery_form(page)
        return True

    def _extract_tracking_code(self, page) -> Optional[str]:
        def _normalize_digits(s: str) -> str:
            fa_digits = "۰۱۲۳۴۵۶۷۸۹"
            ar_digits = "٠١٢٣٤٥٦٧٨٩"
            res = []
            for ch in s:
                if ch in fa_digits:
                    res.append(str(fa_digits.index(ch)))
                elif ch in ar_digits:
                    res.append(str(ar_digits.index(ch)))
                else:
                    res.append(ch)
            return "".join(res)

        selectors = [
            "#ctl00_ContentPlaceHolder1_lblTrackingCode",
            "#ctl00_ContentPlaceHolder1_lblTraceCD",
            "#ctl00_ContentPlaceHolder1_lblTraceCode",
            "#ctl00_ContentPlaceHolder1_lblTrCD",
            "span[id*='TrackingCode']",
            "label[id*='TrackingCode']",
            "span[id*='Trace']",
            "label[id*='Trace']",
            "td:has-text('کد رهگیری') + td",
            "td:has-text('کد رهگیری')",
            "tr:has-text('کد رهگیری')",
            ".alert-success",
        ]
        for selector in selectors:
            try:
                locator = self._first_visible_locator(page, [selector])
                if locator is None:
                    continue
                text = _normalize_digits((locator.inner_text() or "").strip())
                for match in re.finditer(r"(\d{6,20})", text):
                    candidate = match.group(1)
                    if candidate != self.nid and len(candidate) >= 8:
                        return candidate
            except Exception:
                continue

        try:
            body = _normalize_digits(page.inner_text("body") or "")
        except Exception:
            body = ""

        patterns = [
            r"کد\s*رهگیری\s*(?:شما)?\s*[:\-]?\s*(\d{6,20})",
            r"شماره\s*پیگیری\s*[:\-]?\s*(\d{6,20})",
            r"کد\s*پیگیری\s*[:\-]?\s*(\d{6,20})",
            r"tracking\s*code\s*[:\-]?\s*(\d{6,20})",
            r"trace\s*code\s*[:\-]?\s*(\d{6,20})",
        ]
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                candidate = match.group(1)
                if candidate != self.nid and len(candidate) >= 8:
                    return candidate
        return None

    def _solve_firewall_loop(self, page, stop_event) -> bool:
        self.log("فایروال شناسایی شد؛ منتظر ورود دستی کپچا هستم.", "warning", page)
        deadline = time.time() + int(self.settings.get("firewall_manual_timeout", 180))
        while not stop_event.is_set():
            if self.solve_firewall(page):
                delay = 0.1 if self.settings.get("firewall_solve_fast") else 0.3
                if sleep_with_stop(stop_event, delay):
                    return False
            if not self.is_firewall_challenge(page):
                self.log("فایروال رفع شد.", "success", page)
                return True
            if time.time() > deadline:
                self.log("مهلت ورود دستی کپچای فایروال تمام شد.", "error", page)
                DBHandler.update_status(self.nid, "FIREWALL_TIMEOUT", "Recovery firewall timeout")
                return False
            if sleep_with_stop(stop_event, 0.5):
                return False
        return False

    def _solve_and_submit_stage1(self, page) -> bool:
        if self._page_closed(page):
            return False
        if self._detect_system_busy(page):
            return False

        captcha_img = self._first_visible_locator(page, [".BDC_CaptchaImage"])
        captcha_inp = BrowserActions.find_captcha_input(page, "#ctl00_ContentPlaceHolder1_tbCaptcha1")
        submit_btn = self._first_visible_locator(page, ["#ctl00_ContentPlaceHolder1_btnGetTraceCD"])

        if captcha_img is None or captcha_inp is None or submit_btn is None:
            now = time.time()
            if now - self._last_missing_controls_log_ts > 2.0:
                self.log("المان های کپچا/ارسال مرحله اول پیدا نشد.", "warning", page)
                self._last_missing_controls_log_ts = now
            return False

        return self.solve_captcha_with_model(
            page,
            captcha_inp,
            submit_btn,
            stop_event=self._active_stop_event,
            source="recovery_step1",
            captcha_element=captcha_img,
        )

    def _second_stage_visible(self, page) -> bool:
        otp_input = self._first_visible_locator(page, ["#ctl00_ContentPlaceHolder1_tbMobConfCode"])
        captcha_input = self._first_visible_locator(page, ["#ctl00_ContentPlaceHolder1_tbCaptcha2"])
        continue_btn = self._first_visible_locator(page, ["#ctl00_ContentPlaceHolder1_btnContinue2"])
        return otp_input is not None and captcha_input is not None and continue_btn is not None

    def _get_otp_fresh_recovery(self, last_server_poll_ts: float):
        now = time.time()
        if now - last_server_poll_ts >= 2.0:
            try:
                server_code = self.get_otp_code(timeout=1)
                if server_code and len(str(server_code).strip()) == 6:
                    return str(server_code).strip(), "server", now
            except Exception:
                pass
            last_server_poll_ts = now

        try:
            db_code = DBHandler.get_otp(self.nid)
            if db_code and len(str(db_code).strip()) == 6:
                return str(db_code).strip(), "db", last_server_poll_ts
        except Exception:
            pass
        return None, None, last_server_poll_ts

    def _inject_otp_hot_swap(self, otp_input, code: str) -> bool:
        try:
            current = (otp_input.input_value() or "").strip()
        except Exception:
            current = ""
        if current == code:
            return False
        try:
            BrowserActions.human_fill(otp_input, code, timeout=1500)
            return True
        except Exception:
            return False

    def _solve_and_submit_stage2(self, page) -> bool:
        captcha_img = self._first_visible_locator(page, [".BDC_CaptchaImage"])
        captcha_inp = BrowserActions.find_captcha_input(page, "#ctl00_ContentPlaceHolder1_tbCaptcha2")
        continue_btn = self._first_visible_locator(page, ["#ctl00_ContentPlaceHolder1_btnContinue2"])
        if captcha_img is None or captcha_inp is None or continue_btn is None:
            return False
        return self.solve_captcha_with_model(
            page,
            captcha_inp,
            continue_btn,
            stop_event=self._active_stop_event,
            source="recovery_step2",
            captcha_element=captcha_img,
        )

    def _detect_second_stage_failure(self, page) -> bool:
        try:
            body = (page.inner_text("body") or "").lower()
        except Exception:
            return False
        markers = ["نامعتبر", "منقضی", "اشتباه", "کد تایید", "کد تأیید"]
        return any(marker in body for marker in markers)

    def _run_second_stage_sms_confirmation(self, page, stop_event) -> Optional[str]:
        self.log("ورود به مرحله دوم بازیابی (تایید پیامکی).", "warning", page)
        last_server_poll_ts = 0.0
        last_injected_code = None

        while not stop_event.is_set():
            if self._page_closed(page):
                return None
            if self.is_firewall_challenge(page):
                if not self._solve_firewall_loop(page, stop_event):
                    return None
                continue

            tracking = self._extract_tracking_code(page)
            if tracking:
                return tracking

            if not self._second_stage_visible(page):
                now = time.time()
                if now - self._last_step2_log_ts > 2.0:
                    self.log("انتظار برای فرم مرحله دوم...", "info", page)
                    self._last_step2_log_ts = now
                if sleep_with_stop(stop_event, 0.3):
                    return None
                continue

            otp_input = self._first_visible_locator(page, ["#ctl00_ContentPlaceHolder1_tbMobConfCode"])
            if otp_input is None:
                if sleep_with_stop(stop_event, 0.3):
                    return None
                continue

            code, source, last_server_poll_ts = self._get_otp_fresh_recovery(last_server_poll_ts)
            if not code:
                now = time.time()
                if now - self._last_step2_log_ts > 2.0:
                    self.log("کد تایید هنوز نرسیده؛ پایش فعال ادامه دارد.", "info", page)
                    self._last_step2_log_ts = now
                if sleep_with_stop(stop_event, 0.3):
                    return None
                continue

            if self._inject_otp_hot_swap(otp_input, code):
                self.log(f"کد تایید جدید از منبع {source} در فرم وارد شد.", "success", page)
            last_injected_code = code

            if not self._solve_and_submit_stage2(page):
                if sleep_with_stop(stop_event, 0.3):
                    return None
                continue

            wait_deadline = time.time() + 10.0
            while time.time() < wait_deadline and not stop_event.is_set():
                if self._page_closed(page):
                    return None
                if self.is_firewall_challenge(page):
                    break
                tracking = self._extract_tracking_code(page)
                if tracking:
                    DBHandler.save_recovered_tracking_code(self.nid, tracking)
                    return tracking
                new_code, _, last_server_poll_ts = self._get_otp_fresh_recovery(last_server_poll_ts)
                if new_code and new_code != last_injected_code:
                    self.log("کد تایید جدید رسید؛ ارسال قبلی کنار گذاشته شد.", "warning", page)
                    break
                if self._detect_captcha_failure(page) or self._detect_second_stage_failure(page):
                    self.log("خطا در تایید پیامکی/کپچای مرحله دوم؛ تلاش مجدد.", "warning", page)
                    self._refresh_captcha(page)
                    break
                if sleep_with_stop(stop_event, 0.3):
                    return None
        return None

    def _main_recovery_loop(self, page, stop_event) -> Optional[str]:
        self.log("شروع حلقه بازیابی کد رهگیری.", "info", page)
        missing_controls_count = 0
        while not stop_event.is_set():
            if self._page_closed(page):
                self.log("صفحه/مرورگر بسته شد؛ پایان بازیابی.", "warning", page)
                return None

            if self._detect_system_busy(page):
                if not self._handle_system_busy(page, stop_event):
                    return None
                missing_controls_count = 0
                continue

            if self.is_firewall_challenge(page):
                if not self._solve_firewall_loop(page, stop_event):
                    return None
                continue

            if self._second_stage_visible(page):
                step2_code = self._run_second_stage_sms_confirmation(page, stop_event)
                if step2_code:
                    return step2_code
                if stop_event.is_set():
                    return None
                continue

            existing = self._extract_tracking_code(page)
            if existing:
                return existing

            if not self._solve_and_submit_stage1(page):
                missing_controls_count += 1
                if missing_controls_count >= 10:
                    self.log("به دلیل نبودن مداوم المان ها، صفحه بازیابی مجددا بارگذاری می شود.", "warning", page)
                    try:
                        safe_goto(page, TARGET_URL, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)
                        self._fill_recovery_form(page)
                    except Exception:
                        return None
                    missing_controls_count = 0
                if sleep_with_stop(stop_event, 0.3):
                    return None
                continue
            missing_controls_count = 0

            # After stage1 submit: check for direct tracking OR transition to stage2.
            wait_deadline = time.time() + 10.0
            while time.time() < wait_deadline and not stop_event.is_set():
                if self._page_closed(page):
                    return None
                if self._detect_system_busy(page):
                    break
                if self.is_firewall_challenge(page):
                    break
                if self._second_stage_visible(page):
                    step2_code = self._run_second_stage_sms_confirmation(page, stop_event)
                    if step2_code:
                        return step2_code
                    break
                code = self._extract_tracking_code(page)
                if code:
                    DBHandler.save_recovered_tracking_code(self.nid, code)
                    return code
                if self._detect_captcha_failure(page):
                    self.log("کپچای مرحله اول رد شد؛ رفرش و تلاش مجدد.", "warning", page)
                    DBHandler.append_captcha_attempt(self.nid, "", "failed", source="recovery_step1")
                    self._refresh_captcha(page)
                    break
                if sleep_with_stop(stop_event, 0.3):
                    return None
            if sleep_with_stop(stop_event, 0.2):
                return None
        return None
