# نتایج آزمون چرخه صفحه سوم / OTP

تاریخ: ۲۰۲۶-۱۰-۳۰. Python unittest و Chromium واقعی از Playwright نصب‌شده.
تست‌های جدید ترافیک مرورگر را به fixture محلی محدود می‌کنند. runner از SQLite و
profile موقت استفاده و requests شبکه خارجی را مسدود می‌کند.

## نتایج ثبت‌شده

| اجرا | تعداد | زمان | نتیجه |
|---|---:|---:|---|
| بازتولید اولیه | ۳ | ۳٫۸۰۶ s | ۵ شکست شامل زیرسناریوهای مصرف OTP؛ رفتار معیوب اثبات شد |
| مجموعه مرتبط پس از ساخت | ۲۰۱ | ۳۵۰٫۰۲۸ s | موفق |
| آخرین اجرای کامل ثبت‌شده | ۴۳۱ | ۴۷۴٫۴۴۲ s | ۱۵ failure و ۴ error قدیمی؛ آماده استقرار نیست |
| بررسی نسخه مبنا، بخش اول | ۳۴ | ۲۶٫۶۹۱ s | همان ۷ failure و ۲ error |
| بررسی نسخه مبنا، بخش دوم | ۱۲ | ۴۰٫۸۴۸ s | همان ۸ failure و ۲ error |
| پس از اصلاح دریافت تأخیردار | ۶۱ | ۷۸٫۹۴۱ s | موفق |
| بازبینی نهایی DOM / OTP / CAPTCHA / API / UI | ۶۳ | ۶۶٫۹۴۴ s | موفق |
| ایزوله‌سازی آرشیو، تست Chromium و regression OTP | ۳ | ۳٫۵۸۶ s | موفق |
| regression آرشیو و معماری بانک | ۹ | ۰٫۱۰۶ s | موفق |
| اجرای پایانی تمام سناریوهای جدید + آرشیو/معماری | ۳۷ | ۵۲٫۸۳۴ s | موفق؛ تمام ۲۸ سناریوی جدید با هم اجرا شدند |

اعداد سطرها جمع‌پذیر نیستند؛ مجموعه‌ها هم‌پوشانی دارند. اجرای کامل پیش از چند اصلاح
پایانی کوچک و اضافه‌شدن دو تست آخر ثبت شد؛ تغییرات پایانی با مجموعه‌های متمرکز بعدی
بررسی شدند. بنابراین عدد ۴۳۱ به معنای اجرای دوباره تمام تست‌ها بر آخرین byte سورس نیست.
کد فعلی دارای ۲۸ سناریوی جدید در test_unified_otp_workflow.py است.

فهرست نام‌های شکست اجرای کامل با اجتماع نام‌های شکست دو اجرای archive مبنا
مقایسه شد: **۱۹ مورد موجود قبلی، صفر نام جدید**. هیچ skip/xfail برای پنهان‌کردن
شکست افزوده نشد. assertionهای قدیمی «قفل pending نباشد» به «pending بماند»
تبدیل شدند؛ معیار جلوگیری از ارسال تکراری سخت‌گیرانه‌تر شد.

## ماتریس پذیرش مأموریت

| سناریو | شاهد |
|---|---|
| ورود اولیه از فرم کد ملی به OTP و بانک | initial_entry_otp_and_server_confirmed_bank_transition و whole_worker_recovers_from_bank_to_otp_in_the_same_context |
| reload و باقی‌ماندن در بانک | native_reload_stays_on_bank_without_authentication |
| reload به OTP، ID تازه و برگشت به بانک | reload_otp_changed_dom_new_challenge_and_reentry؛ کد جدید با تأخیر می‌رسد |
| کد مصرف‌شده | consumed_code_never_reaccepted_with_local_ttl؛ استفاده مجدد در manual هم رد می‌شود |
| کد منقضی | expired_otp_uses_official_resend_and_new_code |
| تغییر/ابهام DOM | redetect_otp_after_ids_change و ambiguous_dom_never_enters_either_field؛ CAPTCHA با ID تاریخی OTP نیز پذیرفته نمی‌شود |
| قطع اندروید و ورود دستی | android_disconnect_manual_persian_code_in_current_form |
| دو متقاضی | two_applicants_use_independent_contexts_and_challenges؛ Contextها جدا و کد نامرتبط رد می‌شود |
| تب بسته‌شده | target_closed_preserves_checkpoint_without_actions و تست‌های موجود browser/ownership |
| ثبت با نتیجه نامشخص | unknown_final_result_blocks_reload_reauth_and_otp و restart_route_preserves_unresolved_submission |
| CAPTCHA تازه / postback | changed_captcha_clears_previous_answer_without_submitting، postback_waits_for_fresh_dom_and_disabled_continue و ۱۹ تست سیاست CAPTCHA |
| کنترل امنیتی سرور | rate_limit_stops_before_otp_or_reload و server_error_cannot_start_bank_selection_or_final_submit |
| فراخوانی HTTP از thread دیگر | http_repairs_queue_commands_without_cross_thread_browser_access |
| snapshot ایزوله | archive_uses_temporary_root_and_captures_dom_on_owner_thread |

