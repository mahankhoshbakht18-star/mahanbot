import unittest
from unittest.mock import Mock, patch

import browser_launcher
from core.models import BrowserProfileRequest
from routes.settings import update_browser_profile


class BrowserChannelTests(unittest.TestCase):
    def test_edge_profile_save(self):
        with patch("routes.settings.load_browser_profile_settings", return_value=browser_launcher.get_default_browser_profile()), patch("routes.settings.save_browser_profile_settings", side_effect=lambda profile: profile):
            result = update_browser_profile(BrowserProfileRequest(browser="edge", headless=False))
        self.assertEqual(result["browser"], "edge")
        self.assertFalse(result["headless"])

    def test_official_channels_for_regular_and_persistent_launch(self):
        for browser, channel in (("edge", "msedge"), ("chrome", "chrome")):
            for persistent in (False, True):
                with self.subTest(browser=browser, persistent=persistent):
                    playwright = Mock()
                    playwright.chromium.launch.return_value.new_context.return_value.pages = []
                    playwright.chromium.launch_persistent_context.return_value.pages = []
                    profile = browser_launcher.get_default_browser_profile()
                    profile.update(browser=browser, user_data_dir="test-profile" if persistent else None)
                    with patch.object(browser_launcher, "normalize_browser_profile", return_value=profile), patch.object(browser_launcher, "sync_playwright") as starter:
                        starter.return_value.start.return_value = playwright
                        handles = browser_launcher.launch_browser(profile)
                    launch = playwright.chromium.launch_persistent_context if persistent else playwright.chromium.launch
                    self.assertEqual(launch.call_args.kwargs["channel"], channel)
                    browser_launcher.close_browser(*handles)


if __name__ == "__main__":
    unittest.main()
