#!/usr/bin/env bash
set -euo pipefail
backup=${1:?backup directory required}
previous=$(cat "$backup/previous-release")
test -d "$previous"
ln -sfn "$previous" /opt/mahan-otp-relay/current
cp -a "$backup/environment" /etc/mahan-otp-relay.env
cp -a "$backup/systemd" /etc/systemd/system/mahan-otp-relay.service
systemctl daemon-reload
systemctl restart mahan-otp-relay.service
deploy/healthcheck-mahan-otp-relay-v2.sh
