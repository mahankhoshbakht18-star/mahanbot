"""
tests/test_quota_sniping.py — Unit and Integration Tests for Phase 2:
- Quota Sniping Loop & Fast Polling
- Waterfall Bank Priority Evaluation
- Fallback from non-presential to presential when capacity is exhausted
- Bank Cooldown Management
- Sniper settings persistence and normalization
"""
import time
import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from database import DBHandler, normalize_config
from bot_select_bank import (
    process_bank_selection_v2,
    canonicalize_bank_name,
    match_bank_option,
    _is_reserve_option,
)


class TestQuotaSnipingConfig(unittest.TestCase):
    """Test configuration defaults and normalization for sniper & waterfall options."""

    def test_default_config_includes_sniper_keys(self):
        cfg = normalize_config({})
        self.assertIn("sniper_mode_enabled", cfg)
        self.assertFalse(cfg["sniper_mode_enabled"])
        self.assertEqual(cfg["sniper_interval_ms"], 5000)
        self.assertTrue(cfg["auto_switch_alternative_bank"])
        self.assertFalse(cfg["fallback_to_in_person"])
        self.assertEqual(cfg["bank_cooldown_seconds"], 30)

    def test_normalize_config_bounds(self):
        cfg = normalize_config({
            "sniper_mode_enabled": True,
            "sniper_interval_ms": 50,  # Below the minimum five seconds.
            "bank_cooldown_seconds": 2,  # Below min (5) -> bounded to default 30
        })
        self.assertTrue(cfg["sniper_mode_enabled"])
        self.assertEqual(cfg["sniper_interval_ms"], 5000)
        self.assertEqual(cfg["bank_cooldown_seconds"], 30)

        cfg_valid = normalize_config({
            "sniper_mode_enabled": True,
            "sniper_interval_ms": 7000,
            "bank_cooldown_seconds": 45,
            "fallback_to_in_person": True,
        })
        self.assertEqual(cfg_valid["sniper_interval_ms"], 7000)
        self.assertEqual(cfg_valid["bank_cooldown_seconds"], 45)
        self.assertTrue(cfg_valid["fallback_to_in_person"])


