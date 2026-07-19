import tempfile
import unittest
from pathlib import Path

import unified_launcher


class UnifiedLauncherTests(unittest.TestCase):
    def test_env_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mahanbot.env"
            expected = {
                "MAHANBOT_BROWSER_CHANNEL": "msedge",
                "MAHANBOT_SMS_DEVICE_KEY": "device-test-key",
                "MAHANBOT_SMS_PORT": "8010",
            }
            unified_launcher._write_env(path, expected)
            self.assertEqual(unified_launcher._read_env(path), expected)

    def test_log_level_is_case_insensitive_and_has_safe_fallback(self):
        self.assertEqual(unified_launcher._normalize_log_level("info"), "INFO")
        self.assertEqual(unified_launcher._normalize_log_level("Warning"), "WARNING")
        self.assertEqual(unified_launcher._normalize_log_level("invalid"), "INFO")
        self.assertEqual(unified_launcher._normalize_log_level(None), "INFO")

    def test_phone_setup_contains_notification_only_notice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original = unified_launcher.PHONE_SETUP_FILE
            try:
                unified_launcher.PHONE_SETUP_FILE = Path(temp_dir) / "PHONE_SETUP.txt"
                endpoint = unified_launcher._write_phone_setup(
                    {
                        "MAHANBOT_SMS_PORT": "8010",
                        "MAHANBOT_SMS_DEVICE_KEY": "safe-device-key",
                    }
                )
                text = unified_launcher.PHONE_SETUP_FILE.read_text(encoding="utf-8")
                self.assertIn("/api/v1/sms/notify", endpoint)
                self.assertIn("Only the arrival notification is accepted", text)
                self.assertNotIn("otp=", text.lower())
            finally:
                unified_launcher.PHONE_SETUP_FILE = original


if __name__ == "__main__":
    unittest.main()
