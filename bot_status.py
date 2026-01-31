import time
import json
import re
import os
from playwright.sync_api import sync_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
from database import DBHandler
from messages_fa import LOG_MESSAGES
from captcha_service import CaptchaService


class StatusBot:
    def __init__(self, nid, settings, log_callback=None, captcha_service=None):
        self.nid = nid
        self.settings = settings
        self.log = log_callback

        self.user_data = self._load_user_data()
        self.captcha_service = captcha_service or CaptchaService()

        # جلوگیری از خروجی تکراری
        self.saved_receipt = False
        self.last_site_msg = None

        # مسیر ذخیره رسید (کنار همین فایل)
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.save_dir = os.path.join(self.base_dir, "مشاهده_وضعیت")
        os.makedirs(self.save_dir, exist_ok=True)

    def _load_user_data(self):
        user = DBHandler.get_applicant(self.nid)
        if user and user.get("data"):
            try:
                return json.loads(user["data"])
            except:
                return {}
        return {}

    def log_msg(self, msg, level="info"):
        if self.log:
            self.log(self.nid, msg, level)
        print(f"[{self.nid}] {msg}")

    # -----------------------------------------------------------
    # 🔥 ابزارهای کمکی
    # -----------------------------------------------------------

    def safe_visible(self, locator):
        """ایمن‌ترین روش برای چک visible بودن بدون اینکه exception بیاد"""
        try:
            return locator.count() > 0 and locator.is_visible()
        except:
            return False

    def wait_for_any_success_indicator(self, page, timeout=7000):
        """
        منتظر می‌ماند تا یکی از نشانه‌های صفحه‌ی موفقیت دیده شود (بدون نیاز به Refresh)
        """
        success_indicators = [
            "استعلام آخرین وضعیت ثبت درخواست",
            "جایگاه در صف انتظار",
            "تاریخ ثبت درخواست",
            "شعبه پیشنهادی شما",
        ]

        start = time.time()
        while (time.time() - start) * 1000 < timeout:
            try:
                if page.is_closed():
                    return False
                page.wait_for_timeout(300)
                body_text = page.inner_text("body")
                if any(x in body_text for x in success_indicators):
                    return True
            except:
                pass
        return False

    def get_site_message(self, page):
        """پیام خطای سایت از lblMessage"""
        try:
            lbl_msg = page.locator("span[id*='lblMessage']")
            if self.safe_visible(lbl_msg):
                msg_text = lbl_msg.inner_text().strip()
                if msg_text:
                    return msg_text
        except:
            pass
        return ""

    # -----------------------------------------------------------
    # 🛡️ حل فایروال
    # -----------------------------------------------------------

    def solve_firewall_if_exists(self, page):
        """
        حل کپچای ثانویه (فایروال) در صورت وجود
        """
        try:
            if page.is_closed():
                return False

            ans = page.locator("#ans")
            if self.safe_visible(ans):
                self.log_msg(LOG_MESSAGES["firewall_solving"], "warning")

                captcha_box = page.locator("img[src*='base64']").first
                if not self.safe_visible(captcha_box):
                    captcha_box = ans.locator("xpath=..").locator("img").first

                if self.safe_visible(captcha_box):
                    captcha_bytes = captcha_box.screenshot()
                    solved_code = self.captcha_service.solve(captcha_bytes, mode="firewall")

                    if solved_code:
                        self.log_msg(LOG_MESSAGES["firewall_code"].format(code=solved_code), "info")
                        ans.fill(solved_code)

                        # دکمه تایید فایروال
                        page.click("#jar")

                        # اینجا مهمه: به جای networkidle، کوتاه صبر + چک محتوا
                        page.wait_for_timeout(800)
                        return True
                    else:
                        # اگر حل نشد، یک reload نرم
                        page.reload()
                        return False

        except Exception:
            return False

        return False

    # -----------------------------------------------------------
    # ✅ تشخیص موفقیت + ذخیره رسید
    # -----------------------------------------------------------

    def check_success_and_save(self, page):
        """
        بررسی ورود موفق و ذخیره اسکرین‌شات در پوشه
        """
        try:
            if page.is_closed():
                return False, ""

            # اطمینان از آماده بودن DOM
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except:
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

            # استخراج اطلاعات
            info_text = ""
            match_state = re.search(r"صف انتظار.*?استان\s*:\s*(\d+)", body_text, re.S)
            if match_state:
                info_text += LOG_MESSAGES["status_queue_position"].format(position=match_state.group(1))

            # جلوگیری از ذخیره‌ی چندباره
            if self.saved_receipt:
                return True, info_text

            try:
                file_path = os.path.join(self.save_dir, f"{self.nid}.png")
                page.screenshot(path=file_path, full_page=True)
                self.saved_receipt = True
                self.log_msg(LOG_MESSAGES["receipt_saved_path"].format(path=file_path), "success")
            except Exception as e:
                self.log_msg(LOG_MESSAGES["receipt_save_error"].format(error=e), "warning")

            return True, info_text

        except Exception:
            return False, ""

    # -----------------------------------------------------------
    # 🚀 ارسال فرم (کپچا + کلیک) با انتظار صحیح
    # -----------------------------------------------------------

    def submit_form_with_captcha(self, page, tracking_code):
        """
        کپچا را حل می‌کند، فرم را submit می‌کند
        و به جای رفرش، منتظر نشانه‌ی موفقیت می‌ماند.
        """
        try:
            captcha_input = page.locator("input[name='ctl00$ContentPlaceHolder1$tbCaptcha1']")
            nid_input = page.locator("input[name='ctl00$ContentPlaceHolder1$tbIDNo']")
            track_input = page.locator("input[name='ctl00$ContentPlaceHolder1$tbTraceCD']")
            btn_trace = page.locator("#ctl00_ContentPlaceHolder1_btnTrace")
            captcha_img = page.locator("#c_tastrace_ctl00_contentplaceholder1_captcha1_CaptchaImage")

            if not self.safe_visible(captcha_input):
                return False

            # Fill nid + tracking code فقط اگر خالی‌اند
            try:
                if not nid_input.input_value():
                    nid_input.fill(self.nid)
            except:
                nid_input.fill(self.nid)

            try:
                if not track_input.input_value():
                    track_input.fill(tracking_code)
            except:
                track_input.fill(tracking_code)

            # اگر کپچا از قبل پر شده، دوباره حل نکن
            try:
                current_captcha_val = captcha_input.input_value()
            except:
                current_captcha_val = ""

            if current_captcha_val.strip():
                return False

            # صبر برای لود کامل تصویر کپچا
            try:
                captcha_img.wait_for(state="visible", timeout=7000)
            except:
                return False

            page.wait_for_timeout(300)
            captcha_bytes = captcha_img.screenshot()

            solved_code = self.captcha_service.solve(captcha_bytes, mode="general")
            if not solved_code:
                # Reload captcha image
                try:
                    page.locator("a[href*='ReloadImage']").click()
                except:
                    pass
                return False

            self.log_msg(LOG_MESSAGES["captcha_solved"].format(code=solved_code), "info")
            captcha_input.fill(solved_code)

            # ⭐ نکته کلیدی:
            # بعد از کلیک، بجای networkidle منتظر یکی از این‌ها می‌مانیم:
            # 1) تغییر صفحه
            # 2) یا ظهور نشانه‌های موفقیت
            # 3) یا پیام lblMessage

            page.wait_for_timeout(250)

            # کلیک روی دکمه مشاهده وضعیت
            try:
                with page.expect_response(lambda r: "TasTrace" in r.url or "Trace" in r.url, timeout=5000):
                    btn_trace.click()
            except:
                # اگر response نشد، فقط کلیک کن
                try:
                    btn_trace.click()
                except:
                    return False

            # کمی صبر برای رندر شدن نتیجه
            page.wait_for_timeout(700)

            # اگر موفقیت سریع ظاهر شود:
            if self.wait_for_any_success_indicator(page, timeout=7000):
                return True

            # اگر پیام خطا آمد
            msg_text = self.get_site_message(page)
            if msg_text:
                if msg_text != self.last_site_msg:
                    self.last_site_msg = msg_text
                    self.log_msg(LOG_MESSAGES["site_message"].format(message=msg_text), "warning")

                # اگر کد امنیتی اشتباه بود، پاک کن تا دوباره حل شود
                if "کد امنیتی" in msg_text:
                    try:
                        captcha_input.fill("")
                    except:
                        pass

                return False

            # اگر نه موفقیت نه خطا… یعنی سایت نتیجه را نیاورده → یک retry منطقی
            # این همان جایی است که قبلاً شما Refresh دستی می‌کردید.
            self.log_msg(LOG_MESSAGES["captcha_no_result"], "warning")

            # یک بار reload سبک (نه refresh دستی کاربر) برای دریافت نتیجه
            try:
                page.reload()
                page.wait_for_timeout(1000)
            except:
                pass

            return False

        except Exception as e:
            self.log_msg(LOG_MESSAGES["form_submit_error"].format(error=e), "warning")
            return False

    # -----------------------------------------------------------
    # 🧠 اجرای اصلی
    # -----------------------------------------------------------

    def run(self, stop_event):
        tracking_code = self.user_data.get("tracking_code")
        if not tracking_code:
            self.log_msg(LOG_MESSAGES["tracking_missing"], "error")
            return

        self.log_msg(LOG_MESSAGES["status_start"].format(code=tracking_code), "info")

        browser = None
        context = None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)

                context = browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
                )

                page = context.new_page()
                self.log_msg(LOG_MESSAGES["opening_site"], "info")

                # برای اینکه سریع‌تر باشه
                page.set_default_timeout(15000)

                while not stop_event.is_set():
                    try:
                        if page.is_closed():
                            self.log_msg(LOG_MESSAGES["browser_closed_by_user"], "stopped")
                            break

                        # اگر صفحه روی مقصد نیست برو
                        current_url = page.url
                        if "TasTrace" not in current_url and "Trace" not in current_url:
                            try:
                                page.goto("https://ve.cbi.ir/TasTrace.aspx", timeout=30000)
                                page.wait_for_timeout(500)
                            except PlaywrightError as e:
                                if "Target closed" in str(e):
                                    raise e
                                self.log_msg(LOG_MESSAGES["connection_issue"], "warning")
                                time.sleep(3)
                                continue

                        # 1) فایروال
                        if self.solve_firewall_if_exists(page):
                            time.sleep(1)
                            continue

                        # 2) اگر موفقیت و صفحه وضعیت آمد
                        is_success, extracted_info = self.check_success_and_save(page)
                        if is_success:
                            if extracted_info:
                                self.log_msg(LOG_MESSAGES["status_success"].format(info=extracted_info), "success")
                            else:
                                self.log_msg(LOG_MESSAGES["status_success_no_info"], "success")
                            self.log_msg(LOG_MESSAGES["receipt_saved"], "success")

                            # منتظر بماند تا stop یا بستن مرورگر
                            while not stop_event.is_set():
                                if page.is_closed():
                                    break
                                time.sleep(1)
                            break

                        # 3) پیام سایت
                        msg_text = self.get_site_message(page)
                        if msg_text and msg_text != self.last_site_msg:
                            self.last_site_msg = msg_text
                            self.log_msg(LOG_MESSAGES["site_message"].format(message=msg_text), "warning")

                            if "یافت نشد" in msg_text:
                                self.log_msg(LOG_MESSAGES["invalid_info"], "error")
                                time.sleep(2)
                                break

                            if "در دسترس نمی باشد" in msg_text:
                                page.reload()
                                continue

                        # 4) حل کپچا و submit فرم (با انتظار صحیح، بدون نیاز به refresh دستی)
                        submitted = self.submit_form_with_captcha(page, tracking_code)
                        if submitted:
                            # اگر submit گفت موفق شد، دور بعد check_success می‌گیره و ذخیره می‌کنه
                            time.sleep(1)
                            continue

                        time.sleep(1)

                    except PlaywrightError as pe:
                        if "Target closed" in str(pe):
                            self.log_msg(LOG_MESSAGES["browser_closed"], "stopped")
                            break
                        else:
                            self.log_msg(LOG_MESSAGES["temporary_error"].format(error=pe), "warning")
                            time.sleep(2)

                    except Exception as e:
                        self.log_msg(LOG_MESSAGES["unexpected_error"].format(error=e), "error")
                        time.sleep(2)

        except Exception as e:
            self.log_msg(LOG_MESSAGES["unexpected_error"].format(error=e), "error")

        finally:
            if context:
                try:
                    context.close()
                except:
                    pass
            if browser:
                try:
                    browser.close()
                except:
                    pass
            self.log_msg(LOG_MESSAGES["status_completed"], "stopped")
