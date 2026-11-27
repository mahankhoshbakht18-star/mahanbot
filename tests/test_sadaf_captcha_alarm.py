# -*- coding: utf-8 -*-
"""Captchas are solved automatically first; the alarm and manual entry come only after those tries fail."""
import queue
import threading
import time
import unittest
from unittest import mock

from modules.sadaf import runner as runner_module
from modules.sadaf.runner import FORM_AUTO_TRIES, LOGIN_AUTO_TRIES, Runner


def quiet_runner():
    bot = Runner()
    bot.cmds = queue.Queue()
    bot.said = []

    def say(message, status=None, **extra):
        bot.said.append((status, message))
        bot.state["message"] = message
        if status:
            bot.state["status"] = status
            if status != "needs_operator":
                bot.state["alarm"] = None
        bot.state.update(extra)

    bot._say = say
    return bot


def type_after_alarm(bot, code, kind):
    """Operator: wait for the alarm of this kind, then send the code through the panel route."""
    def operator():
        for _ in range(200):
            alarm = bot.state.get("alarm")
            if alarm and alarm["kind"] == kind:
                bot.alarms_seen.append(dict(alarm))
                bot.thread_alive = True
                bot.submit_code(code)
                return
            time.sleep(0.01)
    bot.alarms_seen = []
    thread = threading.Thread(target=operator, daemon=True)
    thread.start()
    return thread


class FormCaptchaAlarmTests(unittest.TestCase):
    def setUp(self):
        self.bot = quiet_runner()
        self.bot.thread = mock.Mock(is_alive=lambda: True)
        self.filled = []
        self.presses = 0
        self.wrong_until = FORM_AUTO_TRIES + 1   # every auto try and the first manual try are rejected

        def js(page, expression, arg=None):
            if "smsPanel" in expression:
                return {"open": True}
            if "termsChecked" in expression or "fillSms" in expression or "markConfirmRequest" in expression:
                return True
            if "formCaptcha()" in expression:
                return {"box": True, "src": "img", "max": 4}
            if "fillFormCaptcha" in expression:
                self.filled.append((arg, (self.bot.state.get("alarm") or {}).get("kind")))
                return True
            return None

        def page_says(page, label, before, secret="", wait=0):
            self.presses += 1
            if self.presses <= self.wrong_until:
                return ["کد امنیتی اشتباه است"]
            return ["درخواست شما با موفقیت ثبت گردید"]

        patches = {
            "_hold": lambda: None, "_js": js, "_wait_sms": lambda *a: "8510273",
            "_refill_after_postback": lambda *a, **k: "", "_click": lambda *a: True, "_snap": lambda *a: None,
            "_lines": lambda page: [], "_page_says": page_says, "_keep_tracking": lambda *a: None,
            "_form_captcha_bytes": lambda *a: b"png",
        }
        for name, fn in patches.items():
            setattr(self.bot, name, fn)

    def test_auto_tries_then_alarm_then_manual(self):
        operator = type_after_alarm(self.bot, "x7k2", "form")
        with mock.patch.object(runner_module, "_sadaf_code", return_value="ab12"), \
                mock.patch.object(runner_module, "_beep"):
            # The first manual code is rejected too, so the operator answers a second alarm.
            second = threading.Thread(target=lambda: (operator.join(), time.sleep(0.05),
                                                      type_after_alarm(self.bot, "q9w3", "form").join()), daemon=True)
            second.start()
            result = self.bot._finish_request(object(), {"id": 1}, "متقاضی ۱", [])
            second.join(2)
        self.assertEqual(result["status"], "success")
        auto = [code for code, alarm in self.filled if code == "ab12"]
        self.assertEqual(len(auto), FORM_AUTO_TRIES, "every automatic try comes before any alarm")
        self.assertTrue(all(alarm is None for code, alarm in self.filled[:FORM_AUTO_TRIES]))
        self.assertEqual([code for code, _ in self.filled[FORM_AUTO_TRIES:]], ["x7k2", "q9w3"])
        self.assertIsNone(self.bot.state.get("alarm"), "alarm clears once the operator answers")

    def test_no_alarm_when_auto_succeeds(self):
        self.wrong_until = 1
        with mock.patch.object(runner_module, "_sadaf_code", return_value="ab12"), \
                mock.patch.object(runner_module, "_beep") as beep:
            result = self.bot._finish_request(object(), {"id": 1}, "متقاضی ۱", [])
        self.assertEqual(result["status"], "success")
        beep.assert_not_called()
        self.assertFalse(any(status == "needs_operator" for status, _ in self.bot.said))

    def test_unreadable_image_asks_operator(self):
        self.wrong_until = 0
        operator = type_after_alarm(self.bot, "m4n5", "form")
        with mock.patch.object(runner_module, "_sadaf_code", return_value=""), \
                mock.patch.object(Runner, "_read_code", return_value=""), \
                mock.patch.object(runner_module, "_beep"):
            result = self.bot._finish_request(object(), {"id": 1}, "متقاضی ۱", [])
        operator.join(2)
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.filled[0][0], "m4n5")


class LoginCaptchaAlarmTests(unittest.TestCase):
    def test_login_tries_automatically_before_alarm(self):
        bot = quiet_runner()
        bot.thread = mock.Mock(is_alive=lambda: True)
        clicks = []
        bot._poll = lambda seconds: (None if bot.cmds.empty() else bot.cmds.get())
        bot._js = lambda page, expression, arg=None: (
            "ok" if "fillLogin" in expression else True if "isLoginPage" in expression else None)
        bot._refresh_captcha = lambda page: None
        bot._fill_credentials = lambda page, person: True
        bot._password_ready = lambda page, person: True
        bot._type_captcha = lambda page: True

        def submit(page):
            clicks.append(bot.state.get("alarm"))
            return "stay"

        bot._submit_login = submit
        manual = []
        bot._apply_manual = lambda page, person, label, code: (manual.append(code), "ok")[1]
        page = mock.Mock()
        context = mock.Mock()
        operator = type_after_alarm(bot, "ab34", "login")
        with mock.patch.object(runner_module, "_beep") as beep:
            done = bot._login(page, context, {"username": "u", "password": "p"}, "متقاضی ۱", "https://example.invalid")
        operator.join(2)
        self.assertTrue(done)
        self.assertEqual(len(clicks), LOGIN_AUTO_TRIES)
        self.assertTrue(all(alarm is None for alarm in clicks), "no alarm during automatic tries")
        self.assertEqual(manual, ["ab34"])
        beep.assert_called()
        self.assertFalse(any(status == "needs_operator" for status, msg in bot.said[:LOGIN_AUTO_TRIES * 2] if "تلاش" in msg))

    def test_form_code_length_rules(self):
        bot = quiet_runner()
        bot.thread = mock.Mock(is_alive=lambda: True)
        bot.state["captcha_kind"] = "login"
        with self.assertRaises(ValueError):
            bot.submit_code("abcde")
        bot.state["captcha_kind"] = "form"
        bot.submit_code("abcde")
        self.assertEqual(bot.cmds.get_nowait()[1], "abcde")


if __name__ == "__main__":
    unittest.main()
