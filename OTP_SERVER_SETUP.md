# سرور ساده OTP ماهان

## تست روی کامپیوتر

```powershell
pip install -r requirements-otp.txt
python otp_server.py
```

سرور روی `http://127.0.0.1:8010` اجرا می‌شود. برای اینکه بات در حالت لوکال از آن استفاده کند:

```powershell
$env:MAHANBOT_OTP_API="http://127.0.0.1:8010"
python server.py
```

تست خودکار کل مسیر ارسال اپ تا دریافت بات:

```powershell
python scripts/test_otp_local.py
```

## نصب روی otp.mahanvip.ir

فایل‌های لازم روی سرور:

- `otp_server.py`
- `requirements-otp.txt`

فرمان اجرا پشت Nginx:

```bash
python -m pip install -r requirements-otp.txt
uvicorn otp_server:app --host 127.0.0.1 --port 8010
```

Nginx باید دامنه `otp.mahanvip.ir` را با HTTPS به `127.0.0.1:8010` هدایت کند.

## مسیر دریافت و تشخیص خطا

داشبورد و API داخلی به‌طور پیش‌فرض روی پورت `8000` هستند. اگر فقط سرور اصلی استفاده می‌شود، اپ به `/receive_sms` یا `/api/sms/webhook` همان سرور POST می‌کند و بات از `/wait_otp/{nid}` کد را می‌خواند. برای رله مستقل، مقدار `MAHANBOT_OTP_API` باید دقیقاً آدرس همان رله‌ای باشد که اپ پیامک را به آن می‌فرستد.

در صورت تنظیم `MAHANBOT_OTP_SECRET`، اپ باید هدر `X-OTP-Secret` را همراه پیامک بفرستد. بات هدرهای تنظیم‌شده `X-OTP-Secret`، `X-API-KEY` و در صورت وجود `MAHANBOT_OTP_TOKEN` هدر Bearer را ارسال می‌کند. کلیدهای واقعی را در مخزن یا لاگ قرار ندهید.

برای تطبیق مطمئن، پیام باید `nid` و در صورت پشتیبانی اپ، `request_id` جاری را داشته باشد. پیام بدون شناسه فقط وقتی به انتظار فعال متصل می‌شود که دقیقاً یک متقاضی منتظر پیامک باشد. شماره فرستنده بانک، شماره گیرنده متقاضی نیست.

فیلدهای `otp_transport_error` و `otp_clock_skew_seconds` در runtime خطای HTTP، شبکه، JSON، تازگی یا اختلاف ساعت را بدون نمایش کد ثبت می‌کنند. Timeout ورود دستی را در همان صفحه فعال نگه می‌دارد.

برای اثبات سلامت بخش اندروید، کد Receiver/Manifest یا APK، لاگ دریافت پیامک و پاسخ HTTP ارسال به سرور لازم است. مجوز SMS، اجرای پس‌زمینه و Battery Optimization از کد پایتون قابل تشخیص قطعی نیستند.
