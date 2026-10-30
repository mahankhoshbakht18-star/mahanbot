"""CAPTCHA regression scenarios using Chromium fixtures and isolated OCR/DB boundaries."""
import base64
import io
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot_core import BotCore
from bot_select_captcha import (
    ModelCaptchaSolver,
    TORCH_AVAILABLE,
    detect_captcha_failure,
    refresh_captcha_or_reload,
    solve_captcha_wrapper,
    wait_for_captcha_image_ready,
    wait_for_manual_captcha_handoff,
)


def fixture_image():
    buffer = io.BytesIO()
    Image.new("RGB", (160, 60), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class CaptchaRefreshPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.context.route("**/*", lambda route: route.abort())
        self.page = self.context.new_page()
        self.page.set_default_timeout(700)
        self.bot = BotCore.__new__(BotCore)
        self.bot.nid = "captcha-fixture-only"
        self.bot.settings = {"captcha_min_length": 4, "captcha_max_length": 8, "captcha_manual_timeout": 6}
        self.bot.captcha_service = Mock()
        self.bot.captcha_service.solve_element.return_value = None
        for method in ("log", "set_runtime_state", "mark_progress", "suspend_watchdog"):
            setattr(self.bot, method, Mock())
        self.db = patch("bot_core.DBHandler.append_captcha_attempt")
        self.db.start()

    def tearDown(self):
        self.db.stop()
        self.context.close()

    def form(self, in_frame=False):
        source = "data:image/png;base64," + base64.b64encode(fixture_image()).decode("ascii")
        markup = f"""<!doctype html><meta charset='utf-8'>
        <a class='BDC_CaptchaImage' style='display:inline-block;padding:12px'>
            <img class='BDC_CaptchaImage' id='test_CaptchaImage' src='{source}'></a>
        <a class='BDC_ReloadLink' href='#' onclick="event.preventDefault();counts.reloads++;
            document.querySelector('img').src='{source}#'+counts.reloads">Reload</a>
        <input id='tbCaptcha' oninput='counts.inputs++' onchange='counts.changes++'>
        <button id='btnContinue' onclick='counts.submits++'>Continue</button>
        <script>window.counts={{reloads:0,submits:0,inputs:0,changes:0}};</script>"""
        if in_frame:
            self.page.set_content("<iframe id='form-frame'></iframe>")
            scope = self.page.locator("iframe").element_handle().content_frame()
            scope.set_content(markup)
        else:
            self.page.set_content(markup)
            scope = self.page
        scope.locator("img").evaluate("img => img.decode()")
        return scope

    def test_empty_ocr_keeps_loaded_image_and_does_not_submit(self):
        self.form()
        result = self.bot.solve_captcha_with_model(self.page, "#tbCaptcha", "#btnContinue")
        self.assertFalse(result)
        self.assertEqual(self.page.evaluate("counts.reloads"), 0)
        self.assertEqual(self.page.evaluate("counts.submits"), 0)

    def test_wrapper_does_not_refresh_or_submit_after_local_recognition_failure(self):
        self.form()
        self.bot.settings["pause_for_manual_captcha"] = False
        self.assertFalse(solve_captcha_wrapper(
            self.bot, self.page, "#tbCaptcha", "#btnContinue", "model"
        ))
        self.assertEqual(self.page.evaluate("counts.reloads"), 0)
        self.assertEqual(self.page.evaluate("counts.submits"), 0)

    def test_otp_and_registration_refresh_helpers_preserve_healthy_image(self):
        from bot_register import RegistrationBot
        from bot_select_otp import _refresh_captcha_image

        for refresh in (_refresh_captcha_image, RegistrationBot._refresh_captcha_in_place):
            with self.subTest(helper=refresh.__qualname__):
                self.form()
                self.page.locator("#tbCaptcha").fill("USER")
                self.assertFalse(refresh(self.bot, self.page))
                self.assertEqual(self.page.evaluate("counts.reloads"), 0)
                self.assertEqual(self.page.evaluate("counts.submits"), 0)
                self.assertEqual(self.page.locator("#tbCaptcha").input_value(), "USER")

    def test_valid_code_on_main_page_fills_events_and_submits_once(self):
        self.form()
        self.bot.captcha_service.solve_element.return_value = "AB12CD"
        self.assertTrue(self.bot.solve_captcha_with_model(
            self.page, "#tbCaptcha", "#btnContinue"
        ))
        self.assertEqual(self.page.locator("#tbCaptcha").input_value(), "AB12CD")
        self.assertGreaterEqual(self.page.evaluate("counts.inputs"), 1)
        self.assertGreaterEqual(self.page.evaluate("counts.changes"), 1)
        self.assertEqual(self.page.evaluate("counts.submits"), 1)
        self.assertEqual(self.page.evaluate("counts.reloads"), 0)

    def test_input_locator_and_button_selector_use_the_inputs_frame(self):
        scope = self.form(in_frame=True)
        self.bot.captcha_service.solve_element.return_value = "AB12CD"
        self.assertTrue(self.bot.solve_captcha_with_model(
            self.page, scope.locator("#tbCaptcha"), "#btnContinue",
            captcha_element=scope.locator("img"),
        ))
        self.assertEqual(scope.evaluate("counts.submits"), 1)

    def test_rejected_image_refresh_uses_its_own_frame_and_consumes_dialog_flag(self):
        scope = self.form(in_frame=True)
        self.page.evaluate("""() => {
            window.mainReloads=0;
            const link=document.createElement('a');
            link.className='BDC_ReloadLink';link.textContent='Other form';
            link.onclick=()=>window.mainReloads++;
            document.body.prepend(link);
        }""")
        self.bot._last_dialog_action = "captcha_invalid"
        self.bot._recovery_flags = {"captcha_invalid": True}
        self.assertTrue(refresh_captcha_or_reload(self.bot, self.page, input_sel="#tbCaptcha"))
        self.assertEqual(self.page.evaluate("mainReloads"), 0)
        self.assertEqual(scope.evaluate("counts.reloads"), 1)
        self.assertIsNone(self.bot._last_dialog_action)
        self.assertFalse(self.bot._recovery_flags["captcha_invalid"])

    def test_manual_text_entered_during_inference_is_preserved(self):
        self.form()

        def solve(image):
            self.page.locator("#tbCaptcha").fill("USER")
            return "AB12CD"

        self.bot.captcha_service.solve_element.side_effect = solve
        self.assertFalse(self.bot.solve_captcha_with_model(self.page, "#tbCaptcha", "#btnContinue"))
        self.assertEqual(self.page.locator("#tbCaptcha").input_value(), "USER")
        self.assertEqual(self.page.evaluate("counts.submits"), 0)

    def test_dialog_needs_rejection_words_as_well_as_captcha_words(self):
        self.assertNotEqual(self.bot._classify_dialog("کد امنیتی را وارد کنید"), "captcha_invalid")
        self.assertEqual(self.bot._classify_dialog("کد امنیتی اشتباه است"), "captcha_invalid")

    def test_empty_memory_result_does_not_screenshot_same_image_to_disk(self):
        from api_solver import CaptchaService

        self.form()
        solver = Mock()
        solver.solve.return_value = None
        service = CaptchaService(solver=solver)
        with patch("api_solver.tempfile.mkdtemp") as make_temp:
            self.assertIsNone(service.solve_element(self.page.locator("img")))
        solver.solve.assert_called_once()
        make_temp.assert_not_called()

    def test_bookkeeping_failure_after_click_preserves_submitted_result(self):
        self.form()
        self.bot.captcha_service.solve_element.return_value = "AB12CD"
        with patch("bot_core.DBHandler.append_captcha_attempt", side_effect=RuntimeError("test DB locked")):
            submitted = self.bot.solve_captcha_with_model(
                self.page, "#tbCaptcha", "#btnContinue"
            )
        self.assertTrue(submitted)
        self.assertEqual(self.page.evaluate("counts.submits"), 1)
        self.assertEqual(self.page.evaluate("counts.reloads"), 0)
        self.assertEqual(self.bot.captcha_service.solve_element.call_count, 1)

    def test_direct_image_in_frame_is_selected_instead_of_shared_class_parent(self):
        scope = self.form(in_frame=True)
        image, found_scope = wait_for_captcha_image_ready(
            self.page, timeout_ms=500, trigger_refresh_if_missing=False
        )
        self.assertIs(found_scope, scope)
        self.assertEqual(image.evaluate("el => el.tagName"), "IMG")

    def test_labels_scripts_and_hidden_errors_are_not_server_rejections(self):
        pages = (
            "<label>security code</label><input id='captcha'>",
            "<script type='application/json'>{\"error\":\"کد امنیتی اشتباه است\"}</script><p>فرم ورود</p>",
            "<div role='alert' style='display:none'>کد امنیتی اشتباه است</div><p>فرم ورود</p>",
        )
        for markup in pages:
            with self.subTest(markup=markup):
                self.page.set_content(markup)
                self.assertFalse(detect_captcha_failure(self.page))

    def test_visible_server_rejection_in_iframe_is_detected(self):
        scope = self.form(in_frame=True)
        scope.locator("body").evaluate(
            "el => el.insertAdjacentHTML('beforeend', '<div role=alert>کد امنیتی اشتباه است</div>')"
        )
        self.assertTrue(detect_captcha_failure(self.page))

    def test_refresh_helper_keeps_healthy_image_even_when_page_reload_is_allowed(self):
        self.form()
        self.page.locator("#tbCaptcha").fill("USER")
        with patch.object(self.page, "reload", side_effect=AssertionError("Unexpected page reload")) as reload_page:
            refreshed = refresh_captcha_or_reload(
                self.bot, self.page, input_sel="#tbCaptcha", allow_reload=True
            )
        self.assertFalse(refreshed)
        self.assertEqual(self.page.evaluate("counts.reloads"), 0)
        self.assertEqual(self.page.locator("#tbCaptcha").input_value(), "USER")
        reload_page.assert_not_called()

    def test_real_rejection_allows_exactly_one_image_refresh(self):
        self.form()
        self.page.locator("body").evaluate(
            "el => el.insertAdjacentHTML('beforeend', '<div role=alert>کد امنیتی اشتباه است</div>')"
        )
        self.page.locator("#tbCaptcha").fill("WRONG1")
        with patch.object(self.page, "reload", side_effect=AssertionError("Unexpected page reload")) as reload_page:
            refreshed = refresh_captcha_or_reload(
                self.bot, self.page, input_sel="#tbCaptcha", allow_reload=True
            )
        self.assertTrue(refreshed)
        self.assertEqual(self.page.evaluate("counts.reloads"), 1)
        self.assertEqual(self.page.locator("#tbCaptcha").input_value(), "")
        reload_page.assert_not_called()

    def test_missing_image_triggers_at_most_one_reload_link_click(self):
        self.page.set_content("""<a class='BDC_ReloadLink' href='#'
            onclick='event.preventDefault();window.reloadClicks++'>Reload</a>
            <script>window.reloadClicks=0;</script>""")
        image, scope = wait_for_captcha_image_ready(
            self.page, timeout_ms=500, trigger_refresh_if_missing=True
        )
        self.assertIsNone(image)
        self.assertIsNone(scope)
        self.assertEqual(self.page.evaluate("reloadClicks"), 1)

    def test_valid_code_fills_dispatches_and_submits_once_in_same_frame(self):
        scope = self.form(in_frame=True)
        targets = []

        def solve(image):
            targets.append(image.evaluate("el => el.tagName"))
            return "AB12CD"

        self.bot.captcha_service.solve_element.side_effect = solve
        result = self.bot.solve_captcha_with_model(self.page, "#tbCaptcha", "#btnContinue")
        self.assertTrue(result)
        self.assertEqual(targets, ["IMG"])
        self.assertEqual(scope.locator("#tbCaptcha").input_value(), "AB12CD")
        self.assertEqual(scope.evaluate("counts.submits"), 1)
        self.assertEqual(scope.evaluate("counts.reloads"), 0)
        self.assertGreaterEqual(scope.evaluate("counts.inputs"), 1)
        self.assertGreaterEqual(scope.evaluate("counts.changes"), 1)

    def test_manual_wait_preserves_unchanged_image_after_ocr_failure(self):
        self.form()
        self.bot.solve_captcha_with_model = Mock(return_value=False)
        clock = [100.0]

        def advance(seconds):
            clock[0] += seconds

        with patch("event_logger.emit_structured_event"), \
             patch("bot_select_captcha.DBHandler.update_status"), \
             patch("bot_select_captcha.time.time", side_effect=lambda: clock[0]), \
             patch("bot_select_captcha.time.sleep", side_effect=advance):
            result = wait_for_manual_captcha_handoff(
                self.bot, self.page, "#tbCaptcha", "#btnContinue", threading.Event(),
                success_selectors=(),
            )
        self.assertFalse(result)
        self.assertEqual(self.page.evaluate("counts.reloads"), 0)
        self.assertEqual(self.page.evaluate("counts.submits"), 0)

    def test_manual_text_is_not_overwritten_by_model_assist(self):
        self.form()
        self.page.locator("#tbCaptcha").fill("USER")
        self.bot.captcha_service.solve_element.return_value = "MODEL1"
        clock = [100.0]
        with patch("event_logger.emit_structured_event"), \
             patch("bot_select_captcha.DBHandler.update_status"), \
             patch("bot_select_captcha.time.time", side_effect=lambda: clock[0]), \
             patch("bot_select_captcha.time.sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            wait_for_manual_captcha_handoff(
                self.bot, self.page, "#tbCaptcha", "#btnContinue", threading.Event(),
                success_selectors=(),
            )
        self.assertEqual(self.page.locator("#tbCaptcha").input_value(), "USER")
        self.assertEqual(self.page.evaluate("counts.submits"), 0)


@unittest.skipUnless(TORCH_AVAILABLE, "PyTorch is required for the CRNN fallback contract")
class CaptchaEngineFallbackTests(unittest.TestCase):
    def solver(self, prediction="AB12", confidence=0.2, fallback="CD34"):
        solver = ModelCaptchaSolver.__new__(ModelCaptchaSolver)
        solver.device = "cpu"
        solver._lock = threading.RLock()
        solver.last_error = None
        model = Mock()
        solver.load_model = Mock(return_value=model)
        tensor = Mock()
        tensor.to.return_value = tensor
        solver.preprocess = Mock(return_value=tensor)
        solver.decode = Mock(return_value=(prediction, confidence))
        solver._ddddocr = Mock()
        solver._ddddocr.classification.return_value = fallback
        return solver, model

    def test_low_confidence_uses_fallback_before_repeating_crnn_preprocessing(self):
        solver, model = self.solver()
        self.assertEqual(solver.solve(fixture_image()), "CD34")
        self.assertEqual(model.call_count, 1)

    def test_valid_medium_confidence_survives_unavailable_fallback(self):
        solver, _ = self.solver(fallback="")
        self.assertEqual(solver.solve(fixture_image()), "AB12")

    def test_model_load_failure_still_returns_valid_local_fallback(self):
        solver, model = self.solver()
        solver.load_model.side_effect = FileNotFoundError("test model unavailable")
        self.assertEqual(solver.solve(fixture_image()), "CD34")
        model.assert_not_called()
        self.assertIsNone(solver.last_error)

    def test_high_confidence_crnn_returns_without_calling_fallback(self):
        solver, model = self.solver(confidence=0.95)
        self.assertEqual(solver.solve(fixture_image()), "AB12")
        self.assertEqual(model.call_count, 1)
        solver._ddddocr.classification.assert_not_called()

    def test_out_of_range_predictions_are_never_returned(self):
        for prediction in ("ABC", "ABCDEFGHI"):
            with self.subTest(prediction=prediction):
                solver, _ = self.solver(prediction=prediction, fallback="")
                self.assertIsNone(solver.solve(fixture_image()))


if __name__ == "__main__":
    unittest.main()
