"""
tests/test_generic_bank_and_captcha_retry.py
Verification of:
1. CAPTCHA model solving with automatic reload/retry on empty or unrendered DOM images.
2. Generalized bank priority logic for ANY applicant-selected bank (Tejarat, Mellat, Melli, Saderat, etc.).
3. 5-second continuous monitoring cycle until 120s SMS OTP expiration.
"""
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bank_session_controller import BankSelectionSessionController, State
from bot_core import BotCore


class MockOwner:
    def __init__(self):
        import threading
        import queue
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


class TestGenericBankAndCaptchaRetry(unittest.TestCase):
    def test_generic_bank_names_extracted_correctly(self):
        """Verify that _get_target_bank_names extracts ANY bank configured for applicant."""
        mock_bot = MagicMock()
        mock_bot.nid = "1234567890"
        mock_bot.settings = {}
        mock_owner = MockOwner()

        ctrl = BankSelectionSessionController(mock_bot, mock_owner)

        # Case A: Priority banks list with custom banks (Tejarat, Mellat)
        ctrl._preferences = MagicMock(return_value={
            "priority_banks": [
                {"name": "بانک تجارت"},
                {"name": "بانک ملت"}
            ]
        })
        names = ctrl._get_target_bank_names()
        self.assertEqual(names, ["بانک تجارت", "بانک ملت"])

        # Case B: Single bank in primary_bank (Bank Melli)
        ctrl._preferences = MagicMock(return_value={"primary_bank": "بانک ملی ایران"})
        names = ctrl._get_target_bank_names()
        self.assertEqual(names, ["بانک ملی ایران"])

        # Case C: Single bank in user_data bank_name (Bank Sepah)
        mock_bot.user_data = {"bank_name": "بانک سپه"}
        ctrl._preferences = MagicMock(return_value={})
        names = ctrl._get_target_bank_names()
        self.assertEqual(names, ["بانک سپه"])

    def test_solve_captcha_with_model_retries_on_empty_code(self):
        """Verify solve_captcha_with_model retries on fresh image when initial attempt returns empty."""
        bot = BotCore(nid="1234567890", settings={"captcha_min_length": 4, "captcha_max_length": 8})
        mock_page = MagicMock()
        mock_input = MagicMock()
        mock_input.input_value.return_value = "83YRMR"

        # Captcha service returns empty on attempt 1, valid code on attempt 2
        mock_service = MagicMock()
        mock_service.solve_element.side_effect = ["", "83YRMR"]
        bot.captcha_service = mock_service

        with patch("browser_actions.BrowserActions.find_captcha_input", return_value=mock_input), \
             patch("browser_actions.BrowserActions.human_fill") as mock_fill, \
             patch("bot_select_captcha.wait_for_captcha_image_ready", return_value=(MagicMock(), mock_page)), \
             patch("bot_core.DBHandler.append_captcha_attempt"), \
             patch("time.sleep"):
            success = bot.solve_captcha_with_model(
                mock_page,
                input_selector="#tbCaptcha",
                button_selector="#btnContinue",
                captcha_element=None,
            )
            self.assertTrue(success)
            self.assertEqual(mock_service.solve_element.call_count, 2)
            mock_fill.assert_called_once_with(mock_input, "83YRMR")

    def test_monitoring_cycle_persists_during_120s_validity(self):
        """Verify that within 120s OTP validity, cycle stays active and re-checks every 5s."""
        mock_bot = MagicMock()
        mock_bot.nid = "1234567890"
        mock_bot._otp_entry_time = time.time() - 30.0  # 30s elapsed (< 120s)
        mock_bot.consume_recovery_flag.return_value = False
        mock_bot.settings = {"bank_refresh_interval": 5.0, "otp_validity_seconds": 120.0}
        mock_bot.user_data = {"bank_name": "بانک تجارت"}
        mock_owner = MockOwner()

        ctrl = BankSelectionSessionController(mock_bot, mock_owner)
        ctrl.observe = MagicMock(return_value=("STEP_3_BANK_SELECT", {"is_bank_page": True, "applicant_nid": "1234567890"}))
        ctrl.state = State.PREFERRED_BANK_NOT_FOUND
        now = time.monotonic()
        ctrl._next_bank_poll = now - 0.5  # Time to poll

        with patch("bank_session_controller.process_bank_selection_v2", return_value="no_match"), \
             patch("bot_select_bank.trigger_bank_capacity_refresh"):
            res = ctrl.tick()
            self.assertEqual(res, "waiting")
            # Bank poll scheduled in future (5s)
            self.assertGreater(ctrl._next_bank_poll, now)


if __name__ == "__main__":
    unittest.main()
