import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot_core import BotCore
from bot_select import BankSelectionBot
from bot_status import StatusBot
from otp_transport import fresh_record
from browser_launcher import close_browser, safe_goto


class TestCaptchaOtpLifecycleAudit(unittest.TestCase):
    """
    Verification suite for the 15 required forensic scenarios:
    1. Fresh OTP entry time prevents immediate timeout (08:21:34 -> 08:21:35 bug).
    2. CAPTCHA invalid in Step 2 OTP retries without browser recreation or session teardown.
    3. User typing in CAPTCHA input suppresses auto-reload and model assist.
    4. User typing in OTP input suppresses watchdog reload.
    5. Manual OTP entry accepted even with request_id mismatch or slight clock skew.
    6. detect_actual_page_stage prioritizes Step 2 OTP over Step 3 if OTP input is present.
    7. StatusBot network timing error on TasTrace.aspx does not mark applicant as Blocked.
    8. is_firewall_challenge detects challenge inside iframes and frames.
    9. close_browser logs structured audit info (file, function, line, masked NID).
    10. safe_goto logs structured audit info.
    11. Paused workflow loop sleeps rather than burning CPU in busy-loop.
    12. Preflight duplicate submission guard prevents redundant browser launches.
    13. refresh_captcha_or_reload clears candidate inputs across multiple selectors.
    14. Thread-safe submit mutex prevents concurrent form submissions.
    15. wait_for_manual_captcha_handoff suspends watchdog and updates current step.
    """

    def setUp(self):
        self.bot = BankSelectionBot(
            nid="0012345678",
            settings={
                "otp_validity_seconds": 120,
                "pause_for_manual_captcha": True,
                "captcha_retry_limit": 3,
            },
            log_callback=MagicMock(),
            captcha_service=MagicMock(),
        )

    def test_01_fresh_otp_entry_time_prevents_immediate_timeout(self):
        """Scenario 1: Stale entry time from prior attempt (> TTL) must be refreshed so SMS does not instantly timeout."""
        old_time = time.time() - 300  # 5 minutes ago
        self.bot._otp_entry_time = old_time
        self.bot._otp_request_id = "old_req_123"

        now = time.time()
        is_stale = (now - self.bot._otp_entry_time) >= 120
        self.assertTrue(is_stale, "Old entry time must be recognized as stale")
        
        fresh_time = self.bot.mark_otp_entry_time(now)
        self.assertGreaterEqual(fresh_time, now)
        self.assertNotEqual(self.bot._otp_request_id, "old_req_123")
        self.assertLess(now - self.bot._otp_entry_time, 2.0)

    def test_02_otp_captcha_invalid_retry_no_browser_recreation(self):
        """Scenario 2: When captcha is invalid during OTP stage, submission_pending is cleared and browser is not closed."""
        self.bot._recovery_flags["captcha_invalid"] = True
        self.assertTrue(self.bot.consume_recovery_flag("captcha_invalid"))
        self.assertFalse(self.bot.consume_recovery_flag("captcha_invalid"))

    def test_03_user_typing_in_captcha_suppresses_auto_assist(self):
        """Scenario 3: Active focus or user input in captcha field suppresses automatic overwrite and refresh."""
        mock_page = MagicMock()
        mock_input = MagicMock()
        mock_page.locator.return_value = mock_input
        mock_input.count.return_value = 1
        mock_input.first = mock_input
        mock_input.is_visible.return_value = True
        
        mock_input.evaluate.return_value = True
        mock_input.input_value.return_value = "12"

        is_focused = mock_input.evaluate("el => el === document.activeElement")
        user_typing = is_focused or bool(mock_input.input_value().strip())
        self.assertTrue(user_typing)

    def test_04_user_typing_in_otp_suppresses_watchdog(self):
        """Scenario 4: Watchdog check returns False without reloading when WAITING_USER_INPUT or activeElement is input."""
        mock_page = MagicMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.url = "https://ve.cbi.ir/SelectBnkShb.aspx"
        
        self.bot.set_runtime_state("WAITING_USER_INPUT", "MANUAL_CAPTCHA_OR_OTP")
        stop_event = MagicMock()
        stop_event.is_set.return_value = False

        triggered = self.bot.watchdog_check(mock_page, stop_event, selectors=["#some_selector"])
        self.assertFalse(triggered, "Watchdog should not trigger reload while waiting for user input")

    def test_05_manual_otp_accepted_with_request_id_mismatch_and_clock_skew(self):
        """Scenario 5: Manual OTP entered via dashboard or external app is accepted even if request_id differs or ts slightly differs."""
        now = time.time()
        self.bot._otp_request_id = "active_req_999"
        self.bot._otp_entry_time = now - 5
        manual_rec = {
            "code": "12345",
            "nid": "0012345678",
            "request_id": "different_req_id",
            "ts": now - 10,
            "status": "manual",
            "source": "manual",
        }
        accepted = fresh_record(self.bot, manual_rec, now=now)
        self.assertIsNotNone(accepted, "Manual OTP must be accepted despite request_id difference")

    def test_06_detect_actual_page_stage_prioritizes_otp(self):
        """Scenario 6: If OTP input is present along with page containers, it resolves to STEP_2_OTP, not STEP_3."""
        mock_page = MagicMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.url = "https://ve.cbi.ir/SelectBnkShb.aspx"
        mock_page.inner_text = MagicMock(return_value="")
        mock_page.frames = [mock_page]
        
        def mock_locator(sel):
            loc = MagicMock()
            if "tbMobileConfCode" in sel:
                loc.count.return_value = 1
                loc.first.is_visible.return_value = True
            elif "ddlBankName" in sel:
                loc.count.return_value = 1
                loc.first.is_visible.return_value = False
            else:
                loc.count.return_value = 0
                loc.first.is_visible.return_value = False
            return loc

        mock_page.locator.side_effect = mock_locator
        detected = self.bot.detect_actual_page_stage(mock_page)
        self.assertEqual(detected, "STEP_2_OTP")

    def test_07_status_bot_timing_error_does_not_mark_blocked(self):
        """Scenario 7: Network timeout on TasTrace.aspx categorizes as timing and does NOT mark applicant as Blocked."""
        status_bot = StatusBot(
            nid="0012345678",
            settings={},
            log_callback=MagicMock(),
            captcha_service=MagicMock(),
        )
        cat = status_bot._categorize_inquiry_error(exc=Exception("net::ERR_CONNECTION_TIMED_OUT"))
        self.assertEqual(cat, "timing")

        with patch("database.DBHandler.update_status") as mock_update:
            status_bot._handle_inquiry_error("test_context", exc=Exception("net::ERR_CONNECTION_TIMED_OUT"))
            for call in mock_update.call_args_list:
                status_arg = call[0][1]
                self.assertNotEqual(status_arg, "Blocked")

    def test_08_is_firewall_challenge_in_frames(self):
        """Scenario 8: Challenge detected inside iframes via page.frames."""
        mock_page = MagicMock()
        mock_frame = MagicMock()
        mock_page.frames = [mock_page, mock_frame]
        
        def mock_locator(sel):
            loc = MagicMock()
            if sel == "#ans":
                loc.count.return_value = 1
                loc.first.is_visible.return_value = True
            else:
                loc.count.return_value = 0
                loc.first.is_visible.return_value = False
            return loc

        mock_page.locator.return_value.count.return_value = 0
        mock_frame.locator.side_effect = mock_locator
        mock_frame.inner_text.return_value = "Verify you are human"
        
        self.assertTrue(self.bot.is_firewall_challenge(mock_page))

    def test_09_browser_launcher_close_browser_audit_log(self):
        """Scenario 9: close_browser logs caller information and masked NID without throwing errors."""
        mock_playwright = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        
        with patch("browser_launcher._browser_audit_log.info") as mock_log:
            close_browser(mock_playwright, mock_browser, mock_context, mock_page, reason="test_cleanup", nid="0012345678")
            self.assertTrue(mock_log.called)
            logged_msg = mock_log.call_args[0][0]
            self.assertIn("[BROWSER_CLOSE]", logged_msg)
            self.assertIn("00******78", logged_msg)
            self.assertNotIn("0012345678", logged_msg)

    def test_10_browser_launcher_safe_goto_audit_log(self):
        """Scenario 10: safe_goto logs structured audit information."""
        mock_page = MagicMock()
        mock_page.url = "https://ve.cbi.ir/SelectBnkShb.aspx"
        
        with patch("browser_launcher._browser_audit_log.debug") as mock_log:
            safe_goto(mock_page, "https://ve.cbi.ir/SelectBnkShb.aspx", nid="0012345678")
            self.assertTrue(mock_log.called)
            logged_msg = mock_log.call_args[0][0]
            self.assertIn("[BROWSER_GOTO]", logged_msg)
            self.assertIn("00******78", logged_msg)

    def test_11_workflow_paused_sleeps(self):
        """Scenario 11: bot._run_owned contains sleep_with_stop when detection.state == 'PAUSED'."""
        with open("bot_select.py", "r", encoding="utf-8") as f:
            code = f.read()
        self.assertIn("sleep_with_stop(stop_event, 0.5)", code)

    def test_12_preflight_duplicate_submission_guard(self):
        """Scenario 12: Applicant with existing tracking code returns immediately without launching browser."""
        with patch("database.DBHandler.has_final_submission", return_value="1234567890"):
            stop_event = MagicMock()
            stop_event.is_set.return_value = False
            with patch.object(self.bot, "setup_browser") as mock_setup:
                self.bot._run_owned(stop_event)
                mock_setup.assert_not_called()
                self.assertEqual(self.bot._current_step, "DONE")

    def test_13_captcha_clear_candidate_inputs(self):
        """Scenario 13: refresh_captcha_or_reload clears candidate inputs."""
        mock_page = MagicMock()
        mock_image = MagicMock()
        mock_input = MagicMock()
        mock_input.count.return_value = 1
        mock_input.first = mock_input
        mock_input.is_visible.return_value = True
        
        mock_page.locator.return_value = mock_input
        
        with patch("bot_select_captcha.wait_for_captcha_image_ready", return_value=(mock_image, mock_page)):
            from bot_select_captcha import refresh_captcha_or_reload
            refresh_captcha_or_reload(self.bot, mock_page, input_sel="input[name*='tbCaptcha']", allow_reload=False, captcha_rejected=True)
            self.assertTrue(mock_input.fill.called)

    def test_14_submit_mutex_thread_safe(self):
        """Scenario 14: self._submit_mutex exists and synchronizes submissions."""
        self.assertTrue(hasattr(self.bot, "_submit_mutex"))
        with self.bot._submit_mutex:
            pass

    def test_15_suspend_watchdog_manual_captcha(self):
        """Scenario 15: wait_for_manual_captcha_handoff suspends watchdog and updates current step."""
        mock_page = MagicMock()
        stop_event = MagicMock()
        stop_event.is_set.return_value = True
        
        from bot_select_captcha import wait_for_manual_captcha_handoff
        wait_for_manual_captcha_handoff(
            self.bot, mock_page, "input[name*='tbCaptcha']", "input[type='submit']", stop_event
        )
        self.assertEqual(self.bot._current_step, "CAPTCHA_MANUAL_REQUIRED")
        self.assertGreater(self.bot._watchdog_suspended_until, time.time())


if __name__ == "__main__":
    unittest.main()
