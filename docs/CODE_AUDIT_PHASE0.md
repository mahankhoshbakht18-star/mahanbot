# ممیزی فنی MahanBot — فاز صفر

تاریخ ممیزی: 2026-07-17

## دامنه بررسی

فایل‌های اصلی بررسی‌شده:

- `server.py`
- `bot_core.py`
- `bot_register.py`
- `bot_select.py`
- `browser_actions.py`
- `browser_launcher.py`
- `captcha_service.py`
- `database.py`
- `event_logger.py`
- رابط وب داخل `static/`

هدف این فاز، تثبیت کد موجود بدون بازنویسی غیرضروری است. تغییرات باید به‌صورت مرحله‌ای، روی Branch مستقل و همراه با تست انجام شوند.

## نتیجه مدیریتی

پروژه از نظر قابلیت‌ها جلوتر از ساختار نگهداری آن رشد کرده است. FastAPI، Playwright، SQLite، دریافت OTP، مدل کپچا و داشبورد در یک برنامه قابل اجرا کنار هم قرار گرفته‌اند؛ اما مسیرهای OTP، Captcha و Browser Interaction چند بار و با رفتار متفاوت پیاده‌سازی شده‌اند. ادامه توسعه بدون یکدست‌سازی این سه بخش، احتمال خطاهای زمان‌بندی و خرابی Session را بالا می‌برد.

## مشکلات بحرانی — P0

### 1. احراز هویت API به‌صورت Fail-Open

در `server.py` اگر `MAHANBOT_API_KEY` تنظیم نشده باشد، کنترل دسترسی درخواست را مجاز می‌کند. برای محیط عملیاتی، نبود کلید باید باعث توقف Startup یا پاسخ 503 شود، نه بازشدن API.

**ریسک:** ثبت یا تغییر داده متقاضی، دریافت یا پاک‌سازی OTP و شروع Job از شبکه غیرمجاز.

### 2. CORS کاملاً باز

`allow_origins=["*"]` همراه با متدها و Headerهای باز برای محیط عملیاتی مناسب نیست.

**راه‌حل:** فهرست Originها از متغیر محیطی خوانده شود و حالت Wildcard فقط در Development فعال باشد.

### 3. ثبت اطلاعات حساس

موارد زیر در Log یا دیتابیس به‌صورت کامل ثبت می‌شوند:

- OTP دریافت‌شده
- متن پیش‌بینی‌شده کپچا
- کد کپچا در `captcha_attempts`
- بعضی Screenshotها با نام کد ملی

**راه‌حل:** OTP و Captcha فقط Mask شوند؛ برای آمار مدل، Hash کوتاه یا نتیجه صحیح/غلط کافی است.

### 4. احتمال مصرف OTP قدیمی

`BankSelectionBot.fetch_otp_fast()` ورودی `min_ts` را می‌گیرد اما مقدار `0.0` را به `wait_for_otp()` می‌فرستد. در کنار مسیر مستقیم DB، امکان مصرف کد تلاش قبلی وجود دارد.

**راه‌حل:** یک منبع حقیقت برای OTP تعریف شود و شرط تازگی بر اساس `otp_ts_ms` اجباری باشد.

### 5. ارسال نهایی ناسازگار

`RegistrationBot` تنظیم `final_submit` را رعایت می‌کند، اما مسیر انتخاب بانک در `bot_select.py` می‌تواند مستقیماً دکمه ثبت نهایی را کلیک کند.

**ریسک:** اجرای عملیات نهایی در حالی که تنظیم توقف پیش از Submit فعال است.

### 6. Reload در وضعیت حساس

Watchdog و بعضی مسیرهای شکست Captcha مستقیماً `page.reload()` اجرا می‌کنند. این رفتار با Guardهای OTP یکسان نیست و ممکن است Session یا فرم OTP از دست برود.

## مشکلات مهم — P1

### 1. سه پیاده‌سازی Captcha

Captcha در این بخش‌ها جداگانه مدیریت شده است:

- `BotCore.solve_firewall`
- `RegistrationBot._handle_captcha_human/_robot`
- `BankSelectionBot.solve_captcha_step`

همه باید فقط از یک سرویس هماهنگ‌کننده استفاده کنند. مدل اختصاصی در `CaptchaService` باقی می‌ماند و تغییر نمی‌کند.

