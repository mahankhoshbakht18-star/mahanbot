import unittest
from unittest.mock import MagicMock, patch
import time
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from core.bank_matcher import PersianBankMatcher, BankOptionInfo
from bank_page_manager import BankPageState as State


class MockOwner:
    def __init__(self):
        import threading
        import queue
        self.operation_lock = threading.RLock()
        self.commands = queue.Queue()
        self.cancellation_token = threading.Event()
        self.page = MagicMock()
        self.context = MagicMock()
        self.bank_page_active = True
        self.paused = False
        self.session_id = "test-session"
        self.validated_at = time.time()

    def assert_owner(self):
        pass


class TestPiece456Puzzle(unittest.TestCase):
    def test_active_bank_auto_final_submit_false_does_not_submit(self):
        """Piece 4: When active bank is found but auto_final_submit is False, do NOT submit, continue monitoring."""
        from bank_session_controller import BankSelectionSessionController
        mock_bot = MagicMock()
        mock_bot.nid = "1234567890"
        mock_bot._otp_entry_time = time.time()  # Active OTP
        mock_bot.consume_recovery_flag.return_value = False
        mock_bot.settings = {"bank_refresh_interval": 5.0, "otp_validity_seconds": 115.0}
        mock_bot.user_data = {"bank_name": "بانک ملت"}
        mock_owner = MockOwner()

        ctrl = BankSelectionSessionController(mock_bot, mock_owner)
        ctrl.observe = MagicMock(return_value=("STEP_3_BANK_SELECT", {"is_bank_page": True, "applicant_nid": "1234567890"}))
        ctrl._preferences = MagicMock(return_value={"priority_banks": [{"name": "بانک ملت", "allow_reserve": False}]})

        # Mock auto_final_submit = False in DB config
        with patch("database.DBHandler.get_config", return_value={"auto_final_submit": False}), \
             patch("database.DBHandler.get_runtime", return_value={}), \
             patch("core.step3_bank_page.Step3BankPage.read_live_bank_options", return_value=[
                 BankOptionInfo(value="12", text="بانک ملت [دارای ظرفیت ثبت نام]", clean_title="ملت", has_active_quota=True)
             ]), \
             patch("core.step3_bank_page.Step3BankPage.click_submit_btn") as mock_submit:
            res = ctrl.evaluate_and_execute_bank_logic(mock_owner.page)
            # Must NOT click submit button
            mock_submit.assert_not_called()
            # Must return cycle_otp for continuing monitoring
            self.assertEqual(res, "cycle_otp")

    def test_reserve_bank_requires_both_allow_reserve_and_auto_submit(self):
        """Piece 4: Reserve bank must only submit if BOTH allow_reserve=True and auto_final_submit=True."""
        from bank_session_controller import BankSelectionSessionController
        mock_bot = MagicMock()
        mock_bot.nid = "1234567890"
        mock_bot._otp_entry_time = time.time()
        mock_bot.consume_recovery_flag.return_value = False
        mock_bot.settings = {"bank_refresh_interval": 5.0, "otp_validity_seconds": 115.0}
        mock_bot.user_data = {"bank_name": "بانک تجارت"}
        mock_owner = MockOwner()

        ctrl = BankSelectionSessionController(mock_bot, mock_owner)
        ctrl.observe = MagicMock(return_value=("STEP_3_BANK_SELECT", {"is_bank_page": True, "applicant_nid": "1234567890"}))

        # Case 1: allow_reserve=False, auto_final_submit=True -> Must NOT submit
        ctrl._preferences = MagicMock(return_value={"priority_banks": [{"name": "بانک تجارت", "allow_reserve": False}]})
        with patch("database.DBHandler.get_config", return_value={"auto_final_submit": True}), \
             patch("database.DBHandler.get_runtime", return_value={}), \
             patch("core.step3_bank_page.Step3BankPage.read_live_bank_options", return_value=[
                 BankOptionInfo(value="18", text="بانک تجارت [در انتظار تخصیص ظرفیت جدید (ذخیره)]", clean_title="تجارت", is_reserve=True)
             ]), \
             patch("core.step3_bank_page.Step3BankPage.click_submit_btn") as mock_submit:
            res = ctrl.evaluate_and_execute_bank_logic(mock_owner.page)
            mock_submit.assert_not_called()
            self.assertEqual(res, "cycle_otp")

        # Case 2: allow_reserve=True, auto_final_submit=True -> MUST submit!
        ctrl._preferences = MagicMock(return_value={"priority_banks": [{"name": "بانک تجارت", "allow_reserve": True}]})
        with patch("database.DBHandler.get_config", return_value={"auto_final_submit": True}), \
             patch("database.DBHandler.get_runtime", return_value={}), \
             patch("core.step3_bank_page.Step3BankPage.read_live_bank_options", return_value=[
                 BankOptionInfo(value="18", text="بانک تجارت [در انتظار تخصیص ظرفیت جدید (ذخیره)]", clean_title="تجارت", is_reserve=True)
             ]), \
             patch("core.step3_bank_page.Step3BankPage.select_bank_option", return_value=True), \
             patch("core.step3_bank_page.Step3BankPage.click_submit_btn", return_value=True) as mock_submit, \
             patch("core.step3_bank_page.Step3BankPage.extract_tracking_or_error", return_value={"is_success": True, "tracking_code": "987654321"}):
            res = ctrl.evaluate_and_execute_bank_logic(mock_owner.page)
            mock_submit.assert_called_once()
            self.assertEqual(res, "confirmed")

    def test_warm_tab_handover_on_expired_otp(self):
        """Piece 6: When OTP is expired, execute warm tab handover to open new tab and close old."""
        from bank_session_controller import BankSelectionSessionController
        mock_bot = MagicMock()
        mock_bot.nid = "1234567890"
        mock_bot._otp_entry_time = time.time() - 130.0  # Expired
        mock_bot.consume_recovery_flag.return_value = False
        mock_bot.settings = {"bank_refresh_interval": 5.0, "otp_validity_seconds": 115.0}
        mock_owner = MockOwner()
        new_mock_page = MagicMock()
        mock_owner.context.new_page.return_value = new_mock_page

        ctrl = BankSelectionSessionController(mock_bot, mock_owner)
        with patch("browser_actions.BrowserActions.fast_fill"), \
             patch("browser_actions.BrowserActions.find_captcha_input"), \
             patch("bot_select_captcha.solve_captcha_step"):
            ok = ctrl.perform_warm_tab_handover()
            self.assertTrue(ok)
            # Must have called context.new_page()
            mock_owner.context.new_page.assert_called_once()
            # Must point to new page
            self.assertEqual(mock_owner.page, new_mock_page)


if __name__ == "__main__":
    unittest.main()
