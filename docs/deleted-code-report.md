# گزارش دقیق کدهای حذف‌شده و سبک‌سازی (Deleted Code Report)

**تاریخ:** ۲۰۲۶-۱۰-۳۰  
**محیط:** Windows 11 Workstation / MAHANBOT  
**شاخه گیت:** `refactor/mahanbot-deep-cleanup`  

---

## ۱. جدول کدهای حذف‌شده

| نام تابع / شناسه | فایل مبدأ | خطوط قبلی | علت حذف | وضعیت تست پس از حذف |
|---|---|---|---|---|
| `fetch_otp_fast` | `bot_select.py` | 133–138 | متد قدیمی خواندن کد پیامک؛ با خط لوله رویدادمحور `wait_for_otp` و `fast_otp_sync_and_inject` جایگزین شده بود. | پاس شد |
| `_ensure_nid_after_reload` | `bot_select.py` | 1018–1039 | بدون فراخواننده؛ اعتبارسنجی و پر کردن کدملی در فاز ورود اولیه و بازیابی متمرکز است. | پاس شد |
| `_wait_for_state_any_scope` | `bot_select.py` | 1176–1186 | بدون فراخواننده؛ فریم‌ها توسط `otp_form_detector` به صورت ایزوله مانیتور می‌شوند. | پاس شد |
| `_has_firewall_signal` | `bot_select.py` | 1188–1198 | بدون فراخواننده؛ ماژول مستقل `bot_select_firewall.py` جایگزین آن شده است. | پاس شد |
| `_process_bank_selection_v2` | `bot_select.py` | 1347–1348 | متد واسط بدون فراخواننده؛ کنترل مستقیماً توسط `BankSelectionSessionController` انجام می‌شود. | پاس شد |
| `_return_to_bank_field` | `bot_select.py` | 1350–1351 | متد واسط قدیمی بدون مصرف. | پاس شد |
| `_select_first_available_branch` | `bot_select.py` | 1353–1354 | متد واسط قدیمی بدون مصرف. | پاس شد |
| `_process_branch_selection` | `bot_select.py` | 1356–1357 | متد واسط قدیمی بدون مصرف. | پاس شد |
| `_wait_for_branch_fully_loaded` | `bot_select.py` | 1359–1360 | متد واسط قدیمی بدون مصرف. | پاس شد |
| `_load_runtime_data` | `bot_select.py` | 1362–1363 | متد واسط قدیمی بدون مصرف. | پاس شد |
| `_handle_otp_dialog` | `bot_select.py` | 1365–1366 | متد واسط قدیمی بدون مصرف؛ دیالوگ‌ها در `BotCore` مدیریت می‌شوند. | پاس شد |
| `_notify_favorite_banks` | `bot_select.py` | 1368–1369 | متد واسط قدیمی بدون مصرف؛ اعلان‌ها در سشن کنترلر مدیریت می‌شوند. | پاس شد |
| `_resend_enabled` | `bot_select.py` | 1377–1378 | متد واسط بدون مصرف. | پاس شد |
| `log_exception` | `bot_core.py` | 202–211 | متد لاگ تریس‌بک با صفر فراخوانی؛ کلیه لاگ‌ها از متد ساختاریافته `log` با پشتیبانی متادیتا عبور می‌کنند. | پاس شد |
| `wait_for_manual_captcha` | `bot_core.py` | 602–611 | نام مستعار منسوخ با صفر فراخوانی. | پاس شد |

---

## ۲. ایمپورت‌های ماژولی حذف‌شده

در فایل `bot_select.py`:
- حذف بلوک کامل:
  ```python
  from bot_select_bank import (
      process_bank_selection_v2,
      return_to_bank_field,
      select_first_available_branch,
      process_branch_selection,
      wait_for_branch_fully_loaded,
      load_runtime_data,
  )
  ```
- حذف موارد زائد از `bot_select_otp`:
  - `handle_otp_dialog`
  - `notify_favorite_banks`
  - `resend_enabled`

---

## ۳. اسکریپت‌های دیباگ خارج‌شده از ریشه مخزن

۲۲ اسکریپت آزمایشی و یک‌بار مصرف از ریشه خارج و به `tools/debug_scratch/` منتقل گردید:
- کاهش آلودگی شاخه اصلی
- تسریع تحلیل‌های استاتیک، Ruff و pytest
- حفظ کامل کدها برای مراجعات تحقیقاتی در پوشه اختصاصی
