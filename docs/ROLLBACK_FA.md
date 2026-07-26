# Rollback

update پیش از تغییر، SHA/release فعلی، environment، Nginx، systemd و dump دیتابیس را backup
می‌کند. در شکست health، script rollback symlink نسخه قبلی را restore و سرویس را restart می‌کند:
`sudo deploy/rollback-mahan-otp-relay-v2.sh /var/backups/mahan-otp-relay/<timestamp>`.