class TestWaterfallBankSelection(unittest.TestCase):
    """Test waterfall priority queue and automatic alternative bank switching."""

    def setUp(self):
        self.bot = MagicMock()
        self.bot.nid = "0012345678"
        self.bot.settings = {
            "auto_switch_alternative_bank": True,
            "fallback_to_in_person": False,
            "bank_cooldown_seconds": 30,
            "auto_final_submit": False,
            "allow_reserve_bank": False,
        }
        self.bot.user_data = {
            "priority_banks": ["صادرات", "ملت"],
            "bank_mode": "online",
        }
        self.bot._firewall_gate = MagicMock(return_value=False)
        self.bot.consume_recovery_flag = MagicMock(return_value=False)
        self.bot._mask = lambda x: x
        self.stop_event = MagicMock()
        self.stop_event.is_set.return_value = False

    @patch("bot_select_bank._ensure_state_and_city_selected")
    @patch("bot_select_bank.load_runtime_data")
    @patch("bot_select_bank._save_bank_page_if_changed")
    @patch("bot_select_bank.DBHandler")
    def test_waterfall_switches_to_second_bank_when_first_is_capacity_full(
        self, mock_db, mock_save, mock_load, mock_ensure
    ):
        mock_load.return_value = {
            "priority_banks": ["صادرات", "ملت"],
            "bank_mode": "online",
            "city": "خرم آباد",
        }
        mock_db.has_final_submission.return_value = None
        mock_db.get_runtime.return_value = {"banks_available": []}

        # Mock page dropdown options: both Saderat and Mellat are in dropdown
        mock_page = MagicMock()
        options = [
            {"text": "-- انتخاب کنید --", "value": "0", "selected": False},
            {"text": "بانک صادرات [دارای ظرفیت ثبت نام]", "value": "19", "selected": False},
            {"text": "بانک ملت [دارای ظرفیت ثبت نام]", "value": "20", "selected": False},
        ]

        with patch("bot_select_bank._read_dropdown_options", return_value=options), \
             patch("bot_select_bank._select_dropdown_with_postback") as mock_select_postback, \
             patch("bot_select_bank._verify_dropdown_selection", return_value=(True, "19", "بانک صادرات")), \
             patch("bot_select_bank.return_to_bank_field") as mock_reset:

            # Saderat online mode fails with capacity full dialog, but Mellat succeeds!
            def mock_select_mode(bot, page, mode, bank_name):
                if bank_name == "صادرات":
                    pending = [True]
                    def consume(key):
                        if key == 'bank_capacity_full' and pending[0]:
                            pending[0] = False
                            return True
                        return False
                    bot.consume_recovery_flag.side_effect = consume
                    return "online", False  # Saderat mode full
                else:
                    bot.consume_recovery_flag.side_effect = None
                    return "online", True   # Mellat succeeds

            with patch("bot_select_bank._select_bank_mode", side_effect=mock_select_mode), \
                 patch("bot_select_bank._detect_page_error_or_capacity_message", return_value=(None, "")):

                res = process_bank_selection_v2(self.bot, mock_page, self.stop_event)

                # Verified: Bot automatically moved to second priority bank (Mellat)
                self.assertEqual(res, "option_selected_waiting_user")
                # Dropdown was reset to 0 before selecting Mellat
                mock_reset.assert_called_once()
                # Cooldown was registered for Saderat
                self.assertIn(canonicalize_bank_name("صادرات"), self.bot._bank_cooldowns)

    @patch("bot_select_bank._ensure_state_and_city_selected")
    @patch("bot_select_bank.load_runtime_data")
    @patch("bot_select_bank._save_bank_page_if_changed")
    @patch("bot_select_bank.DBHandler")
    def test_fallback_to_in_person_when_online_full(
        self, mock_db, mock_save, mock_load, mock_ensure
    ):
        self.bot.settings["fallback_to_in_person"] = True
        mock_load.return_value = {
            "priority_banks": ["صادرات"],
            "bank_mode": "online",
        }
        mock_db.has_final_submission.return_value = None
        mock_db.get_runtime.return_value = {"banks_available": []}

        mock_page = MagicMock()
        options = [
            {"text": "-- انتخاب کنید --", "value": "0", "selected": False},
            {"text": "بانک صادرات [دارای ظرفیت ثبت نام]", "value": "19", "selected": False},
        ]

        with patch("bot_select_bank._read_dropdown_options", return_value=options), \
             patch("bot_select_bank._select_dropdown_with_postback"), \
             patch("bot_select_bank._verify_dropdown_selection", return_value=(True, "19", "بانک صادرات")), \
             patch("bot_select_bank.process_branch_selection", return_value="شعبه مرکزی"), \
             patch("bot_select_bank._detect_page_error_or_capacity_message", return_value=(None, "")):

            # Online fails, in_person succeeds
            def mock_select_mode(bot, page, mode, bank_name):
                if mode == "online":
                    return "online", False
                return "in_person", True

            with patch("bot_select_bank._select_bank_mode", side_effect=mock_select_mode):
                # Mock branch dropdown visible for in_person
                b_loc = MagicMock()
                b_loc.count.return_value = 1
                b_loc.first.is_visible.return_value = True
                b_loc.first.is_enabled.return_value = True
                mock_page.locator.return_value = b_loc

                res = process_bank_selection_v2(self.bot, mock_page, self.stop_event)
                self.assertEqual(res, "option_selected_waiting_user")
                # Verified: Fallback selected in_person
                self.bot.log.assert_any_call(
                    "🔄 روش غیرحضوری برای بانک صادرات در دسترس نیست؛ بر اساس تنظیمات روش حضوری بررسی می‌شود...",
                    "info",
                    mock_page,
                )


class TestSniperEndpoints(unittest.TestCase):
    """Integration test for updating Settings via REST API with sniper options."""

    @classmethod
    def setUpClass(cls):
        from server import app
        cls.client = TestClient(app, cookies={"mahan_session": "test_token"})

    def setUp(self):
        self.session_patch = patch("server._session_user", return_value={"username": "admin", "is_admin": 1, "active": 1})
        self.session_patch.start()

    def tearDown(self):
        self.session_patch.stop()

    def test_settings_endpoint_persists_sniper_config(self):
        payload = {
            "sniper_mode_enabled": True,
            "sniper_interval_ms": 6000,
            "auto_switch_alternative_bank": True,
            "fallback_to_in_person": True,
            "bank_cooldown_seconds": 25,
        }
        res = self.client.post("/settings", json=payload)
        self.assertEqual(res.status_code, 200)

        get_res = self.client.get("/settings")
        self.assertEqual(get_res.status_code, 200)
        data = get_res.json()
        self.assertTrue(data.get("sniper_mode_enabled"))
        self.assertEqual(data.get("sniper_interval_ms"), 6000)
        self.assertTrue(data.get("auto_switch_alternative_bank"))
        self.assertTrue(data.get("fallback_to_in_person"))
        self.assertEqual(data.get("bank_cooldown_seconds"), 25)


if __name__ == "__main__":
    unittest.main()
