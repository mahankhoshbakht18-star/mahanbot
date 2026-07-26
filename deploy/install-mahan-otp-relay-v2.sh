#!/usr/bin/env bash
set -euo pipefail
install_root=/opt/mahan-otp-relay
release_dir="$install_root/releases/$(date -u +%Y%m%dT%H%M%SZ)"
test -f /etc/mahan-otp-relay.env
install -d -m 0750 "$release_dir"
cp -a services/mahan-otp-relay-v2/. "$release_dir/"
python3 -m venv "$release_dir/.venv"
"$release_dir/.venv/bin/pip" install --disable-pip-version-check "$release_dir"
ln -sfn "$release_dir" "$install_root/current"
systemctl daemon-reload
systemctl restart mahan-otp-relay.service
deploy/healthcheck-mahan-otp-relay-v2.sh
