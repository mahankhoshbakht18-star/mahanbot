import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InteractionModeWiringTests(unittest.TestCase):
    def test_dashboard_exposes_separate_interaction_and_captcha_settings(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "script.js").read_text(encoding="utf-8")

        self.assertIn('id="set_interaction_mode"', html)
        self.assertIn('<option value="human">', html)
        self.assertIn('<option value="fast">', html)
        self.assertIn('id="set_captcha_mode"', html)
        self.assertIn("interaction_mode:", script)
        self.assertIn("human_typing_delay_ms:", script)

    def test_bot_text_inputs_do_not_use_direct_playwright_fill(self):
        for name in ("bot_core.py", "bot_register.py", "bot_select.py", "bot_status.py"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn(".fill(", source, msg=name)
            self.assertNotIn("page.fill(", source, msg=name)
            self.assertNotIn(".clear(", source, msg=name)

    def test_legacy_browser_intermediate_page_is_removed(self):
        for name in ("browser_launcher.py", "bot_core.py", "bot_status.py", "unified_server.py"):
            source = (ROOT / name).read_text(encoding="utf-8").lower()
            self.assertNotIn("healthcheck", source, msg=name)
            self.assertNotIn("open_healthcheck_page", source, msg=name)


if __name__ == "__main__":
    unittest.main()
