import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import browser_launcher
import database
from database import DBHandler
from bot_core import BotCore
from bot_select import BankSelectionBot, LoanTypeAdapter
from bot_register import RegistrationBot, RegistrationLoanAdapter
from bot_select_bank import _save_bank_page_if_changed, repair_saved_pages, process_bank_selection_v2
from api_solver import CaptchaService, TEMP_CAPTCHA_FILENAME
from bot_select_captcha import ModelCaptchaSolver
from event_logger import EVENT_BROADCASTER, build_event
from server import JobQueue
from routes.applicants import parse_and_validate_excel_rows
from routes.settings import update_settings
from core.models import SettingsRequest


class FakeInput:
    def __init__(self, values):
        self.values = list(values)
        self.index = 0

    def input_value(self):
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value

    def fill(self, value, **kwargs):
        self.values = [value]
        self.index = 0

    def dispatch_event(self, *args, **kwargs):
        return None


class FakeButton:
    def __init__(self):
        self.clicks = 0

    def click(self, *args, **kwargs):
        self.clicks += 1


class FakePage:
    url = "https://ve.cbi.ir/SelectBnkShb.aspx"

    def content(self):
        return "<html><body>bank snapshot</body></html>"

    def screenshot(self, path, **kwargs):
        Path(path).write_bytes(b"png")


class FakeSnapshotBot:
    def __init__(self):
        self.settings = {
            "save_page_enabled": True,
            "save_html": True,
            "save_screenshot": True,
            "save_only_on_bank_change": True,
        }
        self.user_data = {"full_name": "Test User"}
        self.nid = "0000000000"
        self.messages = []

    def log(self, message, *args, **kwargs):
        self.messages.append(message)


class MockLocator:
    def __init__(self, visible=False, enabled=True, value="", text="", count=1, options=None):
        self._visible = visible
        self._enabled = enabled
        self._value = value
        self._text = text
        self._count = count if visible or count > 0 else 0
        self._options = options or []
        self.clicks = 0
        self.checked = False

    def count(self):
        return self._count

    @property
    def first(self):
        return self

    def nth(self, idx):
        return self

    def is_visible(self):
        return self._visible

    def is_enabled(self):
        return self._enabled

    def is_checked(self):
        return self.checked

    def check(self, **kwargs):
        self.checked = True

    def input_value(self):
        return self._value

    def fill(self, val, **kwargs):
        self._value = str(val)

    def type(self, char, **kwargs):
        self._value += str(char)

    def press(self, key, **kwargs):
        if key == "Backspace":
            self._value = ""

    def click(self, **kwargs):
        self.clicks += 1

    def inner_text(self):
        return self._text

    def text_content(self):
        return self._text

    def get_attribute(self, name):
        if name in {"readonly", "disabled"}:
            return None if self._enabled else "true"
        return "attr"

    def element_handle(self):
        h = Mock()
        h.bounding_box.return_value = {"x": 0, "y": 0, "width": 100, "height": 20}
        return h

    def wait_for(self, **kwargs):
        if not self._visible:
            raise TimeoutError("not visible")

    def scroll_into_view_if_needed(self):
        pass

    def dispatch_event(self, *args, **kwargs):
        pass

    def screenshot(self, *args, **kwargs):
        return b"png"


class MockDialog:
    def __init__(self, message: str, dialog_type: str = "alert"):
        self._message = message
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


class MockWebFormsPage:
    """Simulated ASP.NET SelectBnkShb.aspx / Register.aspx page for deterministic testing."""

    def __init__(self, url="https://ve.cbi.ir/SelectBnkShb.aspx", title="فرم انتخاب بانک"):
        self.url = url
        self._title = title
        self._closed = False
        self.frames = []
        self.locators = {}
        self.body_text = ""
        self.html_content = "<html><body>Mock Page</body></html>"
        self.bank_options = []
        self.branch_options = []
        self.city_options = [{"v": "793", "t": "خرم آباد", "s": True}]
        self.state_options = [{"v": "25", "t": "لرستان", "s": True}]
        self.mode_controls = []
        self.selected_bank = "0"
        self.selected_branch = "0"
        self.dialog_listeners = []
        self.on_select_bank_hook = None
        self.on_click_save_hook = None
        self.reloads = 0
        self._original_goto = Mock()

    def title(self):
        return self._title

    def is_closed(self):
        return self._closed

    def close(self):
        self._closed = True

    def content(self):
        return self.html_content

    def inner_text(self, selector="body", **kwargs):
        if selector == "body":
            return self.body_text
        loc = self.locator(selector)
        return loc.inner_text()

    def on(self, event_name, callback):
        if event_name == "dialog":
            self.dialog_listeners.append(callback)

    def trigger_dialog(self, message: str, dialog_type: str = "alert"):
        dlg = MockDialog(message, dialog_type=dialog_type)
        for cb in self.dialog_listeners:
            cb(dlg)
        return dlg

    def locator(self, selector: str) -> MockLocator:
        if selector in self.locators:
            return self.locators[selector]
        for key, loc in self.locators.items():
            if key in selector or selector in key:
                return loc
        return MockLocator(visible=False, count=0)

    def wait_for_selector(self, selector: str, timeout=1000, state="visible"):
        for part in [s.strip() for s in selector.split(",")]:
            loc = self.locator(part)
            if loc.count() > 0 and (state == "attached" or loc.is_visible()):
                return loc
        raise TimeoutError(f"Selector {selector} not visible")

    def wait_for_function(self, js_code: str, arg=None, timeout=1000):
        return True

    def wait_for_load_state(self, state="domcontentloaded", timeout=1000):
        return True

    def wait_for_timeout(self, ms):
        pass

    def reload(self, **kwargs):
        self.reloads += 1
        return True

    def goto(self, url, **kwargs):
        self.url = url
        self._original_goto(url, **kwargs)
        return Mock(status=200, url=url)

    def select_option(self, selector: str, value=None, label=None):
        if "ddlBankName" in selector:
            self.selected_bank = value
            for opt in self.bank_options:
                opt["selected"] = (opt["value"] == value)
            if self.on_select_bank_hook:
                self.on_select_bank_hook(self, value)
        elif "ddlBranch" in selector:
            self.selected_branch = value
            for opt in self.branch_options:
                opt["selected"] = (opt["value"] == value)
        elif "ddlCity" in selector:
            self.selected_city = value
            for opt in self.city_options:
                opt["s"] = (opt["v"] == value)
        return [value]

    def click(self, selector: str, **kwargs):
        loc = self.locator(selector)
        loc.click()
        if "btnSave" in selector and self.on_click_save_hook:
            self.on_click_save_hook(self)

    def evaluate(self, script: str, arg=None):
        if arg == "#ctl00_ContentPlaceHolder1_ddlBankName":
            return self.bank_options
        if arg == "#ctl00_ContentPlaceHolder1_ddlBranch":
            return self.branch_options
        if arg == "#ctl00_ContentPlaceHolder1_ddlCity":
            return self.city_options
        if arg == "#ctl00_ContentPlaceHolder1_ddlState":
            return self.state_options
        if "input[type='radio']" in script:
            return self.mode_controls
        return []

    def screenshot(self, **kwargs):
        return b"jpg"


