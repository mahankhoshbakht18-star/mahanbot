import unittest
from unittest.mock import MagicMock, patch
import time
import json
import sqlite3

from bot_select import BankSelectionBot, JobStateContext
from bank_session_controller import BankSelectionSessionController
from bank_page_manager import BankPageState as State
from database import DBHandler


class DummyOwner:
    def __init__(self):
        self.page = MagicMock()
        self.page.is_closed = MagicMock(return_value=False)
        self.page.url = "https://ve.cbi.ir/SelectBnkShb.aspx"
        self.page.content = MagicMock(return_value="<html><body><span id='ctl00_ContentPlaceHolder1_labIDNo'>9999999999</span></body></html>")
        self.cancellation_token = MagicMock()
        self.cancellation_token.is_set = MagicMock(return_value=False)
        self.commands = MagicMock()
        self.commands.empty = MagicMock(return_value=True)
        self.paused = False
        self.bank_page_active = False
        self.session_id = "test_session"
        self.last_valid_checkpoint = None
        self.last_successful_transition = 0
        self.current_state = None
        self.operation_lock = MagicMock()
        self.operation_lock.__enter__ = MagicMock(return_value=None)
        self.operation_lock.__exit__ = MagicMock(return_value=None)

    def assert_owner(self):
        pass


