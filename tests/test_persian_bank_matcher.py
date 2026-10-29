import unittest
import sys
import os

# Add workspace to sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from core.bank_matcher import PersianBankMatcher, KNOWN_BANK_CODES, BankOptionInfo


class TestPersianBankMatcher(unittest.TestCase):
    def setUp(self):
        self.matcher = PersianBankMatcher()

    def test_persian_normalization(self):
        self.assertEqual(self.matcher.normalize("بانك ملّي ايران ۱۲۳"), "بانک ملی ایران 123")
        self.assertEqual(self.matcher.normalize("صادرات  \u200c  ایران"), "صادرات ایران")

    def test_composite_bank_priority(self):
        # 'توسعه صادرات' should match 'توسعه صادرات' (code 20), NEVER plain 'صادرات' (code 19)
        options = [
            BankOptionInfo(value="19", text="بانک صادرات ایران [دارای ظرفیت ثبت نام]"),
            BankOptionInfo(value="20", text="بانک توسعه صادرات [دارای ظرفیت ثبت نام]"),
        ]
        match = self.matcher.match_best_bank(priority_name="توسعه صادرات", available_options=options)
        self.assertIsNotNone(match)
        self.assertEqual(match.value, "20")

        # 'صادرات' should match 'صادرات ایران' (code 19), NOT 'توسعه صادرات'
        match2 = self.matcher.match_best_bank(priority_name="صادرات", available_options=options)
        self.assertIsNotNone(match2)
        self.assertEqual(match2.value, "19")

    def test_mehr_vs_mehr_iran(self):
        options = [
            BankOptionInfo(value="60", text="بانک قرض الحسنه مهر ایران [دارای ظرفیت ثبت نام]"),
        ]
        match = self.matcher.match_best_bank(priority_name="مهر ایران", available_options=options)
        self.assertIsNotNone(match)
        self.assertEqual(match.value, "60")

    def test_active_vs_reserve_capacity(self):
        options = [
            BankOptionInfo(value="18", text="بانک تجارت [در انتظار تخصیص ظرفیت جدید (ذخیره)]"),
            BankOptionInfo(value="12", text="بانک ملت [دارای ظرفیت ثبت نام]"),
        ]
        # Active capacity check
        parsed_tejarat = self.matcher.parse_option_text("بانک تجارت [در انتظار تخصیص ظرفیت جدید (ذخیره)]")
        self.assertFalse(parsed_tejarat.has_active_quota)
        self.assertTrue(parsed_tejarat.is_reserve)

        parsed_mellat = self.matcher.parse_option_text("بانک ملت [دارای ظرفیت ثبت نام]")
        self.assertTrue(parsed_mellat.has_active_quota)
        self.assertFalse(parsed_mellat.is_reserve)

        # Match with allow_reserve=False
        match = self.matcher.match_best_bank(priority_name="تجارت", available_options=options, allow_reserve=False)
        self.assertIsNone(match, "Tejarat is reserve-only, should not match when allow_reserve is False")

        # Match with allow_reserve=True
        match_reserve = self.matcher.match_best_bank(priority_name="تجارت", available_options=options, allow_reserve=True)
        self.assertIsNotNone(match_reserve)
        self.assertEqual(match_reserve.value, "18")

    def test_known_bank_codes_completeness(self):
        # 30 official CBI banks
        self.assertEqual(KNOWN_BANK_CODES["19"], "بانک صادرات ایران")
        self.assertEqual(KNOWN_BANK_CODES["12"], "بانک ملت")
        self.assertEqual(KNOWN_BANK_CODES["17"], "بانک ملی ایران")
        self.assertEqual(KNOWN_BANK_CODES["18"], "بانک تجارت")
        self.assertEqual(KNOWN_BANK_CODES["15"], "بانک سپه")
        self.assertEqual(KNOWN_BANK_CODES["14"], "بانک مسکن")
        self.assertEqual(KNOWN_BANK_CODES["16"], "بانک کشاورزی")
        self.assertEqual(KNOWN_BANK_CODES["20"], "بانک توسعه صادرات")
        self.assertEqual(KNOWN_BANK_CODES["22"], "بانک توسعه تعاون")
        self.assertEqual(KNOWN_BANK_CODES["60"], "بانک قرض‌الحسنه مهر ایران")
        self.assertEqual(KNOWN_BANK_CODES["70"], "بانک قرض‌الحسنه رسالت")
        self.assertGreaterEqual(len(KNOWN_BANK_CODES), 30)


if __name__ == "__main__":
    unittest.main()
