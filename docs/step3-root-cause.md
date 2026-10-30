# ریشه خطای بازگشت از صفحه سوم به OTP

تاریخ بررسی: ۲۰۲۶-۱۰-۳۰. مبنا: commit `6d542c1`، پیش از این تعمیر.

## شواهد بازتولید

سه تست Chromium در `tests/test_unified_otp_workflow.py` پیش از اصلاح شکست خوردند:

1. `test_redetect_otp_after_ids_change`: فرم اول شناسایی می‌شد؛ فرم همان متقاضی
   با ID تازه، با وجود label و `autocomplete=one-time-code`، شناسایی نمی‌شد.
2. `test_return_to_otp_is_not_session_expiry`: `observe()` برای یک فرم زنده OTP،
   `SESSION_EXPIRED` برمی‌گرداند، نه `STEP_2_OTP`.
3. `test_consumed_code_never_reaccepted_with_local_ttl`: کد مصرف‌شده با وضعیت‌های
   consumed، received و manual، وقتی در `_last_retained_otp` باقی بود، پذیرفته می‌شد.

خروجی اولیه: ۳ تست، ۵ شکست با احتساب زیرسناریوها. لاگ اولیه در فایل
`%TEMP%/mahanbot-step3-red.txt` نگهداری شد.

## زنجیره اجرای قبلی

- `BankSelectionBot._run_owned` مرورگر را می‌ساخت، Context را به مالک متصل می‌کرد
  و فرم کد ملی را با کنترل‌های واقعی مرحله اول پر می‌کرد.
- مسیر کپچای مرحله اول، دکمه رسمی ادامه را ارسال می‌کرد. در مرحله دوم،
  `fast_otp_sync_and_inject` پیامک را از SQLite/relay می‌گرفت و فرم را ارسال می‌کرد.
- تشخیص مرحله در `BotCore.detect_actual_page_stage` با تشخیص فیلد در
  `_locate_otp_input` و `_locate_otp_captcha_controls` یکسان نبود. حتی نام تاریخی
  `tbMobConfCode` در همه مسیرها پشتیبانی نمی‌شد.
- `BankSelectionSessionController.observe` دیدن مرحله اول یا دوم را انقضای نشست
  می‌دانست. caller در مسیر reauthenticate کوکی‌ها را پاک و به مرحله اول هدایت می‌کرد.
- در کنترلر صفحه سوم، `evaluate_and_execute_bank_logic` پیش از منطق محافظت‌شده
  tick اجرا می‌شد. این مسیر، انتخاب/ثبت و reload جداگانه و تشخیص مبتنی بر سن محلی OTP داشت.
- `mark_otp_entry_time` و `OTPQueueManager.create_request` دو شناسه مستقل می‌ساختند؛
  runtime و bot می‌توانستند درباره درخواست جاری اختلاف داشته باشند.
- handover تب، owner.page و bot.page را عوض می‌کرد، اما متغیر محلی page در caller
  الزاماً تغییر نمی‌کرد. با حذف آن مسیر موازی و بازخوانی owner.page، این اختلاف حذف شد.

## نکته تأییدشده درباره reload

در fixture با POST واقعی مرورگر، reload سند حاصل از تأیید OTP همان POST را دوباره
ارسال کرد. این رفتار با تست محلی بازتولید شد؛ سند POST را نباید برای پایش کورکورانه
reload کرد. مسیر اصلاح‌شده در این حالت از GET آدرس رسمی انتخاب بانک استفاده و
نتیجه را دوباره تشخیص می‌دهد. رفتار ظرفیت سرور واقعی از این تست قابل استنتاج نیست.

## حدود شواهد

دو regression تکمیلی نیز پیش از اصلاح قرمز شدند: دریافت تأخیردار SMS پس از resend
نباید با رکورد consumed درخواست قبلی دوباره reauthenticate ایجاد کند؛ و selector
تاریخی OTP نباید تشخیص زنده «کد امنیتی» را دور بزند. اکنون وضعیت کد به request ID
خودش محدود است و شواهد DOM بررسی‌شده بر fallback تاریخی اولویت قطعی دارند.

این گزارش علت‌های قابل بازتولید در کد و مرورگر محلی را بیان می‌کند. هیچ trace تازه‌ای
از سامانه بانک مرکزی یا گوشی واقعی گرفته نشده است؛ علت دقیق همه رخدادهای تاریخی
و رفتار فعلی سرور مقصد، تأیید میدانی نشده‌اند.
