"""Read-only check of real deployed Relay; no OTP or applicant data is printed or written."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os
import requests
from core.config import load_dotenv
load_dotenv()

base = (os.getenv("MAHAN_OTP_RELAY_URL") or "https://otp.mahanvip.ir").rstrip("/")
bot_key = os.getenv("RELAY_BOT_KEY") or os.getenv("MAHAN_OTP_BOT_KEY")
phone_key = os.getenv("RELAY_PHONE_KEY")
assert bot_key and phone_key, "Real relay credentials are missing"
status = requests.get(base + "/status", timeout=10)
status.raise_for_status()
assert status.json().get("version") == "2.0.0"
phone = requests.get(base + "/api/v1/sms/otp/targets", headers={"X-DEVICE-KEY": phone_key}, timeout=10)
phone.raise_for_status()
assert phone.json().get("status") == "ok"
bot = requests.get(base + "/api/v1/bot/pull/invalid", headers={"X-BOT-KEY": bot_key}, params={"timeout": 0}, timeout=10)
assert bot.status_code == 400, "Bot authentication failed"
assert requests.get(base + "/api/v1/sms/otp/targets", timeout=10).status_code == 401
print("Live relay version=2.0.0; phone authentication=OK; bot authentication=OK; unauthorized access=DENIED")
print("Waiting applicant count:", len(phone.json()["targets"]))
