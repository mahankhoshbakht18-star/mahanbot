#!/usr/bin/env bash
set -euo pipefail
backup=/var/backups/mahan-otp-relay/$(date -u +%Y%m%dT%H%M%SZ)
install -d -m 0700 "$backup"
readlink -f /opt/mahan-otp-relay/current >"$backup/previous-release"
cp -a /etc/mahan-otp-relay.env "$backup/environment"
cp -a /etc/nginx/sites-enabled/otp.mahanvip.ir "$backup/nginx" 2>/dev/null || true
cp -a /etc/systemd/system/mahan-otp-relay.service "$backup/systemd"
pg_dump --format=custom --file="$backup/database.dump" mahan_otp
if ! deploy/install-mahan-otp-relay-v2.sh; then
  deploy/rollback-mahan-otp-relay-v2.sh "$backup"
  exit 1
fi
