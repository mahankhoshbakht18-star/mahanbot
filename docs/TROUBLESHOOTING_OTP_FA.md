# عیب‌یابی OTP

- خاکستری: رضایت یا pairing کامل نیست.
- نارنجی: session/queue/retry در جریان است.
- قرمز 401/403: ارسال متوقف و pairing مجدد شود.
- 404/410: session جاری cancel و بعد از ظاهر شدن فیلد OTP، session تازه ساخته شود.
- timeout شبکه: WorkManager با backoff و Retry-After تلاش کند؛ raw SMS را log نکنید.
