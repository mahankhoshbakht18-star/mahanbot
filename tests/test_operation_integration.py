import sys
import threading
import types
import unittest
from unittest.mock import patch

import operation_integration


class FakeBankSelectionBot:
    def _process_bank_selection_v2(self, page, stop_event):
        return "original"

    def _process_branch_selection(self, page, stop_event):
        return "original"


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
        fake_module = types.ModuleType("bot_select")
        fake_module.BankSelectionBot = FakeBankSelectionBot
        operation_integration._INSTALLED = False
        with patch.dict(sys.modules, {"bot_select": fake_module}):
            operation_integration.install_operation_integration()
        cls.branch_method = FakeBankSelectionBot._process_branch_selection

    def test_final_submit_off_never_clicks_save(self):
        bot = FakeBot()
        page = FakePage()
        stop_event = threading.Event()

        def stop_after_wait(_seconds):
            stop_event.set()

        with (
            patch(
                "operation_integration.DBHandler.get_applicant",
                return_value={"data": '{"bank_final_submit_enabled": false}'},
            ),
            patch("operation_integration.DBHandler.update_status"),
            patch("operation_integration.DBHandler.update_applicant_data"),
            patch("operation_integration.BANK_ARCHIVE_STORE.capture", return_value={}),
            patch("operation_integration.EVENT_BROADCASTER.emit_event"),
            patch("operation_integration.time.sleep", side_effect=stop_after_wait),
        ):
            result = self.branch_method(bot, page, stop_event)

        self.assertEqual(result, "waiting")
        self.assertEqual(page.clicked, [])

    def test_final_submit_on_clicks_save_once_and_revokes_permission(self):
        bot = FakeBot()
        page = FakePage()
        stop_event = threading.Event()

        with (
            patch(
                "operation_integration.DBHandler.get_applicant",
                return_value={"data": '{"bank_final_submit_enabled": true}'},
            ),
            patch("operation_integration.DBHandler.update_status"),
            patch("operation_integration.DBHandler.update_applicant_data", return_value=True) as update_data,
            patch("operation_integration.BANK_ARCHIVE_STORE.capture", return_value={}),
            patch("operation_integration.EVENT_BROADCASTER.emit_event"),
        ):
            result = self.branch_method(bot, page, stop_event)

        self.assertEqual(result, "submitted")
        self.assertEqual(page.clicked, ["#ctl00_ContentPlaceHolder1_btnSave"])
        update_data.assert_called_with("1234567890", {"bank_final_submit_enabled": False})


if __name__ == "__main__":
    unittest.main()
