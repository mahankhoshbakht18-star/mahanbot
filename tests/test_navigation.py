import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import browser_launcher
from bot_core import BotCore
from bot_select import BankSelectionBot
from network_diagnostics import NavigationEvidence, SiteRejectedError, public_url, rejection_info


class NavigationTests(unittest.TestCase):
    def test_queue_preserves_rejection_as_failure(self):
        import time
        from core.job_queue import JobQueue
        from database import DBHandler
        queue = JobQueue(max_concurrency=1)
        with patch.object(queue, "_run_job_isolated"), patch.object(DBHandler, "get_runtime", return_value={"status": "ERROR", "current_step": "SITE_REJECTED", "last_error": "site_rejected"}), patch.object(DBHandler, "update_runtime") as update:
            job = queue.enqueue("select", "0000000000", {})
            queue.start()
            deadline = time.monotonic() + 3
            while not job.finished_at and time.monotonic() < deadline:
                time.sleep(0.01)
            queue.stop()
        self.assertEqual(job.status, "failed")
        self.assertEqual(update.call_args.kwargs["current_step"], "SITE_REJECTED")

    def test_watchdog_never_reloads_rejection_page(self):
        bot = BotCore.__new__(BotCore)
        page = Mock()
        page.inner_text.return_value = "The requested URL was rejected. Your support ID is: 123"
        with self.assertRaises(SiteRejectedError):
            bot.watchdog_check(page)
        page.reload.assert_not_called()

    def test_firewall_solver_never_uses_model(self):
        bot = BotCore.__new__(BotCore)
        bot.captcha_service = Mock()
        page = Mock()
        page.inner_text.return_value = "Enter CAPTCHA"
        self.assertFalse(bot.solve_firewall(page))
        bot.captcha_service.solve_element.assert_not_called()

    def test_select_run_stops_on_rejection_without_restart(self):
        import threading
        bot = BankSelectionBot.__new__(BankSelectionBot)
        bot.nid = "0000000000"
        bot.settings = {}
        bot.retry_limit = 5
        bot.reset_attempt_state = Mock()
        bot.set_runtime_state = Mock()
        bot.log = Mock()
        bot.navigation_evidence = Mock()
        page = Mock()
        page.inner_text.return_value = "The requested URL was rejected. Your support ID is: 123"
        bot.setup_browser = Mock(return_value=(Mock(), Mock(), Mock(), page))
        bot._otp_wait_lock = False
        bot._otp_guard_until = 0
        bot._safe_goto_locked = False
        bot._otp_input_visible = Mock(return_value=False)
        with patch("bot_select.DBHandler.update_status"), patch("bot_select.close_browser"):
            bot.run(threading.Event())
        bot.setup_browser.assert_called_once()
        page._original_goto.assert_called_once()
        self.assertEqual(bot.set_runtime_state.call_args.args[:2], ("ERROR", "SITE_REJECTED"))

    def test_support_id_with_optional_angle_brackets(self):
        for suffix in ("7880755048360349455", "<7880755048360349455>"):
            info = rejection_info("The requested URL was rejected. Your support ID is: " + suffix)
            self.assertEqual(info["support_id"], "7880755048360349455")
        self.assertIsNone(rejection_info("Enter CAPTCHA; support ID: 123"))

    def test_rejected_navigation_is_not_retried(self):
        page = Mock()
        page.inner_text.return_value = "The requested URL was rejected. Your support ID is: 123"
        with self.assertRaises(SiteRejectedError):
            browser_launcher.safe_goto(page, "https://ve.cbi.ir/SelectBnkShb.aspx")
        page._original_goto.assert_called_once()

    def test_diagnostics_strip_secrets_and_are_bounded(self):
        url = "https://user:password@ve.cbi.ir/SelectBnkShb.aspx?token=secret#otp"
        self.assertEqual(public_url(url), "https://ve.cbi.ir/SelectBnkShb.aspx")
        evidence = NavigationEvidence(Mock())
        response = Mock(url=url, status=403)
        response.request.method = "GET"
        response.request.redirected_from = None
        response.request.is_navigation_request.return_value = True
        for _ in range(100):
            evidence.response(response)
        self.assertEqual(len(evidence.events), 80)
        self.assertNotIn("secret", str(evidence.snapshot(Mock(url=url))))

    def test_persistent_profile_uses_existing_root(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(browser_launcher, "PROFILE_ROOT", Path(root)):
                self.assertEqual(browser_launcher._resolve_user_data_dir("applicant"), str(Path(root) / "applicant"))
                with self.assertRaises(browser_launcher.BrowserLaunchError):
                    browser_launcher._resolve_user_data_dir(str(Path(root).parent / "outside"))


if __name__ == "__main__":
    unittest.main()
