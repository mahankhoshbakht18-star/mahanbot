import time
from threading import Event
from unittest.mock import Mock, patch

import unittest

from bot_select_otp import _run_otp_step


def check_manual_captcha_handoff(outcome, server_rejected):
    page = Mock(url="https://example.test/shared")
    page.is_closed.return_value = False
    bank = page.locator.return_value
    bank.count.return_value = 0
    bank.first.is_visible.return_value = True
    page.frames = []
    bot = Mock(settings={"otp_captcha_max_retries": 1}, nid="test")
    issued_at = time.time()
    bot._otp_entry_time = issued_at
    clock = Mock(return_value=issued_at)
    bot._recovery_flags = {}
    bot._consumed_otp_digests = set()
    bot._firewall_gate.return_value = False
    stop = Event()
    otp = Mock()
    otp.input_value.return_value = "123456"
    flow = Mock(http_status=200)
    flow.pump_commands.return_value = False

    def manual_action(token, seconds):
        assert token is stop
        if outcome == "bank_ready":
            bank.count.return_value = 1
        elif outcome == "otp_expired":
            clock.return_value = issued_at + 121
        elif outcome == "target_closed":
            page.is_closed.return_value = True
        else:
            stop.set()
        return stop.is_set()

    with patch("bot_select_otp.time.time", clock), \
         patch("bot_select_otp.DBHandler") as db, \
         patch("core.loan_workflow.workflow_for", return_value=flow), \
         patch("core.otp_queue.OTPQueueManager.mark_verified"), \
         patch("bot_select_otp._entry_form_visible_now", return_value=False), \
         patch("bot_select_otp._locate_otp_input", return_value=(otp, page)), \
         patch("otp_form_detector.detect_otp_form", return_value=Mock(fields={})), \
         patch("bot_select_otp._detect_site_otp_ttl", return_value=120), \
         patch("bot_select_otp.get_otp_from_db_fresh", return_value=("123456", "test")), \
         patch("bot_select_otp.ensure_otp_captcha_prefilled", return_value=True), \
         patch("bot_select_otp._locate_otp_captcha_controls", return_value=(None, Mock(), None)), \
         patch("bot_select_otp._post_submit_watch", return_value=("retry", None, None)) as watch, \
         patch("bot_select_otp._solve_otp_captcha_fast", return_value=server_rejected) as solve:
        db.get_otp_record.return_value = {}
        assert _run_otp_step(bot, page, stop, manual_action) == outcome
        solve.assert_called_once_with(bot, page)
        names = [call.args[0] for call in bot.emit_event.call_args_list]
        assert "MANUAL_WAITING" in names
        assert ("CAPTCHA_REJECTED_BY_SERVER" in names) == server_rejected
        assert watch.call_count == int(server_rejected)
        if outcome == "bank_ready":
            assert names[-2:] == ["MANUAL_ACCEPTED", "PAGE3_READY"]



class ManualOtpRegressionTests(unittest.TestCase):
    def test_outcomes(self):
        for server_rejected in (False, True):
            for outcome in ("bank_ready", "otp_expired", "target_closed", None):
                with self.subTest(outcome=outcome, server_rejected=server_rejected):
                    check_manual_captcha_handoff(outcome, server_rejected)
