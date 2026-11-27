"""Instant SMS wake-up, background remote fetch, and the central timing settings."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import otp_signal
from core import timings


class OtpSignalTests(unittest.TestCase):
    def test_waiter_wakes_as_soon_as_code_is_written(self):
        nid = "0011223344"
        woke = {}

        def waiter():
            started = time.perf_counter()
            woke["fired"] = otp_signal.wait(nid, 5.0)
            woke["ms"] = (time.perf_counter() - started) * 1000

        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.05)
        otp_signal.notify(nid)
        thread.join(2)
        self.assertTrue(woke["fired"])
        self.assertLess(woke["ms"], 500)  # not the 5s timeout

    def test_signal_is_per_applicant(self):
        otp_signal.notify("0000000001")
        self.assertFalse(otp_signal.wait("0000000002", 0.01))
        self.assertTrue(otp_signal.wait("0000000001", 0.01))

    def test_set_otp_notifies(self):
        from database import DBHandler
        with patch.object(DBHandler, "_load_applicant_data", return_value={}), \
             patch.object(DBHandler, "_connect"), \
             patch("otp_signal.notify") as notify:
            self.assertTrue(DBHandler.set_otp("0099887766", "123456"))
        notify.assert_called_once_with("0099887766")


class SmsWaitLoopTests(unittest.TestCase):
    def test_wait_for_sms_returns_early_on_signal(self):
        from bot_select_otp import _wait_for_sms
        bot = SimpleNamespace(nid="0055667788", settings={"otp_idle_check_ms": 1000})
        stop = threading.Event()
        threading.Timer(0.05, otp_signal.notify, args=(bot.nid,)).start()
        started = time.perf_counter()
        stopped = _wait_for_sms(bot, stop, lambda event, seconds: event.is_set())
        self.assertFalse(stopped)
        self.assertLess(time.perf_counter() - started, 0.5)

    def test_fresh_db_read_skips_network_while_fetcher_runs(self):
        import bot_select_otp
        from otp_transport import RemoteOtpFetcher
        bot = SimpleNamespace(nid="0055667789", settings={}, get_otp_code=None)
        bot._otp_fetcher = RemoteOtpFetcher(bot)
        with patch("bot_select_otp.DBHandler.get_otp_record", return_value=None):
            self.assertEqual(bot_select_otp.get_otp_from_db_fresh(bot), (None, None))

    def test_fetcher_not_started_without_remote_source(self):
        from otp_transport import RemoteOtpFetcher, remote_fetcher_running
        bot = SimpleNamespace(nid="1", settings={}, local_api_base="http://127.0.0.1:8000",
                              api_base_url="http://127.0.0.1:8000")
        with patch("relay_transport.config", return_value=None):
            fetcher = RemoteOtpFetcher(bot).start()
        self.assertFalse(remote_fetcher_running(bot))
        fetcher.stop()


class TimingSettingsTests(unittest.TestCase):
    def test_defaults_and_bounds(self):
        self.assertEqual(timings.timing({}, "page3_loop_ms"), 80)
        self.assertEqual(timings.timing({"page3_loop_ms": 5}, "page3_loop_ms"), 80)  # below min -> default
        self.assertEqual(timings.timing({"page3_loop_ms": 300}, "page3_loop_ms"), 300)
        self.assertAlmostEqual(timings.seconds({"otp_idle_check_ms": 250}, "otp_idle_check_ms"), 0.25)

    def test_legacy_alias_is_read(self):
        self.assertEqual(timings.timing({"bank_capacity_recheck_seconds": 45}, "bank_capacity_check_interval"), 45)

    def test_validate_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            timings.validate({"final_submit_poll_ms": 0})
        clean = timings.validate({"bank_list_refresh_seconds": 7})
        self.assertEqual(clean["bank_refresh_interval"], 7)

    def test_old_hidden_five_second_override_is_migrated_once(self):
        cfg = timings.normalize({"bank_list_refresh_seconds": 5.0})
        self.assertEqual(cfg["bank_list_refresh_seconds"], 3.5)
        cfg["bank_list_refresh_seconds"] = 5.0  # user explicitly picks 5 afterwards
        self.assertEqual(timings.normalize(cfg)["bank_list_refresh_seconds"], 5.0)

    def test_schema_covers_every_knob(self):
        keys = [item["key"] for group in timings.schema({}) for item in group["items"]]
        self.assertEqual(sorted(keys), sorted(timings.TIMINGS))

    def test_typing_presets_are_valid_and_reach_live_snapshot(self):
        for _, _, values in timings.PRESETS["typing"]:
            self.assertEqual(timings.validate(values), values)
        timings.normalize({"captcha_type_char_ms": 150})
        self.assertEqual(timings.live()["captcha_type_char_ms"], 150)
        self.assertTrue(0.09 <= timings.jittered_seconds("captcha_type_char_ms") <= 0.21)
        timings.normalize({"captcha_think_ms": 0})
        self.assertEqual(timings.jittered_seconds("captcha_think_ms"), 0.0)

    def test_controller_uses_configured_submit_timeout(self):
        from core.timings import timing
        self.assertEqual(timing({"final_submit_response_timeout_seconds": 25}, "final_submit_response_timeout_seconds"), 25)


if __name__ == "__main__":
    unittest.main()
