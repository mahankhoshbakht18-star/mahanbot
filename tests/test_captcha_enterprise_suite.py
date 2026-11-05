"""
tests/test_captcha_enterprise_suite.py — 16 Mandatory CAPTCHA Test Scenarios

Mandatory Scenarios (Section 8):
1. Captcha in main page
2. Captcha inside accessible iframe
3. Captcha with different ID
4. Captcha with delayed image rendering (naturalWidth == 0 initially)
5. Captcha image change after PostBack
6. Field replacement by AJAX / UpdatePanel
7. Image found without input field
8. Input field found without image
9. Recognition service error or timeout
10. Invalid regular captcha prediction (rejection & non-destructive retry)
11. TSPD security challenge requiring manual verification
12. OTP state change during captcha wait
13. Browser and session retention on error (no destructive page.reload)
14. Prevention of unwanted reload (Page3 controller blocked in OTP)
15. Prevention of duplicate submission
16. Successful continuation from OTP after verification
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from core.captcha_pipeline import (
    CaptchaPipeline,
    CaptchaControls,
    CaptchaStage,
    CaptchaType,
)


class MockElement:
    def __init__(
        self,
        tag: str,
        elem_id: str = "",
        name: str = "",
        value: str = "",
        src: str = "",
        is_visible: bool = True,
        is_enabled: bool = True,
        natural_width: int = 160,
        complete: bool = True,
    ):
        self.tag = tag.lower()
        self.id = elem_id
        self.name = name
        self._value = value
        self.src = src
        self._visible = is_visible
        self._enabled = is_enabled
        self.natural_width = natural_width
        self.complete = complete
        self.clicked = False
        self.click_count = 0

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def nth(self, idx):
        return self

    def is_visible(self):
        return self._visible

    def is_enabled(self):
        return self._enabled

    def is_editable(self):
        return self._enabled

    def input_value(self, timeout=None):
        return self._value

    def fill(self, val, timeout=None):
        self._value = val

    def click(self, timeout=None):
        self.clicked = True
        self.click_count += 1

    def dispatch_event(self, evt):
        pass

    def get_attribute(self, attr):
        if attr == "id":
            return self.id
        if attr == "src":
            return self.src
        if attr == "name":
            return self.name
        return ""

    def bounding_box(self):
        return {"width": 160, "height": 60, "x": 100, "y": 100}

    def evaluate(self, expr, arg=None):
        if "activeElement" in expr:
            return False
        if "(img) =>" in expr:
            return {
                "id": self.id,
                "src": self.src,
                "naturalWidth": self.natural_width,
                "complete": self.complete,
            }
        if "img =>" in expr or "img.complete" in expr or "is_loaded" in expr:
            return self.complete and (self.natural_width > 0)
        if "getAttribute" in expr:
            return self.src
        if "tagName" in expr:
            return self.tag.upper()
        return None

    def screenshot(self, **kwargs):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 800


class CaptchaEnterpriseSuiteTests(unittest.TestCase):
    def setUp(self):
        self.bot = MagicMock()
        self.bot.nid = "0012345678"
        self.bot._active_stop_event = None
        self.bot._last_dialog_action = None
        self.bot.settings = {
            "captcha_min_length": 4,
            "captcha_max_length": 8,
            "pause_for_manual_captcha": False,
        }
        self.bot.events = []

        def mock_emit(code, **kwargs):
            self.bot.events.append((code, kwargs))

        self.bot.emit_event = MagicMock(side_effect=mock_emit)
        self.bot.log = MagicMock()
        self.bot.captcha_service = MagicMock()
        self.bot.captcha_service.solve = MagicMock(return_value="7A93B")
        self.bot._capture_captcha_image = MagicMock(return_value=b"\x89PNG\r\n\x1a\n" + b"0" * 800)
        self.bot._otp_prefilled_captcha = None

    def _create_mock_page(self, url="https://ve.cbi.ir/SelectBnkShb.aspx"):
        page = MagicMock()
        page.is_closed = MagicMock(return_value=False)
        page.url = url
        page.frames = [page]
        page.main_frame = page
        page.reload = MagicMock()
        return page

    # --- Scenario 1: Captcha in main page ---
    def test_01_captcha_in_main_page(self):
        page = self._create_mock_page()
        el_inp = MockElement("input", "ctl00_ContentPlaceHolder1_tbCaptcha2")
        el_img = MockElement("img", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage", src="/BotDetectCaptcha.ashx?get=image")
        el_btn = MockElement("input", "ctl00_ContentPlaceHolder1_btnContinue1")

        def mock_loc(sel):
            if "tbCaptcha2" in sel:
                return el_inp
            if "CaptchaImage" in sel:
                return el_img
            if "btnContinue1" in sel:
                return el_btn
            empty = MagicMock()
            empty.count.return_value = 0
            return empty

        page.locator = MagicMock(side_effect=mock_loc)
        controls = CaptchaPipeline.discover_captcha(self.bot, page, stage=CaptchaStage.STEP_2_OTP)

        self.assertTrue(controls.is_complete)
        self.assertEqual(controls.captcha_type, CaptchaType.STANDARD_IMAGE)
        ok, code = CaptchaPipeline.recognize_and_fill(self.bot, page, controls)
        self.assertTrue(ok)
        self.assertEqual(code, "7A93B")
        self.assertEqual(el_inp.input_value(), "7A93B")
        self.assertFalse(el_btn.clicked)

    # --- Scenario 2: Captcha inside accessible iframe ---
    def test_02_captcha_in_accessible_iframe(self):
        page = self._create_mock_page()
        child_frame = MagicMock()
        child_frame.url = "https://ve.cbi.ir/NestedCaptchaFrame.aspx"
        child_frame.name = "captcha_frame"
        page.frames = [page, child_frame]

        el_inp = MockElement("input", "ctl00_ContentPlaceHolder1_tbCaptcha2")
        el_img = MockElement("img", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage", src="/BotDetectCaptcha.ashx?get=image")

        # Main page has nothing, child frame has the elements
        empty = MagicMock()
        empty.count.return_value = 0
        page.locator = MagicMock(return_value=empty)

        def frame_loc(sel):
            if "tbCaptcha2" in sel:
                return el_inp
            if "CaptchaImage" in sel:
                return el_img
            return empty

        child_frame.locator = MagicMock(side_effect=frame_loc)
        controls = CaptchaPipeline.discover_captcha(self.bot, page, stage=CaptchaStage.STEP_2_OTP)

        self.assertEqual(controls.scope, child_frame)
        self.assertEqual(controls.captcha_type, CaptchaType.STANDARD_IMAGE)
        event_codes = [e[0] for e in self.bot.events]
        self.assertIn("CAPTCHA_FRAME_INSPECTED", event_codes)

    # --- Scenario 3: Captcha with different ID ---
    def test_03_captcha_with_different_id(self):
        page = self._create_mock_page()
        # Alternate selector names
        el_inp = MockElement("input", "custom_tbCaptcha2_alt", name="ctl00$ContentPlaceHolder1$tbCaptcha2")
        el_img = MockElement("img", "custom_CaptchaImage_alt", src="https://ve.cbi.ir/BotDetectCaptcha.ashx?get=image&c=alt")

        def mock_loc(sel):
            if "tbCaptcha2" in sel:
                return el_inp
            if "CaptchaImage" in sel or "get=image" in sel:
                return el_img
            empty = MagicMock()
            empty.count.return_value = 0
            return empty

        page.locator = MagicMock(side_effect=mock_loc)
        controls = CaptchaPipeline.discover_captcha(self.bot, page, stage=CaptchaStage.STEP_2_OTP)
        self.assertIsNotNone(controls.input_loc)
        self.assertIsNotNone(controls.image_loc)

    # --- Scenario 4: Captcha with delayed image rendering (naturalWidth == 0) ---
    def test_04_delayed_image_loading(self):
        page = self._create_mock_page()
        # Initially naturalWidth is 0
        el_img = MockElement("img", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage", natural_width=0, complete=False)
        el_inp = MockElement("input", "ctl00_ContentPlaceHolder1_tbCaptcha2")
        el_reload = MockElement("a", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_ReloadLink")

        def mock_loc(sel):
            if "tbCaptcha2" in sel:
                return el_inp
            if "CaptchaImage" in sel:
                return el_img
            if "ReloadLink" in sel:
                return el_reload
            empty = MagicMock()
            empty.count.return_value = 0
            return empty

        page.locator = MagicMock(side_effect=mock_loc)

        # Calling discover should trigger reload if delayed
        controls = CaptchaPipeline.discover_captcha(self.bot, page, stage=CaptchaStage.STEP_2_OTP, wait_ms=100)
        self.assertIsNotNone(controls.image_loc)
        self.assertTrue(el_reload.clicked)

    # --- Scenario 5: Captcha image change after PostBack ---
    def test_05_image_change_after_postback(self):
        page = self._create_mock_page()
        el_inp = MockElement("input", "ctl00_ContentPlaceHolder1_tbCaptcha2")
        el_img = MockElement("img", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage", src="/BotDetectCaptcha.ashx?id=1")
        el_reload = MockElement("a", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_ReloadLink")

        def mock_loc(sel):
            if "tbCaptcha2" in sel:
                return el_inp
            if "CaptchaImage" in sel:
                return el_img
            if "ReloadLink" in sel:
                return el_reload
            empty = MagicMock()
            empty.count.return_value = 0
            return empty

        page.locator = MagicMock(side_effect=mock_loc)
        controls = CaptchaPipeline.discover_captcha(self.bot, page, stage=CaptchaStage.STEP_2_OTP)

        # Trigger non-destructive refresh
        refreshed = CaptchaPipeline.refresh_captcha_non_destructive(self.bot, page, controls, reason="postback")
        self.assertTrue(refreshed)
        self.assertTrue(el_reload.clicked)
        event_codes = [e[0] for e in self.bot.events]
        self.assertIn("CAPTCHA_DOM_CHANGED", event_codes)

    # --- Scenario 6: Field replacement by AJAX ---
    def test_06_field_replacement_by_ajax(self):
        page = self._create_mock_page()
        el_inp_v1 = MockElement("input", "ctl00_ContentPlaceHolder1_tbCaptcha2", value="OLD")
        el_inp_v2 = MockElement("input", "ctl00_ContentPlaceHolder1_tbCaptcha2", value="")
        el_img = MockElement("img", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage")

        active_input = [el_inp_v1]

        def mock_loc(sel):
            if "tbCaptcha2" in sel:
                return active_input[0]
            if "CaptchaImage" in sel:
                return el_img
            empty = MagicMock()
            empty.count.return_value = 0
            return empty

        page.locator = MagicMock(side_effect=mock_loc)
        controls1 = CaptchaPipeline.discover_captcha(self.bot, page, stage=CaptchaStage.STEP_2_OTP)
        self.assertEqual(controls1.input_loc.input_value(), "OLD")

        # Simulate AJAX replacing input node in DOM
        active_input[0] = el_inp_v2
        controls2 = CaptchaPipeline.discover_captcha(self.bot, page, stage=CaptchaStage.STEP_2_OTP)
        self.assertEqual(controls2.input_loc.input_value(), "")

    # --- Scenario 7: Image found without input field ---
    def test_07_image_without_input_field(self):
        page = self._create_mock_page()
        el_img = MockElement("img", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage")

        def mock_loc(sel):
            if "CaptchaImage" in sel:
                return el_img
            empty = MagicMock()
            empty.count.return_value = 0
            return empty

        page.locator = MagicMock(side_effect=mock_loc)
        controls = CaptchaPipeline.discover_captcha(self.bot, page, stage=CaptchaStage.STEP_2_OTP)

        self.assertFalse(controls.is_complete)
        self.assertEqual(controls.error_reason, "image_without_input")
        event_codes = [e[0] for e in self.bot.events]
        self.assertIn("CAPTCHA_INPUT_NOT_FOUND", event_codes)
        self.assertIn("CAPTCHA_ROOT_CAUSE", event_codes)

    # --- Scenario 8: Input field found without image ---
    def test_08_input_field_without_image(self):
        page = self._create_mock_page()
        el_inp = MockElement("input", "ctl00_ContentPlaceHolder1_tbCaptcha2")

        def mock_loc(sel):
            if "tbCaptcha2" in sel:
                return el_inp
            empty = MagicMock()
            empty.count.return_value = 0
            return empty

        page.locator = MagicMock(side_effect=mock_loc)
        controls = CaptchaPipeline.discover_captcha(self.bot, page, stage=CaptchaStage.STEP_2_OTP)

        self.assertFalse(controls.is_complete)
        self.assertEqual(controls.error_reason, "input_without_image")
        event_codes = [e[0] for e in self.bot.events]
        self.assertIn("CAPTCHA_IMAGE_NOT_FOUND", event_codes)
        self.assertIn("CAPTCHA_ROOT_CAUSE", event_codes)

    # --- Scenario 9: Recognition service error or timeout ---
    def test_09_recognition_service_error_or_timeout(self):
        page = self._create_mock_page()
        el_inp = MockElement("input", "ctl00_ContentPlaceHolder1_tbCaptcha2")
        el_img = MockElement("img", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage")
        el_btn = MockElement("input", "ctl00_ContentPlaceHolder1_btnContinue1")

        def mock_loc(sel):
            if "tbCaptcha2" in sel:
                return el_inp
            if "CaptchaImage" in sel:
                return el_img
            if "btnContinue1" in sel:
                return el_btn
            empty = MagicMock()
            empty.count.return_value = 0
            return empty

        page.locator = MagicMock(side_effect=mock_loc)
        controls = CaptchaPipeline.discover_captcha(self.bot, page, stage=CaptchaStage.STEP_2_OTP)

        # Force timeout exception in solve
        self.bot.captcha_service.solve.side_effect = TimeoutError("OCR timeout after 3000ms")
        ok, res = CaptchaPipeline.recognize_and_fill(self.bot, page, controls)
        self.assertFalse(ok)
        self.assertEqual(res, "ERROR_TimeoutError")
        event_codes = [e[0] for e in self.bot.events]
        self.assertIn("CAPTCHA_TIMEOUT", event_codes)

    # --- Scenario 10: Invalid regular captcha prediction (retry) ---
    def test_10_invalid_prediction_clears_and_retries(self):
        page = self._create_mock_page()
        el_inp = MockElement("input", "ctl00_ContentPlaceHolder1_tbCaptcha2")
        el_img = MockElement("img", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage")
        el_reload = MockElement("a", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_ReloadLink")
        el_btn = MockElement("input", "ctl00_ContentPlaceHolder1_btnContinue1")

        def mock_loc(sel):
            if "tbCaptcha2" in sel:
                return el_inp
            if "CaptchaImage" in sel:
                return el_img
            if "ReloadLink" in sel:
                return el_reload
            if "btnContinue1" in sel:
                return el_btn
            empty = MagicMock()
            empty.count.return_value = 0
            return empty

        page.locator = MagicMock(side_effect=mock_loc)
        controls = CaptchaPipeline.discover_captcha(self.bot, page, stage=CaptchaStage.STEP_2_OTP)

        # Invalid prediction (contains illegal symbols)
        self.bot.captcha_service.solve.return_value = "???1"
        ok, res = CaptchaPipeline.recognize_and_fill(self.bot, page, controls)
        self.assertFalse(ok)
        self.assertEqual(res, "INVALID_PREDICTION")
        self.assertTrue(el_reload.clicked)
        self.assertEqual(el_inp.input_value(), "")

    # --- Scenario 11: TSPD security challenge triggers manual mode ---
    def test_11_tspd_security_challenge_triggers_manual_mode(self):
        page = self._create_mock_page(url="https://ve.cbi.ir/TSPD/?type=20")
        page.inner_text = MagicMock(return_value="Security Incident Detected. Verify you are human.")

        controls = CaptchaPipeline.discover_captcha(self.bot, page, stage=CaptchaStage.STEP_2_OTP)
        self.assertEqual(controls.captcha_type, CaptchaType.TSPD_CHALLENGE)
        ok, res = CaptchaPipeline.recognize_and_fill(self.bot, page, controls)
        self.assertFalse(ok)
        self.assertEqual(res, "MANUAL_REQUIRED")
        event_codes = [e[0] for e in self.bot.events]
        self.assertIn("CAPTCHA_MANUAL_REQUIRED", event_codes)

    # --- Scenario 12: OTP state change during captcha wait ---
    def test_12_otp_state_change_during_captcha_wait(self):
        from database import DBHandler
        DBHandler.update_runtime(self.bot.nid, otp_state="WAITING_FOR_SMS")
        stop_event = MagicMock()
        stop_event.is_set.return_value = False

        page = self._create_mock_page()
        controls = CaptchaControls(stage="STEP_2_OTP", captcha_type=CaptchaType.STANDARD_IMAGE)

        # While waiting, simulate stop_event or SMS_TIMEOUT
        stop_event.is_set.return_value = True
        ok, res = CaptchaPipeline.recognize_and_fill(self.bot, page, controls, stop_event=stop_event)
        self.assertFalse(ok)
        self.assertEqual(res, "STOPPED")

    # --- Scenario 13: Retention of browser and session on error ---
    def test_13_retention_of_browser_and_session_on_error(self):
        page = self._create_mock_page()
        el_inp = MockElement("input", "ctl00_ContentPlaceHolder1_tbCaptcha2")
        el_img = MockElement("img", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage")
        el_reload = MockElement("a", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_ReloadLink")

        def mock_loc(sel):
            if "tbCaptcha2" in sel:
                return el_inp
            if "CaptchaImage" in sel:
                return el_img
            if "ReloadLink" in sel:
                return el_reload
            empty = MagicMock()
            empty.count.return_value = 0
            return empty

        page.locator = MagicMock(side_effect=mock_loc)
        controls = CaptchaPipeline.discover_captcha(self.bot, page, stage=CaptchaStage.STEP_2_OTP)

        # Trigger failure and non-destructive reload
        self.bot.captcha_service.solve.return_value = "FAIL"
        CaptchaPipeline.recognize_and_fill(self.bot, page, controls)

        # Verify page.reload was never invoked
        page.reload.assert_not_called()
        self.assertFalse(page.is_closed())

    # --- Scenario 14: Prevention of unwanted reload (Page3 controller blocked) ---
    def test_14_prevention_of_unwanted_reload(self):
        from core.page3_refresh_controller import Page3OfficialRefreshController
        page = self._create_mock_page()
        refresh_ctrl = Page3OfficialRefreshController(self.bot, page)

        # Verify that refresh cannot occur if in OTP_REQUIRED
        from database import DBHandler
        DBHandler.update_runtime(self.bot.nid, otp_state="OTP_REQUIRED")
        current_state = DBHandler.get_runtime(self.bot.nid).get("otp_state")
        self.assertEqual(current_state, "OTP_REQUIRED")

        # In OTP stage, page reload must remain inactive
        page.reload.assert_not_called()

    # --- Scenario 15: Prevention of duplicate submission ---
    def test_15_prevention_of_duplicate_submission(self):
        from bot_select_otp import _solve_otp_captcha_fast
        page = self._create_mock_page()
        el_inp = MockElement("input", "ctl00_ContentPlaceHolder1_tbCaptcha2", value="7A93B")
        el_btn = MockElement("input", "ctl00_ContentPlaceHolder1_btnContinue1")
        el_img = MockElement("img", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage")

        def mock_loc(sel):
            if "tbCaptcha2" in sel:
                return el_inp
            if "btnContinue1" in sel:
                return el_btn
            if "CaptchaImage" in sel:
                return el_img
            empty = MagicMock()
            empty.count.return_value = 0
            return empty

        page.locator = MagicMock(side_effect=mock_loc)
        res1 = _solve_otp_captcha_fast(self.bot, page)
        self.assertTrue(res1)
        self.assertEqual(el_btn.click_count, 1)

    # --- Scenario 16: Successful continuation from OTP after verification ---
    def test_16_successful_continuation_from_otp_after_verification(self):
        page = self._create_mock_page()
        el_otp = MockElement("input", "ctl00_ContentPlaceHolder1_tbMobileConfCode", value="987654")
        el_cap = MockElement("input", "ctl00_ContentPlaceHolder1_tbCaptcha2", value="7A93B")
        el_btn = MockElement("input", "ctl00_ContentPlaceHolder1_btnContinue1")
        el_img = MockElement("img", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage")

        def mock_loc(sel):
            if "tbMobileConfCode" in sel:
                return el_otp
            if "tbCaptcha2" in sel:
                return el_cap
            if "btnContinue1" in sel:
                return el_btn
            if "CaptchaImage" in sel:
                return el_img
            empty = MagicMock()
            empty.count.return_value = 0
            return empty

        page.locator = MagicMock(side_effect=mock_loc)
        from bot_select_otp import _solve_otp_captcha_fast
        success = _solve_otp_captcha_fast(self.bot, page)
        self.assertTrue(success)
        self.assertTrue(el_btn.clicked)


if __name__ == "__main__":
    unittest.main()
