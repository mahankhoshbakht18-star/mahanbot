"""
tests/test_unified_bank_cycle_suite.py — 20 Mandatory Offline Scenarios for Unified Bank Selection.

Covers the complete marriage loan bank selection cycle on CBI
(ve.cbi.ir/SelectBnkShb.aspx and SelTasCase.aspx) with isolated test database (cbi_test.db).
"""

import json
import os
import shutil
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import unittest
from unittest.mock import MagicMock, patch


from core.test_database_manager import TestDatabaseManager
from core.unified_bank_engine import EngineState, UnifiedBankEngine, normalize_persian


class MockLocator:
    """Mock Playwright locator for simulating DOM interactions in offline tests."""

    def __init__(self, selector: str = "", exists: bool = True, visible: bool = True, value: str = "", text: str = ""):
        self.selector = selector
        self._exists = exists
        self._visible = visible
        self._value = value
        self._text = text
        self.clicked = False
        self.checked = False

    def count(self) -> int:
        return 1 if self._exists else 0

    @property
    def first(self) -> "MockLocator":
        return self

    def is_visible(self) -> bool:
        return self._visible

    def inner_text(self) -> str:
        return self._text

    def input_value(self) -> str:
        return self._value

    def fill(self, val: str):
        self._value = val

    def click(self):
        self.clicked = True

    def check(self):
        self.checked = True

    def screenshot(self) -> bytes:
        return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"


class MockPage:
    """Mock Playwright page simulating CBI stages."""

    def __init__(self, initial_url: str = "https://ve.cbi.ir/SelectBnkShb.aspx"):
        self.url = initial_url
        self.elements = {}
        self.select_options_log = []
        self._content = "<html><body></body></html>"

    def register(self, selector: str, locator: MockLocator):
        self.elements[selector] = locator

    def locator(self, selector: str) -> MockLocator:
        if selector in self.elements:
            return self.elements[selector]
        for part in [p.strip() for p in selector.split(",")]:
            if part in self.elements:
                return self.elements[part]
        for k, v in self.elements.items():
            if k in selector or selector in k:
                return v
        return MockLocator(selector, exists=False, visible=False)


    def wait_for_selector(self, selector: str, timeout: int = 5000) -> MockLocator:
        loc = self.locator(selector)
        if loc.count() > 0 and loc.is_visible():
            return loc
        raise TimeoutError(f"Timeout waiting for selector: {selector}")

    def fill(self, selector: str, val: str):
        loc = self.locator(selector)
        loc.fill(val)

    def click(self, selector: str):
        loc = self.locator(selector)
        loc.click()

    def input_value(self, selector: str) -> str:
        return self.locator(selector).input_value()

    def select_option(self, selector: str, value: str):
        self.select_options_log.append((selector, value))
        if selector in self.elements:
            self.elements[selector]._value = value

    def evaluate(self, script: str, arg: Any = None) -> Any:
        # Mock options reader for dropdowns
        if "ddlBankName" in str(arg):
            return [
                {"value": "0", "text": "انتخاب کنید..."},
                {"value": "11", "text": "بانک صادرات (ظرفیت دارد)"},
                {"value": "12", "text": "بانک تجارت (تکمیل ظرفیت)"},
                {"value": "13", "text": "بانک ملت (ظرفیت دارد)"},
                {"value": "14", "text": "بانک مسکن (ظرفیت دارد)"},
                {"value": "15", "text": "بانک سپه (ظرفیت دارد)"},
                {"value": "16", "text": "بانک کشاورزی (ظرفیت دارد)"},
                {"value": "17", "text": "بانک سامان (رزرو)"},
            ]

        if "ddlCity" in str(arg):
            return [
                {"value": "0", "text": "انتخاب شهر..."},
                {"value": "101", "text": "تهران"},
                {"value": "102", "text": "مشهد"},
            ]
        if "ddlBranch" in str(arg):
            return [
                {"value": "0", "text": "انتخاب شعبه..."},
                {"value": "5001", "text": "شعبه مرکزی کد ۰۱"},
                {"value": "5002", "text": "شعبه انقلاب کد ۰۲"},
            ]
        return []

    def content(self) -> str:
        return self._content

    def set_content(self, html: str):
        self._content = html

    def goto(self, url: str, **kwargs):
        self.url = url


