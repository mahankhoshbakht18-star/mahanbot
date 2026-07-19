import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OperationCenterAssetTests(unittest.TestCase):
    def test_single_sms_submit_path(self):
        script = (ROOT / "static" / "operation_center.js").read_text(encoding="utf-8")
        self.assertIn('"/manual_otp"', script)
        self.assertNotIn('"/receive_sms"', script)
        self.assertIn("mahanbot:sms-arrived", script)

    def test_bank_and_final_submit_events_are_supported(self):
        script = (ROOT / "static" / "operation_center.js").read_text(encoding="utf-8")
        self.assertIn("bank_match_found", script)
        self.assertIn("final_submit_required", script)
        self.assertIn("final_submit_clicked", script)
        self.assertIn("/api/v1/operation-center/final-submit", script)

    def test_archive_assets_exist(self):
        self.assertTrue((ROOT / "static" / "operation_center.css").is_file())
        self.assertTrue((ROOT / "bank_archive.py").is_file())
        self.assertTrue((ROOT / "operation_center_api.py").is_file())
        self.assertTrue((ROOT / "operation_integration.py").is_file())


if __name__ == "__main__":
    unittest.main()
