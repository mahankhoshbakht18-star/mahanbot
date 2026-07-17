import unittest

from browser_actions import BrowserActions, InteractionConfig


class FakePage:
    def __init__(self):
        self.calls = []

    def evaluate(self, script, argument=None):
        self.calls.append((script, argument))

    def wait_for_timeout(self, milliseconds):
        self.calls.append(("wait", milliseconds))


class FakeLocator:
    def __init__(self, *, value="", visible=True, enabled=True, editable=True):
        self.value = value
        self.visible = visible
        self.enabled = enabled
        self.editable = editable
        self.page = FakePage()
        self.selected_all = False
        self.events = []

    def wait_for(self, **kwargs):
        self.events.append(("wait_for", kwargs))

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def is_editable(self):
        return self.editable

    def get_attribute(self, name):
        if name == "readonly" and not self.editable:
            return "readonly"
        return None

    def scroll_into_view_if_needed(self, **kwargs):
        self.events.append(("scroll", kwargs))

    def click(self, **kwargs):
        self.events.append(("click", kwargs))

    def press(self, key):
        self.events.append(("press", key))
        if key == "Control+A":
            self.selected_all = True
        elif key == "Backspace" and self.selected_all:
            self.value = ""
            self.selected_all = False

    def type(self, value, delay=0):
        self.events.append(("type", value, delay))
        self.value += value

    def fill(self, value, **kwargs):
        self.events.append(("fill", value, kwargs))
        self.value = value

    def input_value(self):
        return self.value

    def evaluate(self, script, argument=None):
        self.events.append(("evaluate", argument))
        if argument is not None:
            self.value = str(argument)


class BrowserActionsTests(unittest.TestCase):
    def setUp(self):
        self.config = InteractionConfig(
            timeout_ms=1_000,
            typing_delay_ms=25,
            settle_delay_ms=0,
            js_fallback_enabled=False,
        )

    def test_fill_text_types_and_verifies_value(self):
        locator = FakeLocator(value="old")

        result = BrowserActions.fill_text(locator, "123456", config=self.config)

        self.assertTrue(result)
        self.assertEqual(locator.value, "123456")
        self.assertIn(("type", "123456", 25), locator.events)
        self.assertIn(("press", "Tab"), locator.events)

    def test_fill_text_rejects_readonly_control(self):
        locator = FakeLocator(editable=False)

        result = BrowserActions.fill_text(locator, "value", config=self.config)

        self.assertFalse(result)
        self.assertEqual(locator.value, "")

    def test_force_fill_keeps_legacy_api_but_returns_status(self):
        locator = FakeLocator()
        BrowserActions.configure(self.config)

        result = BrowserActions.force_fill(locator, "09120000000")

        self.assertTrue(result)
        self.assertEqual(locator.value, "09120000000")

    def test_show_log_uses_parameterized_javascript(self):
        page = FakePage()

        result = BrowserActions.show_log_on_page(page, "متن ' تست", "green")

        self.assertTrue(result)
        self.assertEqual(page.calls[0][1]["text"], "متن ' تست")
        self.assertEqual(page.calls[0][1]["color"], "green")


if __name__ == "__main__":
    unittest.main()
