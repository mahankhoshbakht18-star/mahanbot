import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from database import DBHandler
from bot_select import BankSelectionBot
from bank_page_manager import BankPageState as State
from bank_session_controller import BankSelectionSessionController


class MockDialog:
    def __init__(self, message_text: str, dialog_type: str = "alert"):
        self._message = message_text
        self._type = dialog_type
        self.accepted = False
        self.dismissed = False

    def message(self):
        return self._message

    def type(self):
        return self._type

    def accept(self):
        self.accepted = True

    def dismiss(self):
        self.dismissed = True


class MockLocator:
    def __init__(self, visible=True, count=1, value="", text=""):
        self._visible = visible
        self._count = count
        self._value = value
        self._text = text

    def count(self):
        return self._count

    def is_visible(self):
        return self._visible

    def is_enabled(self):
        return True

    def first(self):
        return self

    def input_value(self):
        return self._value

    def inner_text(self):
        return self._text

    def click(self, *args, **kwargs):
        pass

    def fill(self, *args, **kwargs):
        pass


class MockWebFormsPage:
    def __init__(self, url="https://ve.cbi.ir/SelectBnkShb.aspx"):
        self.url = url
        self._dialog_listeners = []
        self.locators = {}
        self._closed = False

    def on(self, event, callback):
        if event == "dialog":
            self._dialog_listeners.append(callback)

    def trigger_dialog(self, message: str, dtype: str = "alert") -> MockDialog:
        d = MockDialog(message, dtype)
        for cb in self._dialog_listeners:
            cb(d)
        return d

    def locator(self, selector):
        if selector in self.locators:
            return self.locators[selector]
        return MockLocator(visible=False, count=0)

    def is_closed(self):
        return self._closed

    def inner_text(self, selector):
        return ""

    def evaluate(self, script, *args):
        return []

    def wait_for_timeout(self, ms):
        pass


