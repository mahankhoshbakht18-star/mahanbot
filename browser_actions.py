class BrowserActions:
    @staticmethod
    def show_log_on_page(page, text, color="blue"):
        """
        تزریق یک باکس کوچک گوشه صفحه مرورگر برای دیدن وضعیت ربات توسط کاربر
        """
        try:
            # کد جاوااسکریپت برای ساختن یا آپدیت کردن باکس لاگ
            js = f"""
            var b = document.getElementById('bot-log');
            if(!b){{
                b = document.createElement('div'); 
                b.id='bot-log';
                b.style.cssText = 'position:fixed;top:10px;left:10px;z-index:999999;padding:8px;border-radius:5px;font-family:Tahoma,sans-serif;font-weight:bold;font-size:12px;background:rgba(255,255,255,0.95);border-right:5px solid {color};color:{color};box-shadow:0 2px 10px rgba(0,0,0,0.2);direction:rtl;';
                document.body.appendChild(b);
            }}
            if(b.innerText !== '{text}') b.innerText = '{text}';
            b.style.borderColor = '{color}'; 
            b.style.color = '{color}';
            """
            page.evaluate(js)
        except Exception:
            pass # اگر ارور داد (مثلا صفحه بسته شده بود) مهم نیست

    @staticmethod
    def force_fill(locator, text):
        """
        متد بسیار مهم برای پر کردن فیلدهایی که:
        1. مخفی هستند
        2. Read-only هستند
        3. با تایپ معمولی Playwright پر نمی‌شوند
        """
        try:
            text = str(text)
            if not locator.is_visible():
                return
            
            # اسکرول کن تا دیده شود
            locator.scroll_into_view_if_needed()
            
            # روش 1: تایپ استاندارد
            try:
                locator.fill("") 
                locator.fill(text)
            except:
                pass # اگر نشد برو روش 2
            
            # بررسی کن آیا واقعا پر شد؟
            val = locator.input_value()
            if val != text:
                # روش 2: تزریق با جاوااسکریپت (قدرتمندترین روش)
                locator.evaluate(f"el => el.value = '{text}'")
                
                # تریگر کردن ایونت‌ها تا سایت بفهمد فیلد تغییر کرده است
                # این خیلی مهم است چون سایت‌های بانکی روی onchange حساس هستند
                locator.evaluate("el => { el.dispatchEvent(new Event('change', {bubbles: true})); el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('blur', {bubbles: true})); }")
        except Exception as e:
            print(f"Force Fill Error: {e}")

    @staticmethod
    def fill_dates(page, data):
        """
        پر کردن تاریخ تولد و ازدواج
        با توجه به اینکه این فیلدها معمولا 3 تکه (سال/ماه/روز) هستند
        """
        try:
            # 1. تاریخ تولد
            # چک می‌کنیم آیا فیلد سال تولد در صفحه وجود دارد؟
            if data.get('birth_year') and page.locator("#ctl00_ContentPlaceHolder1_tbBrYear").is_visible():
                # فقط اگر خالی بود پر کن
                if not page.locator("#ctl00_ContentPlaceHolder1_tbBrYear").input_value():
                    # سال
                    page.locator("#ctl00_ContentPlaceHolder1_tbBrYear").fill(str(data['birth_year']))
                    # ماه (باید دو رقمی باشد مثل 01)
                    page.locator("#ctl00_ContentPlaceHolder1_ddlBrMonth").select_option(value=str(data['birth_month']).zfill(2))
                    # روز
                    page.locator("#ctl00_ContentPlaceHolder1_ddlBrDay").select_option(value=str(data['birth_day']).zfill(2))

            # 2. تاریخ ازدواج (اگر وجود داشت)
            if data.get('marriage_year') and page.locator("#ctl00_ContentPlaceHolder1_tbMarrYear").is_visible():
                if not page.locator("#ctl00_ContentPlaceHolder1_tbMarrYear").input_value():
                    page.locator("#ctl00_ContentPlaceHolder1_tbMarrYear").fill(str(data['marriage_year']))
                    page.locator("#ctl00_ContentPlaceHolder1_ddlMarryMonth").select_option(value=str(data['marriage_month']).zfill(2))
                    page.locator("#ctl00_ContentPlaceHolder1_ddlMarryDay").select_option(value=str(data['marriage_day']).zfill(2))
        except Exception:
            pass

    @staticmethod
    def select_state_bank(page, data):
        """
        انتخاب استان و شهر (در صورتی که در دیتا موجود باشد)
        """
        try:
             state_name = data.get('state')
             if state_name:
                ddl = page.locator("#ctl00_ContentPlaceHolder1_ddlState")
                # اگر دراپ‌داون هست و هنوز روی پیش‌فرض (0) مانده
                if ddl.is_visible() and ddl.input_value() == "0":
                    try: 
                        # انتخاب بر اساس متن (Label) چون کد استان‌ها را حفظ نیستیم
                        ddl.select_option(label=state_name)
                    except: 
                        pass
        except Exception:
            pass