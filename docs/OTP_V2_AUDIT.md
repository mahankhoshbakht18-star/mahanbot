# ممیزی OTP v2

تاریخ ممیزی: ۲۰۲۶-۰۷-۲۶. دامنه بررسی شامل `server.py`، `unified_server.py`،
`unified_launcher.py`، `database.py`، Botها، launcher/actionهای مرورگر، logging،
SMS ingress/bridge، تنظیمات runtime، تست‌های OTP و Android bridge موجود است. فایل‌های
CAPTCHA فقط برای شناخت interface خوانده شدند و hash آن‌ها در `.captcha-freeze.sha256`
قفل شده است.

## یافته‌های بحرانی

| موضوع | شاهد | ریسک / تصمیم v2 |
|---|---|---|
| OTP بدون Session | `/receive_sms`، `/manual_otp` و DB با کلید `nid` | اختلاط متقاضی؛ v2 فقط `session_id` اجباری دارد |
| مسیرهای تکراری | `/manual_otp` و `/otp/manual` | رفتار مبهم؛ v1 فقط compatibility خاموش‌پیش‌فرض |
| Race | `OTP_EVENTS[nid]` و clear/set پیرامون wait | از دست رفتن wakeup؛ v2 transaction و delivery lease |
| مصرف مجدد | `wait_otp/{nid}` با fallback DB | OTP می‌تواند دوباره خوانده شود؛ v2 consume اتمیک |
| OTP قدیمی | `min_ts=0` در server و جریان‌های Bot | پذیرش داده stale؛ v2 الزام `received_at >= waiting_since` |
| OTP متقاضی دیگر | lookup فقط با کد ملی و state مشترک process | session، job و device binding اجباری |
| احراز fail-open | نبود `MAHANBOT_API_KEY` دسترسی را باز می‌گذارد | سرویس v2 بدون secret اصلاً start نمی‌شود |
| Device ID | `X-DEVICE-ID` همراه key ثابت | ID نقش credential گرفته؛ v2 bearer token hash‌شده و revoke |
| CORS | API یکپارچه policy گسترده/نامشخص دارد | v2 هیچ CORS middleware ندارد |
| Rate limit | endpointهای OTP قدیمی محدودیت ندارند | v2 limiter و Retry-After |
| logging حساس | مسیرهای قدیمی code/nid را وارد پیام خطا می‌کنند | logging ساختاریافته با metadata ماسک‌شده |
| API هاردکد | `MAHANBOT_LOCAL_API` پیش‌فرض محلی و bridgeهای v1 | تنها `MAHAN_OTP_RELAY_URL`، پیش‌فرض دامنه رسمی |
| Android 15 | bridge قبلی heartbeat/تنظیمات device key دارد | اپ جدید event-driven و WorkManager؛ بدون FGS دائم |
| reboot | state در حافظه و `OTP_EVENTS` بازیابی نمی‌شود | state پایدار DB/Room و reconcile WorkManager |

## مسیرهای قدیمی و مالکیت

- `server.py`: `/receive_sms`، `/manual_otp`، `/otp/manual`، `/wait_otp/{nid}` و
  `/otp/clear/{nid}`؛ همه session-less هستند.
- `bot_core.py`: `MAHANBOT_LOCAL_API` و دریافت/پاک‌کردن مستقیم OTP بر اساس `nid`.
- `sms_ingress.py` و `server_sms_bridge.py`: احراز قدیمی `X-DEVICE-ID` و
  `X-DEVICE-KEY` و API notify که به مسیر مصرف Bot متصل نیست.
- `database.py`: fallback ذخیره OTP روی رکورد متقاضی؛ باید فقط پشت
  `MAHAN_OTP_LEGACY_FALLBACK=false` باقی بماند و داده قدیمی migrate نشود.
- `otp_relay_client.py` موجود یک client v1 محدود است و قرارداد مرکزی کامل ندارد.
- `unified_launcher.py` مقدار `MAHANBOT_API_KEY` را خالی می‌کند؛ v2 secret مستقل و
  fail-closed دارد.

عبارت `python-ke7tg2.chbk.dev` در snapshot جاری کد پیدا نشد. `/get_otp/{nid}` نیز
در snapshot حاضر route فعال نیست، اما به‌عنوان قرارداد ممنوع در guard تست‌ها ثبت می‌شود.

## Workflow، rollback و مرزبندی

در snapshot بدون metadata گیت، `.github/workflows` و تاریخچه PR در دسترس نبود؛ بنابراین
تکرار workflowهای قبلی قابل اثبات نیست. workflowهای v2 با trigger و artifact مستقل تعریف
می‌شوند. rollback در چهار نقطه انجام می‌شود: backup کد/env/database/nginx/systemd، migration
قابل downgrade، health/smoke پس از restart و rollback خودکار به release قبلی.

## نتیجه

مسیر امن، افزودن سرویس مستقل `services/mahan-otp-relay-v2` و client مرکزی است. endpointهای
v1 فوراً حذف نمی‌شوند، اما با `ENABLE_OTP_V1=false` خاموش‌اند. OTPهای موجود در upgrade پاک
می‌شوند و هرگز به sessionهای v2 نسبت داده نمی‌شوند.