### 2. چند روش ورود متن

کد از `fill`، `type`، `page.evaluate` و `force_fill` استفاده می‌کند. تفاوت رفتار این مسیرها باعث خطاهای پراکنده و تست‌ناپذیری شده است.

**اصلاح فاز جاری:** `BrowserActions` به نقطه مرکزی ورود متن تبدیل شد. `force_fill` برای سازگاری باقی مانده، ولی اکنون تایپ مرحله‌ای و Verification انجام می‌دهد و readonly/disabled را دور نمی‌زند.

### 3. Exceptionهای خاموش

تعداد زیادی `except Exception: pass` وجود دارد. این الگو علت واقعی شکست را حذف می‌کند.

**راه‌حل:** خطاها به دسته‌های Playwright، Network، Validation و DB تقسیم و با `operation`, `nid`, `state` و Exception Type ثبت شوند.

### 4. API خارجی Hard-Coded

آدرس `https://python-ke7tg2.chbk.dev` داخل `BotCore` ثابت است.

**راه‌حل:** فقط از Environment/Settings خوانده شود و در حالت پیش‌فرض غیرفعال باشد.

### 5. SQLite JSON Blob

OTP، بانک‌ها، تلاش‌های کپچا و داده متقاضی همگی در یک ستون JSON قرار دارند. چند Thread ممکن است هم‌زمان JSON را بخوانند و نسخه قبلی را روی تغییر جدید بنویسند.

**راه‌حل:** در فاز بعد، جدول‌های مستقل `otp_events`, `captcha_attempts` و `bank_preferences` ایجاد شوند؛ مهاجرت باید قابل Rollback باشد.

### 6. بارگذاری سنگین مدل هنگام Startup

`torch`, OpenCV و `ddddocr` در Import اولیه بارگذاری می‌شوند. شکست یکی از وابستگی‌ها می‌تواند کل API را از کار بیندازد.

**راه‌حل:** Lazy Loading، Health Status مجزا و خطای قابل‌فهم برای مدل.

## بدهی فنی — P2

- کلاس `BankSelectionBot` بیش از حد بزرگ و چندمسئولیتی است.
- نام وضعیت‌ها بین فارسی و انگلیسی یکسان نیست.
- تنظیمات Runtime و Browser Profile منبع حقیقت واحد ندارند.
- تست‌های واحد و Integration برای OTP، Captcha و Job Queue کافی نیستند.
- Backend و فایل‌های Static در یک ماژول بزرگ مدیریت می‌شوند.
- مستندات نصب و نسخه دقیق وابستگی‌ها کامل نیست.

## تصمیم معماری

بازنویسی کامل توصیه نمی‌شود. مسیر منتخب:

1. یکدست‌سازی Browser Interaction
2. یکدست‌سازی و امن‌سازی OTP
3. ساخت Captcha Orchestrator واحد با حفظ مدل اختصاصی
4. سخت‌سازی API و CORS
5. جداسازی Schema دیتابیس با Migration
6. کوچک‌سازی `server.py` و Botها به Serviceهای مستقل

## تغییرات فاز جاری

- بازنویسی امن `browser_actions.py`
- اضافه شدن `HumanInputProfile`
- تایپ مرحله‌ای با Keyboard Event و Verification
- حذف تزریق JavaScript برای پرکردن فیلد
- عدم تغییر readonly/disabled/hidden
- حفظ Alias قدیمی `force_fill`
- امن‌سازی Status Overlay با آرگومان `page.evaluate`
- اضافه شدن تست‌های واحد مستقل از Playwright واقعی

## معیار پذیرش فاز جاری

- `python -m py_compile browser_actions.py tests/test_browser_actions.py`
- `python -m unittest tests.test_browser_actions`
- فرم رجیستر با اطلاعات تستی پر شود.
- مقدار نهایی هر فیلد با `input_value()` تأیید شود.
- هیچ فیلد readonly یا disabled تغییر نکند.
- مدل کپچا بدون تغییر Load و Inference شود.

## Rollback

Branch این فاز مستقل از `main` است. تا پیش از Merge هیچ تغییری روی نسخه عملیاتی اعمال نمی‌شود. برای بازگشت بعد از Merge می‌توان Commit این فاز را Revert کرد.
