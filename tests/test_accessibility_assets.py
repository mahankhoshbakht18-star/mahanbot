from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "static" / "accessibility_v1.js"
CSS = ROOT / "static" / "accessibility_v1.css"


class AccessibilityAssetTests(unittest.TestCase):
    def test_assets_exist(self):
        self.assertTrue(JS.is_file())
        self.assertTrue(CSS.is_file())

    def test_javascript_contains_required_accessibility_contract(self):
        content = JS.read_text(encoding="utf-8")
        required = (
            "aria-live",
            "a11y-skip-link",
            "role', 'navigation",
            "focusActiveView",
            "setupKeyboardShortcuts",
            "one-time-code",
            "speechSynthesis",
            "Alt+1",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, content)

    def test_css_contains_visible_focus_and_screen_reader_utility(self):
        content = CSS.read_text(encoding="utf-8")
        self.assertIn(":focus-visible", content)
        self.assertIn(".a11y-sr-only", content)
        self.assertIn("forced-colors", content)
        self.assertIn("min-height: 44px", content)


if __name__ == "__main__":
    unittest.main()
