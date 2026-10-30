# اصلاح حل و رفرش کپچا — ۳۰ اکتبر ۲۰۲۶

مبنای اصلاح: commit `e6887cd3c74565c5bc2a06138ceba98a3e3c1148`.
شاخه: `codex/fix-captcha-refresh`.

## مشکل تأییدشده

تست‌های بازتولید روی کد قبلی نشان دادند خروجی خالی OCR دو بار تصویر سالم را رفرش می‌کند؛ انتظار دستی هم تصویر ثابت را دوباره عوض و متن کاربر را پاک می‌کند. ورودی داخل iframe پیدا/ارسال نمی‌شد. CRNN پیش از رسیدن به OCR پنج بار اجرا می‌شد و حتی پیش‌بینی‌های ۳ یا ۹ کاراکتری می‌توانستند برگردند. عبارت عادی `security code`، متن اسکریپت و هشدار مخفی نیز به‌اشتباه رد کپچا محسوب می‌شدند.

## رفتار اصلاح‌شده

- CRNN ابتدا روی تصویر جاری اجرا می‌شود. در نبود پاسخ با اطمینان بالا، `ddddocr` همان تصویر را پیش از پیش‌پردازش‌های بعدی دریافت می‌کند. پاسخ معتبر OCR اولویت دارد؛ در نبود آن، پاسخ معتبر CRNN با اطمینان کمتر حفظ می‌شود. فقط ۴ تا ۸ کاراکتر از `A-Z0-9` پذیرفته می‌شوند.
- اگر هیچ موتور کد معتبری نداد، کد ساختگی تولید نمی‌شود: فرم ارسال نمی‌شود و تصویر برای ورود دستی باقی می‌ماند.
- انتخاب تصویر به `img` محدود است. فریم اصلی دوباره پیمایش نمی‌شود و لینک reload در اسکوپ همان تصویر استفاده می‌شود. ورودی و دکمه داخل iframe نیز پشتیبانی می‌شوند.
- `fill` مستقیم، رویدادهای `input` و `change` و سپس یک کلیک انجام می‌شود. متن دستی، از جمله متنی که هنگام inference وارد شده باشد، بازنویسی نمی‌شود.
- شکست تشخیص محلی، تصویر سالم را رفرش نمی‌کند. helper مشترک فقط در صورت رد کپچا یا لود نشدن تصویر اجازه رفرش می‌دهد. نشانه دیالوگ رد، بعد از مصرف پاک می‌شود. بازیابی تصویر ناموجود در هر فراخوان حداکثر یک بار روی لینک کلیک می‌کند.
- انتظار دستی روی تصویر ثابت شکست‌خورده رفرش دوره‌ای ندارد. متن صفحه فقط از محتوای رندرشده بررسی می‌شود؛ عنوان معمولی فرم، script و پیام مخفی شاهد رد کپچا نیستند.
- ثبت‌نام، انتخاب بانک و مرحله پیامک از همین سیاست پیروی می‌کنند. timeout پاسخ OTP به ارسال مجدد یا تعویض تصویر منجر نمی‌شود. خطای ثبت وضعیت DB پس از کلیک نیز نتیجه «ارسال شد» را به شکست قابل تکرار تبدیل نمی‌کند.
- فراخوانی تکراری OCR در `api_solver.py` حذف شد؛ اگر OCR حافظه‌ای پاسخ نداد، همان تصویر برای حل دوباره روی دیسک ذخیره نمی‌شود. مسیر فایل فقط برای شکست خود screenshot حافظه‌ای باقی است.
- وابستگی `ddddocr>=1.5.6,<2.0.0` به requirements اضافه شد. ساخت EXE انجام نشده است.

## بررسی انجام‌شده

**۴۹ تست مرتبط، ۱۲٫۶۵۳ ثانیه، OK، بدون skip؛ روی کد نهایی با runner پروژه و دیتابیس موقت.**

این مجموعه شامل ۲۴ تست جدید در `tests/test_captcha_refresh_policy.py`، تست‌های موتور، تصویر و اسکوپ، مسیر ورود به OTP، تحویل دستی، انقضای OTP، رد ظرفیت، حذف فایل موقت و انتظار پاسخ نامعلوم است. تست‌های مرورگر با Chromium واقعی headless، تصویر ساختگی و مسدودسازی شبکه خارجی اجرا شدند. مرز تشخیص OCR در تست‌های رفتاری mock شده است؛ این تست‌ها ادعای دقت OCR روی بانک ندارند.