class TestUnifiedBankCycle20Scenarios(unittest.TestCase):
    """The 20 Mandatory Offline Mock Test Scenarios."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_cycle.db")
        self.db = TestDatabaseManager(self.db_path)
        self.db.init_database(force_reseed=True)

        self.engine = UnifiedBankEngine(db_path=self.db_path, headless=True)
        # Patch solver with mock predicting clean codes
        self.engine.solver = MagicMock()
        self.engine.solver.solve.return_value = "8742A"

    def tearDown(self):
        self.engine.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # Scenario 1: Correct NID Entry
    # --------------------------------------------------------------------------
    def test_01_correct_nid_entry(self):
        page = MockPage("https://ve.cbi.ir/SelectBnkShb.aspx")
        page.register("#ctl00_ContentPlaceHolder1_tbIDNo", MockLocator("#ctl00_ContentPlaceHolder1_tbIDNo"))
        page.register("#ctl00_ContentPlaceHolder1_botdetectcaptcha_CaptchaImage", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_tbCaptcha", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_btnLogin", MockLocator())

        applicant = self.db.get_applicant("0010000001")

        with patch.object(self.engine, "_is_on_otp_page", side_effect=[False, True]):
            result = self.engine._execute_step1(applicant, page)

        self.assertTrue(result)
        self.assertEqual(page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").input_value(), "0010000001")

    # --------------------------------------------------------------------------
    # Scenario 2: Captcha 1 Failure & Recovery
    # --------------------------------------------------------------------------
    def test_02_captcha1_failure_and_recovery(self):
        page = MockPage("https://ve.cbi.ir/SelectBnkShb.aspx")
        page.register("#ctl00_ContentPlaceHolder1_tbIDNo", MockLocator("#ctl00_ContentPlaceHolder1_tbIDNo"))
        page.register("#ctl00_ContentPlaceHolder1_botdetectcaptcha_CaptchaImage", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_tbCaptcha", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_btnLogin", MockLocator())
        reload_loc = MockLocator("#ctl00_ContentPlaceHolder1_botdetectcaptcha_ReloadIcon")
        page.register("#ctl00_ContentPlaceHolder1_botdetectcaptcha_ReloadIcon", reload_loc)

        applicant = self.db.get_applicant("0010000001")

        # First attempt solver returns None, second attempt solver returns valid code
        self.engine.solver.solve.side_effect = [None, "99B7C"]

        with patch.object(self.engine, "_is_on_otp_page", side_effect=[False, True]):
            result = self.engine._execute_step1(applicant, page)

        self.assertTrue(result)
        self.assertTrue(reload_loc.clicked)
        self.assertEqual(page.locator("#ctl00_ContentPlaceHolder1_tbCaptcha").input_value(), "99B7C")


    # --------------------------------------------------------------------------
    # Scenario 3: OTP Receipt from Relay / Database
    # --------------------------------------------------------------------------
    def test_03_otp_receipt_from_relay_and_injected(self):
        page = MockPage("https://ve.cbi.ir/SelTasCase.aspx")
        page.register("#ctl00_ContentPlaceHolder1_tbMobileConfCode", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_botdetectcaptcha2_CaptchaImage", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_tbCaptcha2", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_btnContinue1", MockLocator())

        applicant = self.db.get_applicant("0010000001")
        self.db.set_otp("0010000001", "765432")

        with patch.object(self.engine, "_is_on_page3", side_effect=[False, True]):
            result = self.engine._execute_step2(applicant, page)

        self.assertTrue(result)
        self.assertEqual(page.locator("#ctl00_ContentPlaceHolder1_tbMobileConfCode").input_value(), "765432")

    # --------------------------------------------------------------------------
    # Scenario 4: Missing Captcha 2 Handled Gracefully
    # --------------------------------------------------------------------------
    def test_04_missing_captcha2_handled_gracefully(self):
        page = MockPage("https://ve.cbi.ir/SelTasCase.aspx")
        page.register("#ctl00_ContentPlaceHolder1_tbMobileConfCode", MockLocator(value="123456"))
        # Captcha image initially missing, then appears
        missing_cap = MockLocator(exists=False, visible=False)
        reload_icon = MockLocator()
        page.register("#ctl00_ContentPlaceHolder1_botdetectcaptcha2_CaptchaImage", missing_cap)
        page.register("#ctl00_ContentPlaceHolder1_botdetectcaptcha2_ReloadIcon", reload_icon)
        page.register("#ctl00_ContentPlaceHolder1_tbCaptcha2", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_btnContinue1", MockLocator())

        applicant = self.db.get_applicant("0010000001")

        # solver first returns None (missing image), then returns code
        self.engine.solver.solve.side_effect = [None, "CAP22"]
        with patch.object(self.engine, "_is_on_page3", side_effect=[False, False, True]):
            result = self.engine._execute_step2(applicant, page)

        self.assertTrue(result)
        self.assertTrue(reload_icon.clicked)

    # --------------------------------------------------------------------------
    # Scenario 5: Manual Confirmation of Step 2 + Auto-continuation (Freeze Fix)
    # --------------------------------------------------------------------------
    def test_05_manual_confirmation_auto_continues_without_freeze(self):
        page = MockPage("https://ve.cbi.ir/SelTasCase.aspx")
        page.register("#ctl00_ContentPlaceHolder1_tbMobileConfCode", MockLocator())

        applicant = self.db.get_applicant("0010000001")
        # No OTP in DB triggers manual wait
        # Mock _is_on_page3 to return False twice, then True (simulating user manually submitting)
        with patch.object(self.engine, "_is_on_page3", side_effect=[False, False, True]):
            with patch("time.sleep", return_value=None):
                result = self.engine._wait_for_manual_step2_handoff(page, "0010000001")

        self.assertTrue(result)
        # Verify applicant status updated to require manual intervention
        app = self.db.get_applicant("0010000001")
        self.assertEqual(app["status"], "نیاز به تأیید دستی")

    # --------------------------------------------------------------------------
    # Scenario 6: Valid Entry to Page 3
    # --------------------------------------------------------------------------
    def test_06_valid_entry_to_page3(self):
        page = MockPage("https://ve.cbi.ir/SelectBnkShb.aspx")
        page.register("#ctl00_ContentPlaceHolder1_ddlBankName", MockLocator())
        self.assertTrue(self.engine._is_on_page3(page))

    # --------------------------------------------------------------------------
    # Scenario 7: Priority Bank Present & Active Selection
    # --------------------------------------------------------------------------
    def test_07_priority_bank_present_active_selection(self):
        page = MockPage("https://ve.cbi.ir/SelectBnkShb.aspx")
        page.register("#ctl00_ContentPlaceHolder1_ddlBankName", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_ddlCity", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_rbOnline", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_btnSubmit", MockLocator())
        page.set_content("<div>کد رهگیری: TRK-987654</div>")

        applicant = self.db.get_applicant("0010000001")  # priority_banks: ["بانک صادرات", "بانک تجارت"], auto_final_submit: True
        result = self.engine._execute_page3(applicant, page)

        self.assertTrue(result)
        # Verify bank 11 (صادرات) was selected
        self.assertIn(("#ctl00_ContentPlaceHolder1_ddlBankName", "11"), page.select_options_log)
        # Verify tracking code saved
        saved = self.db.get_applicant("0010000001")
        self.assertEqual(saved["status"], "ثبت قطعی")
        self.assertEqual(saved["tracking_code"], "TRK-987654")

    # --------------------------------------------------------------------------
    # Scenario 8: Priority Bank Absent + Controlled Wait
    # --------------------------------------------------------------------------
    def test_08_priority_bank_absent_controlled_wait(self):
        page = MockPage("https://ve.cbi.ir/SelectBnkShb.aspx")
        page.register("#ctl00_ContentPlaceHolder1_ddlBankName", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_ddlCity", MockLocator())

        applicant = self.db.get_applicant("0010000004")  # priority: ["بانک مهر ایران", "بانک رسالت"], not in mock dropdown
        result = self.engine._execute_page3(applicant, page)

        self.assertFalse(result)
        app = self.db.get_applicant("0010000004")
        self.assertEqual(app["status"], "عدم ظرفیت بانک")

    # --------------------------------------------------------------------------
    # Scenario 9: City Selection Without Refresh Trick
    # --------------------------------------------------------------------------
    def test_09_city_selection_without_refresh_trick(self):
        page = MockPage("https://ve.cbi.ir/SelectBnkShb.aspx")
        page.register("#ctl00_ContentPlaceHolder1_ddlCity", MockLocator())

        self.engine._select_city_if_needed(page, "#ctl00_ContentPlaceHolder1_ddlCity", "تهران")
        # Should select city 101 exactly once
        self.assertEqual(len(page.select_options_log), 1)
        self.assertEqual(page.select_options_log[0], ("#ctl00_ContentPlaceHolder1_ddlCity", "101"))

    # --------------------------------------------------------------------------
    # Scenario 10: In-person Selection With First Available Branch
    # --------------------------------------------------------------------------
    def test_10_in_person_selection_with_branch(self):
        page = MockPage("https://ve.cbi.ir/SelectBnkShb.aspx")
        page.register("#ctl00_ContentPlaceHolder1_ddlBankName", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_ddlCity", MockLocator())
        rb_in_person = MockLocator("#ctl00_ContentPlaceHolder1_rbInPerson")
        page.register("#ctl00_ContentPlaceHolder1_rbInPerson", rb_in_person)
        page.register("#ctl00_ContentPlaceHolder1_ddlBranch", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_btnSubmit", MockLocator())
        page.set_content("<div>کد رهگیری: TRK-BRANCH-01</div>")

        applicant = self.db.get_applicant("0010000005")  # mode: in_person, auto_final_submit: True
        result = self.engine._execute_page3(applicant, page)

        self.assertTrue(result)
        self.assertTrue(rb_in_person.checked)
        # Verify branch selected
        branch_selections = [v for sel, v in page.select_options_log if "ddlBranch" in sel]
        self.assertTrue(len(branch_selections) > 0)

    # --------------------------------------------------------------------------
    # Scenario 11: Online Selection Without Branch Wait
    # --------------------------------------------------------------------------
    def test_11_online_selection_without_branch_wait(self):
        page = MockPage("https://ve.cbi.ir/SelectBnkShb.aspx")
        page.register("#ctl00_ContentPlaceHolder1_ddlBankName", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_ddlCity", MockLocator())
        rb_online = MockLocator("#ctl00_ContentPlaceHolder1_rbOnline")
        page.register("#ctl00_ContentPlaceHolder1_rbOnline", rb_online)
        page.register("#ctl00_ContentPlaceHolder1_btnSubmit", MockLocator())
        page.set_content("<div>کد رهگیری: TRK-ONLINE-01</div>")

        applicant = self.db.get_applicant("0010000001")  # mode: online, auto_final_submit: True
        result = self.engine._execute_page3(applicant, page)

        self.assertTrue(result)
        self.assertTrue(rb_online.checked)
        # Verify branch dropdown was never queried or selected
        branch_selections = [v for sel, v in page.select_options_log if "ddlBranch" in sel]
        self.assertEqual(len(branch_selections), 0)

    # --------------------------------------------------------------------------
    # Scenario 12: Final Submit Toggle OFF (Ready Confirmation Stop)
    # --------------------------------------------------------------------------
    def test_12_final_submit_toggle_off_halts_at_ready_confirmation(self):
        page = MockPage("https://ve.cbi.ir/SelectBnkShb.aspx")
        page.register("#ctl00_ContentPlaceHolder1_ddlBankName", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_ddlCity", MockLocator())
        btn_submit = MockLocator("#ctl00_ContentPlaceHolder1_btnSubmit")
        page.register("#ctl00_ContentPlaceHolder1_btnSubmit", btn_submit)

        applicant = self.db.get_applicant("0010000002")  # auto_final_submit: False
        # Stop signal after reaching ready state to finish test cleanly
        def trigger_stop():
            for _ in range(30):
                if self.engine.state == EngineState.READY_FOR_MANUAL_CONFIRMATION:
                    break
                time.sleep(0.1)
            self.assertEqual(self.engine.state, EngineState.READY_FOR_MANUAL_CONFIRMATION)
            self.engine.stop()

        t = threading.Thread(target=trigger_stop)
        t.start()
        self.engine._execute_page3(applicant, page)
        t.join()


        # Submit button must NOT have been clicked!
        self.assertFalse(btn_submit.clicked)
        saved = self.db.get_applicant("0010000002")
        self.assertEqual(saved["status"], "آماده تأیید نهایی")

    # --------------------------------------------------------------------------
    # Scenario 13: Final Submit Toggle ON (Safe Submission)
    # --------------------------------------------------------------------------
    def test_13_final_submit_toggle_on_executes_submission(self):
        page = MockPage("https://ve.cbi.ir/SelectBnkShb.aspx")
        page.register("#ctl00_ContentPlaceHolder1_ddlBankName", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_ddlCity", MockLocator())
        btn_submit = MockLocator("#ctl00_ContentPlaceHolder1_btnSubmit")
        page.register("#ctl00_ContentPlaceHolder1_btnSubmit", btn_submit)
        page.set_content("<div>با موفقیت ثبت شد. کد پیگیری: ABC12345</div>")

        applicant = self.db.get_applicant("0010000003")  # auto_final_submit: True
        result = self.engine._execute_page3(applicant, page)

        self.assertTrue(result)
        self.assertTrue(btn_submit.clicked)
        saved = self.db.get_applicant("0010000003")
        self.assertEqual(saved["status"], "ثبت قطعی")
        self.assertEqual(saved["tracking_code"], "ABC12345")

    # --------------------------------------------------------------------------
    # Scenario 14: Page 3 Reload Returning to OTP with Prefilled Code
    # --------------------------------------------------------------------------
    def test_14_page3_reload_retains_prefilled_otp(self):
        # Simulate Page 3 reload redirecting to OTP with prefilled code
        page = MockPage("https://ve.cbi.ir/SelTasCase.aspx")
        otp_field = MockLocator("#ctl00_ContentPlaceHolder1_tbMobileConfCode", value="987654")
        page.register("#ctl00_ContentPlaceHolder1_tbMobileConfCode", otp_field)
        page.register("#ctl00_ContentPlaceHolder1_botdetectcaptcha2_CaptchaImage", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_tbCaptcha2", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_btnContinue1", MockLocator())

        applicant = self.db.get_applicant("0010000001")
        # DB has NO otp set!
        self.assertIsNone(self.db.get_otp("0010000001"))

        with patch.object(self.engine, "_is_on_page3", side_effect=[False, True]):
            result = self.engine._execute_step2(applicant, page)

        self.assertTrue(result)
        self.assertEqual(self.engine.state, EngineState.STEP2_SUBMITTING)
        # Prefilled value was preserved
        self.assertEqual(otp_field.input_value(), "987654")


    # --------------------------------------------------------------------------
    # Scenario 15: Captcha Solve on Prefilled OTP Returning to Page 3
    # --------------------------------------------------------------------------
    def test_15_captcha_solve_on_prefilled_otp_returns_to_page3(self):
        page = MockPage("https://ve.cbi.ir/SelTasCase.aspx")
        page.register("#ctl00_ContentPlaceHolder1_tbMobileConfCode", MockLocator(value="554433"))
        page.register("#ctl00_ContentPlaceHolder1_botdetectcaptcha2_CaptchaImage", MockLocator())
        cap_input = MockLocator("#ctl00_ContentPlaceHolder1_tbCaptcha2")
        page.register("#ctl00_ContentPlaceHolder1_tbCaptcha2", cap_input)
        btn_continue = MockLocator("#ctl00_ContentPlaceHolder1_btnContinue1")
        page.register("#ctl00_ContentPlaceHolder1_btnContinue1", btn_continue)

        applicant = self.db.get_applicant("0010000001")
        self.engine.solver.solve.return_value = "CP998"

        with patch.object(self.engine, "_is_on_page3", side_effect=[False, True]):
            result = self.engine._execute_step2(applicant, page)

        self.assertTrue(result)
        self.assertEqual(cap_input.input_value(), "CP998")
        self.assertTrue(btn_continue.clicked)

    # --------------------------------------------------------------------------
    # Scenario 16: Server-side OTP Expiration Returning to Step 1
    # --------------------------------------------------------------------------
    def test_16_server_otp_expiration_returns_to_step1(self):
        page = MockPage("https://ve.cbi.ir/SelTasCase.aspx")
        page.register("#ctl00_ContentPlaceHolder1_tbMobileConfCode", MockLocator(value="112233"))
        page.register("#ctl00_ContentPlaceHolder1_botdetectcaptcha2_CaptchaImage", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_tbCaptcha2", MockLocator())
        page.register("#ctl00_ContentPlaceHolder1_btnContinue1", MockLocator())
        # Error label reports expiration
        page.register("#ctl00_ContentPlaceHolder1_labError", MockLocator(text="کد پیامک منقضی شده است"))

        applicant = self.db.get_applicant("0010000001")

        with patch.object(self.engine, "_is_on_page3", return_value=False):
            result = self.engine._execute_step2(applicant, page)

        self.assertFalse(result)
        self.assertEqual(self.engine.state, EngineState.OTP_EXPIRED_RESTART)

    # --------------------------------------------------------------------------
    # Scenario 17: Duplicate Submission Prevention
    # --------------------------------------------------------------------------
    def test_17_duplicate_submission_prevention(self):
        # Mark applicant as already confirmed
        self.db.save_tracking_code("0010000001", "ALREADY-CONFIRMED")
        applicant = self.db.get_applicant("0010000001")
        self.assertEqual(applicant["status"], "ثبت قطعی")

        with patch.object(self.engine, "_process_single_applicant") as mock_proc:
            self.engine._run_orchestrator(specific_nid="0010000001")
            # Process single applicant must NOT be called for already confirmed applicant
            mock_proc.assert_not_called()

    # --------------------------------------------------------------------------
    # Scenario 18: Stop / Pause / Resume Lifecycle Without Rogue Processes
    # --------------------------------------------------------------------------
    def test_18_stop_pause_resume_lifecycle(self):
        self.assertEqual(self.engine.state, EngineState.IDLE)
        self.engine.pause()
        self.assertEqual(self.engine.state, EngineState.PAUSED)
        self.engine.resume()
        self.engine.stop()
        self.assertEqual(self.engine.state, EngineState.STOPPED)
        # Browser resources cleanly cleaned up
        self.assertIsNone(self.engine._page)
        self.assertIsNone(self.engine._context)

    # --------------------------------------------------------------------------
    # Scenario 19: Applicant Session Isolation
    # --------------------------------------------------------------------------
    def test_19_applicant_session_isolation(self):
        # Verifies that processing resets context between applicants
        with patch.object(self.engine, "_process_single_applicant", return_value=True):
            with patch.object(self.engine, "_close_browser") as mock_close:
                self.engine._run_orchestrator()
                # Close browser called after each processed applicant
                self.assertTrue(mock_close.call_count >= 1)

    # --------------------------------------------------------------------------
    # Scenario 20: One-Button Execution via API
    # --------------------------------------------------------------------------
    def test_20_one_button_execution_via_api(self):
        from fastapi.testclient import TestClient
        import server

        client = TestClient(server.app)
        start_res = client.post("/api/unified/start", json={})
        self.assertEqual(start_res.status_code, 200)
        self.assertTrue(start_res.json()["success"])

        status_res = client.get("/api/unified/status")
        self.assertEqual(status_res.status_code, 200)
        status_data = status_res.json()
        self.assertTrue(status_data["success"])
        self.assertIn("state", status_data["status"])

        # Stop engine cleanly
        stop_res = client.post("/api/unified/stop")
        self.assertEqual(stop_res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
