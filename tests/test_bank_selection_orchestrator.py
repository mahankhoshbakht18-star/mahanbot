"""
tests/test_bank_selection_orchestrator.py — Unit Tests for Unified Bank Selection Architecture
"""
import unittest
from unittest.mock import MagicMock
from core.bank_selection import (
    BankSelectionOrchestrator,
    PersianBankMatcher,
    BankCatalogParser,
    BankQuotaClassifier,
    QuotaStatus,
    PostbackSynchronizer,
    BankOptionInfo,
    KNOWN_BANK_CODES,
    BANK_CANONICAL_TO_CODE,
)


class TestBankSelectionArchitecture(unittest.TestCase):

    def test_catalog_parser_html(self):
        sample_html = """
        <select id="ctl00_ContentPlaceHolder1_ddlBankName">
            <option value="0">--- انتخاب کنید ---</option>
            <option value="19">بانک صادرات ایران [دارای ظرفیت ثبت نام] متقاضی در صف : 1,250 نفر</option>
            <option value="20">بانک توسعه صادرات (ذخیره) متقاضی در صف : 45 نفر</option>
            <option value="12">بانک ملت [دارای ظرفیت]</option>
        </select>
        """
        options = BankCatalogParser.parse_html(sample_html)
        self.assertEqual(len(options), 3)

        # Bank 1: Saderat (Active)
        self.assertEqual(options[0].value, "19")
        self.assertTrue(options[0].has_active_quota)
        self.assertFalse(options[0].is_reserve)
        self.assertEqual(options[0].waiting_count, 1250)

        # Bank 2: Tose'e Saderat (Reserve)
        self.assertEqual(options[1].value, "20")
        self.assertFalse(options[1].has_active_quota)
        self.assertTrue(options[1].is_reserve)
        self.assertEqual(options[1].waiting_count, 45)

        # Bank 3: Mellat (Active)
        self.assertEqual(options[2].value, "12")
        self.assertTrue(options[2].has_active_quota)
        self.assertFalse(options[2].is_reserve)

    def test_composite_precedence_matching(self):
        sample_html = """
        <select id="ctl00_ContentPlaceHolder1_ddlBankName">
            <option value="20">بانک توسعه صادرات [دارای ظرفیت ثبت نام]</option>
            <option value="19">بانک صادرات ایران [دارای ظرفیت ثبت نام]</option>
        </select>
        """
        options = BankCatalogParser.parse_html(sample_html)

        # User wants 'صادرات' -> MUST match Saderat (value 19), NOT Tose'e Saderat (value 20)
        match_saderat = PersianBankMatcher.match_best_bank("صادرات", options)
        self.assertIsNotNone(match_saderat)
        self.assertEqual(match_saderat.value, "19")

        # User wants 'توسعه صادرات' -> MUST match Tose'e Saderat (value 20)
        match_tosee = PersianBankMatcher.match_best_bank("توسعه صادرات", options)
        self.assertIsNotNone(match_tosee)
        self.assertEqual(match_tosee.value, "20")

    def test_quota_classifier_reserve_toggle(self):
        sample_html = """
        <select id="ctl00_ContentPlaceHolder1_ddlBankName">
            <option value="14">بانک مسکن (ذخیره)</option>
        </select>
        """
        options = BankCatalogParser.parse_html(sample_html)

        # When allow_reserve is False -> NO_MATCH
        status, matched, msg = BankQuotaClassifier.classify_preferences(
            options=options,
            target_banks=["مسکن"],
            allow_reserve=False,
        )
        self.assertEqual(status, QuotaStatus.NO_MATCH)
        self.assertIsNone(matched)

        # When allow_reserve is True -> RESERVE_QUOTA
        status, matched, msg = BankQuotaClassifier.classify_preferences(
            options=options,
            target_banks=["مسکن"],
            allow_reserve=True,
        )
        self.assertEqual(status, QuotaStatus.RESERVE_QUOTA)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.value, "14")

    def test_postback_synchronizer_safe_evaluation(self):
        # Mock page returning False for Sys.WebForms in evaluate
        mock_page = MagicMock()
        mock_page.evaluate.return_value = False

        self.assertFalse(PostbackSynchronizer.is_in_postback(mock_page))
        self.assertTrue(PostbackSynchronizer.wait_for_postback_complete(mock_page, timeout_ms=50))

    def test_static_known_codes_integrity(self):
        self.assertEqual(KNOWN_BANK_CODES["19"], "بانک صادرات ایران")
        self.assertEqual(KNOWN_BANK_CODES["12"], "بانک ملت")
        self.assertEqual(KNOWN_BANK_CODES["17"], "بانک ملی ایران")
        self.assertEqual(len(KNOWN_BANK_CODES), 30)


if __name__ == "__main__":
    unittest.main()
