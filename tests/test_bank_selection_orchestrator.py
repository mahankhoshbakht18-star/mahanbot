"""
Bank matcher and the shared ASP.NET postback wait.
(The unused core/bank_selection package was removed; its matcher lives in core.bank_matcher.)
"""
import unittest
from unittest.mock import MagicMock

from core.bank_matcher import PersianBankMatcher, BankOptionInfo, KNOWN_BANK_CODES
from core.aspnet_postback import wait_for_postback


class TestBankSelectionArchitecture(unittest.TestCase):

    def test_composite_precedence_matching(self):
        options = [
            BankOptionInfo(value="20", text="بانک توسعه صادرات [دارای ظرفیت ثبت نام]"),
            BankOptionInfo(value="19", text="بانک صادرات ایران [دارای ظرفیت ثبت نام]"),
        ]
        # 'صادرات' must match Saderat (19), not Tose'e Saderat (20)
        match_saderat = PersianBankMatcher.match_best_bank("صادرات", options)
        self.assertIsNotNone(match_saderat)
        self.assertEqual(match_saderat.value, "19")
        match_tosee = PersianBankMatcher.match_best_bank("توسعه صادرات", options)
        self.assertIsNotNone(match_tosee)
        self.assertEqual(match_tosee.value, "20")

    def test_postback_wait_is_safe_on_idle_page(self):
        page = MagicMock()
        page.evaluate.return_value = "idle"
        self.assertTrue(wait_for_postback(page, timeout_ms=50))
        page.evaluate.return_value = "timeout"
        self.assertFalse(wait_for_postback(page, timeout_ms=50))

    def test_postback_wait_survives_full_navigation(self):
        page = MagicMock()
        page.evaluate.side_effect = Exception("Execution context was destroyed")
        self.assertTrue(wait_for_postback(page, timeout_ms=50))
        page.wait_for_load_state.assert_called_once()

    def test_static_known_codes_integrity(self):
        self.assertEqual(KNOWN_BANK_CODES["19"], "بانک صادرات ایران")
        self.assertEqual(KNOWN_BANK_CODES["12"], "بانک ملت")
        self.assertEqual(KNOWN_BANK_CODES["17"], "بانک ملی ایران")
        self.assertEqual(len(KNOWN_BANK_CODES), 30)


if __name__ == "__main__":
    unittest.main()