در یک smoke مستقل، هر دو موتور نصب‌شده واقعاً بارگذاری شدند و تصویر متنی ساختگی خروجی معتبر چهارکاراکتری داد. زمان اولین `solve` در همان اجرا ۱۲۸٫۷ms بود؛ این یک اندازه‌گیری منفرد است، نه تضمین زمان یا دقت روی BotDetect زنده.

نسخه‌های محیط بررسی: Python 3.12، Playwright 1.56.0، ddddocr 1.5.6 و PyTorch 2.7.1 روی CPU برای inference. بررسی نحوی فایل‌های تغییرکرده و `git diff --check` موفق بودند.

برای بازاجرای پذیرش کپچا با جداسازی داده:

```powershell
python -B scripts\run_tests.py test_captcha_refresh_policy
```

بازاجرای مجموعه ۴۹تایی:

```powershell
python -B scripts\run_tests.py `
  test_captcha_refresh_policy `
  test_captcha_and_direct_navigation `
  test_generic_bank_and_captcha_retry.TestGenericBankAndCaptchaRetry.test_solve_captcha_with_model_preserves_image_on_empty_code `
  test_otp_capacity_retry_and_pause `
  test_otp_expiry_and_manual `
  test_regressions.RegressionTests.test_local_captcha_is_validated_filled_and_submitted `
  test_regressions.RegressionTests.test_captcha_service_never_returns_an_answer `
  test_regressions.RegressionTests.test_captcha_element_screenshot_is_always_removed `
  test_regressions.MandatoryV180Tests.test_01_normal_entry_to_otp_stage `
  test_regressions.MandatoryV180Tests.test_02_captcha_failure_and_manual_entry_handoff `
  test_regressions.MandatoryV180Tests.test_16_delayed_captcha_image_waits_and_solves_with_model `
  test_critical_repair.CriticalRepairTests.test_timeout_preserves_form_and_manual_fallback `
  test_video_regressions.VideoStateTests.test_unknown_otp_submit_waits_without_resending_form
```

runner بدون آرگومان همچنان کل مجموعه را اجرا می‌کند. دریافت نام تست‌ها فقط برای بازاجرای ایزوله بخش مربوط اضافه شده است.

## محدودیت مجموعه کامل و شاهد خط مبنا

اجرای کامل `python -B scripts\run_tests.py` شروع شد، شکست‌های متعدد داشت و پیش از پایان متوقف شد؛ نتیجه کامل یا سبز بودن کل پروژه ادعا نمی‌شود. چهار مورد زیر در محیط مستقل، با ماژول‌های اصلیِ بازیابی‌شده از commit مبنا نیز دوباره اجرا شدند: **۴ تست، ۳ failure و ۱ error در ۲۳٫۸۸۰ ثانیه**. بنابراین این چهار شکست پیش از این اصلاح نیز وجود دارند:

- `test_radar_ingestion_and_telegram_dedup`: شناسه رویداد `None` است.
- `test_api_endpoints_status_and_controls`: redirect ورود به‌جای JSON وضعیت ظرفیت.
- `test_quota_auto_final_disabled_prepares_without_clicking_request`: روش غیرحضوری طبق انتظار تست انتخاب نمی‌شود.
- `test_dynamic_bank_value_is_read_from_dom`: مقدار بانک `0` می‌ماند و `127` نمی‌شود.

سایر شکست‌های مجموعه کامل جداگانه ریشه‌یابی نشده‌اند. این اصلاح به منطق بانک، ظرفیت، احراز هویت یا رادار برای عبور دادن آن تست‌ها دست نمی‌زند.

لاگ‌های محلی این نوبت در پوشه زیر هستند؛ شامل لاگ اجرای کامل ناتمام، مقایسه مبنا و خروجی موفق runner نهایی. داده واقعی متقاضیان یا فایل‌های تنظیمات خصوصی برای انتشار در Git انتخاب نشده‌اند.

```text
C:\Users\amir\AppData\Local\Temp\mahan-captcha-6b1bff7552c344b0ba891f9563b8f564
```

سامانه زنده `ve.cbi.ir`، ثبت واقعی، پیامک واقعی و بسته اجرایی ویندوز آزمایش نشدند. برای اعمال اصلاح در نسخه در حال اجرا، پردازش قدیمی برنامه باید بسته و کد تازه اجرا شود. فایل‌های کامل پروژه به هم وابسته‌اند؛ انتقال صرفاً دو فایل اصلی، اصلاح callerهای ثبت‌نام و OTP را منتقل نمی‌کند.

APIهای مرورگر با مستندات Playwright از Context7 تطبیق داده شدند؛ مشخصات بسته OCR با [مخزن رسمی ddddocr](https://github.com/sml2h3/ddddocr) بررسی شد.