class RegressionTests(unittest.TestCase):
    def test_attempt_state_is_clean_for_five_browser_restarts(self):
        bot = BotCore.__new__(BotCore)
        bot._run_started_at = 0.0
        bot.set_runtime_state = Mock()
        for attempt in range(1, 6):
            bot._safe_goto_locked = True
            bot._otp_wait_lock = True
            bot._otp_guard_until = 9999999999.0
            bot._captcha_retry = 9
            bot._otp_retry = 9
            bot._dialog_handling = True
            bot.reset_attempt_state(attempt)
            self.assertFalse(bot._safe_goto_locked)
            self.assertFalse(bot._otp_wait_lock)
            self.assertEqual(bot._otp_guard_until, 0.0)
            self.assertEqual(bot._captcha_retry, 0)
            self.assertEqual(bot._otp_retry, 0)
            self.assertFalse(bot._dialog_handling)

    def test_local_captcha_is_validated_filled_and_submitted(self):
        bot = BotCore.__new__(BotCore)
        bot.nid = "0000000000"
        bot.settings = {}
        bot.captcha_service = Mock()
        bot.captcha_service.solve_element.return_value = "ab12"
        bot.set_runtime_state = Mock()
        bot.log = Mock()
        bot.mark_progress = Mock()
        captcha_input = FakeInput([""])
        submit = FakeButton()
        with patch.object(database.DBHandler, "append_captcha_attempt") as append:
            result = bot.solve_captcha_with_model(
                Mock(),
                captcha_input,
                submit,
                stop_event=threading.Event(),
                source="test",
                captcha_element=Mock(),
            )
        self.assertTrue(result)
        self.assertEqual(submit.clicks, 1)
        self.assertEqual(captcha_input.input_value(), "AB12")
        append.assert_called_once_with("0000000000", "AB12", "submitted", source="test")

    def test_captcha_service_never_returns_an_answer(self):
        self.assertIsNone(CaptchaService().solve(b"not-an-image"))

    def test_captcha_element_screenshot_is_always_removed(self):
        class RecordingSolver:
            def __init__(self):
                self.path = None

            def solve(self, image_path):
                self.path = image_path
                self.assertion = os.path.isfile(image_path)
                return "AB12"

        class ScreenshotElement:
            def screenshot(self, path, **kwargs):
                Path(path).write_bytes(b"temporary screenshot")

        with tempfile.TemporaryDirectory() as temp_dir:
            solver = RecordingSolver()
            service = CaptchaService(solver=solver, temp_root=temp_dir)
            self.assertEqual(service.solve_element(ScreenshotElement()), "AB12")
            self.assertTrue(solver.assertion)
            self.assertEqual(os.path.basename(solver.path), TEMP_CAPTCHA_FILENAME)
            self.assertFalse(os.path.exists(solver.path))
            self.assertEqual(os.listdir(temp_dir), [])

    def test_model_path_uses_environment_override(self):
        expected = os.path.abspath("custom-captcha-model.pth")
        with patch.dict(os.environ, {"CAPTCHA_MODEL_PATH": expected}):
            solver = ModelCaptchaSolver(device="cpu")
        self.assertEqual(solver.model_path, expected)

    def test_job_enqueue_is_atomic_per_applicant(self):
        queue = JobQueue(max_concurrency=1)
        results = []
        lock = threading.Lock()

        def enqueue():
            result = queue.enqueue("select", "0000000000", {})
            with lock:
                results.append(result)

        threads = [threading.Thread(target=enqueue) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        created = [job for job in results if job is not None]
        self.assertEqual(len(created), 1)
        self.assertEqual(len(queue.list_jobs()), 1)

    def test_snapshot_is_signature_based_and_repairs_orphans(self):
        bot = FakeSnapshotBot()
        page = FakePage()
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = os.path.join(temp_dir, "applicant")
            os.makedirs(archive)
            with patch("bot_select_bank._applicant_archive_dir", return_value=archive):
                _save_bank_page_if_changed(bot, page, ["Bank A", "Bank B"])
                first_files = sorted(os.listdir(archive))
                _save_bank_page_if_changed(bot, page, ["Bank B", "Bank A"])
                self.assertEqual(first_files, sorted(os.listdir(archive)))
                _save_bank_page_if_changed(bot, page, ["Bank A"])
            manifest_path = os.path.join(archive, "banks_seen.json")
            with open(manifest_path, "r", encoding="utf-8") as stream:
                manifest = json.load(stream)
            self.assertEqual(len(manifest["snapshots"]), 2)

            orphan = os.path.join(archive, "banks-old-orphan.html")
            Path(orphan).write_text("legacy", encoding="utf-8")
            report = repair_saved_pages(temp_dir)
            self.assertEqual(report["orphan_files_added"], 1)
            with open(manifest_path, "r", encoding="utf-8") as stream:
                repaired = json.load(stream)
            self.assertTrue(any(item.get("recovered") for item in repaired["snapshots"]))

    def test_legacy_spouse_migration_is_safe(self):
        old_path = database.DB_PATH
        old_cache = dict(database.DBHandler._settings_cache)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                test_db = os.path.join(temp_dir, "migration.db")
                conn = sqlite3.connect(test_db)
                conn.execute("CREATE TABLE applicants (id INTEGER PRIMARY KEY, full_name TEXT, national_id TEXT UNIQUE, status TEXT, last_log TEXT, data TEXT)")
                conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
                conn.execute(
                    "INSERT INTO settings VALUES ('config', ?)",
                    (json.dumps({"captcha_mode": "robot", "retry_count": 1000}),),
                )
                conn.execute("INSERT INTO applicants VALUES (1,'A','0000000001','Ready','-',?)", (json.dumps({"spouse": "a name"}),))
                conn.execute("INSERT INTO applicants VALUES (2,'B','0000000002','Ready','-',?)", (json.dumps({"spouse": "1234567890"}),))
                conn.commit()
                conn.close()
                database.DB_PATH = test_db
                database.DBHandler._settings_cache.clear()
                database.DBHandler.init_db()
                conn = sqlite3.connect(test_db)
                rows = [json.loads(row[0]) for row in conn.execute("SELECT data FROM applicants ORDER BY id")]
                config = json.loads(conn.execute("SELECT value FROM settings WHERE key='config'").fetchone()[0])
                conn.close()
                self.assertNotIn("spouse_national_id", rows[0])
                self.assertEqual(rows[1]["spouse_national_id"], "1234567890")
                self.assertEqual(config["captcha_mode"], "model")
                self.assertEqual(config["retry_count"], 5)
        finally:
            database.DB_PATH = old_path
            database.DBHandler._settings_cache.clear()
            database.DBHandler._settings_cache.update(old_cache)

    def test_reset_runtime_returns_the_new_payload(self):
        with patch.object(database.DBHandler, "_connect") as connect:
            connection = Mock()
            context = Mock()
            context.__enter__ = Mock(return_value=connection)
            context.__exit__ = Mock(return_value=False)
            connect.return_value = context
            payload = database.DBHandler.reset_runtime("0000000000", run_id="run-1")
        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(payload["current_step"], "OPEN_BROWSER")

    def test_browser_launch_failure_is_not_silenced(self):
        starter = Mock()
        starter.start.side_effect = RuntimeError("launch failed early")
        with patch.object(browser_launcher, "sync_playwright", return_value=starter):
            with self.assertRaises(browser_launcher.BrowserLaunchError):
                browser_launcher.launch_browser(browser_launcher.get_default_browser_profile())

    def test_cancel_watcher_does_not_close_playwright_cross_thread(self):
        bot = BotCore.__new__(BotCore)
        bot.log = Mock()
        stop = threading.Event()
        with patch("bot_core.close_browser") as close:
            bot._start_cancel_watcher(stop, Mock(), Mock(), Mock(), Mock(), threading.Event())
            stop.set()
            time.sleep(0.2)
        close.assert_not_called()

    def test_event_timestamps_have_millisecond_precision(self):
        event = build_event("test")
        self.assertRegex(event["timestamp"], r"\.\d{3}\+00:00$")


class MandatoryV180Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = os.path.join(self.temp_dir.name, "test_v180.db")
        DBHandler._settings_cache.clear()
        DBHandler.init_db()
        EVENT_BROADCASTER.event_history.clear()
        EVENT_BROADCASTER._last_log_by_nid.clear()

        self.nid = "0012345678"
        with DBHandler._connect() as conn:
            conn.execute(
                "INSERT INTO applicants (full_name, national_id, status, data) VALUES (?, ?, 'Ready', ?)",
                (
                    "متقاضی تست لرستان",
                    self.nid,
                    '{"state": "لرستان", "city": "خرم آباد", "priority_banks": ["صادرات"], "bank_mode": "online", "zip_code": "0012345678", "postal_code": "0012345678", "spouse_national_id": "0098765432"}',
                ),
            )
            conn.commit()

    def tearDown(self):
        database.DB_PATH = self.old_db_path
        DBHandler._settings_cache.clear()
        self.temp_dir.cleanup()

    def test_01_normal_entry_to_otp_stage(self):
        """Scenario 1: Normal entry on Step 1 fills National ID, solves CAPTCHA, and reaches OTP stage."""
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()
        nid_loc = MockLocator(visible=True, value="")
        cap_img = MockLocator(visible=True)
        cap_inp = MockLocator(visible=True, value="")
        btn_send = MockLocator(visible=True)
        otp_inp = MockLocator(visible=False, count=0)

        page.locators["#ctl00_ContentPlaceHolder1_tbIDNo"] = nid_loc
        page.locators[".BDC_CaptchaImage"] = cap_img
        page.locators["input[name$='tbCaptcha1']"] = cap_inp
        page.locators["input[name$='btnSendConfirmCode']"] = btn_send
        page.locators["input[name$='tbMobileConfCode']"] = otp_inp

        def on_submit_click(**kwargs):
            btn_send.clicks += 1
            nid_loc._visible = False
            nid_loc._count = 0
            otp_inp._visible = True
            otp_inp._count = 1

        btn_send.click = on_submit_click
        bot.captcha_service = Mock()
        bot.captcha_service.solve_element.return_value = "AB123"

        self.assertEqual(bot.detect_actual_page_stage(page), "STEP_1_ENTRY_FORM")
        self.assertTrue(bot._fill_input_with_verification(page, nid_loc, self.nid, "National ID"))
        self.assertEqual(nid_loc.input_value(), self.nid)

        stop_event = threading.Event()
        solved = bot._solve_primary_captcha_fast(page, stop_event)
        self.assertTrue(solved)
        self.assertEqual(bot.detect_actual_page_stage(page), "STEP_2_OTP")

    def test_02_captcha_failure_and_manual_entry_handoff(self):
        """Scenario 2: CAPTCHA model fails bounded retries and hands off cleanly to manual user input without infinite loop."""
        settings = DBHandler.get_config()
        settings["captcha_max_retries"] = 2
        settings["pause_for_manual_captcha"] = True
        settings["captcha_manual_timeout"] = 5
        bot = BankSelectionBot(self.nid, settings)

        page = MockWebFormsPage()
        cap_img = MockLocator(visible=True)
        cap_inp = MockLocator(visible=True, value="")
        btn_send = MockLocator(visible=True)
        otp_inp = MockLocator(visible=False, count=0)

        page.locators[".BDC_CaptchaImage"] = cap_img
        page.locators["input[name$='tbCaptcha1']"] = cap_inp
        page.locators["input[name$='btnSendConfirmCode']"] = btn_send
        page.locators["input[name$='tbMobileConfCode']"] = otp_inp

        bot.captcha_service = Mock()
        bot.captcha_service.solve_element.return_value = None

        def simulate_user_manual_entry():
            time.sleep(0.35)
            otp_inp._visible = True
            otp_inp._count = 1

        threading.Thread(target=simulate_user_manual_entry, daemon=True).start()
        stop_event = threading.Event()
        ok = bot._solve_primary_captcha_fast(page, stop_event)
        self.assertTrue(ok)
        self.assertEqual(bot.captcha_service.solve_element.call_count, 2)
        events = [e.get("event_code") for e in EVENT_BROADCASTER.event_history if e.get("event_code")]
        self.assertIn("CAPTCHA_MANUAL_REQUIRED", events)

    def test_03_valid_otp_entry_and_transition_to_bank_selection(self):
        """Scenario 3: Valid OTP is received, validated, injected, and transitions to SelectBnkShb.aspx."""
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()

        otp_inp = MockLocator(visible=True, value="")
        cap_img = MockLocator(visible=True)
        cap_inp = MockLocator(visible=True, value="")
        btn_cont = MockLocator(visible=True)
        bank_ddl = MockLocator(visible=False, count=0)

        page.locators["#ctl00_ContentPlaceHolder1_tbMobileConfCode"] = otp_inp
        page.locators[".BDC_CaptchaImage"] = cap_img
        page.locators["#ctl00_ContentPlaceHolder1_tbCaptcha2"] = cap_inp
        page.locators["#ctl00_ContentPlaceHolder1_btnContinue1"] = btn_cont
        page.locators["#ctl00_ContentPlaceHolder1_ddlBankName"] = bank_ddl

        def on_continue_click(**kwargs):
            otp_inp._visible = False
            otp_inp._count = 0
            bank_ddl._visible = True
            bank_ddl._count = 1

        btn_cont.click = on_continue_click
        bot.captcha_service = Mock()
        bot.captcha_service.solve_element.return_value = "CD456"

        stop_event = threading.Event()
        bot._active_stop_event = stop_event

        def delayed_sms():
            time.sleep(0.1)
            DBHandler.save_otp(self.nid, "654321")

        threading.Thread(target=delayed_sms, daemon=True).start()
        res = bot.fast_otp_sync_and_inject(page, stop_event)
        self.assertEqual(res, "bank_ready")
        self.assertEqual(otp_inp.input_value(), "654321")
        self.assertFalse(bot._otp_wait_lock)
        self.assertEqual(bot._otp_guard_until, 0.0)

    def test_04_bank_selection_page_clears_false_otp_waiting_lock(self):
        """Scenario 4: On SelectBnkShb.aspx with ddlBankName visible and no OTP input, OTP wait lock never suppresses recovery."""
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()
        page.locators["#ctl00_ContentPlaceHolder1_ddlBankName"] = MockLocator(visible=True, count=1)
        page.locators["input[name$='tbMobileConfCode']"] = MockLocator(visible=False, count=0)

        bot._otp_wait_lock = True
        bot._otp_guard_until = time.time() + 120.0
        bot._safe_goto_locked = True

        self.assertEqual(bot.detect_actual_page_stage(page), "STEP_3_BANK_SELECT")
        reloaded = bot._reload_guarded(page, reason="state timeout")
        self.assertTrue(reloaded)
        self.assertFalse(bot._otp_wait_lock)
        self.assertEqual(bot._otp_guard_until, 0.0)
        self.assertFalse(bot._safe_goto_locked)

    def test_05_bank_saderat_capacity_full_dialog_and_classification(self):
        """Scenario 5: Selecting Bank Saderat non-in-person triggers Lorestan capacity full alert -> classified as BANK_CAPACITY_FULL, not generic_error."""
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()
        bot.attach_dialog_handler(page)

        page.locators["#ctl00_ContentPlaceHolder1_ddlBankName"] = MockLocator(visible=True, count=1)
        page.bank_options = [
            {"value": "0", "text": "-- انتخاب کنید --", "selected": True},
            {"value": "19", "text": "بانک صادرات [دارای ظرفیت ثبت نام]", "selected": False},
        ]

        lorestan_alert = "ظرفیت ثبت نام غیرحضوری بانک صادرات در استان لرستان تکمیل شده است."
        page.on_select_bank_hook = lambda p, val: p.trigger_dialog(lorestan_alert, "alert")

        stop_event = threading.Event()
        result = process_bank_selection_v2(bot, page, stop_event)
        self.assertEqual(result, "capacity_full")
        self.assertEqual(bot._last_dialog_action, "bank_capacity_full")
        self.assertFalse(bot._recovery_flags.get("generic_error"))

        rt = DBHandler.get_runtime(self.nid)
        self.assertEqual(rt.get("status"), "BANK_CAPACITY_FULL")
        events = [e.get("event_code") for e in EVENT_BROADCASTER.event_history if e.get("event_code")]
        self.assertIn("BANK_OPTION_SELECTED", events)
        self.assertIn("BANK_CAPACITY_FULL", events)
        self.assertNotIn("FINAL_SUBMIT_CONFIRMED", events)

    def test_06_preserve_online_mode_without_switching_to_in_person(self):
        """Scenario 6: When user specifies online (غیرحضوری) mode and online radio is disabled/unavailable, bot NEVER switches to in-person (حضوری)."""
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()
        page.locators["#ctl00_ContentPlaceHolder1_ddlBankName"] = MockLocator(visible=True, count=1)
        page.bank_options = [
            {"value": "19", "text": "بانک صادرات [دارای ظرفیت ثبت نام]", "selected": False},
        ]
        in_person_radio = MockLocator(visible=True, enabled=True)
        page.locators["#rbtnHozoori"] = in_person_radio
        page.mode_controls = [
            {"kind": "radio", "id": "rbtnGheirHozoori", "name": "mode", "label": "غیرحضوری", "checked": False, "disabled": True},
            {"kind": "radio", "id": "rbtnHozoori", "name": "mode", "label": "حضوری", "checked": False, "disabled": False},
        ]

        stop_event = threading.Event()
        result = process_bank_selection_v2(bot, page, stop_event)
        self.assertEqual(result, "capacity_full")
        self.assertEqual(in_person_radio.clicks, 0)

    def test_07_priority_bank_missing_from_list(self):
        """Scenario 7: When priority bank is not in dropdown, emits BANK_OPTION_NOT_FOUND and returns no_match without returning to Step 1."""
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()
        page.locators["#ctl00_ContentPlaceHolder1_ddlBankName"] = MockLocator(visible=True, count=1)
        page.bank_options = [
            {"value": "13", "text": "بانک رفاه کارگران [دارای ظرفیت ثبت نام]", "selected": False},
            {"value": "21", "text": "پست بانک ایران [دارای ظرفیت ثبت نام]", "selected": False},
        ]

        stop_event = threading.Event()
        result = process_bank_selection_v2(bot, page, stop_event)
        self.assertEqual(result, "no_match")
        events = [e.get("event_code") for e in EVENT_BROADCASTER.event_history if e.get("event_code")]
        self.assertIn("BANK_OPTION_NOT_FOUND", events)

    def test_08_real_session_expiration_detection_and_checkpoint_recovery(self):
        """Scenario 8: Real session expiration dialog or redirect saves checkpoint and transitions cleanly to re-auth."""
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()
        bot.attach_dialog_handler(page)

        page.trigger_dialog("مهلت زمانی نشست شما به پایان رسیده است. لطفاً مجدداً وارد شوید.", "alert")
        self.assertEqual(bot._last_dialog_action, "session_expired")
        self.assertTrue(bot.consume_recovery_flag("session_expired"))

        DBHandler.set_otp(self.nid, "111222", ts=time.time() - 300)
        code, _ = bot._get_otp_from_db_fresh()
        self.assertIsNone(code)

    def test_09_checkpoint_save_and_resume_on_step3(self):
        """Scenario 9: Checkpoint saves state without OTP/CAPTCHA and resumes directly on Step 3 when session is alive."""
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()
        page.locators["#ctl00_ContentPlaceHolder1_ddlBankName"] = MockLocator(visible=True, count=1)

        cp = bot.save_checkpoint(
            "STEP_3_BANK_SELECT",
            page=page,
            selected_bank="صادرات",
            bank_mode="online",
            otp_code="999999",
            captcha_code="ABCD",
        )
        self.assertNotIn("otp_code", cp)
        self.assertNotIn("captcha_code", cp)
        self.assertEqual(cp["selected_bank"], "صادرات")

        bot2 = BankSelectionBot(self.nid, settings)
        resumed_stage = bot2._restore_from_checkpoint_if_available(page)
        self.assertEqual(resumed_stage, "STEP_3_BANK_SELECT")
        events = [e.get("event_code") for e in EVENT_BROADCASTER.event_history if e.get("event_code")]
        self.assertIn("CHECKPOINT_SAVED", events)
        self.assertIn("CHECKPOINT_RESTORED", events)

    def test_10_dialog_close_and_synchronized_watchdog_recovery(self):
        """Scenario 10: Watchdog and Recovery resync DOM stage on Step 3 without destructive reload."""
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()
        page.locators["#ctl00_ContentPlaceHolder1_ddlBankName"] = MockLocator(visible=True, count=1)

        bot._last_progress_time = time.time() - 100
        bot._otp_wait_lock = True
        triggered = bot.watchdog_check(page, stop_event=threading.Event())
        self.assertTrue(triggered)
        self.assertEqual(page.reloads, 0)
        self.assertFalse(bot._otp_wait_lock)
        events = [e.get("event_code") for e in EVENT_BROADCASTER.event_history if e.get("event_code")]
        self.assertIn("RECOVERY_STARTED", events)
        self.assertIn("RECOVERY_COMPLETED", events)

    def test_11_waf_or_rejection_stops_immediately(self):
        """Scenario 11: WAF / SiteRejectedError logs WAF_BLOCKED and stops without reloading or retrying."""
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()
        page.body_text = "The requested URL was rejected. Your support ID is: 987654321"
        bot.setup_browser = Mock(return_value=(Mock(), Mock(), Mock(), page))

        bot.run(threading.Event())
        rt = DBHandler.get_runtime(self.nid)
        self.assertEqual(rt.get("status"), "ERROR")
        self.assertEqual(rt.get("current_step"), "SITE_REJECTED")
        events = [e.get("event_code") for e in EVENT_BROADCASTER.event_history if e.get("event_code")]
        self.assertIn("WAF_BLOCKED", events)

    def test_12_manual_user_stop_and_safe_browser_close(self):
        """Scenario 12: User stop sets stop_event and owning thread closes browser cleanly."""
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()
        pw, browser, ctx = Mock(), Mock(), Mock()
        stop_event = threading.Event()
        stop_event.set()

        closed = bot.close_browser_on_stop(stop_event, pw, browser, ctx, page)
        self.assertTrue(closed)
        self.assertTrue(page.is_closed())

    def test_13_duplicate_final_submission_prevention(self):
        """Scenario 13: Once final submission is confirmed with tracking code, duplicate submission is strictly blocked."""
        settings = DBHandler.get_config()
        settings["auto_final_submit"] = True
        settings["final_submit"] = True
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()
        page.locators["#ctl00_ContentPlaceHolder1_ddlBankName"] = MockLocator(visible=True, count=1)
        save_btn = MockLocator(visible=True, count=1)
        tracking_lbl = MockLocator(visible=False, text="", count=0)
        page.locators["#ctl00_ContentPlaceHolder1_btnSave"] = save_btn
        page.locators["#ctl00_ContentPlaceHolder1_lblTrackingCode"] = tracking_lbl
        page.bank_options = [
            {"value": "19", "text": "بانک صادرات [دارای ظرفیت ثبت نام]", "selected": False},
        ]

        def on_save(p):
            tracking_lbl._visible = True
            tracking_lbl._count = 1
            tracking_lbl._text = "کد رهگیری: 8877665544"

        page.on_click_save_hook = on_save

        stop_event = threading.Event()
        res1 = process_bank_selection_v2(bot, page, stop_event)
        self.assertEqual(res1, "submitted_confirmed")
        self.assertEqual(save_btn.clicks, 1)
        self.assertEqual(DBHandler.has_final_submission(self.nid), "8877665544")

        res2 = process_bank_selection_v2(bot, page, stop_event)
        self.assertEqual(res2, "already_submitted")
        self.assertEqual(save_btn.clicks, 1)

    def test_14_marriage_and_childbearing_loan_modes_and_excel_leading_zeros(self):
        """Scenario 14: Separate execution & validation for Marriage vs Childbearing loans, plus Excel postal code leading zero preservation."""
        marriage_adapter = LoanTypeAdapter("marriage", {"spouse_national_id": "0011223344"})
        child_adapter = LoanTypeAdapter("childbearing", {"child_national_id": "0055667788"})
        self.assertEqual(marriage_adapter.kind, "marriage")
        self.assertEqual(child_adapter.kind, "childbearing")
        self.assertTrue(marriage_adapter.validate_applicant("0012345678", {"spouse_national_id": "0011223344"})[0])
        self.assertTrue(child_adapter.validate_applicant("0012345678", {"child_national_id": "0055667788"})[0])
        self.assertFalse(child_adapter.validate_applicant("0012345678", {"child_national_id": "123"})[0])

        reg_child = RegistrationLoanAdapter("فرزندآوری", {"child_national_id": "0055667788", "postal_code": "0012345678"})
        self.assertEqual(reg_child.kind, "childbearing")
        self.assertTrue(reg_child.validate_data("0012345678", {"child_national_id": "0055667788", "postal_code": "0012345678"})[0])

        raw_excel_rows = [
            {
                "نام و نام خانوادگی": "علی رضایی",
                "کد ملی": 12345678,
                "کد ملی همسر": "0098765432",
                "کد پستی": 681345678,
                "موبایل": 9123456789,
                "استان": "لرستان",
                "شهر": "خرم آباد",
                "نوع وام": "ازدواج",
            },
            {
                "نام و نام خانوادگی": "ردیف ناقص",
                "کد ملی": "",
                "کد پستی": "123",
            },
        ]
        valid, errors = parse_and_validate_excel_rows(raw_excel_rows)
        self.assertEqual(len(valid), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(valid[0]["national_id"], "0012345678")
        self.assertEqual(valid[0]["data"]["postal_code"], "0681345678")
        self.assertEqual(valid[0]["data"]["mobile"], "09123456789")

    def test_15_persistence_of_settings_and_state_across_restart(self):
        """Scenario 15: Settings (20s capacity recheck, 5s bank refresh, 120s OTP, auto_final_submit=False) and checkpoints persist across DB reload."""
        req = SettingsRequest(
            bank_capacity_recheck_seconds=25.0,
            bank_list_refresh_seconds=6.0,
            otp_validity_seconds=150,
            auto_final_submit=False,
            allow_reserve_bank=True,
            resume_from_checkpoint=True,
            save_page_enabled=True,
            repair_page_enabled=True,
            pause_for_manual_captcha=True,
            pause_for_manual_otp=True,
            diagnostic_logging_enabled=True,
        )
        res = update_settings(req, True)
        self.assertEqual(res["status"], "ok")

        DBHandler._settings_cache.clear()
        reloaded = DBHandler.get_config()
        self.assertEqual(reloaded["bank_capacity_recheck_seconds"], 25.0)
        self.assertEqual(reloaded["bank_list_refresh_seconds"], 6.0)
        self.assertEqual(reloaded["otp_validity_seconds"], 150)
        self.assertFalse(reloaded["auto_final_submit"])
        self.assertTrue(reloaded["allow_reserve_bank"])



    def test_16_delayed_captcha_image_waits_and_solves_with_model(self):
        """Scenario 16: When CAPTCHA image is not initially visible on page, bot triggers reload/waits for it to appear and solves it with model."""
        from bot_select_captcha import wait_for_captcha_image_ready
        settings = DBHandler.get_config()
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()

        cap_img = MockLocator(visible=False, count=0)
        reload_btn = MockLocator(visible=True, count=1)
        cap_inp = MockLocator(visible=True, value="")
        btn_send = MockLocator(visible=True)
        otp_inp = MockLocator(visible=False, count=0)

        page.locators[".BDC_CaptchaImage"] = cap_img
        page.locators[".BDC_ReloadLink"] = reload_btn
        page.locators["input[name$='tbCaptcha1']"] = cap_inp
        page.locators["input[name$='btnSendConfirmCode']"] = btn_send
        page.locators["input[name$='tbMobileConfCode']"] = otp_inp

        def on_reload_click(**kwargs):
            reload_btn.clicks += 1
            cap_img._visible = True
            cap_img._count = 1

        reload_btn.click = on_reload_click

        def on_submit_click(**kwargs):
            btn_send.clicks += 1
            otp_inp._visible = True
            otp_inp._count = 1

        btn_send.click = on_submit_click
        bot.captcha_service = Mock()
        bot.captcha_service.solve_element.return_value = "XY987"

        stop_event = threading.Event()
        solved = bot._solve_primary_captcha_fast(page, stop_event)
        self.assertTrue(solved)
        self.assertGreaterEqual(reload_btn.clicks, 1)
        self.assertEqual(cap_inp.input_value(), "XY987")
        self.assertEqual(btn_send.clicks, 1)



    def test_17_manual_otp_to_step3_and_bank_saderat_iran_online_selection(self):
        """Scenario 17: Manual OTP entry in browser transitions to Step 3, matches 'بانک صادرات ایران' against 'بانک صادرات [دارای ظرفیت ثبت نام]', applies غیرحضوری, skips branch, and logs all Step 3 events."""
        from bot_select_bank import canonicalize_bank_name, match_bank_option

        # Verify canonical Persian bank matching rules
        self.assertEqual(canonicalize_bank_name("بانک صادرات ایران"), "saderat")
        self.assertEqual(canonicalize_bank_name("بانک صادرات [دارای ظرفیت ثبت نام] - متقاضی در صف : 4259 نفر"), "saderat")
        self.assertEqual(canonicalize_bank_name("توسعه صادرات  [در انتظار تخصیص ظرفیت جدید (ذخیره)]"), "tosee_saderat")
        self.assertTrue(match_bank_option("بانک صادرات ایران", "بانک صادرات [دارای ظرفیت ثبت نام] - متقاضی در صف : 4259 نفر"))
        self.assertFalse(match_bank_option("بانک صادرات ایران", "توسعه صادرات  [در انتظار تخصیص ظرفیت جدید (ذخیره)]"))
        self.assertFalse(match_bank_option("صادرات", "توسعه صادرات [دارای ظرفیت ثبت نام]"))
        self.assertTrue(match_bank_option("بانک اقتصاد نوین", "بانک اقتصادنوين  [دارای ظرفیت ثبت نام]"))

        # Update applicant with exact prompt settings: Bank: بانک صادرات ایران, Mode: غیرحضوری
        DBHandler.update_applicant_data(
            self.nid,
            {
                "state": "استان سیستان وبلوچستان",
                "city": "مهرستان",
                "priority_banks": ["بانک صادرات ایران", "بانک رفاه کارگران"],
                "bank_mode": "غیرحضوری",
            },
        )
        settings = DBHandler.get_config()
        settings["auto_final_submit"] = False
        settings["final_submit"] = False
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()

        # Simulate manual OTP typed directly in browser (6 digits) + CAPTCHA solved -> transition to Step 3
        otp_inp = MockLocator(visible=True, value="789456")
        cap_img = MockLocator(visible=True)
        cap_inp = MockLocator(visible=True, value="")
        btn_cont = MockLocator(visible=True)
        bank_ddl = MockLocator(visible=False, count=0)

        page.locators["#ctl00_ContentPlaceHolder1_tbMobileConfCode"] = otp_inp
        page.locators[".BDC_CaptchaImage"] = cap_img
        page.locators["#ctl00_ContentPlaceHolder1_tbCaptcha2"] = cap_inp
        page.locators["#ctl00_ContentPlaceHolder1_btnContinue1"] = btn_cont
        page.locators["#ctl00_ContentPlaceHolder1_ddlBankName"] = bank_ddl

        def on_continue(**kwargs):
            otp_inp._visible = False
            otp_inp._count = 0
            bank_ddl._visible = True
            bank_ddl._count = 1

        btn_cont.click = on_continue
        bot.captcha_service = Mock()
        bot.captcha_service.solve_element.return_value = "XY12Z"
        stop_event = threading.Event()
        bot._active_stop_event = stop_event

        otp_res = bot.fast_otp_sync_and_inject(page, stop_event)
        self.assertEqual(otp_res, "bank_ready")
        self.assertFalse(bot._otp_wait_lock)

        # Now on Step 3 (SelectBnkShb.aspx) with real dropdown options from saved HTML
        city_ddl = MockLocator(visible=True, count=1)
        branch_ddl = MockLocator(visible=True, enabled=True, count=1)
        online_radio = MockLocator(visible=True, enabled=True, count=1)
        in_person_radio = MockLocator(visible=True, enabled=True, count=1)
        page.locators["#ctl00_ContentPlaceHolder1_ddlCity"] = city_ddl
        page.locators["#ctl00_ContentPlaceHolder1_ddlBranch"] = branch_ddl
        page.locators["#rbtnGheirHozoori"] = online_radio
        page.locators["#rbtnHozoori"] = in_person_radio

        page.city_options = [
            {"v": "0", "t": "-- انتخاب کنيد --", "s": False},
            {"v": "793", "t": "مهرستان", "s": True},
        ]
        page.bank_options = [
            {"value": "0", "text": "-- انتخاب کنيد --", "selected": True},
            {"value": "20", "text": "توسعه صادرات  [در انتظار تخصیص ظرفیت جدید (ذخیره)] - متقاضی در صف : 18 نفر", "selected": False},
            {"value": "13", "text": "بانک رفاه کارگران [دارای ظرفیت ثبت نام] - متقاضی در صف : 2696 نفر", "selected": False},
            {"value": "19", "text": "بانک صادرات [دارای ظرفیت ثبت نام] - متقاضی در صف : 4259 نفر", "selected": False},
        ]
        page.mode_controls = [
            {"kind": "radio", "id": "rbtnGheirHozoori", "name": "mode", "label": "غیرحضوری", "checked": False, "disabled": False},
            {"kind": "radio", "id": "rbtnHozoori", "name": "mode", "label": "حضوری", "checked": False, "disabled": False},
        ]

        def on_online_click(**kwargs):
            online_radio.clicks += 1
            page.mode_controls[0]["checked"] = True

        online_radio.click = on_online_click

        res = process_bank_selection_v2(bot, page, stop_event)
        self.assertEqual(res, "option_selected_waiting_user")
        self.assertEqual(page.selected_bank, "19")
        self.assertEqual(online_radio.clicks, 1)
        self.assertEqual(in_person_radio.clicks, 0)
        self.assertIn(page.selected_branch, (None, "0"))

        events = [e.get("event_code") for e in EVENT_BROADCASTER.event_history if e.get("event_code")]
        for expected_ev in (
            "BANK_PREFERENCES_LOADED",
            "BANK_OPTION_FOUND",
            "BANK_OPTION_SELECTED",
            "BANK_SELECTION_VERIFIED",
            "BANK_MODE_SELECTED",
            "BANK_MODE_VERIFIED",
            "BANK_FORM_READY",
        ):
            self.assertIn(expected_ev, events)

    def test_18_in_person_mode_branch_priority_and_reserve_bank_toggle(self):
        """Scenario 18: In-person (حضوری) mode selects preferred branch and emits BRANCH_SELECTED; reserve bank only selected when allow_reserve_bank=True."""
        DBHandler.update_applicant_data(
            self.nid,
            {
                "state": "سیستان و بلوچستان",
                "city": "مهرستان",
                "priority_banks": ["بانک ملت"],
                "bank_mode": "حضوری",
                "priority_branches": ["شعبه مرکزی مهرستان", "102"],
            },
        )
        settings = DBHandler.get_config()
        settings["allow_reserve_bank"] = False
        settings["auto_final_submit"] = False
        bot = BankSelectionBot(self.nid, settings)
        page = MockWebFormsPage()
        page.locators["#ctl00_ContentPlaceHolder1_ddlBankName"] = MockLocator(visible=True, count=1)
        page.locators["#ctl00_ContentPlaceHolder1_ddlBranch"] = MockLocator(visible=True, enabled=True, count=1)
        page.bank_options = [
            {"value": "12", "text": "بانک ملت [در انتظار تخصیص ظرفیت جدید (ذخیره)] - متقاضی در صف : 2165 نفر", "selected": False},
        ]
        page.branch_options = [
            {"value": "0", "text": "-- انتخاب کنید --", "selected": True},
            {"value": "101", "text": "شعبه آزادی", "selected": False},
            {"value": "102", "text": "شعبه مرکزی مهرستان", "selected": False},
        ]

        stop_event = threading.Event()
        res_no_reserve = process_bank_selection_v2(bot, page, stop_event)
        self.assertEqual(res_no_reserve, "no_match")
        events1 = [e.get("event_code") for e in EVENT_BROADCASTER.event_history if e.get("event_code")]
        self.assertIn("BANK_SELECTION_FAILED", events1)
        self.assertIn("BANK_OPTION_NOT_FOUND", events1)

        EVENT_BROADCASTER.event_history.clear()
        EVENT_BROADCASTER._last_log_by_nid.clear()
        bot.settings["allow_reserve_bank"] = True
        res_with_reserve = process_bank_selection_v2(bot, page, stop_event)
        self.assertEqual(res_with_reserve, "option_selected_waiting_user")
        self.assertEqual(page.selected_bank, "12")
        self.assertEqual(page.selected_branch, "102")
        events2 = [e.get("event_code") for e in EVENT_BROADCASTER.event_history if e.get("event_code")]
        self.assertIn("BRANCH_SELECTED", events2)
        self.assertIn("BANK_FORM_READY", events2)

    def test_19_compact_browser_window_top_right_and_no_maximize(self):
        """Scenario 19: Browser opens in a compact 540x680 window at top_right of primary work area, clamps on small screens, and never maximizes."""
        def_prof = browser_launcher.get_default_browser_profile()
        self.assertFalse(def_prof["headless"])
        self.assertEqual(def_prof["window_width"], 540)
        self.assertEqual(def_prof["window_height"], 680)
        self.assertEqual(def_prof["window_position"], "top_right")
        self.assertFalse(def_prof["start_maximized"])

        geom = browser_launcher.compute_window_geometry(def_prof, work_area=(0, 0, 1920, 1040))
        self.assertEqual(geom["width"], 540)
        self.assertEqual(geom["height"], 680)
        self.assertEqual(geom["x"], 1920 - 540 - 12)
        self.assertEqual(geom["y"], 12)

        small_geom = browser_launcher.compute_window_geometry(def_prof, work_area=(0, 0, 800, 600))
        self.assertEqual(small_geom["width"], 540)
        self.assertLessEqual(small_geom["height"], 600 - 24)
        self.assertGreaterEqual(small_geom["x"], 0)
        self.assertLessEqual(small_geom["x"] + small_geom["width"], 800)
        self.assertLessEqual(small_geom["y"] + small_geom["height"], 600)

        playwright = Mock()
        playwright.chromium.launch.return_value.new_context.return_value.pages = []
        with patch.object(browser_launcher, "sync_playwright") as starter:
            starter.return_value.start.return_value = playwright
            handles = browser_launcher.launch_browser(def_prof)
        launch_kwargs = playwright.chromium.launch.call_args.kwargs
        args = launch_kwargs.get("args", [])
        self.assertTrue(any(a.startswith("--window-size=540,680") for a in args))
        self.assertTrue(any(a.startswith("--window-position=") for a in args))
        self.assertFalse(any(a.startswith("--start-maximized") for a in args))
        self.assertFalse(any(a.startswith("--start-fullscreen") for a in args))
        self.assertFalse(any(a.startswith("--kiosk") for a in args))
        browser_launcher.close_browser(*handles)


if __name__ == "__main__":
    unittest.main()
