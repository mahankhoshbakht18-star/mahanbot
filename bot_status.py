# bot_status.py

import time

import json

import re

import os

from browser_launcher import (

    BrowserLaunchError,

    PlaywrightError,

    close_browser,

    launch_browser,

    safe_goto,

)

from database import DBHandler

from api_solver import CaptchaService

from browser_actions import BrowserActions

from bot_helpers import sleep_with_stop

from errors import JobRunError





TARGET_URL = "https://ve.cbi.ir/TasTrace.aspx"





class StatusBot:

    def __init__(self, nid, settings, log_callback=None, captcha_service=None, browser_profile=None):

        self.nid = nid

        self.settings = settings

        self.log = log_callback

        self.browser_profile = browser_profile or {}

        self.captcha_service = captcha_service or CaptchaService()

        self.user_data = self._load_user_data()



        self.saved_receipt = False

        self.last_site_msg = None



        self.base_dir = os.path.dirname(os.path.abspath(__file__))

        self.save_dir = os.path.join(self.base_dir, "مشاهده_وضعیت")

        os.makedirs(self.save_dir, exist_ok=True)



    @staticmethod

    def _mask(value):

        text = str(value or "")

        if len(text) <= 4:

            return "*" * len(text)

        return text[:2] + "*" * (len(text) - 4) + text[-2:]



    def _load_user_data(self):

        row = DBHandler.get_applicant(self.nid)

        if row:

            user = dict(row)

            if user.get("data"):

                try:

                    return json.loads(user["data"])

                except Exception:

                    return {}

        return {}



    def log_msg(self, msg, level="info", meta=None):

        if self.log:

            try:

                self.log(self.nid, msg, level, meta)

            except TypeError:

                self.log(self.nid, msg, level)

        console_line = f"[{time.strftime('%H:%M:%S')}][{level.upper()}] {self._mask(self.nid)}: {msg}"

        try:

            print(console_line)

        except UnicodeEncodeError:

            print(console_line.encode("ascii", "replace").decode("ascii"))



    def _solve_captcha_with_model(self, captcha_img, captcha_input, submit_button, source):
        """Same shared CBI BotDetect path used by register / select / OTP."""
        if self._stop_event.is_set():
            return False
        try:
            from captcha_engine import solve_and_fill_captcha
            page = getattr(captcha_input, "page", None) or getattr(captcha_img, "page", None)
            if page is None:
                return False
            DBHandler.update_status(self.nid, "Processing", "Solving CAPTCHA locally")
            res = solve_and_fill_captcha(
                page,
                button_selector="",
                submit=False,
                max_retries=3,
                bot=self,
                stop_event=self._stop_event,
                step="STATUS",
            )
            if not res.filled:
                return False
            if res.code:
                DBHandler.append_captcha_attempt(self.nid, res.code, "submitted", source=source or "status")
            return True
        except Exception as exc:
            self.log_msg(f"خطا در حل محلی کپچا: {exc}", "warning")
            return False



    def _categorize_inquiry_error(self, exc=None, message=None):

        text = str(exc).lower() if exc else ""

        msg = (message or "").lower()

        if "timeout" in text or "timed out" in text or "timeout" in msg:

            return "timing"

        if "target closed" in text or "browser" in text or "closed" in text:

            return "client"

        if "captcha" in text or "کد امنیتی" in msg:

            return "data"

        if "connection" in text or "network" in text:

            return "timing"

        if "server" in text or "خطای سرور" in msg or "در دسترس" in msg:

            return "server"

        return "server"



    def _handle_inquiry_error(self, context, exc=None, message=None, extra=None):

        category = self._categorize_inquiry_error(exc, message)

        meta = {

            "category": category,

            "context": context,

        }

        if exc:

            meta["error"] = str(exc)

        if message:

            meta["message"] = message

        if extra:

            meta["extra"] = extra

        if category == "timing":
            self.log_msg("⚠️ خطای موقت شبکه یا تاخیر در استعلام وضعیت (TasTrace). وضعیت متقاضی تغییر نکرد.", "warning", meta)
        else:
            self.log_msg(f"⛔ خطای استعلام وضعیت ({category}).", "warning", meta)
            try:
                DBHandler.update_status(self.nid, "خطای استعلام", f"Inquiry error ({category}): {message or str(exc or '')[:50]}")
            except Exception:
                pass

        try:
            self._stop_event.set()
        except Exception:
            pass



    def _wrap_page_navigation(self, page):

        original_goto = page.goto



        def guarded_goto(url, **kwargs):

            return safe_goto(page, url, log_callback=self._log_for_safe_goto, **kwargs)



        page.goto = guarded_goto  # type: ignore[assignment]

        page._original_goto = original_goto  # type: ignore[attr-defined]



    def _log_for_safe_goto(self, message, level="warning", page=None, meta=None):

        self.log_msg(message, level, meta)



    def safe_visible(self, locator):

        try:

            return locator.count() > 0 and locator.is_visible()

        except Exception:

            return False



    def wait_for_any_success_indicator(self, page, timeout=7000):

        success_indicators = [

            "استعلام آخرین وضعیت ثبت درخواست",

            "جایگاه در صف انتظار",

            "تاریخ ثبت درخواست",

            "شعبه پیشنهادی شما",

        ]



        start = time.time()

        while (time.time() - start) * 1000 < timeout:

            if page.is_closed():

                return False

            if sleep_with_stop(self._stop_event, 0.3):

                return False

            try:

                body_text = page.inner_text("body")

                if any(x in body_text for x in success_indicators):

                    return True

            except Exception:

                pass

        return False



    def get_site_message(self, page):

        try:

            lbl_msg = page.locator("span[id*='lblMessage']")

            if self.safe_visible(lbl_msg):

                msg_text = lbl_msg.inner_text().strip()

                if msg_text:

                    return msg_text

        except Exception:

            pass

        return ""



    def solve_firewall_if_exists(self, page):

        try:

            if page.is_closed() or self._stop_event.is_set():

                return False



            ans = page.locator("#ans")

            if self.safe_visible(ans):

                self.log_msg("🛡️ فایروال شناسایی شد. منتظر ورود دستی کاربر...", "warning")

                return False



        except Exception:

            return False



        return False



    def check_success_and_save(self, page):

        try:

            if page.is_closed() or self._stop_event.is_set():

                return False, ""



            try:

                page.wait_for_load_state("domcontentloaded", timeout=5000)

            except Exception:

                pass



            try:

                body_text = page.inner_text("body")

            except Exception:

                body_text = ""



            success_indicators = [

                "استعلام آخرین وضعیت ثبت درخواست",

                "جایگاه در صف انتظار",

                "تاریخ ثبت درخواست",

                "شعبه پیشنهادی شما",

                "صف ذخیره",

                "درخواست خود را بصورت رزرو ثبت کرده اید",

                "در انتظار دریافت کد رهگیری",

            ]



            result_table = page.locator("#ctl00_ContentPlaceHolder1_GridView1")

            url_matches = "TasTrace.aspx" in (page.url or "")

            table_visible = self.safe_visible(result_table)

            is_logged_in = table_visible and url_matches



            if not is_logged_in:

                is_logged_in = any(indicator in body_text for indicator in success_indicators)

            if not is_logged_in:

                return False, ""



            info_text = ""

            match_state = re.search(r"صف انتظار.*?استان\s*:\s*(\d+)", body_text, re.S)

            if match_state:

                info_text += f" | نوبت استان: {match_state.group(1)}"



            match_reserve = re.search(r"جایگاه در صف ذخیره.*?(?:در انتظار دریافت کد رهگیری\s*:\s*|\s*:\s*)(\d+)", body_text, re.S)

            if match_reserve:

                reserve_rank = match_reserve.group(1)

                info_text += f" | نوبت صف ذخیره: {reserve_rank}"

                try:

                    DBHandler.update_status(self.nid, f"صف ذخیره ({reserve_rank})", f"استعلام موفق: نوبت در صف ذخیره {reserve_rank}")

                except Exception:

                    pass



            if self.saved_receipt:

                return True, info_text



            try:

                file_path = os.path.join(self.save_dir, f"{self.nid}.png")

                page.screenshot(path=file_path, full_page=True)

                self.saved_receipt = True

                self.log_msg(f"📸 رسید وضعیت ذخیره شد: {self._mask(self.nid)}.png", "success")

            except Exception as e:

                self.log_msg(f"خطا در ذخیره عکس: {e}", "warning")



            return True, info_text



        except Exception:

            return False, ""




    def _stamp_field(self, locator):
        locator.evaluate(
            """(el) => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new FocusEvent('blur', { bubbles: true }));
            }"""
        )

    def _field_text(self, locator) -> str:
        try:
            return (locator.input_value(timeout=400) or "").strip()
        except Exception:
            return ""

    def _commit_status_fields(self, nid_input, track_input, captcha_input, tracking_code) -> bool:
        """Validators only see a value after input, change, and blur."""
        nid = str(self.nid or "").strip()
        if self._field_text(nid_input) != nid:
            nid_input.fill(nid, timeout=500)
        self._stamp_field(nid_input)
        if self._field_text(nid_input) != nid:
            self.log_msg("کد ملی قبل از ارسال خالی است؛ فرم ارسال نمی‌شود.", "warning")
            return False
        track = str(tracking_code or "").strip()
        if track:
            if self._field_text(track_input) != track:
                track_input.fill(track, timeout=500)
            self._stamp_field(track_input)
            if self._field_text(track_input) != track:
                self.log_msg("کد رهگیری قبل از ارسال خالی است؛ فرم ارسال نمی‌شود.", "warning")
                return False
        if not self._field_text(captcha_input):
            self.log_msg("فیلد کپچا قبل از ارسال خالی است؛ فرم ارسال نمی‌شود.", "warning")
            return False
        self._stamp_field(captcha_input)
        try:
            captcha_input.press("Tab", timeout=300)
        except Exception:
            pass
        return True

    def _send_status_postback(self, page, captcha_input, btn_trace, captcha_img, src_before_click) -> str:
        """Click once, then wait for a real postback. Never refresh the captcha on a timer."""
        state = {"post": False, "response": False, "alert": ""}

        def on_request(request):
            try:
                url = request.url or ""
                if request.method == "POST" and "TasTrace.aspx" in url:
                    state["post"] = True
            except Exception:
                pass

        def on_response(response):
            try:
                url = response.url or ""
                if response.request.method == "POST" and "TasTrace.aspx" in url:
                    state["response"] = True
            except Exception:
                pass

        def on_dialog(dialog):
            try:
                state["alert"] = dialog.message or ""
            except Exception:
                state["alert"] = ""
            try:
                dialog.accept()
            except Exception:
                try:
                    dialog.dismiss()
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("dialog", on_dialog)
        try:
            try:
                btn_trace.click(timeout=500, force=False)
            except Exception:
                pass
            deadline = time.perf_counter() + 0.5
            while time.perf_counter() < deadline and not state["post"]:
                time.sleep(0.05)
            if not state["post"]:
                try:
                    captcha_input.press("Enter", timeout=400)
                except Exception:
                    pass
                time.sleep(0.35)
            if not state["post"]:
                try:
                    page.evaluate(
                        """() => {
                            const btn = document.getElementById('ctl00_ContentPlaceHolder1_btnTrace');
                            const target = btn && btn.name ? btn.name : 'ctl00$ContentPlaceHolder1$btnTrace';
                            if (typeof window.__doPostBack === 'function') window.__doPostBack(target, '');
                            else if (btn) btn.click();
                        }"""
                    )
                except Exception:
                    pass

            if not state["post"]:
                try:
                    page._mahan_hold_captcha = True
                except Exception:
                    pass
                self.log_msg("کلیک ارسال پست‌بک نساخت؛ همان کپچا بدون رفرش دوباره فرستاده می‌شود.", "warning")
                return "retry_same"

            outcome = self._watch_status_postback(page, captcha_img, src_before_click, state)
            if outcome == "captcha_rejected":
                self.log_msg("کپچا رفرش شد. تلاش مجدد...", "warning")
                return "soft_retry"
            if outcome == "success":
                return "submitted"
            if outcome == "message":
                return "submitted"
            self._awaiting_postback_until = time.time() + 4
            self.log_msg("پاسخ پست‌بک هنوز قطعی نیست؛ کپچا رفرش نمی‌شود.", "info")
            return "waiting"
        finally:
            for event, handler in (("request", on_request), ("response", on_response), ("dialog", on_dialog)):
                try:
                    page.remove_listener(event, handler)
                except Exception:
                    pass

    def _watch_status_postback(self, page, captcha_img, src_before_click, state) -> str:
        success_markers = (
            "استعلام آخرین وضعیت ثبت درخواست",
            "جایگاه در صف انتظار",
            "تاریخ ثبت درخواست",
            "شعبه پیشنهادی شما",
            "صف ذخیره",
            "در انتظار دریافت کد رهگیری",
        )
        deadline = time.time() + 15
        src_changed_at = 0.0
        while time.time() < deadline and not self._stop_event.is_set():
            alert = state.get("alert") or ""
            if alert:
                return "captcha_rejected" if self._is_captcha_error(alert) else "message"
            if self.safe_visible(page.locator("#ctl00_ContentPlaceHolder1_GridView1")):
                return "success"
            body = ""
            try:
                body = page.inner_text("body", timeout=400) or ""
            except Exception:
                body = ""
            if any(marker in body for marker in success_markers):
                return "success"
            msg = self.get_site_message(page)
            if msg:
                return "captcha_rejected" if self._is_captcha_error(msg) else "message"
            if state.get("response"):
                new_src = ""
                try:
                    new_src = captcha_img.get_attribute("src") or ""
                except Exception:
                    new_src = ""
                time.sleep(0.35)
                if self.safe_visible(page.locator("#ctl00_ContentPlaceHolder1_GridView1")):
                    return "success"
                try:
                    body = page.inner_text("body", timeout=400) or ""
                except Exception:
                    body = ""
                if any(marker in body for marker in success_markers):
                    return "success"
                msg = self.get_site_message(page)
                if msg:
                    return "captcha_rejected" if self._is_captcha_error(msg) else "message"
                if src_before_click and new_src and new_src != src_before_click and not src_changed_at:
                    src_changed_at = time.time()
            if src_changed_at and time.time() - src_changed_at >= 3:
                return "captcha_rejected"
            time.sleep(0.12)
        return "captcha_rejected" if src_changed_at else ""

    @staticmethod
    def _is_captcha_error(text: str) -> bool:
        raw = str(text or "")
        has_captcha = any(word in raw for word in ("کد امنیتی", "کپچا", "عبارت امنیتی"))
        has_reject = any(word in raw for word in ("نامعتبر", "نادرست", "اشتباه", "صحیح"))
        return has_captcha and has_reject

    def submit_form_with_captcha(self, page, tracking_code):

        try:

            if self._stop_event.is_set():

                return "idle"



            captcha_selector = "input[name='ctl00$ContentPlaceHolder1$tbCaptcha1']"

            captcha_input = BrowserActions.find_captcha_input(page, captcha_selector)

            nid_input = page.locator("input[name='ctl00$ContentPlaceHolder1$tbIDNo']")

            track_input = page.locator("input[name='ctl00$ContentPlaceHolder1$tbTraceCD']")

            btn_trace = page.locator("#ctl00_ContentPlaceHolder1_btnTrace")

            captcha_img = page.locator("#c_tastrace_ctl00_contentplaceholder1_captcha1_CaptchaImage")



            if captcha_input is None or not self.safe_visible(captcha_input):

                return "idle"



            try:

                if not nid_input.input_value():

                    try:

                        BrowserActions.human_fill(nid_input, self.nid, timeout=750)

                    except Exception:

                        BrowserActions.fast_fill(nid_input, self.nid, timeout=750)

            except Exception:

                try:

                    BrowserActions.human_fill(nid_input, self.nid, timeout=750)

                except Exception:

                    BrowserActions.fast_fill(nid_input, self.nid, timeout=750)



            try:

                if tracking_code:

                    if not track_input.input_value():

                        try:

                            BrowserActions.human_fill(track_input, tracking_code, timeout=750)

                        except Exception:

                            BrowserActions.fast_fill(track_input, tracking_code, timeout=750)

                else:

                    # CBI allows empty tracking code for reserve queue inquiries

                    track_input.fill("")

            except Exception:

                if tracking_code:

                    try:

                        BrowserActions.human_fill(track_input, tracking_code, timeout=750)

                    except Exception:

                        BrowserActions.fast_fill(track_input, tracking_code, timeout=750)



            try:

                captcha_img.wait_for(state="visible", timeout=2500)

            except Exception:

                return "idle"



            if self._stop_event.is_set():

                return "idle"



            if not self._solve_captcha_with_model(captcha_img, captcha_input, btn_trace, "status"):
                return "idle"
            if not self._commit_status_fields(nid_input, track_input, captcha_input, tracking_code):
                return "idle"
            src_before_click = ""
            try:
                src_before_click = captcha_img.get_attribute("src") or ""
            except Exception:
                pass
            return self._send_status_postback(page, captcha_input, btn_trace, captcha_img, src_before_click)



        except Exception as exc:

            self._handle_inquiry_error(

                "submit_form_with_captcha",

                exc=exc,

                extra={"tracking_code": self._mask(tracking_code)},

            )

            return "blocked"



    def _keep_result_open(self, page, stop_event, poll_seconds=0.5):
        """Leave the status page on screen until the user closes the window or presses Stop.

        Nothing else is started from here: no reload, no new browser, no next applicant.
        """
        self.log_msg("🪟 نتیجه استعلام روی مرورگر باز ماند؛ برای پایان، پنجره را ببندید یا «توقف» را بزنید.", "info")
        while not stop_event.is_set():
            try:
                if page is None or page.is_closed():
                    break
                # Keeps the Playwright connection serviced and notices a closed window.
                page.wait_for_timeout(int(poll_seconds * 1000))
            except Exception:
                break
        DBHandler.update_runtime(self.nid, browser_status="CLOSED")

    def run(self, stop_event):

        playwright = None

        browser = None

        context = None

        page = None

        self._stop_event = stop_event



        try:

            tracking_code = self.user_data.get("tracking_code") or ""

            if not tracking_code:

                self.log_msg("ℹ️ کد رهگیری خالی است؛ طبق دستورالعمل سامانه، استعلام متقاضی صف ذخیره (رزرو) بدون نیاز به کد رهگیری انجام می‌شود.", "info")

            else:

                self.log_msg(f"شروع استعلام برای کد: {self._mask(tracking_code)}", "info")



            def open_target_browser():
                self.log_msg("🌐 در حال باز کردن مرورگر...", "info")
                try:
                    from core.browser_profile_health import BrowserProfileHealthManager
                    udd = (self.browser_profile or {}).get("user_data_dir") or getattr(self, "user_dir", None)
                    if udd:
                        BrowserProfileHealthManager.clear_stale_locks(udd)
                except Exception:
                    pass
                new_playwright, new_browser, new_context, new_page = launch_browser(self.browser_profile)

                self._wrap_page_navigation(new_page)

                new_page.set_default_timeout(8000)

                new_page.set_default_navigation_timeout(45000)

                loaded = False

                for attempt_nav in range(1, 6):

                    if stop_event.is_set():

                        break

                    try:

                        self.log_msg(f"در حال بارگذاری سامانه استعلام وضعیت (تلاش {attempt_nav} از 5)...", "info")

                        new_page.goto(TARGET_URL, timeout=45000, wait_until="commit")

                        try:

                            new_page.wait_for_load_state("domcontentloaded", timeout=12000)

                        except Exception:

                            pass

                        loaded = True

                        break

                    except Exception as e_nav:

                        # Check if target form elements are already present despite timeout

                        try:

                            if new_page.locator("input[name$='tbTraceCD'], input[name$='tbIDNo'], .BDC_CaptchaImage").count() > 0:

                                loaded = True

                                break

                        except Exception:

                            pass

                        self.log_msg(f"⚠️ تاخیر در پاسخ سرور استعلام ({e_nav})؛ شکیبایی و تلاش مجدد بدون بستن مرورگر...", "warning")

                        if sleep_with_stop(stop_event, 2.0, step=0.1):

                            break

                if not loaded and not stop_event.is_set():

                    try:

                        if new_page.locator("input[name$='tbTraceCD'], input[name$='tbIDNo']").count() > 0:

                            loaded = True

                    except Exception:

                        pass

                if not loaded and not stop_event.is_set():
                    close_browser(new_playwright, new_browser, new_context, new_page)
                    self.log_msg("⚠️ عدم پاسخ‌دهی سامانه استعلام بانک مرکزی (TasTrace) پس از ۵ تلاش؛ فرآیند استعلام بدون تغییر وضعیت پایان یافت.", "warning")
                    return None, None, None, None
                return new_playwright, new_browser, new_context, new_page



            def reopen_target_browser(reason):

                nonlocal playwright, browser, context, page

                close_browser(playwright, browser, context, page)

                playwright = browser = context = page = None

                if sleep_with_stop(stop_event, 0.15, step=0.05):

                    return False

                self.log_msg(f"مرورگر متوقف/بسته شد ({reason})؛ بازکردن فوری مرورگر جدید...", "warning")

                playwright, browser, context, page = open_target_browser()

                self.log_msg("مرورگر جدید باز شد و ادامه عملیات انجام می‌شود.", "info")

                return True



            while not stop_event.is_set():

                try:

                    playwright, browser, context, page = open_target_browser()
                    if page is not None:
                        break
                    else:
                        return

                except (BrowserLaunchError, PlaywrightError) as exc:

                    self.log_msg(f"بازکردن مرورگر ناموفق بود؛ تلاش مجدد سریع: {exc}", "warning")

                    if sleep_with_stop(stop_event, 0.35, step=0.05):

                        return



            if page is None:

                return



            while not stop_event.is_set():

                try:

                    if page is None or page.is_closed():

                        if not reopen_target_browser("page closed"):

                            break

                        continue



                    current_url = page.url or ""

                    if "TasTrace.aspx" not in current_url:

                        try:

                            page.goto(TARGET_URL, timeout=15000, wait_until="commit")

                        except PlaywrightError as e:

                            if "Target closed" in str(e):

                                raise e

                            self.log_msg("⚠️ مشکل اینترنت. تلاش مجدد...", "warning")

                            if sleep_with_stop(stop_event, 0.4, step=0.05):

                                break

                            continue



                    if stop_event.is_set():

                        break



                    if self.solve_firewall_if_exists(page):

                        if sleep_with_stop(stop_event, 0.15, step=0.05):

                            break

                        continue



                    is_success, extracted_info = self.check_success_and_save(page)

                    if is_success:

                        self.log_msg(f"✅ موفقیت! {extracted_info}", "success")

                        self.log_msg("🎉 رسید ذخیره شد و عملیات پایان یافت.", "success")
                        DBHandler.update_status(self.nid, "success", "استعلام وضعیت با موفقیت ذخیره شد")
                        DBHandler.update_runtime(
                            self.nid,
                            status="SUCCESS",
                            current_step="DONE",
                            browser_status="OPEN",
                        )
                        self._keep_result_open(page, stop_event)
                        break



                    msg_text = self.get_site_message(page)

                    if msg_text and msg_text != self.last_site_msg:

                        self.last_site_msg = msg_text

                        self.log_msg(f"⚠️ پیام سایت: {msg_text}", "warning")



                        if "خطای سرور" in msg_text or "server error" in msg_text.lower():

                            self._handle_inquiry_error(

                                "site_message",

                                message=msg_text,

                                extra={"tracking_code": self._mask(tracking_code)},

                            )

                            break



                        if "یافت نشد" in msg_text:

                            self.log_msg("⛔ اطلاعات اشتباه است.", "error")

                            if sleep_with_stop(stop_event, 2):

                                break

                            break



                        if "در دسترس نمی باشد" in msg_text:

                            try:

                                page.reload()

                            except Exception:

                                pass

                            continue



                    if time.time() < float(getattr(self, "_awaiting_postback_until", 0) or 0):
                        if sleep_with_stop(stop_event, 0.3, step=0.05):
                            break
                        continue

                    submission_status = self.submit_form_with_captcha(page, tracking_code)

                    if submission_status == "waiting":
                        continue

                    if submission_status in {"soft_retry", "retry_same", "submitted"}:

                        continue

                    if submission_status == "blocked":

                        break



                    if sleep_with_stop(stop_event, 0.1, step=0.05):

                        break



                except PlaywrightError as pe:

                    if stop_event.is_set():

                        close_browser(playwright, browser, context, page)

                        break

                    if "Target closed" in str(pe):

                        if not reopen_target_browser("target closed"):

                            break

                        continue

                    self.log_msg(f"خطای موقت: {pe}", "warning")

                    if sleep_with_stop(stop_event, 0.35, step=0.05):

                        break



                except BrowserLaunchError as exc:

                    if stop_event.is_set():

                        close_browser(playwright, browser, context, page)

                        break

                    self.log_msg(f"خطا در مرورگر: {exc.message}؛ تلاش برای بازیابی...", "warning")

                    try:

                        if not reopen_target_browser(exc.code):

                            break

                    except (BrowserLaunchError, PlaywrightError):

                        if sleep_with_stop(stop_event, 0.35, step=0.05):

                            break



                except Exception as e:

                    if stop_event.is_set():

                        close_browser(playwright, browser, context, page)

                        break

                    error_text = str(e).lower()

                    if page is None or "target closed" in error_text or "browser has been closed" in error_text:

                        try:

                            if not reopen_target_browser("browser unavailable"):

                                break

                            continue

                        except (BrowserLaunchError, PlaywrightError):

                            if sleep_with_stop(stop_event, 0.35, step=0.05):

                                break

                            continue

                    self.log_msg(f"خطای غیرمنتظره: {e}", "error")

                    if sleep_with_stop(stop_event, 0.35, step=0.05):

                        break



        except JobRunError:

            pass

        except BrowserLaunchError as exc:

            self.log_msg(f"Error: {exc.message}", "error")

        except Exception as e:

            self.log_msg(f"Error: {e}", "error")

        finally:

            close_browser(playwright, browser, context, page)

            self.log_msg("پایان عملیات.", "stopped")

