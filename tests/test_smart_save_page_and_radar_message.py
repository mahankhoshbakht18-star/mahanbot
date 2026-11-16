import unittest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from core.bank_matcher import PersianBankMatcher, BankOptionInfo
from core.step3_bank_page import Step3BankPage
from core.telegram_notifier import TelegramNotifierService


class TestSmartSavePageAndRadarMessage(unittest.TestCase):
    def setUp(self):
        self.step3 = Step3BankPage()
        self.notifier = TelegramNotifierService()

    def test_parse_option_with_waiting_queue_count(self):
        text = "بانک ملت [دارای ظرفیت ثبت نام] متقاضی در صف : 1,450 نفر"
        info = PersianBankMatcher.parse_option_text(text)
        self.assertEqual(info.clean_title, "ملت")
        self.assertTrue(info.has_active_quota)
        self.assertFalse(info.is_reserve)
        self.assertEqual(info.waiting_count, 1450)

    def test_parse_option_reserve_with_waiting_count(self):
        text = "بانک تجارت [در انتظار تخصیص ظرفیت جدید (ذخیره)] متقاضی در صف : 850 نفر"
        info = PersianBankMatcher.parse_option_text(text)
        self.assertEqual(info.clean_title, "تجارت")
        self.assertFalse(info.has_active_quota)
        self.assertTrue(info.is_reserve)
        self.assertEqual(info.waiting_count, 850)

    def test_smart_deduplication_signature(self):
        opts1 = [
            BankOptionInfo(value="12", text="بانک ملت [دارای ظرفیت ثبت نام]", clean_title="ملت", has_active_quota=True),
            BankOptionInfo(value="19", text="بانک صادرات [دارای ظرفیت ثبت نام]", clean_title="صادرات", has_active_quota=True)
        ]
        sig1 = self.step3.calculate_bank_signature(opts1)
        # Same banks -> Same signature
        opts2 = [
            BankOptionInfo(value="19", text="بانک صادرات [دارای ظرفیت ثبت نام]", clean_title="صادرات", has_active_quota=True),
            BankOptionInfo(value="12", text="بانک ملت [دارای ظرفیت ثبت نام]", clean_title="ملت", has_active_quota=True)
        ]
        sig2 = self.step3.calculate_bank_signature(opts2)
        self.assertEqual(sig1, sig2)

        # Status changes from reserve to active -> Signature changes
        opts3 = [
            BankOptionInfo(value="12", text="بانک ملت [دارای ظرفیت ثبت نام]", clean_title="ملت", has_active_quota=True),
            BankOptionInfo(value="19", text="بانک صادرات [در انتظار تخصیص (ذخیره)]", clean_title="صادرات", is_reserve=True)
        ]
        sig3 = self.step3.calculate_bank_signature(opts3)
        self.assertNotEqual(sig1, sig3)

    def test_beautiful_radar_notification_formatting(self):
        active_banks = [
            {"bank_name": "بانک ملت", "waiting_count": 1450},
            {"bank_name": "بانک صادرات ایران", "waiting_count": 320}
        ]
        reserve_banks = [
            {"bank_name": "بانک تجارت", "waiting_count": 2100}
        ]
        msg = self.notifier.format_beautiful_radar_message(
            province_name="تهران",
            city_name="تهران",
            active_banks=active_banks,
            reserve_banks=reserve_banks,
            applicant_nid="0012345678",
            action_status="پایش فعال ظرفیت"
        )
        self.assertIn("<b>استان:</b> تهران", msg)
        self.assertIn("<b>شهر:</b> تهران", msg)
        self.assertIn("بانک ملت", msg)
        self.assertIn("۱,۴۵۰", msg)  # Persian formatted number
        self.assertIn("بانک تجارت", msg)
        self.assertIn("۲,۱۰۰", msg)
        self.assertIn("پایش فعال ظرفیت", msg)

    @patch.object(TelegramNotifierService, "send_raw_telegram", return_value=(True, "OK"))
    def test_page3_banks_broadcast_directly_to_channel(self, mock_send):
        active = [{"bank_name": "ملت", "waiting_count": 10}]
        reserve = [{"bank_name": "تجارت", "waiting_count": 20}]
        with patch.object(TelegramNotifierService, "get_channel_id", return_value="-1003720759140"):
            ok1, detail1 = self.notifier.broadcast_page3_banks_to_channel(
                province_name="فارس",
                active_banks=active,
                reserve_banks=reserve,
                provincial_only=True,
            )
            ok2, detail2 = self.notifier.broadcast_page3_banks_to_channel(
                province_name="فارس",
                active_banks=active,
                reserve_banks=reserve,
                provincial_only=True,
            )
        self.assertTrue(ok1)
        self.assertEqual(detail1, "sent")
        self.assertFalse(ok2)
        self.assertEqual(detail2, "duplicate_digest")
        self.assertGreaterEqual(mock_send.call_count, 1)
        sent_text = mock_send.call_args_list[0][0][1]
        self.assertIn("ملت", sent_text)
        self.assertIn("تجارت", sent_text)
        self.assertIn("فارس", sent_text)


if __name__ == "__main__":
    unittest.main()
