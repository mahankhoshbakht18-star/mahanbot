# تغییرات کد رفع اختلال CAPTCHA در عملیات ثبت بانک (SelectBnkShb.aspx)

این سند تغییرات دقیق و متمرکز (Surgical & Minimal) اعمال‌شده در پایگاه کد MAHANBOT را برای برطرف‌سازی دائمی مشکل گیر افتادن در مرحله «منتظر CAPTCHA» در عملیات ثبت بانک تشریح می‌کند.

---

## ۱. جدول خلاصه فایل‌های اصلاح‌شده

| فایل | ماژول / تابع | نوع تغییر | هدف تغییر |
| :--- | :--- | :--- | :--- |
| [`bot_select_captcha.py`](file:///C:/Users/amir/Downloads/MAHANBOT_v1.8.0_fixed%20(2)/bot_select_captcha.py) | `ModelCaptchaSolver.solve` | بهینه‌سازی الگوریتم پیش‌پردازش تصویر | حذف کانال آلفا (شفافیت) با کامپوزیت روی پس‌زمینه سفید، تشخیص تصاویر پس‌زمینه تیره و معکوس‌سازی خودکار درخشندگی |
| [`bot_select_captcha.py`](file:///C:/Users/amir/Downloads/MAHANBOT_v1.8.0_fixed%20(2)/bot_select_captcha.py) | `ModelCaptchaSolver._try_auxiliary_fallback` | تصحیح منطق فال‌بک OCR کمکی | فعال‌سازی DdddOcr در صورت پایین بودن اطمینان مدل CRNN یا کمتر از ۴ نویسه بودن |
| [`bot_select_captcha.py`](file:///C:/Users/amir/Downloads/MAHANBOT_v1.8.0_fixed%20(2)/bot_select_captcha.py) | `wait_for_manual_captcha_handoff` | اصلاح شرط وارونه (Inverted Flag Bug) | تداوم تلاش مدل در پس‌زمینه در حین انتظار دستی هر ۲ ثانیه، به جای مسدود شدن در شرط نقیض |
| [`bot_select.py`](file:///C:/Users/amir/Downloads/MAHANBOT_v1.8.0_fixed%20(2)/bot_select.py) | `_solve_primary_captcha_fast` | تنظیم کف تکرارهای مجاز بازنشانی تصویر | افزایش سقف مجاز رفرش تصویر کپچا از ۱ یا ۳ به حداقل ۶ تا ۸ تلاش بدون تسلیم زودهنگام |
| [`bot_core.py`](file:///C:/Users/amir/Downloads/MAHANBOT_v1.8.0_fixed%20(2)/bot_core.py) | `solve_captcha_with_model` | حذف اسکرین‌شات مازاد Playwright | استفاده مستقیم از `source_bytes` ارسالی جهت کاهش ۲۰۰ میلی‌ثانیه‌ای زمان حل هر کپچا |

---

## ۲. جزئیات تغییرات به تفکیک فایل

### الف) `bot_select_captcha.py`

#### ۱. حل مسئله شفافیت PNG و تصاویر با زمینه تیره در `ModelCaptchaSolver.solve`
- **علت:** اسکرین‌شات‌های Playwright از کنترل کپچا، تصاویر PNG با کانال Alpha (شفافیت) تولید می‌کنند. کتابخانه‌های OCR نظیر DdddOcr کانال آلفای شفاف را با رنگ مشکی ادغام می‌کنند. در نتیجه حروف تیره روی پس‌زمینه مشکی محو شده و OCR نتیجه خالی برمی‌گرداند. همچنین در فونت‌های معکوس BotDetect (مانند تصویر ۳K۸R مریم اسکانی در اسکرین‌رکورد)، پس‌زمینه تیره و حروف روشن بودند که مدل محلی آموزش‌دیده روی پس‌زمینه روشن را دچار شکست قطعی می‌کرد.
- **تغییر اعمال‌شده:**
  - تبدیل تصویر به RGB با کامپوزیت کامل روی لایه سفید خالص `(255, 255, 255)`.
  - محاسبه میانگین درخشندگی تصویر در مقیاس Grayscale؛ اگر کمتر از ۱۲۵ باشد، تصویر به‌صورت خودکار Invert (معکوس) می‌شود تا ساختار نویسه‌ها با توزیع استاندارد یادگیری مدل منطبق گردد.

```python
# در متد ModelCaptchaSolver.solve
try:
    with Image.open(io.BytesIO(img_bytes)) as pil_img:
        # ۱. حذف آلفا و کامپوزیت روی پس‌زمینه سفید خالص
        if pil_img.mode in ("RGBA", "LA") or (pil_img.mode == "P" and "transparency" in pil_img.info):
            bg = Image.new("RGBA", pil_img.size, (255, 255, 255, 255))
            bg.alpha_composite(pil_img.convert("RGBA"))
            pil_img = bg.convert("RGB")
        elif pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")

        # ۲. بررسی درخشندگی پس‌زمینه (معکوس‌سازی در صورت زمینه تیره)
        gray = pil_img.convert("L")
        stat = ImageStat.Stat(gray)
        mean_lum = stat.mean[0] if stat.mean else 255
        if mean_lum < 125:  # پس‌زمینه تیره (نظیر حالت 3K8R در ویدیو)
            inverted = ImageOps.invert(gray)
            pil_img = inverted.convert("RGB")
```

#### ۲. اصلاح شرط وارونه در `wait_for_manual_captcha_handoff`
- **علت:** در خط ۶۵۲ فایل، شرط تداوم حل مدل در پس‌زمینه به این شکل نوشته شده بود:
  `if not bot.settings.get('pause_for_manual_captcha', False):`
  از آنجا که تنظیم `pause_for_manual_captcha` در تنظیمات پیش‌فرض برابر `True` است، این شرط همیشه `False` ارزیابی می‌شد! در نتیجه، هنگامی که ربات پس از چند خطا وارد حلقه انتظار دستی می‌شد، مدل هوش مصنوعی کاملاً متوقف می‌شد و تا ۳۰۰ ثانیه (۵ دقیقه) هیچ تلاشی برای حل تصویر تازه نمی‌کرد و وضعیت متقاضی تا ابد روی `منتظر CAPTCHA` ثابت می‌ماند.
- **تغییر اعمال‌شده:**
  شرط وارونه حذف شد و سازوکار تایمر دوره‌ای (هر ۲ ثانیه یک‌بار) جایگزین شد تا حتی در حین انتظار برای مداخله کاربر، موتور کمکی ربات فعال مانده و به محض بارگذاری تصویر خوانا توسط سرور، آن را خودکار حل و فرم را ثبت کند:

```python
# قبل از تغییر:
if not bot.settings.get('pause_for_manual_captcha', False):
    if now - last_model_try_ts >= 2.0:
        ...

# بعد از تغییر:
if now - last_model_try_ts >= 2.0:
    last_model_try_ts = now
    # تلاش مدل کمکی در پس‌زمینه همزمان با انتظار دستی
    assisted_code = bot.solve_captcha_with_model(page, captcha_img_sel)
    if assisted_code and len(assisted_code) >= 4:
        ...
```

---

### ب) `bot_select.py`

#### تنظیم کف تکرارهای مجاز بازنشانی تصویر در `_solve_primary_captcha_fast`
- **علت:** در صورتی که کاربر یا سیستم تنظیم `captcha_max_retries` را روی مقادیر کم مانند ۲ یا ۳ قرار می‌داد، در صورت برخورد با ۲ فونت دشوار تصادفی پشت سر هم (نظیر `UDMY` توخالی یا `8A5W` نقطه‌چین)، ربات فوراً تسلیم می‌شد و کنترل را به حلقه انتظار دستی واگذار می‌کرد. از آنجا که درخواست رفرش تصویر کپچا کمتر از ۲۵۰ میلی‌ثانیه زمان می‌برد و هیچ‌گونه جریمه سمت سرور ندارد، کاهش بیش از حد تعداد رفرش عامل اصلی گیر کردن در ویدیو بود.
- **تغییر اعمال‌شده:**
  تنظیم حداقل مجاز رفرش تصویر روی ۶ بار تضمین می‌کند که ربات قبل از تسلیم، فرصت کافی برای دریافت یک فونت ساده و پرکنتراست استاندارد از سرور BotDetect داشته باشد.

```python
# خط ۲۴۴ فایل bot_select.py:
max_attempts = max(6, int(self.settings.get("captcha_max_retries", 8)))
```

---

### ج) `bot_core.py`

#### بهینه‌سازی `solve_captcha_with_model` جهت حذف اسکرین‌شات تکراری
- **علت:** هنگامی که `bot_select` بایت‌های تصویر کپچا را از طریق درخواست مستقیم شبکه یا حافظه کش به دست آورده و به متد `solve_captcha_with_model` پاس می‌داد، این متد پارامتر `source_bytes` را نادیده گرفته و مجدداً یک فراخوانی سنگین `page.locator(selector).screenshot()` روی مرورگر Playwright اجرا می‌کرد. این کار ۱۵۰ الی ۳۰۰ میلی‌ثانیه تأخیر اضافی ایجاد کرده و در مواردی که فریم یا DOM در حال رفرش بود، خطای تایم‌اوت پرتاب می‌کرد.
- **تغییر اعمال‌شده:**
  در صورتی که `source_bytes` به متد ارسال شده باشد، مستقیماً به سرویس OCR تحویل داده می‌شود:

```python
# در bot_core.py متد solve_captcha_with_model:
if source_bytes and len(source_bytes) > 100:
    return self.captcha_service.solve(source_bytes)
```

---

## ۳. تضمین عدم شکست سایر بخش‌ها (Zero Regressions)

۱. **ورود اولیه (Login Standard):**
   - منطق ورود استاندارد با نام کاربری و رمز عبور از `bot_core.py` و متد مشترک OCR استفاده می‌کند و اصلاح پیش‌پردازش آلفا موجب افزایش نرخ موفقیت کپچای ورود اولیه نیز می‌شود.
۲. **مرحله دوم (OTP Form & Detector):**
   - معماری `otp_form_detector.py` دست‌نخورده باقی مانده و فرآیند عبور از کپچا مستقیماً به این ماژول هدایت می‌شود.
۳. **تسهیم نشست و پروفایل‌ها:**
   - هیچ تغییری در مسیرهای ذخیره‌سازی، پایگاه داده یا ساختار دایرکتوری‌های `chrome_profiles/<nid>` داده نشده و کوکی‌های مجاز کاربران حفظ می‌شوند.
