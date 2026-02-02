# bot_status.py
import time
import json
import re
import os
from playwright.sync_api import Error as PlaywrightError
from browser_launcher import (
    BrowserLaunchError,
    close_browser,
    launch_browser,
    open_healthcheck_page,
    safe_goto,
)
from database import DBHandler
from captcha_service import CaptchaService
from errors import JobRunError


TARGET_URL = "https://ve.cbi.ir/TasTrace.aspx"


def sleep_with_stop(stop_event, seconds: float, step: float = 0.2) -> bool:
    if seconds <= 0:
        return stop_event.is_set()
    end = time.time() + seconds
    while time.time() < end:
        if stop_event.is_set():
            return True
        time.sleep(min(step, end - time.time()))
    return stop_event.is_set()


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
        print(f"[{self.nid}] {msg}")

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
                self.log_msg("🛡️ فایروال شناسایی شد. در حال حل...", "warning")

                captcha_box = page.locator("img[src*='base64']").first
                if not self.safe_visible(captcha_box):
                    captcha_box = ans.locator("xpath=..").locator("img").first

                if self.safe_visible(captcha_box):
                    captcha_bytes = captcha_box.screenshot()
                    solved_code = self.captcha_service.solve(captcha_bytes, mode="firewall")

                    if solved_code:
                        self.log_msg(f"کد فایروال: {solved_code}", "info")
                        ans.fill(solved_code)
                        page.click("#jar")
                        page.wait_for_timeout(800)
                        return True
                    else:
                        page.reload()
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

            body_text = page.inner_text("body")

            success_indicators = [
                "استعلام آخرین وضعیت ثبت درخواست",
                "جایگاه در صف انتظار",
                "تاریخ ثبت درخواست",
                "شعبه پیشنهادی شما",
            ]

            is_logged_in = any(indicator in body_text for indicator in success_indicators)
            if not is_logged_in:
                return False, ""

            info_text = ""
            match_state = re.search(r"صف انتظار.*?استان\s*:\s*(\d+)", body_text, re.S)
            if match_state:
                info_text += f" | نوبت استان: {match_state.group(1)}"

            if self.saved_receipt:
                return True, info_text

            try:
                file_path = os.path.join(self.save_dir, f"{self.nid}.png")
                page.screenshot(path=file_path, full_page=True)
                self.saved_receipt = True
                self.log_msg(f"📸 رسید وضعیت ذخیره شد: {file_path}", "success")
            except Exception as e:
                self.log_msg(f"خطا در ذخیره عکس: {e}", "warning")

            return True, info_text

        except Exception:
            return False, ""

    def submit_form_with_captcha(self, page, tracking_code):
        try:
            if self._stop_event.is_set():
                return False

            captcha_input = page.locator("input[name='ctl00$ContentPlaceHolder1$tbCaptcha1']")
            nid_input = page.locator("input[name='ctl00$ContentPlaceHolder1$tbIDNo']")
            track_input = page.locator("input[name='ctl00$ContentPlaceHolder1$tbTraceCD']")
            btn_trace = page.locator("#ctl00_ContentPlaceHolder1_btnTrace")
            captcha_img = page.locator("#c_tastrace_ctl00_contentplaceholder1_captcha1_CaptchaImage")

            if not self.safe_visible(captcha_input):
                return False

            try:
                if not nid_input.input_value():
                    nid_input.fill(self.nid)
            except Exception:
                nid_input.fill(self.nid)

            try:
                if not track_input.input_value():
                    track_input.fill(tracking_code)
            except Exception:
                track_input.fill(tracking_code)

            try:
                current_captcha_val = captcha_input.input_value()
            except Exception:
                current_captcha_val = ""

            if current_captcha_val.strip():
                return False

            try:
                captcha_img.wait_for(state="visible", timeout=7000)
            except Exception:
                return False

            page.wait_for_timeout(300)
            if self._stop_event.is_set():
                return False

            captcha_bytes = captcha_img.screenshot()
            solved = self.captcha_service.solve(captcha_bytes, mode="general")
            if not solved or len(str(solved).strip()) < 4:
                try:
                    page.locator(".BDC_ReloadLink").first.click()
                except Exception:
                    pass
                return False

            captcha_input.fill(str(solved).strip())
            btn_trace.click()

            # Wait a bit, then next loop will check success indicators
            page.wait_for_timeout(500)
            return True

        except Exception:
            return False

    def run(self, stop_event):
        playwright = None
        browser = None
        context = None
        page = None
        self._stop_event = stop_event

        try:
            playwright, browser, context, page = launch_browser(self.browser_profile)
            self._wrap_page_navigation(page)
            open_healthcheck_page(page, log_callback=self._log_for_safe_goto)

            tracking_code = self.user_data.get("tracking_code")
            if not tracking_code:
                error = JobRunError(
                    "MISSING_TRACKING_CODE",
                    "کد رهگیری برای این متقاضی ثبت نشده است.",
                    {"nid": self.nid},
                )
                self.log_msg(error.message, "error", error.to_dict())
                raise error

            self.log_msg(f"شروع استعلام برای: {tracking_code}", "info")

            # ✅ DIRECT NAVIGATION (must happen immediately)
            try:
                page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")
            except Exception:
                if sleep_with_stop(stop_event, 1.0):
                    return
                page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")

            page.set_default_timeout(15000)

            while not stop_event.is_set():
                try:
                    if page.is_closed():
                        self.log_msg("مرورگر توسط کاربر بسته شد.", "stopped")
                        break

                    # Keep on target
                    current_url = page.url or ""
                    if "TasTrace.aspx" not in current_url:
                        try:
                            page.goto(TARGET_URL, timeout=30000, wait_until="domcontentloaded")
                            page.wait_for_timeout(500)
                        except PlaywrightError as e:
                            if "Target closed" in str(e):
                                raise e
                            self.log_msg("⚠️ مشکل اینترنت. تلاش مجدد...", "warning")
                            if sleep_with_stop(stop_event, 3):
                                break
                            continue

                    if stop_event.is_set():
                        break

                    if self.solve_firewall_if_exists(page):
                        if sleep_with_stop(stop_event, 1):
                            break
                        continue

                    is_success, extracted_info = self.check_success_and_save(page)
                    if is_success:
                        self.log_msg(f"✅ موفقیت! {extracted_info}", "success")
                        self.log_msg("🎉 رسید ذخیره شد و عملیات پایان یافت.", "success")
                        while not stop_event.is_set():
                            time.sleep(1)
                        break

                    msg_text = self.get_site_message(page)
                    if msg_text and msg_text != self.last_site_msg:
                        self.last_site_msg = msg_text
                        self.log_msg(f"⚠️ پیام سایت: {msg_text}", "warning")

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

                    submitted = self.submit_form_with_captcha(page, tracking_code)
                    if submitted:
                        if sleep_with_stop(stop_event, 1):
                            break
                        continue

                    if sleep_with_stop(stop_event, 1):
                        break

                except PlaywrightError as pe:
                    if stop_event.is_set():
                        close_browser(playwright, browser, context, page)
                        break
                    if "Target closed" in str(pe):
                        self.log_msg("مرورگر بسته شد.", "stopped")
                        break
                    self.log_msg(f"خطای موقت: {pe}", "warning")
                    if sleep_with_stop(stop_event, 2):
                        break

                except BrowserLaunchError as exc:
                    if stop_event.is_set():
                        close_browser(playwright, browser, context, page)
                        break
                    self.log_msg(f"خطا در مرورگر: {exc.message}", "error")
                    break

                except Exception as e:
                    if stop_event.is_set():
                        close_browser(playwright, browser, context, page)
                        break
                    self.log_msg(f"خطای غیرمنتظره: {e}", "error")
                    if sleep_with_stop(stop_event, 2):
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