ورود و ادامه در fixture از POST واقعی مرورگر استفاده می‌کند. در worker کامل فقط
setup مرورگر و پاسخ CAPTCHA آزمایشی آماده می‌شوند؛ dispatcher واقعی bot، کنترلر
بانک، مسیر بازیابی، تشخیص فرم و OTP اجرا می‌شوند. ساخت مرورگر یک بار، Page و
Context در دو ورود بانک همان‌اند و دو POST تأیید با دو کد متفاوت ثبت می‌شوند.

## شکست‌های موجود قبل از تغییر

- API ظرفیت: test_api_endpoints_status_and_controls.
- داشبورد: دو تست کنترل ثبت متقاضی / فرم ویرایش متقاضی.
- لایسنس: سه تست LicenseClientIntegrationTests و یک تست مدیریت LicenseUiTests.
- مدل OCR: سه تست CaptchaEngineFallbackTests.
- API تنظیمات ثبت: test_settings_patch_is_validated_preserves_other_data_and_supports_inheritance.
- ناوبری: test_queue_preserves_rejection_as_failure و test_select_run_stops_on_rejection_without_restart.
- شعبه/حضوری: test_fallback_to_in_person_when_online_full و سناریوی ۱۸ MandatoryV180Tests.
- حذف ثبت‌نام: دو تست نبود کد رهگیری / endpoint حذف.
- تلگرام: دو تست تنظیمات ثابت و قالب اعلان.

برخی از این موارد ناسازگاری fixture با احراز هویت یا قرارداد فعلی‌اند؛ «شکست تست»
به‌تنهایی اثبات باگ عملیاتی نیست. برای سبزکردن آن‌ها، احراز هویت یا guardها حذف نشدند.
تست قدیمی تلگرام به مقدار credential ثابت وابسته است؛ مقدار آن در این گزارش یا
artifact خلاصه منتشر نشده و هیچ پیام واقعی ارسال نشده است.

## بررسی‌های استاتیک و اندازه‌گیری

- py_compile روی تمام Pythonهای تغییرکرده و ماژول‌های جدید: موفق.
- تعداد فایل‌های Python بررسی‌شده: ۴۲.
- `node --check static/script.js`: موفق.
- `git diff --check`: موفق؛ فقط هشدار تبدیل LF/CRLF Git دیده شد.
- یک benchmark محلی ۱۰ نمونه: میانه ۲۴٫۶۴۳ ms و بیشینه ۵۲٫۶۱۶ ms. اجرای بعدی
  میانه ۲۳٫۷۸۸ ms و بیشینه ۵۰٫۵۳۸ ms داشت. baseline سرعت قابل مقایسه موجود نیست.

خلاصه بدون متن SMS، token یا مقدار فرم در `diagnostics/step3-verification.json` است.
لاگ‌های خام اجراها در `%TEMP%/mahanbot-step3-*.txt` باقی‌اند و به گزارش عمومی کپی
نشده‌اند. فایل‌های آزمایشی آرشیوساز قدیمی در پوشه پشتیبان مجاور پروژه نگه داشته شدند.

## بازتولید

```powershell
python scripts/run_tests.py test_unified_otp_workflow
python scripts/run_tests.py test_unified_otp_workflow test_otp_expiry_and_manual test_captcha_refresh_policy.CaptchaRefreshPolicyTests test_video_regressions.VideoStateTests test_regressions.MandatoryV180Tests.test_03_valid_otp_entry_and_transition_to_bank_selection test_dashboard_ui.DashboardUiTests.test_workflow_badge_shows_current_phase_and_escapes_server_reason
python scripts/run_tests.py test_step3_bank_page test_bank_selection_orchestrator
python scripts/run_tests.py
node --check static/script.js
git diff --check
```

هیچ گوشی، اتصال GSM واقعی، نشست واقعی بانک، ارسال نهایی واقعی یا deployment در
این مأموریت آزموده/اجرا نشده است. گیت سراسری هنوز سبز نیست.
