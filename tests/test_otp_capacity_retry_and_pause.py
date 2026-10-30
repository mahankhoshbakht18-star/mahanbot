import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import threading
import time
from unittest.mock import MagicMock, patch

from live_sessions import LIVE_SESSIONS, BrowserSessionOwner
from bot_core import BotCore
import bot_select_otp


class TestOtpCapacityRetryAndPause(unittest.TestCase):
    def setUp(self):
        LIVE_SESSIONS.reset()

    def tearDown(self):
        LIVE_SESSIONS.reset()

    def test_live_session_enqueue_command(self):
        stop_event = threading.Event()
        owner = LIVE_SESSIONS.acquire("1234567890", stop_event)
        self.assertFalse(owner.paused)

        ok = LIVE_SESSIONS.enqueue_command("1234567890", "pause")
        self.assertTrue(ok)
        self.assertTrue(owner.paused)
        self.assertEqual(owner.commands.get_nowait(), "pause")

        ok2 = LIVE_SESSIONS.enqueue_command("1234567890", "resume")
        self.assertTrue(ok2)
        self.assertFalse(owner.paused)
        self.assertEqual(owner.commands.get_nowait(), "resume")

        # Non-existent session returns False without exception
        ok_nonexistent = LIVE_SESSIONS.enqueue_command("9999999999", "pause")
        self.assertFalse(ok_nonexistent)

    def test_classify_capacity_updating_dialog(self):
        bot = BotCore.__new__(BotCore)
        msg = "سامانه در حال بروزرسانی ظرفیت بانک ها می باشد و تا پایان عملیات بروزرسانی (تقریبا یک ساعت) امکان انتخاب بانک و شعبه پیشنهادی ندارید"
        cat = bot._classify_dialog(msg)
        self.assertEqual(cat, "system_capacity_updating")

    def test_dialog_handler_on_otp_stage_does_not_halt_capacity(self):
        bot = BotCore.__new__(BotCore)
        bot.nid = "1234567890"
        bot.settings = {}
        bot._dialog_handlers_attached = set()
        bot._recovery_flags = {}
        bot.current_step = "STEP_2_OTP"
        bot.log = MagicMock()
        bot.emit_event = MagicMock()
        bot.set_runtime_state = MagicMock()

        # Mock page where OTP input is visible
        mock_page = MagicMock()
        mock_loc = MagicMock()
        mock_loc.count.return_value = 1
        mock_loc.first.is_visible.return_value = True
        mock_page.locator.return_value = mock_loc

        # Attach handler
        bot.attach_dialog_handler(mock_page)

        # Retrieve handler registered on mock_page.on('dialog', ...)
        args, kwargs = mock_page.on.call_args
        self.assertEqual(args[0], "dialog")
        handler_fn = args[1]

        # Simulate alert from CBI
        mock_dialog = MagicMock()
        mock_dialog.type.return_value = "alert"
        mock_dialog.message.return_value = "سامانه در حال بروزرسانی ظرفیت بانک ها می باشد و تا پایان عملیات بروزرسانی (تقریبا یک ساعت) امکان انتخاب بانک و شعبه پیشنهادی ندارید"

        handler_fn(mock_dialog)

        # Dialog should be accepted/dismissed
        mock_dialog.accept.assert_called()

        # Should NOT have set bank_capacity_full recovery flag
        self.assertNotIn("bank_capacity_full", bot._recovery_flags)
        # Should have set last_dialog_action to retry
        self.assertEqual(bot._last_dialog_action, "otp_step_capacity_retry")
        # Should NOT have called set_runtime_state to BANK_CAPACITY_FULL
        bot.set_runtime_state.assert_not_called()

    def test_post_submit_watch_retries_on_capacity_update_alert(self):
        bot = MagicMock()
        bot.nid = "1234567890"
        bot._last_dialog_action = "otp_step_capacity_retry"
        bot._last_dialog_message = "سامانه در حال بروزرسانی ظرفیت بانک ها می باشد"
        bot._recovery_flags = {}

        mock_page = MagicMock()
        mock_page.inner_text.return_value = "فرم ورود کد تایید"

        stop_event = threading.Event()
        sleep_fn = MagicMock(return_value=False)

        with patch("bot_select_otp._bank_select_visible_now", return_value=False), \
             patch("bot_select_otp._entry_form_visible_now", return_value=False), \
             patch("bot_select_otp._detect_failure_type", return_value=None), \
             patch("bot_select_otp._refresh_captcha_image") as mock_refresh:

            status, new_code, _ = bot_select_otp._post_submit_watch(
                bot, mock_page, stop_event, sleep_fn, last_code="12345", wait_seconds=1.0
            )

            self.assertEqual(status, "retry")
            mock_refresh.assert_not_called()
            self.assertIsNone(bot._last_dialog_action)

    def test_post_submit_watch_detects_otp_expiry(self):
        bot = MagicMock()
        bot.nid = "1234567890"
        bot._last_dialog_action = "otp_expired"
        bot._recovery_flags = {}
        bot._consumed_otp_digests = set()

        mock_page = MagicMock()
        mock_page.inner_text.return_value = "کد تایید منقضی شده است"

        stop_event = threading.Event()
        sleep_fn = MagicMock(return_value=False)

        with patch("bot_select_otp._bank_select_visible_now", return_value=False), \
             patch("bot_select_otp._entry_form_visible_now", return_value=False), \
             patch("bot_select_otp._detect_failure_type", return_value="otp_expired"):

            status, new_code, _ = bot_select_otp._post_submit_watch(
                bot, mock_page, stop_event, sleep_fn, last_code="12345", wait_seconds=1.0
            )

            self.assertEqual(status, "otp_expired")
            self.assertTrue(bot._set_recovery_flag.called)


if __name__ == "__main__":
    unittest.main()
