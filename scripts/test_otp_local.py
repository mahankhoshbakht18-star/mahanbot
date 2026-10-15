"""End-to-end local check: Android-style POST -> bot-style long wait."""

import os
import subprocess
import sys
import tempfile
import threading
import time

import requests


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = "18010"
BASE_URL = f"http://127.0.0.1:{PORT}"
NID = "0013547890"
CODE = "483921"


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        env = os.environ.copy()
        env["MAHAN_OTP_DB"] = os.path.join(temp_dir, "otp-test.db")
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "otp_server:app", "--host", "127.0.0.1", "--port", PORT],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(40):
                try:
                    if requests.get(f"{BASE_URL}/health", timeout=0.3).ok:
                        break
                except requests.RequestException:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Local OTP server did not start")

            result = {}

            def bot_wait():
                response = requests.get(
                    f"{BASE_URL}/wait_otp/{NID}", params={"timeout": 5, "min_ts": time.time()}, timeout=7
                )
                response.raise_for_status()
                result.update(response.json())

            waiter = threading.Thread(target=bot_wait)
            waiter.start()
            time.sleep(0.3)
            response = requests.post(
                f"{BASE_URL}/receive_sms", json={"nid": NID, "code": CODE}, timeout=2
            )
            response.raise_for_status()
            waiter.join(timeout=7)
            assert result.get("otp") == CODE, result
            fetched = requests.get(f"{BASE_URL}/get_otp/{NID}", timeout=2)
            fetched.raise_for_status()
            assert fetched.json().get("otp") == CODE
            requests.post(f"{BASE_URL}/otp/clear/{NID}", timeout=2).raise_for_status()
            assert requests.get(f"{BASE_URL}/get_otp/{NID}", timeout=2).status_code == 204
            print(f"OK: app -> server -> bot | nid={NID} otp={CODE}")
        finally:
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
