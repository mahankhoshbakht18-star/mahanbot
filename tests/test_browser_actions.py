import unittest

from browser_actions import BrowserActions, HumanInputProfile


class FakeLocator:
    def __init__(self, *, visible=True, enabled=True, readonly=None, disabled=None, initial=""):
        self.visible = visible
        self.enabled = enabled
        self.attributes = {"readonly": readonly, "disabled": disabled}
        self.value = initial
        self.calls = []
        self._selected_all = False

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def get_attribute(self, name):
        return self.attributes.get(name)

    def scroll_into_view_if_needed(self):
        self.calls.append(("scroll",))

    def click(self):
        self.calls.append(("click",))

    def press(self, key):
        self.calls.append(("press", key))
        if key == "Control+A":
            self._selected_all = True
        elif key == "Backspace" and self._selected_all:
            self.value = ""
            self._selected_all = False

    def clear(self):
        self.calls.append(("clear",))
        self.value = ""

    def type(self, text, delay=0):
        self.calls.append(("type", text, delay))
        self.value += text

    def fill(self, text):
        self.calls.append(("fill", text))
        self.value = text

    def input_value(self):
        return self.value

    def select_option(self, **kwargs):
        self.calls.append(("select_option", kwargs))
        self.value = kwargs.get("value") or kwargs.get("label") or ""


class FakePage:
    def __init__(self):
        self.calls = []

    def evaluate(self, script, payload):
        self.calls.append((script, payload))


class BrowserActionsTests(unittest.TestCase):
    def setUp(self):
        self.profile = HumanInputProfile(
            min_key_delay_ms=0,
            max_key_delay_ms=0,
            pre_input_delay_ms=0,
            post_input_delay_ms=0,
            click_delay_ms=0,
        )

    def test_human_fill_uses_keyboard_sequence(self):
        locator = FakeLocator(initial="old")

        result = BrowserActions.human_fill(locator, "123456", profile=self.profile)

        self.assertTrue(result)
        self.assertEqual(locator.value, "123456")
        self.assertIn(("press", "Control+A"), locator.calls)
        self.assertIn(("press", "Backspace"), locator.calls)
        self.assertTrue(any(call[0] == "type" for call in locator.calls))
        self.assertFalse(any(call[0] == "fill" for call in locator.calls))

    def test_human_fill_rejects_readonly_field(self):
        locator = FakeLocator(readonly="readonly")

        result = BrowserActions.human_fill(locator, "value", profile=self.profile)

        self.assertFalse(result)
        self.assertEqual(locator.calls, [])

    def test_force_fill_keeps_backward_compatibility(self):
        locator = FakeLocator()

        result = BrowserActions.force_fill(locator, "abc")

        self.assertTrue(result)
        self.assertEqual(locator.value, "abc")

    def test_show_log_passes_data_as_argument(self):
        page = FakePage()
        message = "quote ' and </script>"

        BrowserActions.show_log_on_page(page, message, "red")

        self.assertEqual(len(page.calls), 1)
        _, payload = page.calls[0]
        self.assertEqual(payload["text"], message)
        self.assertEqual(payload["color"], "red")

    def test_invalid_log_color_falls_back_to_blue(self):
        page = FakePage()

        BrowserActions.show_log_on_page(page, "test", "url(javascript:bad)")

        _, payload = page.calls[0]
        self.assertEqual(payload["color"], "blue")


if __name__ == "__main__":
    unittest.main()
