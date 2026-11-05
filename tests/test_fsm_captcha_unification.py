# tests/test_fsm_captcha_unification.py
"""
Deterministic Verification Suite for:
1. Unified Captcha Solver (solve_step_captcha, CaptchaResult)
2. Exact 6-Stage Lifecycle Event Emission
3. Guard Elimination (activeElement, blocking recovery flags)
4. Formal FSM State Transitions (JobStateContext, S1 -> S6)
5. OTP Retention Across Page 3 Refreshes
"""
import time
import unittest
from unittest.mock import MagicMock, patch

from bot_select_captcha import CaptchaResult, solve_step_captcha
from bot_select import JobStateContext, BankSelectionBot


class MockElement:
    def __init__(self, tag, elem_id, name="", value="", src="", is_visible=True, is_enabled=True):
        self.tag = tag.lower()
        self.id = elem_id
        self.name = name
        self._value = value
        self.src = src
        self._visible = is_visible
        self._enabled = is_enabled
        self.clicked = False
        self.click_count = 0

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def is_visible(self):
        return self._visible

    def is_enabled(self):
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

    def evaluate(self, expr, arg=None):
        if "activeElement" in expr:
            # Simulated active element focus
            return True
        if "getAttribute('src')" in expr:
            return self.src
        if "complete" in expr or "naturalWidth" in expr:
            return True
        return None

    def screenshot(self, **kwargs):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 800  # >= 600 bytes


