# tests/test_step2_captcha_resolution.py
import unittest
from unittest.mock import MagicMock, patch
import time

from otp_form_detector import detect_otp_form, live_scopes
from bot_select_otp import (
    _locate_otp_captcha_controls,
    ensure_otp_captcha_prefilled,
    _solve_otp_captcha_fast,
    _refresh_captcha_image,
)


class MockElement:
    def __init__(self, tag, elem_id, name="", value="", src="", is_visible=True, is_enabled=True):
        self.tag = tag.lower()
        self.id = elem_id
        self.name = name
        self._value = value
        self.src = src
        self._visible = is_visible
        self._enabled = is_enabled
        self.attributes = {"id": elem_id, "name": name, "src": src}

    def count(self):
        return 1

    @property
    def first(self):
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

    def dispatch_event(self, evt):
        pass

    def evaluate(self, expr, arg=None):
        if "activeElement" in expr:
            return False
        if "getAttribute('src')" in expr:
            return self.src
        if "complete" in expr or "naturalWidth" in expr:
            return True
        return None

    def element_handle(self, timeout=None):
        return self

    def screenshot(self, **kwargs):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 800  # >= 600 bytes


class TestStep2CaptchaResolution(unittest.TestCase):
    def setUp(self):
        self.bot = MagicMock()
        self.bot.nid = "0012345678"
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
        self.bot._recovery_flags = {}

    def _build_mock_page(self):
        page = MagicMock()
        page.is_closed = MagicMock(return_value=False)
        page.url = "https://ve.cbi.ir/SelectBnkShb.aspx"
        page.main_frame = page

        # Simulated CBI DOM items returned by FORM_EVIDENCE evaluate
        items = [
            {
                'tag': 'input', 'type': 'text', 'id': 'ctl00_ContentPlaceHolder1_tbMobileConfCode',
                'name': 'ctl00$ContentPlaceHolder1$tbMobileConfCode', 'label': 'کد پیامک',
                'nearby': '', 'placeholder': '', 'autocomplete': 'one-time-code', 'aria': '',
                'maxlength': 6, 'enabled': True, 'filled': False, 'form': 0, 'src': '', 'imageReady': False
            },
            {
                'tag': 'input', 'type': 'text', 'id': 'ctl00_ContentPlaceHolder1_tbCaptcha2',
                'name': 'ctl00$ContentPlaceHolder1$tbCaptcha2', 'label': 'کد امنیتی',
                'nearby': '', 'placeholder': '', 'autocomplete': '', 'aria': '',
                'maxlength': 8, 'enabled': True, 'filled': False, 'form': 0, 'src': '', 'imageReady': False
            },
            {
                'tag': 'input', 'type': 'submit', 'id': 'ctl00_ContentPlaceHolder1_btnContinue1',
                'name': 'ctl00$ContentPlaceHolder1$btnContinue1', 'label': '',
                'nearby': '', 'placeholder': '', 'autocomplete': '', 'aria': '',
                'maxlength': -1, 'enabled': True, 'filled': False, 'form': 0, 'src': '', 'imageReady': False,
                'text': 'ادامه'
            },
            {
                'tag': 'img', 'type': '', 'id': 'c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage',
                'name': '', 'label': '', 'nearby': '', 'placeholder': '', 'autocomplete': '', 'aria': '',
                'maxlength': 0, 'enabled': True, 'filled': False, 'form': 0,
                'src': '/BotDetectCaptcha.ashx?get=image&c=c_selectbnkshb_ctl00_contentplaceholder1_captcha2',
                'imageReady': True
            },
            {
                'tag': 'img', 'type': '', 'id': 'c_selectbnkshb_ctl00_contentplaceholder1_captcha2_SoundIcon',
                'name': '', 'label': '', 'nearby': '', 'placeholder': '', 'autocomplete': '', 'aria': '',
                'maxlength': 0, 'enabled': True, 'filled': False, 'form': 0,
                'src': '/BotDetectCaptcha.ashx?get=sound-icon',
                'imageReady': True
            }
        ]

        def mock_eval(script, *args, **kwargs):
            if "isInAsyncPostBack" in script:
                return True
            if "querySelectorAll" in script:
                return items
            return None

        page.evaluate = MagicMock(side_effect=mock_eval)

        # Mock elements
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

    def test_01_sound_icon_filtered_and_captcha_image_detected(self):
        """SoundIcon must be ignored; true CaptchaImage is cleanly detected by UnifiedOTPFormDetector."""
        page, _, el_cap, el_btn, el_img, _ = self._build_mock_page()
        form = detect_otp_form(page)
        self.assertTrue(form.ready)
        self.assertIn("image", form.fields)
        self.assertEqual(
            form.fields["image"]["id"],
            "c_selectbnkshb_ctl00_contentplaceholder1_captcha2_CaptchaImage"
        )
        self.assertIn("captcha", form.fields)
        self.assertIn("continue", form.fields)
        self.assertIn("otp", form.fields)

    def test_02_tspd_frame_ignored_in_scopes(self):
        """Security frames like TSPD are omitted from live_scopes to prevent contamination."""
        page, _, _, _, _, _ = self._build_mock_page()
        tspd_frame = MagicMock()
        tspd_frame.name = "tspd_frame"
        tspd_frame.url = "https://ve.cbi.ir/TSPD/?type=20"
        page.frames = [page, tspd_frame]

        scopes = list(live_scopes(page))
        self.assertIn(page, scopes)
        self.assertNotIn(tspd_frame, scopes)

    def test_03_prefill_only_warms_image_without_ocr_fill(self):
        """Prefill must NOT OCR/fill early — only ensure image exists and clear stale field."""
        page, el_otp, el_cap, el_btn, el_img, _ = self._build_mock_page()
        el_cap.fill("STALE")
        res = ensure_otp_captcha_prefilled(self.bot, page)
        self.assertTrue(res)
        self.assertEqual(el_cap.input_value(), "")
        self.assertEqual(getattr(el_btn, "clicked", False), False)
        self.assertIsNone(self.bot._otp_prefilled_captcha)
        self.bot.captcha_service.solve.assert_not_called()

    def test_04_manual_captcha_setting_emits_event_and_pauses(self):
        """When pause_for_manual_captcha is enabled, CAPTCHA_MANUAL_REQUIRED is emitted."""
        self.bot.settings["pause_for_manual_captcha"] = True
        page, _, el_cap, _, _, _ = self._build_mock_page()

        res = ensure_otp_captcha_prefilled(self.bot, page)
        self.assertFalse(res)
        event_codes = [e[0] for e in self.bot.events]
        self.assertIn("CAPTCHA_MANUAL_REQUIRED", event_codes)
        self.assertEqual(el_cap.input_value(), "")  # Not filled by model

    def test_05_refresh_captcha_uses_reload_link_without_page_reload(self):
        """Refreshing a rejected CAPTCHA clicks ReloadLink without refreshing the page."""
        page, _, _, _, _, el_reload = self._build_mock_page()
        page.reload = MagicMock()

        # Mock wait_for_captcha_image_ready
        with patch("bot_select_captcha.wait_for_captcha_image_ready") as mock_wait:
            mock_img = MagicMock()
            mock_img.evaluate.return_value = "/BotDetectCaptcha.ashx?old"
            mock_wait.return_value = (mock_img, page)

            _refresh_captcha_image(self.bot, page, captcha_rejected=True)
            self.assertFalse(getattr(el_reload, "clicked", False))
            page.reload.assert_not_called()  # Never do full page reload!

    def test_06_submit_clears_stale_and_ocr_once_without_forced_refresh(self):
        """Submit clears stale field and OCRs once; force_refresh only after rejection."""
        page, el_otp, el_cap, el_btn, el_img, el_reload = self._build_mock_page()
        el_otp.fill("123456")
        el_cap.fill("STALE")
        self.bot._otp_prefilled_captcha = "STALE"
        self.bot.captcha_service.solve.return_value = "AB12"

        with patch("bot_select_captcha.solve_step_captcha") as mock_solve:
            from bot_select_captcha import CaptchaResult
            mock_solve.return_value = CaptchaResult(success=True, code="AB12", attempts_used=1)
            ok = _solve_otp_captcha_fast(self.bot, page)
            self.assertTrue(ok)
            self.assertTrue(mock_solve.called)
            kwargs = mock_solve.call_args.kwargs
            self.assertFalse(kwargs.get("force_refresh"))
            self.assertEqual(el_cap.input_value(), "")
            self.assertTrue(getattr(el_btn, "clicked", False))
            self.assertEqual(self.bot._otp_prefilled_captcha, "AB12")


if __name__ == "__main__":
    unittest.main()
