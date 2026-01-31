import time
import random
import re
from bot_core import BotCore
from browser_actions import BrowserActions
from browser_launcher import BrowserLaunchError, close_browser
from database import DBHandler

class RegistrationBot(BotCore):
    def run(self, stop_event):
        attempt = 0
        playwright = None
        browser = None
        context = None
        page = None
        try:
            playwright, browser, context, page = self.setup_browser(stop_event)
            if not page:
                return
            
            # ============================================================
            # مدیریت هوشمند پیام‌های خطا (Alert Handler) - اصلاح شده
            # ============================================================
            def handle_dialog(dialog):
                try:
                    msg = dialog.message()
                    
                    # نکته حیاتی: اینجا متغیر page را حذف کردیم.
                    # وقتی Alert باز است، نمی‌توان روی صفحه چیزی نوشت.
                    # فقط به داشبورد ارسال می‌کنیم.
                    self.log(f"❌ پیام سایت: {msg}", "error") 
                    
                    # منطق پاکسازی کد نامعتبر
                    if any(x in msg for x in ["منقضی", "نامعتبر", "اشتباه", "صحیح نمی باشد"]):
                        DBHandler.clear_otp(self.nid)
                        self.log("♻️ کد نامعتبر از دیتابیس پاک شد.", "warning")
                    
                    # بستن فوری پنجره
                    dialog.accept()
                except Exception as e:
                    print(f"Dialog Error: {e}")

            # فعال‌سازی گوش‌به‌زنگ برای پاپ‌آپ‌ها
            page.on("dialog", handle_dialog)
            # ============================================================

            try:
                self.log("🚀 ربات آماده‌سازی شد", "info", page)
                captcha_mode = self.settings.get('captcha_mode', 'human')
                allow_final_submit = self.settings.get('final_submit', False)

                while attempt < self.retry_limit:
                    if stop_event.is_set():
                        break
                    attempt += 1

                    try:
                        if page.url == "about:blank" or "Register" not in page.url:
                             try: page.goto("https://ve.cbi.ir/Register.aspx", timeout=60000)
                             except: continue

                        if stop_event.is_set():
                            break
                        self.solve_firewall(page)
                        d = self.user_data

                        # ==========================
                        # STEP 1: فرم هویتی
                        # ==========================
                        if page.locator("#ctl00_ContentPlaceHolder1_btnSendConfirmCode").is_visible():
                            if not page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").input_value():
                                self.log(f"📝 مرحله ۱: پر کردن فرم", "registering", page)
                                BrowserActions.force_fill(page.locator("#ctl00_ContentPlaceHolder1_tbIDNo"), self.nid)
                                if d.get('spouse'): BrowserActions.force_fill(page.locator("#ctl00_ContentPlaceHolder1_tbIDNo2"), d.get('spouse'))
                                
                                # تاریخ‌ها
                                if d.get('birth_y'):
                                    page.locator("#ctl00_ContentPlaceHolder1_tbBrYear").fill(str(d.get('birth_y')))
                                    page.select_option("#ctl00_ContentPlaceHolder1_ddlBrMonth", value=str(d.get('birth_m')).zfill(2))
                                    page.select_option("#ctl00_ContentPlaceHolder1_ddlBrDay", value=str(d.get('birth_d')).zfill(2))
                                
                                if d.get('marriage_y'):
                                    page.locator("#ctl00_ContentPlaceHolder1_tbMarrYear").fill(str(d.get('marriage_y')))
                                    page.select_option("#ctl00_ContentPlaceHolder1_ddlMarryMonth", value=str(d.get('marriage_m')).zfill(2))
                                    page.select_option("#ctl00_ContentPlaceHolder1_ddlMarryDay", value=str(d.get('marriage_d')).zfill(2))
                                
                                BrowserActions.force_fill(page.locator("#ctl00_ContentPlaceHolder1_tbMobileNo"), d.get('mobile', ''))

                            self._solve_captcha_wrapper(page, "#ctl00_ContentPlaceHolder1_tbCaptcha1", "#ctl00_ContentPlaceHolder1_btnSendConfirmCode", captcha_mode)
                            try: page.wait_for_selector("#ctl00_ContentPlaceHolder1_tbMobileConfCode", timeout=5000)
                            except: pass
                            continue

                        # ==========================
                        # STEP 2: کد تایید
                        # ==========================
                        if page.locator("#ctl00_ContentPlaceHolder1_btnContinue1").is_visible():
                            otp = self.get_otp_code()
                            if otp:
                                self.log(f"✅ ورود کد: {otp}", "success", page)
                                page.locator("#ctl00_ContentPlaceHolder1_tbMobileConfCode").fill(otp)
                                self._solve_captcha_wrapper(page, "#ctl00_ContentPlaceHolder1_tbCaptcha2", "#ctl00_ContentPlaceHolder1_btnContinue1", captcha_mode)
                                time.sleep(4)
                            else:
                                self.log("📩 منتظر کد پیامک...", "waiting sms", page)
                                time.sleep(3)
                            continue

                        # ==========================
                        # STEP 3: اطلاعات سکونت
                        # ==========================
                        if page.locator("#ctl00_ContentPlaceHolder1_btnContinue2").is_visible():
                            self.log("📍 مرحله ۳: اطلاعات تکمیلی", "registering", page)
                            
                            try: page.select_option("#ctl00_ContentPlaceHolder1_ddlIsarST", value="0")
                            except: pass

                            # وضعیت سربازی
                            try:
                                if page.locator("#ctl00_ContentPlaceHolder1_ddlSarbasiST").is_visible():
                                    mil_status = str(d.get('military_status', '1'))
                                    page.select_option("#ctl00_ContentPlaceHolder1_ddlSarbasiST", value=mil_status)
                            except: pass

                            # 1. استان
                            user_state = d.get('state')
                            state_val = self._find_select_value(page, "#ctl00_ContentPlaceHolder1_ddlState", user_state)
                            if state_val:
                                if page.locator("#ctl00_ContentPlaceHolder1_ddlState").input_value() != state_val:
                                    self.log(f"استان: {user_state}", "info", page)
                                    page.select_option("#ctl00_ContentPlaceHolder1_ddlState", value=state_val)
                                    self.log("⏳ صبر برای رفرش شهرها...", "info", page)
                                    try: page.wait_for_function("document.getElementById('ctl00_ContentPlaceHolder1_ddlCity').options.length > 1", timeout=15000)
                                    except: time.sleep(5)
                            else:
                                self.log(f"⚠️ استان '{user_state}' پیدا نشد", "error", page)

                            # 2. شهر
                            user_city = d.get('city')
                            if user_city:
                                city_val = self._find_select_value(page, "#ctl00_ContentPlaceHolder1_ddlCity", user_city)
                                if city_val:
                                    self.log(f"شهر: {user_city}", "info", page)
                                    page.select_option("#ctl00_ContentPlaceHolder1_ddlCity", value=city_val)
                                else:
                                    self.log(f"⚠️ شهر '{user_city}' پیدا نشد", "warning", page)

                            # 3. تلفن و کد پستی
                            if d.get('phone'): page.locator("#ctl00_ContentPlaceHolder1_tbTel").fill(str(d.get('phone')), force=True)
                            if d.get('zip_code'): page.locator("#ctl00_ContentPlaceHolder1_tbZipCD").fill(str(d.get('zip_code')), force=True)

                            self._solve_captcha_wrapper(page, "#ctl00_ContentPlaceHolder1_tbCaptcha3", "#ctl00_ContentPlaceHolder1_btnContinue2", captcha_mode)
                            continue

                        # ==========================
                        # STEP 4: تایید نهایی
                        # ==========================
                        if page.locator("#ctl00_ContentPlaceHolder1_btnSave").is_visible():
                            self.log("🏁 مرحله نهایی", "registering", page)
                            chk = page.locator("#ctl00_ContentPlaceHolder1_chkBoxWarning")
                            if not chk.is_checked(): chk.click(force=True)
                            
                            if not allow_final_submit:
                                self.log("🛑 توقف (ثبت نهایی خاموش)", "stop", page)
                                time.sleep(10)
                                return 
                            
                            page.locator("#ctl00_ContentPlaceHolder1_btnSave").click()
                            self.log("💾 ثبت نهایی شد", "info", page)
                            time.sleep(10)
                            continue

                        # بررسی موفقیت
                        if "ShowTrackingCode" in page.url or page.locator("span:has-text('کد رهگیری')").is_visible():
                            try:
                                full_text = page.locator("body").text_content()
                                match = re.search(r'کد رهگیری\s*[:\-\s]*(\d{10})', full_text)
                                code = match.group(1) if match else "---"
                                DBHandler.save_success_data(self.nid, code)
                                self.log(f"🎉 ثبت موفق! کد: {code}", "success", page)
                            except: self.log("🎉 ثبت نام موفق!", "success", page)
                            return

                    except Exception:
                        time.sleep(1)

            except BrowserLaunchError as exc:
                self.log(f"Fatal: {exc.message}", "error")
            except Exception as e:
                self.log(f"Fatal: {e}", "error")
        finally:
            close_browser(playwright, browser, context, page)
            self.log("مرورگر بسته شد.", "stop")

    # --- توابع کمکی ---
    def _solve_captcha_wrapper(self, page, input_sel, btn_sel, mode):
        if mode == 'robot': return self._handle_captcha_robot(page, input_sel, btn_sel)
        else: return self._handle_captcha_human(page, input_sel, btn_sel)

    def _handle_captcha_human(self, page, input_selector, btn_selector):
        try:
            captcha_img = page.locator(".BDC_CaptchaImage").first
            if not captcha_img.is_visible(): return False
            code = self.captcha_service.solve(captcha_img.screenshot(), mode='general')
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
        except: return False

    def _handle_captcha_robot(self, page, input_selector, btn_selector):
        try:
            captcha_img = page.locator(".BDC_CaptchaImage").first
            if not captcha_img.is_visible(): return False
            code = self.captcha_service.solve(captcha_img.screenshot(), mode='general')
            if code and len(code) >= 4:
                page.locator(input_selector).fill(code)
                page.locator(btn_selector).click()
                return True
            else:
                page.locator(".BDC_ReloadLink").first.click()
                time.sleep(1)
                return False
        except: return False

    def _normalize_text(self, text):
        if not text: return ""
        text = str(text)
        text = text.replace("ي", "ی").replace("ك", "ک")
        text = re.sub(r'\s+', '', text)
        text = text.replace("\u200c", "")
        return text

    def _find_select_value(self, page, selector, text_to_match):
        if not text_to_match: return None
        try:
            target = self._normalize_text(text_to_match)
            options = page.locator(f"{selector} option").all()
            for opt in options:
                opt_raw = opt.text_content()
                opt_norm = self._normalize_text(opt_raw)
                if opt_norm == target: return opt.get_attribute("value")
                if len(target) > 3 and target in opt_norm:
                     if "سوران" in target and "سیب" not in target and "سیب" in opt_norm: continue 
                     return opt.get_attribute("value")
        except: pass
        return None
