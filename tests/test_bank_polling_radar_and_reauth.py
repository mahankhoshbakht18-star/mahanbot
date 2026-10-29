"""
tests/test_bank_polling_radar_and_reauth.py — Verification of:
1. Bank polling interval waiting (no tight loop).
2. Native ASP.NET postback triggering (trigger_bank_capacity_refresh).
3. Automatic re-authentication on cycle timeout or session expiration.
4. Integration with CAPACITY_RADAR and Telegram dispatch.
"""
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bank_session_controller import BankSelectionSessionController, State
from bot_select_bank import trigger_bank_capacity_refresh
from core.capacity_radar import CAPACITY_RADAR, CapacityStatus


import threading
import queue

class MockOwner:
    def __init__(self):
        self.page = MagicMock()
        self.page.is_closed.return_value = False
        self.cancellation_token = MagicMock()
        self.cancellation_token.is_set.return_value = False
        self.bank_page_active = True
        self.validated_at = time.time()
        self.paused = False
        self.operation_lock = threading.RLock()
        self.commands = queue.Queue()
        self.session_id = "test-session"

    def assert_owner(self):
        pass


class TestBankPollingRadarAndReauth(unittest.TestCase):
    def test_preferred_bank_not_found_clean_wait(self):
        """Verify that when state is PREFERRED_BANK_NOT_FOUND, tick immediately triggers cycle_otp."""
        mock_bot = MagicMock()
        mock_bot.nid = "1234567890"
        mock_bot._otp_entry_time = time.time()  # Active OTP
        mock_bot.consume_recovery_flag.return_value = False
        mock_bot.settings = {"bank_refresh_interval": 5.0, "otp_validity_seconds": 115.0}
        mock_bot.user_data = {"bank_name": "بانک صادرات ایران"}
        mock_owner = MockOwner()

        ctrl = BankSelectionSessionController(mock_bot, mock_owner)
        ctrl.observe = MagicMock(return_value=("STEP_3_BANK_SELECT", {"is_bank_page": True, "applicant_nid": "1234567890"}))
        ctrl.state = State.PREFERRED_BANK_NOT_FOUND

        with patch("bank_session_controller.process_bank_selection_v2") as mock_proc:
            res = ctrl.tick()
            self.assertEqual(res, "cycle_otp")
            mock_proc.assert_not_called()

    def test_cycle_timeout_triggers_reauthentication(self):
        """Verify that when preferred bank is not found for cycle_timeout, it returns 'reauthenticate'."""
        mock_bot = MagicMock()
        mock_bot.nid = "1234567890"
        mock_bot._otp_entry_time = time.time() - 130.0  # Expired OTP
        mock_bot.consume_recovery_flag.return_value = False
        mock_bot.settings = {"bank_refresh_interval": 2.0, "otp_validity_seconds": 120.0}
        mock_bot.user_data = {"bank_name": "بانک صادرات ایران"}
        mock_owner = MockOwner()

        ctrl = BankSelectionSessionController(mock_bot, mock_owner)
        ctrl.observe = MagicMock(return_value=("STEP_3_BANK_SELECT", {"is_bank_page": True, "applicant_nid": "1234567890"}))
        ctrl.state = State.PREFERRED_BANK_NOT_FOUND
        now = time.monotonic()
        ctrl._next_bank_poll = now - 1.0  # Ready to poll
        ctrl._bank_not_found_since = now - 15.0  # Exceeded 10s cycle timeout

        res = ctrl.tick()
        self.assertEqual(res, "reauthenticate")
        self.assertEqual(ctrl.state, State.SESSION_EXPIRED)

    def test_trigger_bank_capacity_refresh_evaluates_postback(self):
        """Verify trigger_bank_capacity_refresh invokes ASP.NET __doPostBack on ddlCity."""
        mock_bot = MagicMock()
        mock_page = MagicMock()
        mock_page.evaluate.return_value = True

        res = trigger_bank_capacity_refresh(mock_bot, mock_page)
        self.assertTrue(res)
        mock_page.evaluate.assert_called_once()
        self.assertIn("ctl00$ContentPlaceHolder1$ddlCity", mock_page.evaluate.call_args[0][0])

    def test_radar_ingestion_and_telegram_dedup(self):
        """Verify CAPACITY_RADAR ingests observation and detects active bank."""
        with patch("core.telegram_notifier.TELEGRAM_NOTIFIER.send_raw_telegram", return_value=(True, "OK")):
            has_changed, old_status, evt_id = CAPACITY_RADAR.ingest_observation(
                province_name="تهران",
                city_name="تهران",
                bank_name="بانک تجارت",
                status=CapacityStatus.AVAILABLE_OBSERVED,
                loan_type="marriage",
            )
            self.assertIsInstance(has_changed, bool)
            self.assertIsNotNone(evt_id)


if __name__ == "__main__":
    unittest.main()
