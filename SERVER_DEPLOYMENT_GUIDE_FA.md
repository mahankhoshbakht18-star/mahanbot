# راهنمای استقرار سرور MAHANBOT 2.0 و سرور پیامک (otp.mahanvip.ir)

این راهنما مراحل کامل راه‌اندازی و میزبانی نسخه جدید ماهان‌بات و سرور رله پیامک را بر روی سرور لینوکس ابونتو (Ubuntu VPS) با دامنه `otp.mahanvip.ir` توضیح می‌دهد.

---

## ۱. پیش‌نیازها

- یک سرور ابونتو (نسخه 22.04 یا 24.04 LTS)
- دامنه `otp.mahanvip.ir` با رکورد A متصل به IP سرور
- دسترسی SSH به سرور با کاربر `root`

---

## ۲. نصب خودکار با یک دستور (Automated Deployment)

پس از اتصال به سرور از طریق SSH، دستورات زیر را اجرا کنید:

```bash
# ۱. کلون یا آپلود فایل‌های پروژه در سرور
mkdir -p /var/www/mahanbot
cd /var/www/mahanbot

# ۲. اجرای اسکریپت نصب خودکار
chmod +x deploy/deploy_server.sh
./deploy/deploy_server.sh
```

این اسکریپت به صورت خودکار موارد زیر را انجام می‌دهد:
1. پایتون، Nginx، Certbot و ابزارهای لازم را نصب می‌کند.
2. محیط مجازی `.venv` را ساخته و تمام کتابخانه‌ها را نصب می‌کند.
3. مرورگر کرومیوم Playwright را نصب می‌کند.
4. سرویس‌های سیستمی `mahan-otp` (پورت 8010) و `mahanbot` (پورت 8000) را در پس‌زمینه لینوکس فعال و راه‌اندازی می‌کند.
5. وب‌سرور Nginx را با دایرکتوری دانلود اختصاصی `/downloads/` پیکربندی می‌کند.

---

## ۳. فعال‌سازی گواهی امنیتی SSL (HTTPS) رایگان

پس از اتصال دامنه به IP سرور، دستور زیر را اجرا کنید:

```bash
certbot --nginx -d otp.mahanvip.ir
```

---

## ۴. قرار دادن فایل‌های دانلود در سرور (`/downloads/`)

مسیر فایل‌های قابل دانلود در سرور لینوکس در آدرس زیر قرار دارد:
`/var/www/mahanbot/downloads/`

برای فعال شدن لینک‌های مستقیم دانلود:

۱. **فایل APK اپلیکیشن اندروید:**
فایل `mahan-otp-2.0.1.apk` را داخل این پوشه قرار دهید:
`https://otp.mahanvip.ir/downloads/mahan-otp-2.0.1.apk`

۲. **فایل فشرده نسخه نهایی ماهان‌بات:**
فایل `mahanbot-latest.zip` (که با اسکریپت `scripts/package_release.py` ساخته می‌شود) را داخل این پوشه بگذارید:
`https://otp.mahanvip.ir/downloads/mahanbot-latest.zip`

---

## ۵. دستورات کاربردی مدیریت سرویس‌ها

```bash
# بررسی وضعیت سرور OTP
systemctl status mahan-otp

# بررسی وضعیت بات
systemctl status mahanbot

# مشاهده لاگ‌های زنده دریافت پیامک
journalctl -u mahan-otp -f

# ری‌استارت سرویس‌ها
systemctl restart mahan-otp
systemctl restart mahanbot
```

---

## ۶. بررسی صحت عملکرد لینک‌ها و API

- سلامت سرور پیامک: `https://otp.mahanvip.ir/health`
- استعلام نسخه و آپدیت: `https://otp.mahanvip.ir/api/version`
- پوشه دانلودها: `https://otp.mahanvip.ir/downloads/`
- لینک دانلود APK: `https://otp.mahanvip.ir/downloads/mahan-otp-2.0.1.apk`
