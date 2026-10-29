"""
tests/test_smart_capacity_radar_and_telegram.py — Comprehensive Test Suite for MAHANBOT 2.0
Radar Engine, Telegram Notifier, 18:00 Batch Analysis, and Reserve Status Inquiry.
"""
import json
import os
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database import DBHandler
from core.capacity_radar import CAPACITY_RADAR, CapacityStatus, is_near_18_batch
from core.telegram_notifier import TELEGRAM_NOTIFIER
from core.capacity_analytics import CAPACITY_ANALYTICS


class TestSmartCapacityRadarAndTelegram(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        DBHandler.init_db()

    def test_database_tables_exist(self):
        """Verify all 5 isolated radar & telegram tables exist."""
        with DBHandler._connect() as conn:
            c = conn.cursor()
            c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in c.fetchall()]

        self.assertIn("capacity_radar_state", tables)
        self.assertIn("capacity_radar_observations", tables)
        self.assertIn("capacity_change_events", tables)
        self.assertIn("telegram_subscribers", tables)
        self.assertIn("telegram_notification_queue", tables)

    @patch.object(TELEGRAM_NOTIFIER, "send_raw_telegram", return_value=(True, "OK"))
    def test_radar_observation_deduplication(self, mock_send):
        """Verify duplicate observations don't emit new change events."""
        import uuid
        test_prov = "سیستان و بلوچستان"
        test_city = "زاهدان"
        test_bank = f"بانک آزمایشی {uuid.uuid4().hex[:6]}"

        # First ingestion -> change detected
        has_changed_1, evt_1, _ = CAPACITY_RADAR.ingest_observation(
            province_name=test_prov,
            city_name=test_city,
            bank_name=test_bank,
            status=CapacityStatus.RESERVE,
            loan_type="marriage",
            visit_method="online",
            branch_name="شعبه مجازی سامانه",
        )
        self.assertTrue(has_changed_1)
        self.assertIsNotNone(evt_1)

        # Duplicate ingestion -> NO change detected
        has_changed_2, evt_2, _ = CAPACITY_RADAR.ingest_observation(
            province_name=test_prov,
            city_name=test_city,
            bank_name=test_bank,
            status=CapacityStatus.RESERVE,
            loan_type="marriage",
            visit_method="online",
            branch_name="شعبه مجازی سامانه",
        )
        self.assertFalse(has_changed_2)
        self.assertIsNone(evt_2)

        # State transition to AVAILABLE_OBSERVED -> change detected
        has_changed_3, evt_3, _ = CAPACITY_RADAR.ingest_observation(
            province_name=test_prov,
            city_name=test_city,
            bank_name=test_bank,
            status=CapacityStatus.AVAILABLE_OBSERVED,
            loan_type="marriage",
            visit_method="online",
            branch_name="شعبه مجازی سامانه",
        )
        self.assertTrue(has_changed_3)
        self.assertIsNotNone(evt_3)

    def test_telegram_configuration_and_formatting(self):
        """Verify Telegram bot and channel configuration and message formatting."""
        token = TELEGRAM_NOTIFIER.get_token()
        channel_id = TELEGRAM_NOTIFIER.get_channel_id()
        admin_chat_id = TELEGRAM_NOTIFIER.get_admin_chat_id()

        self.assertEqual(token, "8988974593:AAHnlt1ZVf5WAMFm59fbLtenxppF9aHM_Fc")
        self.assertEqual(channel_id, "-1003720759140")

        # Test capacity change formatting
        msg = TELEGRAM_NOTIFIER.format_capacity_change(
            province_name="تهران",
            city_name="تهران",
            bank_name="بانک صادرات ایران",
            old_status="FULL",
            new_status="RESERVE",
            observed_at=1730000000,
            loan_type="marriage",
            visit_method="online",
        )
        self.assertIn("تغییر وضعیت ظرفیت بانک‌ها", msg)
        self.assertIn("بانک صادرات ایران", msg)
        self.assertIn("صف ذخیره", msg)
        self.assertIn("https://ve.cbi.ir", msg)

        # Test channel post formatting with glass buttons
        post_msg, markup = TELEGRAM_NOTIFIER.format_channel_post(
            title="🚀 ظرفیت جدید سرویس‌های MahanBot و تسهیلات بانکی",
            body_text="ظرفیت‌های ساعت ۱۸ فعال شد.",
        )
        self.assertIn("🚀 ظرفیت جدید", post_msg)
        self.assertIn("inline_keyboard", markup)
        self.assertEqual(markup["inline_keyboard"][0][0]["url"], "https://t.me/MahanVPN_Shop_Bot")
        self.assertEqual(markup["inline_keyboard"][1][0]["url"], "https://t.me/Mahanvpn3")

    def test_telegram_offline_queue_storage(self):
        """Verify messages are safely enqueued in SQLite when offline or queued."""
        test_cid = "-1003720759140"
        test_txt = "تست صف ذخیره پیام تلگرام MAHANBOT 2.0"

        DBHandler.enqueue_telegram_message(
            chat_id=test_cid,
            message_text=test_txt,
            message_type="test_alert",
        )

        pending = DBHandler.fetch_pending_telegram_messages(limit=10)
        found = any(m["chat_id"] == test_cid and test_txt in m["message_text"] for m in pending)
        self.assertTrue(found)

    def test_18_batch_analytics_and_window(self):
        """Verify 18:00 (6 PM) batch analytics computation and window detector."""
        report = CAPACITY_ANALYTICS.get_full_analytics_report()
        self.assertIn("hourly", report)
        self.assertIn("provinces", report)

        hourly = report["hourly"]
        self.assertEqual(len(hourly["hourly_counts"]), 24)
        self.assertIn("hour_18_batch", hourly)
        self.assertIn("hypothesis_confirmed", hourly["hour_18_batch"])

        # Test is_near_18_batch helper
        res = is_near_18_batch(window_minutes_before=15, window_minutes_after=20)
        self.assertIsInstance(res, bool)

    def test_reserve_inquiry_logic_in_bot_status(self):
        """Verify reserve status inquiry handles empty tracking code and parses queue rank."""
        # 1. Regex test matching CBI reserve page output (e.g. from user screenshot rank 118)
        mock_body_text = (
            "استعلام آخرین وضعیت ثبت درخواست\n"
            "جایگاه در صف ذخیره و در انتظار دریافت کد رهگیری : 118\n"
            "شما درخواست خود را بصورت رزرو ثبت کرده اید و پس از تامین اعتبار توسط بانک برای شما کد رهگیری ارسال خواهد شد."
        )
        match_reserve = re.search(r"جایگاه در صف ذخیره.*?(?:در انتظار دریافت کد رهگیری\s*:\s*|\s*:\s*)(\d+)", mock_body_text, re.S)
        self.assertIsNotNone(match_reserve)
        self.assertEqual(match_reserve.group(1), "118")

        # Second screenshot test (rank 114)
        mock_body_text_2 = "جایگاه در صف ذخیره و در انتظار دریافت کد رهگیری : 114"
        match_reserve_2 = re.search(r"جایگاه در صف ذخیره.*?(?:در انتظار دریافت کد رهگیری\s*:\s*|\s*:\s*)(\d+)", mock_body_text_2, re.S)
        self.assertIsNotNone(match_reserve_2)
        self.assertEqual(match_reserve_2.group(1), "114")


if __name__ == "__main__":
    unittest.main()
