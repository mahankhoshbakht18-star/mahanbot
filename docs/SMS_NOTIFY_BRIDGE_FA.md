# اتصال امن اعلان پیامک SMSForwardManager به MahanBot

## هدف

این فاز فقط **رسیدن پیامک جدید** را از گوشی Android به MahanBot اعلام می‌کند. موارد زیر از گوشی خارج نمی‌شوند:

- متن پیامک
- OTP یا کد تأیید
- PIN و رمز
- کد ملی
- شماره کامل فرستنده

کاربر کد تأیید را مستقیماً در مرورگر بازشده توسط MahanBot وارد می‌کند. داشبورد فقط هشدار صوتی/تصویری نشان می‌دهد.

## جریان نهایی

```text
MahanBot وارد صفحه OTP می‌شود
→ گوشی پیامک جدید دریافت می‌کند
→ SMSForwardManager یک Arrival Signal بدون متن ارسال می‌کند
→ داشبورد هشدار «پیامک رسید» نمایش می‌دهد
→ کاربر کد را مستقیماً در مرورگر وارد می‌کند
→ بات ادامه فرم را تشخیص می‌دهد
```

## فایل‌های این فاز

### MahanBot

- `sms_notify_core.py`: Dedup، Heartbeat و نگهداری کوتاه‌مدت رخدادها
- `server_sms_bridge.py`: API اعلان و تزریق رابط هشدار در داشبورد
- `static/sms_notify_bridge.js`: هشدار صوتی/تصویری و وضعیت آنلاین دستگاه
- `setup_sms_bridge_windows.cmd`: ساخت تنظیمات و کلید دستگاه
- `start_sms_bridge_windows.cmd`: اجرای سرور Bridge
- `open_sms_bridge_firewall_admin.cmd`: بازکردن پورت فقط روی شبکه Private

### Android

- `android_bridge/MahanBotBridgeSettings.kt`
- `android_bridge/MahanBotNotifyClient.kt`
- `android_bridge/MahanBotSmsNotifyHook.kt`
- `android_bridge/MahanBotBridgeSettingsCard.kt`

> خط `package` فایل‌های Android را با package واقعی پروژه SMSForwardManager هماهنگ کنید.

---

# راه‌اندازی سمت Windows

## ۱. نصب پایه

ابتدا نصب معمول MahanBot را انجام دهید:

```bat
setup_windows.cmd
```

سپس:

```bat
setup_sms_bridge_windows.cmd
```

این فایل یک کلید تصادفی در فایل محلی زیر ایجاد می‌کند:

```text
.env.sms-bridge
```

کلید واقعی را داخل Git یا چت عمومی قرار ندهید.

## ۲. استفاده از دیتابیس قبلی

فایل `.env.sms-bridge` را با Notepad باز کنید و مسیر دیتابیس را اضافه کنید:

```env
MAHANBOT_DB_PATH=C:\Users\amir\Desktop\mahanbot-old\cbi_ultimate.db
```

## ۳. Windows Firewall

روی فایل زیر راست‌کلیک و **Run as administrator** را بزنید:

```text
open_sms_bridge_firewall_admin.cmd
```

Rule فقط برای شبکه‌های دارای Profile برابر `Private` و فقط پورت MahanBot ایجاد می‌شود.

## ۴. اجرای سرور

```bat
start_sms_bridge_windows.cmd
```

داشبورد کامپیوتر:

```text
http://127.0.0.1:8000/
```

## ۵. پیدا کردن IP کامپیوتر

در CMD اجرا کنید:

```bat
ipconfig
```

IPv4 کارت Wi-Fi یا Ethernet را پیدا کنید. نمونه:

```text
192.168.1.20
```

نشانی قابل استفاده در گوشی:

```text
http://192.168.1.20:8000
```

`127.0.0.1` را داخل گوشی وارد نکنید؛ این نشانی روی گوشی به خود گوشی اشاره می‌کند.

## ۶. بررسی وضعیت اتصال

در PowerShell کامپیوتر:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/sms/notify/status
```

خروجی باید نشان دهد:

```text
status                 ok
mode                   arrival-notification-only
device_key_configured  True
message_content_accepted False
otp_content_accepted     False
```

---

# یکپارچه‌سازی سمت Android

## ۱. کپی فایل‌ها

چهار فایل پوشه `android_bridge` را داخل package مناسب ماژول `app` قرار دهید و package بالای فایل‌ها را با namespace پروژه هماهنگ کنید.

مثال مسیر:

```text
app/src/main/java/<your-package>/mahanbot/
```

## ۲. مجوز اینترنت

در Manifest اپ باید این مجوز موجود باشد:

```xml
<uses-permission android:name="android.permission.INTERNET" />
```

نسخه 2.4.0 احتمالاً از قبل برای قابلیت‌های اینترنتی این مجوز را دارد؛ دوباره‌کاری لازم نیست.

## ۳. نمایش تنظیمات Bridge

در صفحه تنظیمات Compose اضافه کنید:

```kotlin
MahanBotBridgeSettingsCard()
```

سپس در اپ:

1. اتصال MahanBot را فعال کنید.
2. نشانی کامپیوتر را مثل `http://192.168.1.20:8000` وارد کنید.
3. مقدار `MAHANBOT_SMS_DEVICE_KEY` را از فایل `.env.sms-bridge` وارد کنید.
4. روی «ذخیره» بزنید.
5. روی «تست اتصال» بزنید.

