# معماری OTP v2

`MahanBot → Create Session → Relay → Paired Device → Consent/private receiver → local extraction
→ encrypted Room queue → HTTPS → delivery lease → Bot fill → consume`.

مرز اعتماد روی Relay است؛ گوشی و رایانه ارتباط مستقیم ندارند. شناسه دستگاه credential نیست.
توکن خام فقط هنگام claim نمایش داده و در سرور تنها HMAC آن نگهداری می‌شود. OTP در حالت موقت
رمز و پس از consume غیرقابل بازیابی می‌شود. Session با job، hash کد ملی، device و بازه زمانی
مقید است. CAPTCHA خارج از این معماری و کاملاً freeze است.

ADR: long-poll محافظه‌کارانه به‌جای polling سریع انتخاب شد؛ delivery lease امکان crash recovery
همان Job را می‌دهد، اما Job دیگر را مسدود می‌کند. Redis optional است و correctness به آن وابسته نیست.
