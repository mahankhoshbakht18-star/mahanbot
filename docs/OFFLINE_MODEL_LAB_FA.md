# اتصال مدل محلی به هسته MahanBot

## وضعیت فعلی

مدل اختصاصی `my_captcha_model.pth` اکنون فقط یک ابزار جداگانه در داشبورد نیست. تمام پیش‌بینی‌های آزمایشگاه مدل از مسیر مرکزی زیر عبور می‌کنند:

```text
CaptchaService.predict_local
```

حالت سازگار متنی نیز در هسته وجود دارد:

```python
CaptchaService().solve(image_bytes, mode="local_test")
```

این اتصال برای تست‌های ساختگی یا تصاویر آزمایشی متعلق به توسعه‌دهنده طراحی شده است.

## مرز اجرای زنده

- حالت‌های `general` و `firewall` در `CaptchaService` همیشه `None` برمی‌گردانند.
- در Job بانکی، بات همچنان وارد جریان ورود دستی کپچا می‌شود.
- مدل به Playwright، Screenshot صفحه زنده، Locator یا دکمه Submit متصل نیست.
- نتیجه مدل فقط در مسیر `local_test` تولید می‌شود.
- تصاویر آزمایشی در RAM پردازش و روی دیسک ذخیره نمی‌شوند.
- خروجی شامل این مشخصات است:

```json
{
  "integration_route": "CaptchaService.local_test",
  "scope": "offline-test-only",
  "live_workflow_connected": false
}
```

## روش استفاده از داشبورد

1. فایل `START_MAHANBOT.cmd` را اجرا کنید.
2. وارد بخش «آزمایشگاه مدل» شوید.
3. یک تصویر PNG، JPG، WEBP یا BMP تا حجم ۲ مگابایت انتخاب کنید.
4. دکمه «اجرای مدل آفلاین» را بزنید.
5. درخواست از API به `CaptchaService` و سپس مدل CRNN ارسال می‌شود.
6. خروجی CTC و اطمینان تقریبی نمایش داده می‌شود.
7. برای آزادسازی RAM دکمه «آزادسازی حافظه مدل» را بزنید.

## استفاده در تست Python

```python
from captcha_service import CaptchaService

service = CaptchaService()
result = service.predict_local(image_bytes)
print(result["prediction"])
```

یا برای سازگاری با رابط قدیمی:

```python
text = service.solve(image_bytes, mode="local_test")
```

## معماری

- ورودی خاکستری: `160 × 60`
- CNN چهارمرحله‌ای
- BiLSTM دو‌لایه
- خروجی CTC برای اعداد و حروف بزرگ انگلیسی
- بارگذاری Lazy: PyTorch فقط هنگام اولین تست مدل وارد حافظه می‌شود.
- Adapter مرکزی: `CaptchaService`
- موتور inference: `OfflineModelLab`

## API محلی

- `GET /api/v1/model/status`
- `POST /api/v1/model/load`
- `POST /api/v1/model/predict`
- `POST /api/v1/model/unload`

درخواست Predict به‌صورت `multipart/form-data` و با فیلد `image` ارسال می‌شود. Endpoint پیش‌بینی از `CaptchaService.predict_local` استفاده می‌کند.

## تست‌ها

فایل زیر اتصال هسته و مدل را کنترل می‌کند:

```text
tests/test_captcha_service_local_model.py
```

تست Windows CI نیز checkpoint واقعی را از طریق `CaptchaService` اجرا و هم‌زمان تأیید می‌کند که حالت `general` مدل را فراخوانی نمی‌کند.

## Rollback

برای حذف اتصال هسته:

1. `captcha_service.py` را به نسخه Manual-only قبلی برگردانید.
2. در `model_lab_api.py` فراخوانی `CaptchaService.predict_local` را با موتور مستقیم جایگزین کنید.
3. تست `tests/test_captcha_service_local_model.py` را حذف کنید.

جریان دستی اجرای زنده مستقل از این قابلیت باقی می‌ماند.