## ۴. Hook دریافت پیامک

داخل BroadcastReceiver فعلی، پس از اینکه Android رسیدن پیام را تأیید کرد، Hook را اجرا کنید:

```kotlin
import java.util.UUID

val receivedAt = System.currentTimeMillis()
val localEventSeed = UUID.randomUUID().toString()

MahanBotSmsNotifyHook.onSmsArrived(
    context = context,
    receiver = this,
    receivedAtMillis = receivedAt,
    localEventSeed = localEventSeed,
    senderHint = "bank",
)
```

بهتر است اگر اپ برای هر پیام یک شناسه داخلی پایدار دارد، همان شناسه را به‌عنوان `localEventSeed` بدهید تا پیام تکراری Dedup شود.

موارد زیر را به Hook ندهید:

```text
SMS body
OTP
کد ملی
شماره کامل فرستنده
```

`senderHint` فقط یک دسته‌بندی عمومی کوتاه مثل `bank` است و اختیاری است.

## ۵. HTTP در شبکه آزمایشگاهی

برای تست روی Wi-Fi خصوصی می‌توان از HTTP استفاده کرد. اگر Debug Manifest فعلی `usesCleartextTraffic=true` دارد، کار اضافه لازم نیست.

برای انتشار واقعی:

- از HTTPS استفاده کنید.
- کلید دستگاه را با Android Keystore/EncryptedSharedPreferences نگه دارید.
- پورت را مستقیماً روی اینترنت عمومی باز نکنید.

---

# قرارداد API

## اعلان رسیدن پیامک

```http
POST /api/v1/sms/notify
X-DEVICE-ID: android-device-id
X-DEVICE-KEY: private-device-key
Content-Type: application/json
```

```json
{
  "device_id": "android-device-id",
  "message_id": "opaque-local-event-id",
  "received_at": 1784370000.0,
  "source": "android",
  "sender_hint": "bank"
}
```

فیلد اضافه مانند `body`، `otp` یا `code` توسط سرور رد می‌شود.

## Heartbeat

```http
POST /api/v1/sms/notify/heartbeat
X-DEVICE-ID: android-device-id
X-DEVICE-KEY: private-device-key
Content-Type: application/json
```

```json
{
  "device_id": "android-device-id",
  "sent_at": 1784370000.0
}
```

## مشاهده وضعیت در داشبورد

```http
GET /api/v1/sms/notify/latest
GET /api/v1/sms/notify/devices
```

این دو مسیر از احراز هویت مدیریتی MahanBot استفاده می‌کنند.

---

# عیب‌یابی

## خطای 401

کلید واردشده در اپ با `MAHANBOT_SMS_DEVICE_KEY` یکسان نیست.

## خطای 503

متغیر `MAHANBOT_SMS_DEVICE_KEY` هنگام اجرای سرور بارگذاری نشده است. سرور را با `start_sms_bridge_windows.cmd` اجرا کنید.

## اپ می‌گوید اتصال برقرار نیست

موارد زیر را بررسی کنید:

- گوشی و کامپیوتر روی یک شبکه باشند.
- در اپ از IP کامپیوتر استفاده شده باشد، نه `127.0.0.1`.
- Windows Network Profile روی شبکه مورداعتماد `Private` باشد.
- Rule فایروال ساخته شده باشد.
- VPN گوشی یا کامپیوتر مسیر شبکه محلی را مسدود نکرده باشد.
- سرور هنوز در CMD در حال اجرا باشد.

## پیامک رسید ولی هشدار در داشبورد دیده نشد

- صفحه را با `Ctrl + F5` بازآوری کنید.
- پایین صفحه وضعیت «دستگاه آنلاین» را بررسی کنید.
- دکمه «تست اتصال» در اپ را بزنید.
- مسیر وضعیت اتصال را کنترل کنید.

## هشدار صدا ندارد

مرورگرها پخش صدا را تا اولین کلیک کاربر محدود می‌کنند. یک بار داخل داشبورد کلیک کنید؛ هشدارهای بعدی صدا خواهند داشت.

---

# مرز این فاز

این فاز:

- رسیدن پیامک را اعلام می‌کند.
- وضعیت آنلاین گوشی را نشان می‌دهد.
- اعلان تکراری را کنترل می‌کند.
- اپراتور را برای ورود دستی کد راهنمایی می‌کند.

این فاز:

- متن پیامک را منتقل نمی‌کند.
- OTP را استخراج یا وارد نمی‌کند.
- احراز هویت بانکی را دور نمی‌زند.
- فرم نهایی را بدون تأیید اپراتور ارسال نمی‌کند.
