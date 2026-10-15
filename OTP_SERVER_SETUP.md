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