class OtpExpiryAndSaderatMonitoringTests(unittest.TestCase):
    def setUp(self):
        import json
        self.nid = "9400000064"
        self.user_data = {
            "national_id": self.nid,
            "name": "سعید دادکاریان",
            "mobile": "09120000000",
            "state": "سیستان و بلوچستان",
            "city": "زاهدان",
            "priority_banks": ["بانک صادرات"],
            "primary_bank": "بانک صادرات",
        }
        with DBHandler._connect() as conn:
            conn.execute("DELETE FROM applicants WHERE national_id = ?", (self.nid,))
            conn.execute(
                "INSERT INTO applicants (full_name, national_id, status, data) VALUES (?, ?, 'Ready', ?)",
                ("سعید دادکاریان", self.nid, json.dumps(self.user_data)),
            )
            conn.commit()
        DBHandler.update_runtime(self.nid, status="Ready", current_step="STEP_1_ENTRY_FORM", priority_banks=["بانک صادرات"], city="زاهدان")

    def tearDown(self):
        try:
            with DBHandler._connect() as conn:
                conn.execute("DELETE FROM applicants WHERE national_id = ?", (self.nid,))
                conn.commit()
        except Exception:
            pass

    def test_01_otp_expired_alert_auto_accepted_and_classified(self):
        """Verify the exact Iranian CBI alert popup is accepted instantly and classified as otp_expired."""
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()
        bot.attach_dialog_handler(page)

        # CBI Exact Alert text from screenshot:
        cbi_alert = "کد تایید تلفن همراه نامعتبر بوده و یا منقضی شده است !"
        dlg = page.trigger_dialog(cbi_alert, "alert")

        # 1. Dialog must be accepted immediately so browser does not hang
        self.assertTrue(dlg.accepted, "Dialog was not auto-accepted")
        # 2. Action must be classified as otp_expired
        self.assertEqual(bot._last_dialog_action, "otp_expired")
        # 3. Recovery flag must be set
        self.assertTrue(bot.consume_recovery_flag("otp_expired"))
        # 4. DB status must be updated
        status_info = DBHandler.get_applicant(self.nid)
        self.assertIn("منقضی", str(status_info["status"] or ""))

    def test_02_otp_sync_and_inject_exits_cleanly_on_expired(self):
        """fast_otp_sync_and_inject returns 'otp_expired' when alert is caught."""
        from bot_select_otp import fast_otp_sync_and_inject

        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()
        bot.attach_dialog_handler(page)

        # Mock OTP input field on page
        page.locators["#ctl00_ContentPlaceHolder1_tbMobileConfCode"] = MockLocator(visible=True, count=1, value="583043")

        # Trigger expired alert
        page.trigger_dialog("کد تایید تلفن همراه نامعتبر بوده و یا منقضی شده است !")

        stop_event = threading.Event()
        result = fast_otp_sync_and_inject(bot, page, stop_event, lambda ev, s: False)
        self.assertEqual(result, "otp_expired")

    def test_03_bank_saderat_missing_monitors_without_freezing(self):
        """When Bank Saderat is not in the dropdown, the bot monitors and does not freeze."""
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)

        owner = MagicMock()
        owner.assert_owner = lambda: None
        owner.page = MockWebFormsPage()
        owner.cancellation_token = threading.Event()
        owner.commands = MagicMock()
        owner.commands.empty.return_value = True
        owner.operation_lock = MagicMock()
        owner.operation_lock.__enter__ = MagicMock(return_value=None)
        owner.operation_lock.__exit__ = MagicMock(return_value=None)
        owner.paused = False
        owner.bank_page_active = True

        controller = BankSelectionSessionController(bot, owner)

        # Mock observe returning bank page with only Resalat bank (Saderat missing)
        mock_data = {
            "is_bank_page": True,
            "applicant_nid": self.nid,
            "state": State.BANK_OPTIONS_READY,
            "banks": [{"name": "بانک قرض‌الحسنه رسالت", "value": "12"}],
        }
        controller.observe = MagicMock(return_value=("STEP_3_BANK_SELECT", mock_data))

        # Mock process_bank_selection_v2 returning 'no_match'
        with patch("bank_session_controller.process_bank_selection_v2", return_value="no_match"):
            tick_result = controller.tick()

        # Bot must transition to PREFERRED_BANK_NOT_FOUND, not hang
        self.assertEqual(controller.state, State.PREFERRED_BANK_NOT_FOUND)
        self.assertEqual(tick_result, "waiting")

        # Verify DB runtime updated with monitoring status
        rt = DBHandler.get_runtime(self.nid)
        self.assertEqual(rt.get("status"), "پایش بانک‌ها")
        self.assertGreater(rt.get("next_refresh", 0), time.time())

        # Test cycle timeout: after timeout without finding Saderat, it returns 'reauthenticate'
        controller._bank_not_found_since = time.monotonic() - 150  # Over 90s limit
        with patch("bank_session_controller.process_bank_selection_v2", return_value="no_match"):
            cycle_result = controller.tick()

        self.assertEqual(cycle_result, "reauthenticate")

    def test_04_session_expired_in_bank_controller_returns_reauthenticate(self):
        """When session expires while on bank page, controller returns 'reauthenticate'."""
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)

        owner = MagicMock()
        owner.assert_owner = lambda: None
        owner.page = MockWebFormsPage()
        owner.cancellation_token = threading.Event()
        owner.commands = MagicMock()
        owner.commands.empty.return_value = True
        owner.operation_lock = MagicMock()
        owner.operation_lock.__enter__ = MagicMock(return_value=None)
        owner.operation_lock.__exit__ = MagicMock(return_value=None)
        owner.paused = False
        owner.bank_page_active = True

        controller = BankSelectionSessionController(bot, owner)
        bot.attach_dialog_handler(owner.page)

        # Trigger session expired dialog
        owner.page.trigger_dialog("مدت زمان نشست شما به پایان رسیده است. لطفا مجددا وارد شوید.")

        tick_result = controller.tick()
        self.assertEqual(tick_result, "reauthenticate")


if __name__ == "__main__":
    unittest.main()