class TestTargetedBankSelectionRepair(unittest.TestCase):
    def setUp(self):
        self.bot = BankSelectionBot("9999999999", {"navigation_timeout_ms": 1000, "bank_refresh_interval": 5.0})
        self.bot.live_owner = DummyOwner()

    def tearDown(self):
        # Clean up test NID runtime
        try:
            DBHandler.clear_checkpoint("9999999999")
        except Exception:
            pass

    def test_01_saeed_dadkarian_db_clean_state(self):
        """Verify Saeed Dadkarian (6450700394) is in a clean ready state without stale locks."""
        conn = sqlite3.connect(r"F:\mahanvip\cbi_ultimate.db")
        cur = conn.cursor()
        
        # Check applicants table
        cur.execute("SELECT status, otp_code, otp_created_at, data FROM applicants WHERE national_id = '6450700394';")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "آماده")
        self.assertIsNone(row[1])
        self.assertIsNone(row[2])
        data = json.loads(row[3])
        self.assertEqual(data.get("captcha_attempts"), [])

        # Check checkpoints & automation_runtime are clean
        cur.execute("SELECT COUNT(*) FROM checkpoints WHERE nid = '6450700394';")
        self.assertEqual(cur.fetchone()[0], 0)

        cur.execute("SELECT COUNT(*) FROM automation_runtime WHERE nid = '6450700394';")
        self.assertEqual(cur.fetchone()[0], 0)

        # Check no pending otp_requests
        cur.execute("SELECT COUNT(*) FROM otp_requests WHERE applicant_id = '6450700394' AND verification_status = 'pending';")
        self.assertEqual(cur.fetchone()[0], 0)
        conn.close()

    def test_02_job_state_context_120s_otp_expiry(self):
        """Verify OTP 120s validity is strictly tracked and independent of browser session."""
        ctx = JobStateContext(applicant_national_id="6450700394", otp_ttl_seconds=120)
        self.assertFalse(ctx.is_otp_valid())

        # Received 10s ago -> valid
        ctx.cached_otp_code = "123456"
        ctx.otp_received_at = time.time() - 10.0
        self.assertTrue(ctx.is_otp_valid())

        # Received 125s ago -> expired
        ctx.otp_received_at = time.time() - 125.0
        self.assertFalse(ctx.is_otp_valid())

        # Invalidate explicitly
        ctx.cached_otp_code = "654321"
        ctx.otp_received_at = time.time()
        self.assertTrue(ctx.is_otp_valid())
        ctx.invalidate_otp()
        self.assertFalse(ctx.is_otp_valid())
        self.assertIsNone(ctx.cached_otp_code)

    def test_03_safe_goto_guarded_with_force_and_cleared_locks(self):
        """Verify _safe_goto_guarded allows recovery navigation to Step 1 when force=True or locks cleared."""
        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://ve.cbi.ir/SelectBnkShb.aspx"

        # Ensure bank_page_active is False
        self.bot.live_owner.bank_page_active = False
        self.bot._otp_input_visible = MagicMock(return_value=True)
        self.bot.detect_actual_page_stage = MagicMock(return_value="STEP_2_OTP")

        # Case A: When actively waiting for OTP lock, navigation is suppressed
        self.bot._otp_wait_lock = True
        with patch("bot_select.safe_goto") as mock_goto:
            res = self.bot._safe_goto_guarded(page, "https://ve.cbi.ir/SelectBnkShb.aspx")
            self.assertFalse(res)
            mock_goto.assert_not_called()

        # Case B: When force=True (used during authentication recovery), navigation proceeds!
        with patch("bot_select.safe_goto") as mock_goto:
            res = self.bot._safe_goto_guarded(page, "https://ve.cbi.ir/SelectBnkShb.aspx", force=True)
            self.assertTrue(res)
            mock_goto.assert_called_once()

        # Case C: When clear_otp_locks() is called, navigation is allowed
        self.bot.clear_otp_locks()
        with patch("bot_select.safe_goto") as mock_goto:
            res = self.bot._safe_goto_guarded(page, "https://ve.cbi.ir/SelectBnkShb.aspx")
            self.assertTrue(res)
            mock_goto.assert_called_once()

    def test_04_checkpoint_never_assumes_page3_without_live_dom(self):
        """Verify stale checkpoint never claims STEP_3_BANK_SELECT without live DOM verification."""
        page = MagicMock()
        page.is_closed.return_value = False

        # Mock DB checkpoint returning STEP_3_BANK_SELECT from hours ago
        stale_cp = {
            "last_verified_step": "STEP_3_BANK_SELECT",
            "saved_at": time.time() - 3600.0,
            "priority_banks": ["صادرات"],
        }
        with patch.object(DBHandler, "get_checkpoint", return_value=stale_cp):
            with patch.object(DBHandler, "has_final_submission", return_value=""):
                # Page is actually at Step 1
                self.bot.detect_actual_page_stage = MagicMock(return_value="STEP_1_ENTRY_FORM")
                res = self.bot._restore_from_checkpoint_if_available(page)
                # Must return STEP_1_ENTRY_FORM, NEVER STEP_3_BANK_SELECT!
                self.assertEqual(res, "STEP_1_ENTRY_FORM")

    def test_05_page3_monitoring_reload_and_redetect_flow(self):
        """Verify Page 3 controller monitors and calls reload_and_redetect when bank not found."""
        owner = DummyOwner()
        owner.bank_page_active = True
        controller = BankSelectionSessionController(self.bot, owner)
        controller._submitting = False
        controller._requests = set()
        controller._prepared = True
        controller.state = State.PREFERRED_BANK_NOT_FOUND
        controller._next_bank_poll = time.monotonic() - 1.0  # Ready to poll!

        # Mock observe returning STEP_3_BANK_SELECT data
        with patch.object(controller, "observe", return_value=("STEP_3_BANK_SELECT", {"is_bank_page": True})):
            with patch.object(controller, "reload_and_redetect", return_value="cycle_otp") as mock_reload:
                res = controller.tick()
                self.assertEqual(res, "cycle_otp")
                mock_reload.assert_called_once()

    def test_06_browser_launcher_stability_flags_for_crashed_bubble(self):
        """Verify browser launcher includes crash restore suppression flags."""
        import browser_launcher
        import inspect
        src = inspect.getsource(browser_launcher.launch_browser)
        self.assertIn("--disable-session-crashed-bubble", src)
        self.assertIn("--hide-crash-restore-bubble", src)

    def test_07_independent_applicant_with_tracking_code_prevents_duplicate(self):
        """Verify independent applicant with existing tracking code returns already_submitted."""
        page = MagicMock()
        page.is_closed.return_value = False

        independent_bot = BankSelectionBot("0011223344", {})
        with patch.object(DBHandler, "has_final_submission", return_value="5796625739"):
            res = independent_bot._restore_from_checkpoint_if_available(page)
            self.assertEqual(res, "DONE")


if __name__ == "__main__":
    unittest.main()
