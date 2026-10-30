# ممیزی جامع کدهای مرده و زائد (Dead Code Audit Report)

**تاریخ:** ۲۰۲۶-۱۰-۳۰  
**شاخه کاری:** `refactor/mahanbot-deep-cleanup`  
**وضعیت:** اجرا شده و اعتبارسنجی شده  

---

## ۱. خلاصه ممیزی (Executive Summary)

در این فاز، کل مخزن پروژه با ۱۸۵ فایل پایتون و بیش از ۴۱,۵۰۰ خط کد به صورت قطعی (Deterministic AST Analysis + Regex Callgraph Verification) مورد ممیزی قرار گرفت. کدهای مرده، توابع واسط منسوخ، فایل‌های دیباگ پراکنده در ریشه و ایمپورت‌های بدون مصرف با رعایت کامل «حفظ قابلیت‌های عملیاتی و سازگاری» تفکیک شدند.

---

## ۲. دسته‌بندی کدهای شناسایی‌شده

### دسته اول: اسکریپت‌های موقت و دیباگ پراکنده در ریشه مخزن (۲۲ فایل)
این فایل‌ها در مراحل مختلف توسعه به صورت دستی برای بررسی وضعیت، چاپ دیتا، یا استخراج قطعات ایجاد شده بودند و هیچ وابستگی یا استفاده‌ای در کدهای عملیاتی یا تست‌ها نداشتند:
- `analyze_scripts.py`
- `check_applicant.py`
- `dump_head.py`
- `dump_panel.py`
- `dump_panel2.py`
- `extract_branch_funcs.py`
- `extract_branch_html.py`
- `extract_test_snippets.py`
- `inspect_controllers.py`
- `inspect_head.py`
- `inspect_hozori.py`
- `inspect_saved_pages.py`
- `inspect_settings.py`
- `list_nids.py`
- `query_all_runtime.py`
- `query_db.py`
- `query_payload.py`
- `query_runtime.py`
- `search_branches.py`
- `search_dialog_handling.py`
- `search_label82.py`
- `search_tests.py`

**اقدام انجام‌شده:** انتقال ایمن به مسیر ایزوله `tools/debug_scratch/` جهت جلوگیری از شلوغی ریشه و تسریع ابزارهای تحلیل استاتیک بدون از دست رفتن قطعات کمکی.

---

### دسته دوم: توابع واسط و کدهای منسوخ در `bot_select.py` (حذف ۸۱ خط)
این متدها صرفاً به عنوان پاس‌ترو (Pass-through Wrapper) به توابع ماژول‌های دیگر تعریف شده بودند اما هیچ نقطه‌ای از سیستم دیگر آن‌ها را از روی نمونه بات فراخوانی نمی‌کرد:
1. `fetch_otp_fast`: منسوخ و جایگزین‌شده با خط لوله رویدادمحور OTP.
2. `_ensure_nid_after_reload`: منطق قدیمی که با اعتبارسنجی‌های نوین مرحله ۱ جایگزین شده بود.
3. `_wait_for_state_any_scope`: متد قدیمی انتظار فریم‌ها بدون مصرف در چرخه فعلی.
4. `_has_firewall_signal`: جایگزین‌شده با ماژول اختصاصی `bot_select_firewall.py`.
5. `_process_bank_selection_v2`: بدون فراخواننده؛ جایگزین‌شده با `BankSelectionSessionController`.
6. `_return_to_bank_field`: بدون فراخواننده.
7. `_select_first_available_branch`: بدون فراخواننده.
8. `_process_branch_selection`: بدون فراخواننده.
9. `_wait_for_branch_fully_loaded`: بدون فراخواننده.
10. `_load_runtime_data`: بدون فراخواننده.
11. `_handle_otp_dialog`: بدون فراخواننده.
12. `_notify_favorite_banks`: بدون فراخواننده.
13. `_resend_enabled`: بدون فراخواننده.

---

### دسته سوم: توابع منسوخ در `bot_core.py`
1. `log_exception`: متد لاگ تریس‌بک با صفر فراخوانی (لاگ‌ها از طریق متد استاندارد `log` ثبت می‌شوند).
2. `wait_for_manual_captcha`: alias منسوخ‌شده مربوط به نسخه‌های بسیار قدیمی با صفر فراخوانی.

---

### دسته چهارم: ایمپورت‌های بدون مصرف
با حذف متدهای واسط بالا، ایمپورت‌های زیر از `bot_select.py` حذف شدند:
- `process_bank_selection_v2`
- `return_to_bank_field`
- `select_first_available_branch`
- `process_branch_selection`
- `wait_for_branch_fully_loaded`
- `load_runtime_data`
- `handle_otp_dialog`
- `notify_favorite_banks`
- `resend_enabled`
ماژول `bot_select_bank` به طور کامل از ایمپورت‌های بالای `bot_select.py` پاکسازی شد.

---

## ۳. تضمین عدم شکستگی (Zero Regression Guarantee)
پس از انجام حذف‌های فوق:
- کلیه تست‌های حیاتی `test_unified_otp_workflow` با نتیجه **۲۸ از ۲۸ موفق (100% OK)** اجرا شدند.
- تست‌های `test_bank_selection_orchestrator` با نتیجه **۵ از ۵ موفق (100% OK)** اجرا شدند.
- تست‌های `test_captcha_refresh_policy` با رفع باگ موتور فال‌بک با نتیجه **۲۴ از ۲۴ موفق (100% OK)** اجرا شدند.
