"""The CBI delete page reports success via a JS alert, not page text."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bot_core import BotCore
from bot_delete import DeleteRequestBot

SUCCESS = "اطلاعات درخواست تسهیلات قرض الحسنه ازدواج شما با موفقیت حذف شد"


class DeleteSuccessDialogTests(unittest.TestCase):
    def _bot(self):
        bot = DeleteRequestBot.__new__(DeleteRequestBot)
        bot._delete_dialog_msg = ""
        bot.settings = {}
        bot.log = MagicMock()
        bot._first_visible_locator = MagicMock(return_value=None)
        return bot

    def _page(self, body=""):
        page = MagicMock()
        page.inner_text.return_value = body
        page.is_closed.return_value = False
        return page

    def test_classifier_does_not_call_success_an_error(self):
        bot = BotCore.__new__(BotCore)
        self.assertEqual(bot._classify_dialog(SUCCESS), "request_deleted")

    def test_success_alert_is_seen_by_check_result(self):
        bot = self._bot()
        page = self._page("")
        self.assertEqual(bot._check_result(page), (None, ""))
        bot._handle_dialog(SimpleNamespace(message=SUCCESS, accept=lambda: None))
        ok, msg = bot._check_result(page)
        self.assertTrue(ok)
        self.assertEqual(msg, SUCCESS)

    def test_otp_stage_returns_on_alert_even_without_otp_input(self):
        bot = self._bot()
        bot._otp_entry_time = 1.0
        bot._delete_dialog_msg = SUCCESS
        stop = MagicMock()
        stop.is_set.return_value = False
        self.assertTrue(bot._run_stage2_otp(self._page(""), stop))

    def test_stage2_fills_otp_and_captcha_but_never_clicks_delete(self):
        bot = self._bot()
        bot._otp_entry_time = 1.0
        bot.settings = {"delete_otp_wait_sec": 1, "delete_manual_wait_sec": 0}
        bot.set_runtime_state = MagicMock()
        bot._fetch_otp = MagicMock(return_value="12345")
        otp = MagicMock()
        otp.input_value.return_value = "12345"
        captcha_inp, captcha_img = MagicMock(), MagicMock()
        captcha_inp.input_value.return_value = "AB12C"
        bot._first_visible_locator = MagicMock(side_effect=lambda page, sels: (
            otp if "ConfCode" in sels[0] else captcha_inp if "Captcha2" in sels[0] else captcha_img))
        bot.solve_captcha_with_model = MagicMock(return_value=True)
        stop = MagicMock()
        stop.is_set.return_value = False
        with patch("bot_delete.sleep_with_stop", return_value=False):
            self.assertFalse(bot._run_stage2_otp(self._page(""), stop))  # times out waiting for the user
        bot.solve_captcha_with_model.assert_called_once()
        args, kwargs = bot.solve_captcha_with_model.call_args
        self.assertIs(kwargs["submit"], False)
        self.assertIsNone(args[2])
        self.assertTrue(any("خودتان بزنید" in str(c.args[0]) for c in bot.log.call_args_list))


if __name__ == "__main__":
    unittest.main()
