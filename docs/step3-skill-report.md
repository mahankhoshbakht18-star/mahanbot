# استفاده واقعی از مهارت‌ها — ۲۰۲۶-۱۰-۳۰

فهرست مهارت‌های ارائه‌شده در نشست بررسی و موارد مرتبط انتخاب شد. ریشه مهارت‌های
مهندسی `C:/Users/amir/.codex/skills/` است؛ ابزار ناموجود نصب یا قابل استفاده فرض نشد.
مهارت‌های Ponytail تکراری به‌عنوان یک دستورالعمل مشترک اعمال شدند.

| مهارت | کاربرد و خروجی واقعی |
|---|---|
| using-agent-skills | انتخاب جریان عیب‌یابی، تست، ساخت تدریجی، بازبینی و مستندسازی |
| ponytail | استفاده از بانک‌گزینی محافظت‌شده موجود؛ حذف مسیر موازی reload/submit؛ بدون وابستگی جدید |
| debugging-and-error-recovery | بازتولید سه شکست اولیه در Chromium پیش از اصلاح |
| test-driven-development | تست قرمز، اصلاح، تست سبز؛ سناریوهای DOM/API/هم‌زمانی و نتیجه نامشخص |
| planning-and-task-breakdown | چک‌لیست پذیرش در task.md؛ تاریخچه کار قبلی حفظ شد |
| incremental-implementation | slice تشخیص، OTP، مالکیت/بازیابی، قفل ثبت و رابط؛ تست‌های مرحله‌ای |
| git-workflow-and-versioning | شاخه codex/step3-unified-otp-recovery و archive مبنای 6d542c1؛ بدون push/merge |
| graphify | query اجرا شد؛ graph.json در graphify-out موجود نبود و query شکست خورد؛ روابط با rg و خواندن callerها دنبال شد؛ گراف ادعایی ساخته نشد |
| browser-testing-with-devtools | دستورالعمل ایزوله‌سازی مرورگر خوانده شد؛ MCP مربوط به DevTools موجود نبود؛ جایگزین واقعی Playwright Python/Chromium با route محلی استفاده شد |
| security-and-hardening | کنترل تعلق OTP، حذف پذیرش کد مصرف‌شده، masking، مالکیت thread، قفل پایدار؛ بدون Strix یا هدف خارجی |
| api-and-interface-design | خروجی ساختاریافته تشخیص؛ حفظ entry pointهای قدیمی؛ HTTP command queue و خطای 409 برای درخواست ناسازگار |
| observability-and-instrumentation | workflow_state/reason/check time، انتقال‌های ماسک‌شده و diagnostic بدون مقدار فرم |
| performance-optimization | زمان‌سنجی تشخیص/fill؛ حذف OCR تکراری روی همان bytes و refresh بی‌دلیل؛ ادعای سرعت سامانه واقعی نشده است |
| frontend-ui-engineering | برچسب فارسی با کلاس‌های موجود، role=status و escapeHtml؛ حفظ theme |
| code-review-and-quality | بررسی مسیرهای نهایی، callerهای OTP، رفتار POST reload و تست‌های شکست‌خورده |
| documentation-and-adrs | هفت سند خواسته‌شده، توضیح تصمیم‌ها و محدودیت‌های میدانی |

Context7 با `resolve_library_id` و `query_docs` برای `/microsoft/playwright-python`
واقعاً فراخوانی شد: locatorها، انتظار DOM، response در برابر navigation و remove_listener.
نام API ابزار نصب‌شده query_docs بود، نه get-library-docs قدیمی.

مهارت‌های تصاویر، Office، Google Drive، Sites، Pets، نصب skill، تغییر provider،
و انتشار استفاده نشدند؛ این مأموریت به آن‌ها نیاز نداشت. OmniRoute، Strix و
playwright-stealth استفاده نشدند. هیچ زیربخش برای تحلیل به provider دیگری ارسال نشد.
