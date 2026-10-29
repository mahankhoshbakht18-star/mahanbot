import unittest
from unittest.mock import MagicMock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from core.step3_bank_page import Step3BankPage
from core.bank_matcher import BankOptionInfo


class TestStep3BankPage(unittest.TestCase):
    def setUp(self):
        self.mock_page = MagicMock()
        self.step3 = Step3BankPage()

    def test_disable_logout_trap(self):
        self.mock_page.evaluate.return_value = True
        result = self.step3.disable_logout_trap(self.mock_page)
        self.assertTrue(result)
        self.mock_page.evaluate.assert_called()
        call_arg = self.mock_page.evaluate.call_args[0][0]
        self.assertIn("window.onbeforeunload = null", call_arg)
        self.assertIn("CloseSession", call_arg)

    def test_wait_for_update_panel_evaluates_prm(self):
        self.mock_page.evaluate.return_value = True
        ok = self.step3.wait_for_update_panel(self.mock_page, timeout_ms=2000)
        self.assertTrue(ok)
        eval_arg = self.mock_page.evaluate.call_args[0][0]
        self.assertIn("get_isInAsyncPostBack", eval_arg)

    def test_read_bank_options(self):
        self.mock_page.evaluate.return_value = [
            {"value": "0", "text": "-- انتخاب بانک --"},
            {"value": "19", "text": "بانک صادرات ایران [دارای ظرفیت ثبت نام]"},
            {"value": "18", "text": "بانک تجارت [در انتظار تخصیص ظرفیت جدید (ذخیره)]"}
        ]
        opts = self.step3.read_live_bank_options(self.mock_page)
        self.assertEqual(len(opts), 2)
        self.assertEqual(opts[0].value, "19")
        self.assertTrue(opts[0].has_active_quota)
        self.assertEqual(opts[1].value, "18")
        self.assertTrue(opts[1].is_reserve)

    def test_extract_tracking_or_error(self):
        # When error label is visible
        self.mock_page.evaluate.return_value = {
            "tracking_code": None,
            "error_msg": "ظرفیت بانک انتخاب شده تکمیل است",
            "is_success": False
        }
        res = self.step3.extract_tracking_or_error(self.mock_page)
        self.assertFalse(res["is_success"])
        self.assertEqual(res["error_msg"], "ظرفیت بانک انتخاب شده تکمیل است")

        # When tracking code exists
        self.mock_page.evaluate.return_value = {
            "tracking_code": "987654321",
            "error_msg": "",
            "is_success": True
        }
        res2 = self.step3.extract_tracking_or_error(self.mock_page)
        self.assertTrue(res2["is_success"])
        self.assertEqual(res2["tracking_code"], "987654321")


if __name__ == "__main__":
    unittest.main()
