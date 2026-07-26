# اتصال MahanBot به رله HTTPS آروان

در فایل `mahanbot.env` سه مقدار زیر را از فایل اعتبارنامه سرور وارد کنید:

```env
MAHAN_OTP_RELAY_URL=https://otp.mahanvip.ir
MAHAN_OTP_BOT_KEY=...
MAHAN_OTP_PHONE_KEY=...
```

پس از اجرای `START_MAHANBOT.cmd`، فایل `PHONE_SETUP.txt` آدرس و کلید لازم برای اپ اندروید را نمایش می‌دهد.

تمام جریان‌های OTP در `RegistrationBot` و `BankSelectionBot` ابتدا پنجره انتظار را روی Relay ثبت می‌کنند، کد را با long polling امن دریافت می‌کنند و پس از عبور موفق از مرحله OTP آن را مصرف‌شده اعلام می‌کنند. `StatusBot` به OTP نیاز ندارد و تغییری در منطق آن انجام نشده است.
