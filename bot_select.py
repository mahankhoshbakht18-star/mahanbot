import time
import json
import random
from playwright.sync_api import Error as PlaywrightError
from bot_core import BotCore
from browser_launcher import BrowserLaunchError, close_browser
from database import DBHandler

class BankSelectionBot(BotCore):
    def run(self, stop_event, loan_type="rbtnNaghdi"):
        attempt = 0
        # آدرس مستقیم صفحه انتخاب بانک (شروع فرآیند)
        TARGET_URL = "https://ve.cbi.ir/SelectBnkShb.aspx"

        while attempt < self.retry_limit:
            if stop_event.is_set(): break
            attempt += 1
            
            playwright = None
            browser = None
            context = None
            page = None
            try:
                # اتصال به مرورگر
                playwright, browser, context, page = self.setup_browser(stop_event)
                page.on("dialog", lambda dialog: dialog.accept())

                self.log(f"🚀 شروع عملیات (دور {attempt})...", "info", page)
                captcha_mode = self.settings.get('captcha_mode', 'human')

                try: page.goto(TARGET_URL, timeout=60000)
                except: pass

                while not stop_event.is_set():
                    
                    # 1. تشخیص مسدودی (Soft WAF)
                    if page.locator("body").get_by_text("درخواست شما رد شد").is_visible():
                        self.log("⛔ مسدودی! رفرش...", "error", page)
                        time.sleep(2)
                        page.goto(TARGET_URL)
                        continue

                    # 2. حل فایروال
                    if self.solve_firewall(page):
                        continue

                    # ============================================================
                    # 🟢 مرحله ۱: ورود کد ملی
                    # ============================================================
                    if page.locator("#ctl00_ContentPlaceHolder1_btnSendConfirmCode").is_visible():
                        # پر کردن کد ملی
                        if not page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").input_value():
                            page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").fill(self.nid)
                        
                        # حل کپچا و کلیک (کپچا 1)
                        self._solve_captcha_wrapper(
                            page, 
                            "#ctl00_ContentPlaceHolder1_tbCaptcha1", 
                            "#ctl00_ContentPlaceHolder1_btnSendConfirmCode", 
                            captcha_mode
                        )
                        # صبر کوتاه برای لود شدن صفحه بعد
                        try: page.wait_for_selector("#ctl00_ContentPlaceHolder1_tbMobileConfCode", timeout=3000)
                        except: pass

                    # ============================================================
                    # 🔵 مرحله ۲: ورود کد پیامک (OTP) - اصلاح شده
                    # ============================================================
                    elif page.locator("#ctl00_ContentPlaceHolder1_tbMobileConfCode").is_visible():
                        
                        # دریافت کد از دیتابیس (دقیقاً مشابه متد فایل رجیستر)
                        otp_code = self._get_otp_from_db_fresh()
                        
                        if otp_code:
                            # چک می‌کنیم آیا فیلد خالی است یا مقدارش اشتباه است
                            current_val = page.locator("#ctl00_ContentPlaceHolder1_tbMobileConfCode").input_value()
                            
                            if current_val != otp_code:
                                self.log(f"✅ دریافت کد پیامک: {otp_code}", "success", page)
                                page.locator("#ctl00_ContentPlaceHolder1_tbMobileConfCode").fill(otp_code)
                                time.sleep(0.5)
                            
                            # حل کپچای دوم و کلیک دکمه ادامه
                            # توجه: کپچا اینجا tbCaptcha2 است و دکمه btnContinue1
                            success = self._solve_captcha_wrapper(
                                page,
                                "#ctl00_ContentPlaceHolder1_tbCaptcha2",
                                "#ctl00_ContentPlaceHolder1_btnContinue1",
                                captcha_mode
                            )
                            
                            if success:
                                self.log("👆 تایید کد پیامک...", "info", page)
                                # انتظار بیشتر برای رفرش صفحه و رفتن به انتخاب بانک
                                time.sleep(3) 
                        else:
                            self.log("📩 منتظر دریافت پیامک...", "waiting sms", page)
                            time.sleep(2)

                    # ============================================================
                    # 🟣 مرحله ۳: انتخاب بانک (صفحه ۴ - طبق المنت ارسالی شما)
                    # ============================================================
                    elif page.locator("#ctl00_ContentPlaceHolder1_ddlBankName").is_visible():
                        # توجه: آی‌دی در این صفحه ddlBankName است
                        result = self._process_bank_selection_v2(page)
                        
                        if result == "success":
                            self.log("🎉 بانک رزرو شد! پایان عملیات.", "success", page)
                            return 
                        elif result == "no_match":
                            self.log("❌ بانک مورد نظر یافت نشد. رفرش...", "warning", page)
                            page.reload()
                        elif result == "waiting":
                            time.sleep(2) # در حال پردازش

                    # ============================================================
                    # 🟡 سایر صفحات (انتخاب شعبه، لاگین مجدد و ...)
                    # ============================================================
                    
                    # انتخاب شعبه (اگر بعد از انتخاب بانک آمد)
                    elif page.locator("#ctl00_ContentPlaceHolder1_ddlBranch").is_visible():
                        self._process_branch_selection(page)

                    # اگر به صفحه لاگین پرت شد
                    elif page.locator("#ctl00_ContentPlaceHolder1_btnLogin").is_visible():
                        self._perform_login_standard(page, captcha_mode)

                    # موفقیت نهایی (کد رهگیری)
                    elif page.locator("#ctl00_ContentPlaceHolder1_lblTrackingCode").is_visible():
                        code = page.locator("#ctl00_ContentPlaceHolder1_lblTrackingCode").inner_text()
                        self.log(f"✅ کد رهگیری: {code}", "success")
                        DBHandler.save_success_data(self.nid, code)
                        return

                    time.sleep(0.5)

            except PlaywrightError as pe:
                if "Target closed" in str(pe):
                    self.log("🛑 مرورگر بسته شد.", "stopped")
                    stop_event.set()
                    return
                time.sleep(2)
            except BrowserLaunchError as exc:
                self.log(f"خطا در مرورگر: {exc.message}", "error")
                time.sleep(2)
            except Exception:
                time.sleep(2)
            finally:
                close_browser(playwright, browser, context, page)

            if not stop_event.is_set():
                time.sleep(2)

    # --- متد جدید و مستقیم خواندن دیتابیس (مشابه فایل رجیستر) ---
    def _get_otp_from_db_fresh(self):
        """خواندن مستقیم و تازه از دیتابیس برای اطمینان از دریافت کد"""
        try:
            # فراخوانی مستقیم هندلر دیتابیس برای گرفتن آخرین وضعیت
            row = DBHandler.get_applicant(self.nid)
            if row and row['data']:
                data = json.loads(row['data'])
                return data.get('otp_code')
        except: 
            pass
        return None

    # --- منطق جدید انتخاب بانک (بر اساس المنت ddlBankName) ---
    def _process_bank_selection_v2(self, page):
        try:
            dropdown_id = "#ctl00_ContentPlaceHolder1_ddlBankName"
            
            # خواندن تمام گزینه‌های موجود
            # فرمت متن گزینه‌ها: "بانک تجارت [دارای ظرفیت ثبت نام] - متقاضی در صف : 3846 نفر"
            options = page.locator(f"{dropdown_id} option").all()
            available_banks = {}
            
            for opt in options:
                val = opt.get_attribute("value")
                txt = opt.inner_text().strip()
                if val and val != "0":
                    available_banks[txt] = val

            user_priorities = self.user_data.get('banks', [])
            if not user_priorities:
                self.log("⚠️ لیست اولویت بانک خالی است!", "error")
                return "error"

            for priority in user_priorities:
                # استخراج نام بانک از اولویت کاربر (مثلاً "تجارت")
                target_name = priority['name'] if isinstance(priority, dict) else priority
                
                # جستجو در لیست بانک‌های سایت
                found_val = None
                found_text = ""
                
                for b_text, b_val in available_banks.items():
                    if target_name in b_text:
                        found_val = b_val
                        found_text = b_text
                        break
                
                if found_val:
                    self.log(f"🎯 بانک یافت شد: {target_name}", "selecting", page)
                    
                    # انتخاب بانک
                    page.select_option(dropdown_id, value=found_val)
                    
                    # چون المنت AutoPostBack دارد، باید صبر کنیم تا صفحه رفرش شود
                    self.log("⏳ در حال بارگذاری شعب...", "info", page)
                    try: 
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except: 
                        time.sleep(3)
                    
                    return "success" # یا رفتن به مرحله بعد
            
            return "no_match"
        except Exception as e:
            return "error"

    # --- انتخاب شعبه (اگر فعال شد) ---
    def _process_branch_selection(self, page):
        try:
            branch_ddl = "#ctl00_ContentPlaceHolder1_ddlBranch"
            # اگر هنوز شعبه‌ای انتخاب نشده
            if page.locator(branch_ddl).input_value() == "0":
                # فعلاً اولین شعبه موجود را انتخاب می‌کنیم (یا بر اساس کد شعبه اگر دارید)
                page.locator(branch_ddl).select_option(index=1)
                
                # دکمه ثبت نهایی
                if self.settings.get('final_submit', False):
                    self.log("🔥 ثبت نهایی...", "success", page)
                    page.click("#ctl00_ContentPlaceHolder1_btnRegister")
                else:
                    self.log("🛑 توقف قبل از ثبت نهایی (حالت تست)", "warning", page)
                    time.sleep(5)
        except: pass

    # --- توابع عمومی (کپچا و لاگین) ---
    def _solve_captcha_wrapper(self, page, input_sel, btn_sel, mode):
        try:
            captcha_img = page.locator(".BDC_CaptchaImage").first
            if not captcha_img.is_visible(): return False
            
            if page.locator(input_sel).input_value() and mode == 'robot':
                page.locator(btn_sel).click()
                return True

            code = self.captcha_service.solve(captcha_img.screenshot(), mode='general')
            
            if code and len(code) >= 4:
                self.log(f"🧩 حل شد: {code}", "info", page)
                inp = page.locator(input_sel)
                inp.clear()
                
                if mode == 'human':
                    inp.type(code, delay=random.randint(150, 300))
                    time.sleep(0.5)
                    page.locator(btn_sel).click(delay=random.randint(50, 150))
                else:
                    inp.fill(code)
                    page.locator(btn_sel).click()
                return True
            else:
                self.log("❌ خطا در خواندن. رفرش...", "warning", page)
                try: page.locator(".BDC_ReloadLink").first.click()
                except: pass
                time.sleep(1.5)
                return False
        except: return False

    def _perform_login_standard(self, page, mode):
        try:
            if not page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").input_value():
                page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").fill(self.nid)
            self._solve_captcha_wrapper(page, "#ctl00_ContentPlaceHolder1_tbCaptcha", "#ctl00_ContentPlaceHolder1_btnLogin", mode)
        except: pass
