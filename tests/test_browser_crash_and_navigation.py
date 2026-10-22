import unittest
from unittest.mock import MagicMock, patch
import threading
from bot_core import BotCore
from browser_launcher import close_browser, safe_goto
from database import DBHandler, normalize_config


class BrowserCrashAndNavigationTests(unittest.TestCase):
    def test_01_navigation_timeout_defaults_to_35000(self):
        cfg = normalize_config({"navigation_timeout_ms": 15000})
        self.assertEqual(cfg["navigation_timeout_ms"], 35000)

        cfg2 = normalize_config({})
        self.assertEqual(cfg2["navigation_timeout_ms"], 35000)

    def test_02_close_browser_tags_intentionally_closing(self):
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_browser = MagicMock()
        mock_playwright = MagicMock()

        close_browser(mock_playwright, mock_browser, mock_context, mock_page)

        self.assertTrue(getattr(mock_page, "_mahan_closing_intentionally", False))
        self.assertTrue(getattr(mock_context, "_mahan_closing_intentionally", False))
        self.assertTrue(getattr(mock_browser, "_mahan_closing_intentionally", False))

    def test_03_manual_close_watcher_ignores_intentional_close(self):
        bot = BotCore("0012345678", {})
        stop_event = threading.Event()
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_browser = MagicMock()

        close_callback = None
        def mock_page_on(event, cb):
            nonlocal close_callback
            if event == "close":
                close_callback = cb
        mock_page.on = mock_page_on

        bot._attach_manual_close_watchers(stop_event, mock_browser, mock_context, mock_page)
        self.assertIsNotNone(close_callback)

        # Mark intentional close on page
        setattr(mock_page, "_mahan_closing_intentionally", True)
        
        # Fire close event
        close_callback()

        # stop_event should NOT be set, and no crash/user close should be recorded
        self.assertFalse(stop_event.is_set())

    def test_04_safe_goto_timeout_recovers_if_target_inputs_exist(self):
        mock_page = MagicMock()
        mock_page.url = "https://ve.cbi.ir/SelectBnkShb.aspx"
        
        # Simulate page.goto raising TimeoutError
        mock_goto = MagicMock(side_effect=Exception("Page.goto: Timeout 35000ms exceeded"))
        setattr(mock_page, "_original_goto", mock_goto)
        
        # But target element locator returns count > 0
        mock_locator = MagicMock()
        mock_locator.count.return_value = 1
        mock_page.locator.return_value = mock_locator

        # Should recover and not raise exception
        result = safe_goto(mock_page, "https://ve.cbi.ir/SelectBnkShb.aspx")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
