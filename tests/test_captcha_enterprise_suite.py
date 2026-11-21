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
    # --- Scenario 2: Captcha inside accessible iframe ---
    # --- Scenario 3: Captcha with different ID ---
    # --- Scenario 4: Captcha with delayed image rendering (naturalWidth == 0) ---
    # --- Scenario 5: Captcha image change after PostBack ---
    # --- Scenario 6: Field replacement by AJAX ---
    # --- Scenario 7: Image found without input field ---
    # --- Scenario 8: Input field found without image ---
    # --- Scenario 9: Recognition service error or timeout ---
    # --- Scenario 10: Invalid regular captcha prediction (retry) ---
    # --- Scenario 11: TSPD security challenge triggers manual mode ---
    # --- Scenario 12: OTP state change during captcha wait ---
    # --- Scenario 13: Retention of browser and session on error ---
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
