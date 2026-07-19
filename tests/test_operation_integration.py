import threading
import unittest
from unittest.mock import patch

from operation_integration import install_operation_integration


class FakeBot:
    nid = "1234567890"

    def __init__(self):
        self.logs = []

    def _wait_for_branch_fully_loaded(self, page, selector):
        return None

    def log(self, message, level="info", page=None):
        self.logs.append((message, level))


class FakePage:
    url = "https://example.test/bank"

    def __init__(self):
        self.clicked = []

    def evaluate(self, script, selector):
        if "const first = options.find" in script:
            return "branch-1"
        if "selectedIndex" in script:
            if "ddlBankName" in selector:
                return "بانک ملی"
            return "شعبه مرکزی"
        return None

    def click(self, selector):
        self.clicked.append(selector)

    def is_closed(self):
        return False


class OperationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_operation_integration()
        from bot_select import BankSelectionBot

        cls.branch_method = BankSelectionBot._process_branch_selection

    def test_final_submit_off_never_clicks_save(self):
        bot = FakeBot()
        page = FakePage()
        stop_event = threading.Event()

        def stop_after_wait(_seconds):
            stop_event.set()

        with (
            patch("operation_integration.DBHandler.get_config", return_value={"final_submit": False}),
            patch("operation_integration.DBHandler.update_status"),
            patch("operation_integration.BANK_ARCHIVE_STORE.capture", return_value={}),
            patch("operation_integration.EVENT_BROADCASTER.emit_event"),
            patch("operation_integration.time.sleep", side_effect=stop_after_wait),
        ):
            result = self.branch_method(bot, page, stop_event)

        self.assertEqual(result, "waiting")
        self.assertEqual(page.clicked, [])

    def test_final_submit_on_clicks_save_once(self):
        bot = FakeBot()
        page = FakePage()
        stop_event = threading.Event()

        with (
            patch("operation_integration.DBHandler.get_config", return_value={"final_submit": True}),
            patch("operation_integration.DBHandler.update_status"),
            patch("operation_integration.BANK_ARCHIVE_STORE.capture", return_value={}),
            patch("operation_integration.EVENT_BROADCASTER.emit_event"),
        ):
            result = self.branch_method(bot, page, stop_event)

        self.assertEqual(result, "submitted")
        self.assertEqual(page.clicked, ["#ctl00_ContentPlaceHolder1_btnSave"])


if __name__ == "__main__":
    unittest.main()
