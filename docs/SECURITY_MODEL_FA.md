# مدل امنیتی OTP v2

تهدیدها: replay، سرقت token، اختلاط متقاضی، OTP stale، worker race، log leakage و device revoke.
کنترل‌ها: bearer token hash‌شده، HMAC کد ملی، idempotency key، transaction/row lock، delivery
lease، timestamp محدود، CORS بسته، trusted host، body limit، secret fail-closed، no-store و
حذف ciphertext پس از consume. Secretها فقط environment هستند.
