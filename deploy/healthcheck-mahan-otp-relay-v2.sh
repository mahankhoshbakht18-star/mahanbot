#!/usr/bin/env bash
set -euo pipefail
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:18019/status >/dev/null
curl --fail --silent --show-error --max-time 15 https://otp.mahanvip.ir/status >/dev/null
echo "OTP Relay v2 health check passed"
