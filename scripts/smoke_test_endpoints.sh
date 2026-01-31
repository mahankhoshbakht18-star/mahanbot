#!/usr/bin/env bash
set -euo pipefail

API_URL=${API_URL:-http://127.0.0.1:8000}
API_KEY_HEADER=()
if [[ -n "${MAHANBOT_API_KEY:-}" ]]; then
  API_KEY_HEADER=( -H "X-API-KEY: ${MAHANBOT_API_KEY}" )
fi

timestamp=$(date +%s)
NID=${NID:-"9${timestamp:1:9}"}

payload=$(cat <<JSON
{
  "full_name": "Smoke Test",
  "national_id": "${NID}",
  "data": {
    "tracking_code": "TRK-${NID}"
  }
}
JSON
)

echo "==> Creating applicant ${NID}"
curl -sS -X POST "${API_URL}/applicants" \
  -H "Content-Type: application/json" \
  -d "${payload}"

echo -e "\n==> POST /bot/start-select"
select_payload=$(cat <<JSON
{
  "nid": "${NID}",
  "loan_type": "rbtnNaghdi"
}
JSON
)

curl -sS -X POST "${API_URL}/bot/start-select" \
  "${API_KEY_HEADER[@]}" \
  -H "Content-Type: application/json" \
  -d "${select_payload}"

echo -e "\n==> POST /bot/action/view-status/${NID}"
curl -sS -X POST "${API_URL}/bot/action/view-status/${NID}" \
  "${API_KEY_HEADER[@]}"

echo -e "\n==> GET /applicants"
curl -sS "${API_URL}/applicants"

echo -e "\nSmoke test completed."
