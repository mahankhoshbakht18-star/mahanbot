# استقرار آروان

environment را در `/etc/mahan-otp-relay.env` با permission 0600 قرار دهید. سپس
`deploy/install-mahan-otp-relay-v2.sh` را اجرا کنید. سرویس روی `127.0.0.1:18019` bind می‌شود.
Nginx باید TLS، HSTS، body limit و proxy به همین port داشته باشد. پس از deploy، health محلی،
عمومی و smoke API اجرا می‌شود. هیچ secret در خروجی script چاپ نمی‌شود.
