# MahanBot OTP Companion

سامانه مستقل دریافت رضایت‌محور OTP شامل FastAPI Relay v2، client session-based در MahanBot و
اپ Android با دو flavor است. تمام ارتباط‌ها از `https://otp.mahanvip.ir` عبور می‌کنند و منطق
CAPTCHA بدون تغییر با guard هش محافظت می‌شود.

## Build و تست

- Backend: `pip install -e './services/mahan-otp-relay-v2[test]'` سپس
  `pytest services/mahan-otp-relay-v2/tests`.
- MahanBot: `pytest tests/test_otp_relay_client_v2.py tests/test_captcha_freeze_guard.py`.
- Android با JDK 17 و SDK 35: در `android/mahanbot-otp-companion`، taskهای
  `assembleConsentDebug` و `assemblePrivateDebug` را اجرا کنید.

Artifactها `MahanBot-OTP-Companion-1.0.0-consent-debug.apk` و
`MahanBot-OTP-Companion-1.0.0-private-debug.apk` هستند. Consent flavor مجوز SMS ندارد؛ private
فقط `RECEIVE_SMS` دارد. Pairing از Dashboard با code یا QR پنج‌دقیقه‌ای انجام و token در Keystore
نگهداری می‌شود؛ هیچ secret دستی در UI نیست.

## Deploy و rollback

`.env.example` را بدون commit secret تکمیل و `deploy/install-mahan-otp-relay-v2.sh` اجرا کنید.
ارتقا با `deploy/update-mahan-otp-relay-v2.sh` backup می‌گیرد و شکست health باعث rollback می‌شود.
جزئیات در `docs/ARVAN_DEPLOY_FA.md` و `docs/ROLLBACK_FA.md` است.

## خطاهای رایج

401/403 نیازمند توقف queue و pairing مجدد است؛ 404/410 نیازمند session تازه است؛ network/429/5xx
با backoff و `Retry-After` retry می‌شوند. `final_submit` همچنان false است و ثبت نهایی فقط با اجازه
صریح کاربر انجام می‌شود.