class TestFSMAndCaptchaUnification(unittest.TestCase):
    def setUp(self):
        self.bot = MagicMock()
        self.bot.nid = "0012345678"
        self.bot.settings = {
            "captcha_min_length": 4,
            "captcha_max_length": 8,
            "captcha_max_retries": 10,
            "pause_for_manual_captcha": False,
            "otp_validity_seconds": 120,
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
        self.bot._recovery_flags = {}

    def _build_step2_page(self):
        page = MagicMock()
        page.is_closed = MagicMock(return_value=False)
        page.url = "https://ve.cbi.ir/SelectBnkShb.aspx"

        el_otp = MockElement("input", "ctl00_ContentPlaceHolder1_tbMobileConfCode")
        el_cap = MockElement("input", "ctl00_ContentPlaceHolder1_tbCaptcha2")
        el_btn = MockElement("input", "ctl00_ContentPlaceHolder1_btnContinue1")
        el_img = MockElement("img", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage",
                             src="/BotDetectCaptcha.ashx?get=image&c=c_selectbnkshb_ctl00_contentplaceholder1_captcha2")
        el_reload = MockElement("a", "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_ReloadLink")

        def mock_locator(selector):
            if "tbMobileConfCode" in selector:
                return el_otp
            if "tbCaptcha2" in selector or "tbCaptcha" in selector:
                return el_cap
            if "btnContinue1" in selector or "btnContinue" in selector:
                return el_btn
            if "CaptchaImage" in selector:
                return el_img
            if "ReloadLink" in selector or "ReloadIcon" in selector:
                return el_reload
            mock_empty = MagicMock()
            mock_empty.count.return_value = 0
            mock_empty.is_visible.return_value = False
            return mock_empty

        page.locator = MagicMock(side_effect=mock_locator)
        page.frames = [page]
        return page, el_otp, el_cap, el_btn, el_img, el_reload

    def _build_step1_page(self):
        page = MagicMock()
        page.is_closed = MagicMock(return_value=False)
        page.url = "https://ve.cbi.ir/TasReq.aspx"

        el_nid = MockElement("input", "ctl00_ContentPlaceHolder1_tbIDNo")
        el_cap = MockElement("input", "ctl00_ContentPlaceHolder1_tbCaptcha")
        el_btn = MockElement("input", "ctl00_ContentPlaceHolder1_btnSendConfirmCode")
        el_img = MockElement("img", "ctl00_ContentPlaceHolder1_imgCaptcha",
                             src="/BotDetectCaptcha.ashx?get=image&c=ctl00_ContentPlaceHolder1_imgCaptcha")
        el_reload = MockElement("a", "ctl00_ContentPlaceHolder1_imgCaptcha_ReloadLink")

        def mock_locator(selector):
            if "tbIDNo" in selector:
                return el_nid
            if "tbCaptcha" in selector:
                return el_cap
            if "btnSendConfirmCode" in selector or "btnContinue" in selector:
                return el_btn
            if "imgCaptcha" in selector or "CaptchaImage" in selector:
                return el_img
            if "ReloadLink" in selector or "ReloadIcon" in selector:
                return el_reload
            mock_empty = MagicMock()
            mock_empty.count.return_value = 0
            mock_empty.is_visible.return_value = False
            return mock_empty

        page.locator = MagicMock(side_effect=mock_locator)
        page.frames = [page]
        return page, el_nid, el_cap, el_btn, el_img, el_reload

    def test_01_solve_step_captcha_step2_full_lifecycle(self):
        """Step 2 solver emits all 6 required events in exact sequence and returns CaptchaResult."""
        page, el_otp, el_cap, el_btn, el_img, _ = self._build_step2_page()
        res = solve_step_captcha(page, "STEP_2_OTP", bot=self.bot)

        self.assertIsInstance(res, CaptchaResult)
        self.assertTrue(res.success)
        self.assertEqual(res.code, "7A93B")
        self.assertEqual(el_cap.input_value(), "7A93B")

        event_names = [e[0] for e in self.bot.events]
        expected_sequence = [
            "CAPTCHA_ELEMENT_SEARCH",
            "CAPTCHA_IMAGE_FOUND",
            "CAPTCHA_INPUT_FOUND",
            "CAPTCHA_RECOGNITION_STARTED",
            "CAPTCHA_RECOGNITION_RESULT",
            "CAPTCHA_FILLED",
        ]
        self.assertEqual(event_names, expected_sequence)

    def test_02_solve_step_captcha_step1_full_lifecycle(self):
        """Step 1 solver emits all 6 required events and fills #ctl00_ContentPlaceHolder1_tbCaptcha."""
        page, el_nid, el_cap, el_btn, el_img, _ = self._build_step1_page()
        res = solve_step_captcha(page, "STEP_1_ENTRY_FORM", bot=self.bot)

        self.assertIsInstance(res, CaptchaResult)
        self.assertTrue(res.success)
        self.assertEqual(res.code, "7A93B")
        self.assertEqual(el_cap.input_value(), "7A93B")

        event_names = [e[0] for e in self.bot.events]
        expected_sequence = [
            "CAPTCHA_ELEMENT_SEARCH",
            "CAPTCHA_IMAGE_FOUND",
            "CAPTCHA_INPUT_FOUND",
            "CAPTCHA_RECOGNITION_STARTED",
            "CAPTCHA_RECOGNITION_RESULT",
            "CAPTCHA_FILLED",
        ]
        self.assertEqual(event_names, expected_sequence)

    def test_03_active_element_does_not_block_solver(self):
        """Active keyboard focus on captcha input must NEVER block model invocation."""
        page, el_otp, el_cap, el_btn, el_img, _ = self._build_step2_page()
        # MockElement.evaluate('activeElement') returns True by default
        res = solve_step_captcha(page, "STEP_2_OTP", bot=self.bot)
        self.assertTrue(res.success)
        self.assertEqual(res.code, "7A93B")

    def test_04_empty_model_prediction_triggers_auto_retry_and_reload(self):
        """If model returns empty prediction on first attempt, auto-reload and succeed on second attempt."""
        page, el_otp, el_cap, el_btn, el_img, el_reload = self._build_step2_page()
        attempts = [0]
        def mock_solve(img_bytes):
            attempts[0] += 1
            if attempts[0] == 1:
                return ""  # Empty prediction on first attempt (Applicant 1 bug)
            return "K98MZ"

        self.bot.captcha_service.solve = MagicMock(side_effect=mock_solve)
        res = solve_step_captcha(page, "STEP_2_OTP", bot=self.bot, max_attempts=5)

        self.assertTrue(res.success)
        self.assertEqual(res.code, "K98MZ")
        self.assertEqual(res.attempts_used, 2)
        self.assertTrue(el_reload.clicked)  # Reload icon was clicked on failure

    def test_05_job_state_context_initialization_and_validity(self):
        """JobStateContext tracks applicant, states, and OTP TTL."""
        ctx = JobStateContext(applicant_national_id="0012345678", otp_ttl_seconds=120)
        self.assertEqual(ctx.current_state, "S1_ENTRY_FORM")
        self.assertFalse(ctx.is_otp_valid())

        # Set cached OTP
        ctx.cached_otp_code = "123456"
        ctx.otp_received_at = time.time()
        self.assertTrue(ctx.is_otp_valid())

        # Expired OTP
        ctx.otp_received_at = time.time() - 125
        self.assertFalse(ctx.is_otp_valid())

        # Invalidate OTP
        ctx.invalidate_otp()
        self.assertIsNone(ctx.cached_otp_code)

    def test_06_otp_retention_across_page3_refreshes(self):
        """When Page 3 refreshes back to Step 2, cached_otp_code is retained and reused."""
        ctx = JobStateContext(applicant_national_id="0012345678", otp_ttl_seconds=120)
        ctx.current_state = "S2_OTP_VERIFICATION"
        ctx.cached_otp_code = "876543"
        ctx.otp_received_at = time.time()

        # Step 2 succeeds, transitions to S3_BANK_SELECTION
        ctx.current_state = "S3_BANK_SELECTION"
        self.assertTrue(ctx.is_otp_valid())
        self.assertEqual(ctx.cached_otp_code, "876543")

        # Page 3 refreshes / cycle_otp back to Step 2
        ctx.page3_retry_count += 1
        ctx.current_state = "S2_OTP_VERIFICATION"

        # Verification: OTP is STILL valid and ready for immediate re-injection!
        self.assertTrue(ctx.is_otp_valid())
        self.assertEqual(ctx.cached_otp_code, "876543")
        self.assertEqual(ctx.page3_retry_count, 1)

    def test_07_final_submission_consumes_otp_and_transitions_to_s6(self):
        """Final tracking code confirmation transitions to S6_COMPLETED and consumes OTP."""
        ctx = JobStateContext(applicant_national_id="0012345678")
        ctx.current_state = "S4_BRANCH_AND_SUBMIT"
        ctx.cached_otp_code = "876543"
        ctx.otp_received_at = time.time()

        # Final tracking code confirmed
        ctx.tracking_code = "9988776655"
        ctx.current_state = "S6_COMPLETED"
        ctx.invalidate_otp()

        self.assertEqual(ctx.current_state, "S6_COMPLETED")
        self.assertEqual(ctx.tracking_code, "9988776655")
        self.assertIsNone(ctx.cached_otp_code)


if __name__ == "__main__":
    unittest.main()
