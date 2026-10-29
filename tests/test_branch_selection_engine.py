"""
tests/test_branch_selection_engine.py — Comprehensive Test Suite (T01 - T20)
Verifying resilient branch selection on CBI marriage loan system (ve.cbi.ir/SelectBnkShb.aspx).
"""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from core.state_machine import BotState, transition_state, get_current_state
from bot_select_bank import (
    detect_branch_controls,
    wait_for_branch_fully_loaded,
    get_available_branches,
    select_branch_robust,
    select_first_available_branch,
    process_branch_selection,
    process_bank_selection_v2,
    _normalize_persian,
    _normalize_persian_key,
)
from database import DBHandler


class MockPage:
    """Mock Playwright Page for simulating ASP.NET WebForms DOM."""
    def __init__(self, controls=None, branches=None):
        self.controls = controls if controls is not None else {
            "control_type": "select",
            "select_selector": "#ctl00_ContentPlaceHolder1_ddlBranch",
            "listbox_selector": "",
            "search_selector": "",
            "hidden_selector": "",
            "is_non_presential": False,
            "options_count": 3
        }
        self.branches = branches if branches is not None else [
            {"code": "03445", "name": "مرکزی زاهدان", "text": "مرکزی زاهدان - کد 03445", "value": "03445", "selected": False},
            {"code": "03446", "name": "دانشگاه زاهدان", "text": "دانشگاه زاهدان - کد 03446", "value": "03446", "selected": False},
            {"code": "03447", "name": "معلم زاهدان", "text": "معلم زاهدان - کد 03447", "value": "03447", "selected": False},
        ]
        self.selected_val = ""
        self.selected_text = ""
        self.search_filled = ""
        self.hidden_val = ""
        self.postback_in_progress = False

    def evaluate(self, script, arg=None):
        if "get_isInAsyncPostBack" in script:
            return self.postback_in_progress
        if "is_non_presential" in script or "searchCandidates" in script:
            return dict(self.controls)
        if "const list = []" in script or "selects = document.querySelectorAll('select')" in script:
            res = []
            for b in self.branches:
                item = dict(b)
                item["selected"] = bool(b.get("selected")) or (b["value"] == self.selected_val or b["code"] == self.selected_val)
                res.append(item)
            return res
        if "el.selectedIndex >= 0" in script:
            if self.selected_val:
                return {"ok": True, "val": self.selected_val, "text": self.selected_text}
            return {"ok": False, "val": "", "text": ""}
        if "const el = document.querySelector(sel)" in script and isinstance(arg, dict):
            val = arg.get("val") or arg.get("code")
            txt = arg.get("txt")
            self.selected_val = val
            self.selected_text = txt
            self.hidden_val = arg.get("code") or val
            return True
        return None

    def locator(self, selector):
        mock_loc = MagicMock()
        mock_loc.count.return_value = 1
        mock_loc.first = MagicMock()
        mock_loc.first.is_visible.return_value = True
        mock_loc.first.is_enabled.return_value = True

        def fill_side_effect(val):
            self.search_filled = val
        mock_loc.first.fill.side_effect = fill_side_effect

        def select_option_side_effect(value=None, label=None):
            if value:
                self.selected_val = str(value)
                for b in self.branches:
                    if b["value"] == str(value) or b["code"] == str(value):
                        self.selected_text = b["text"]
                        break
            elif label:
                self.selected_text = str(label)
                for b in self.branches:
                    if b["text"] == str(label):
                        self.selected_val = b["value"]
                        break
        mock_loc.select_option.side_effect = select_option_side_effect
        mock_loc.first.select_option.side_effect = select_option_side_effect
        return mock_loc

    def wait_for_timeout(self, ms):
        pass


class TestBranchSelectionEngine(unittest.TestCase):
    """Test suite covering T01 to T20 branch selection scenarios."""

    def setUp(self):
        self.bot = MagicMock()
        self.bot.nid = "1234567890"
        self.bot.user_data = {"city": "زاهدان", "branch_code": "03445"}
        self.bot.settings = {"auto_final_submit": False}
        self.bot.log = MagicMock()
        self.bot.emit_event = MagicMock()
        self.bot.consume_recovery_flag = MagicMock(return_value=False)
        self.bot.save_checkpoint = MagicMock()
        self.bot._firewall_gate = MagicMock(return_value=False)
        self.bot.wait_for_aspnet_postback = MagicMock(return_value=True)
        self.stop_event = threading.Event()

        # Set FSM state to SELECTING_BANK for valid transitions
        DBHandler.update_runtime(self.bot.nid, fsm_state="SELECTING_BANK", current_step="SELECTING_BANK")

    # T01: Standard select detection & single option branch selection
    def test_t01_standard_select_selection(self):
        page = MockPage()
        ok, info, branches = select_branch_robust(self.bot, page, stop_event=self.stop_event)
        self.assertTrue(ok)
        self.assertEqual(info["code"], "03445")
        self.assertIn("مرکزی زاهدان", info["text"])

    # T02: Standard select matching preferred branch by Persian title
    def test_t02_preferred_branch_matching(self):
        page = MockPage()
        ok, info, branches = select_branch_robust(
            self.bot,
            page,
            preferred_branches=["دانشگاه زاهدان"],
            stop_event=self.stop_event
        )
        self.assertTrue(ok)
        self.assertEqual(info["code"], "03446")
        self.assertIn("دانشگاه", info["text"])

    # T03: ASP.NET ListBox (<select size="5">) branch selection
    def test_t03_listbox_branch_selection(self):
        page = MockPage(controls={
            "control_type": "listbox",
            "select_selector": "",
            "listbox_selector": "#ctl00_ContentPlaceHolder1_lstBranch",
            "search_selector": "",
            "hidden_selector": "",
            "is_non_presential": False,
            "options_count": 3
        })
        ok, info, branches = select_branch_robust(self.bot, page, stop_event=self.stop_event)
        self.assertTrue(ok)
        self.assertEqual(page.selected_val, "03445")

    # T04: Autocomplete / Search input filter + ListBox selection
    def test_t04_searchable_filter_input(self):
        page = MockPage(controls={
            "control_type": "searchable",
            "select_selector": "",
            "listbox_selector": "#ctl00_ContentPlaceHolder1_lstBranch",
            "search_selector": "#ctl00_ContentPlaceHolder1_txtSearch",
            "hidden_selector": "",
            "is_non_presential": False,
            "options_count": 3
        })
        ok, info, branches = select_branch_robust(
            self.bot,
            page,
            branch_code="03445",
            stop_event=self.stop_event
        )
        self.assertTrue(ok)
        self.assertEqual(page.search_filled, "03445")
        self.assertEqual(info["code"], "03445")

    # T05: Custom clickable table rows or div branch list
    def test_t05_custom_table_branch_list(self):
        page = MockPage(controls={
            "control_type": "table_list",
            "select_selector": "",
            "listbox_selector": "",
            "search_selector": "",
            "hidden_selector": "",
            "is_non_presential": False,
            "options_count": 3
        })
        ok, info, branches = select_branch_robust(self.bot, page, stop_event=self.stop_event)
        self.assertTrue(ok)
        self.assertIsNotNone(info["value"])

    # T06: Hidden branch code synchronization (hdnBranchCode)
    def test_t06_hidden_code_sync(self):
        page = MockPage(controls={
            "control_type": "select",
            "select_selector": "#ctl00_ContentPlaceHolder1_ddlBranch",
            "listbox_selector": "",
            "search_selector": "",
            "hidden_selector": "#ctl00_ContentPlaceHolder1_hdnBranchCode",
            "is_non_presential": False,
            "options_count": 3
        })
        ok, info, branches = select_branch_robust(self.bot, page, branch_code="03447", stop_event=self.stop_event)
        self.assertTrue(ok)
        self.assertEqual(page.hidden_val, "03447")

    # T07: UpdatePanel2 postback latency handling (async AJAX rendering)
    def test_t07_postback_latency_handling(self):
        page = MockPage()
        page.postback_in_progress = True
        def finish_postback():
            time.sleep(0.05)
            page.postback_in_progress = False
        threading.Thread(target=finish_postback).start()
        ctrl = wait_for_branch_fully_loaded(page, timeout_ms=3000)
        self.assertIn(ctrl.get("control_type"), ["select", "listbox", "searchable", "table_list"])

    # T08: Transition from non-in-person to in-person mode
    def test_t08_in_person_mode_transition(self):
        page = MockPage()
        rbtn_mock = MagicMock()
        rbtn_mock.count.return_value = 1
        rbtn_mock.first.is_visible.return_value = True
        with patch.object(page, 'locator', return_value=rbtn_mock):
            ok, info, branches = select_branch_robust(self.bot, page, stop_event=self.stop_event)
            self.assertTrue(ok)
            rbtn_mock.first.click.assert_called()

    # T09: Capacity full detected during branch selection phase
    def test_t09_capacity_full_handling(self):
        self.bot.consume_recovery_flag.return_value = True
        page = MockPage(controls={"control_type": "none", "options_count": 0, "is_non_presential": False}, branches=[])
        ok, info, branches = select_branch_robust(self.bot, page, stop_event=self.stop_event)
        self.assertFalse(ok)

    # T10: No branches available in DOM -> FSM transitions to BRANCH_NOT_FOUND
    def test_t10_no_branches_transition_not_found(self):
        page = MockPage(controls={"control_type": "none", "options_count": 0, "is_non_presential": False}, branches=[])
        ok, info, branches = select_branch_robust(self.bot, page, stop_event=self.stop_event)
        self.assertFalse(ok)
        self.bot.emit_event.assert_called_with(
            "BRANCH_NOT_FOUND",
            step="STEP_3_BANK_SELECT",
            reason="branches_empty",
            result="not_found",
            message=unittest.mock.ANY,
            level="warning",
            page=page,
        )

    # T11: Branch selection verification failure -> FSM transitions to BRANCH_SELECTION_FAILED
    def test_t11_verification_failure_transition(self):
        page = MockPage()
        def fail_ver_eval(script, arg=None):
            if "el.selectedIndex >= 0" in script:
                return {"ok": False, "val": "", "text": ""}
            if "const list = []" in script or "selects = document.querySelectorAll('select')" in script:
                return [dict(b, selected=False) for b in page.branches]
            return None
        page.evaluate = fail_ver_eval
        ok, info, branches = select_branch_robust(self.bot, page, stop_event=self.stop_event)
        self.assertFalse(ok)
        self.bot.emit_event.assert_called_with(
            "BRANCH_SELECTION_FAILED",
            step="STEP_3_BANK_SELECT",
            reason=unittest.mock.ANY,
            result="unverified",
            message=unittest.mock.ANY,
            level="warning",
            page=page,
        )

    # T12: Selection persistence check
    def test_t12_selection_persistence(self):
        page = MockPage()
        ok, info, branches = select_branch_robust(self.bot, page, stop_event=self.stop_event)
        self.assertTrue(ok)
        self.assertEqual(page.selected_val, "03445")

    # T13: Auto-submit disabled: halts at BRANCH_SELECTED waiting for user
    def test_t13_auto_submit_disabled_halts(self):
        self.bot.settings["auto_final_submit"] = False
        page = MockPage()
        val = process_branch_selection(self.bot, page, self.stop_event, trigger_submit=True)
        self.assertEqual(val, "03445")
        self.bot.log.assert_called()

    # T14: Manual branch selection via API
    def test_t14_manual_branch_selection_api(self):
        from routes.queue import set_applicant_branch
        # Ensure state allows transition
        DBHandler.update_runtime(self.bot.nid, fsm_state="BRANCH_NOT_FOUND", current_step="BRANCH_NOT_FOUND")
        res = set_applicant_branch(
            nid=self.bot.nid,
            payload={"branch_code": "03445", "branch_name": "مرکزی زاهدان"},
            _=True
        )
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["branch_code"], "03445")

    # T15: Persian normalization robustness (ي/ی, ك/ک, spaces)
    def test_t15_persian_normalization(self):
        arabic_text = "مركزي  زاهدان"
        persian_text = "مرکزی زاهدان"
        self.assertEqual(_normalize_persian_key(arabic_text), _normalize_persian_key(persian_text))

    # T16: Branch code matching vs branch name matching
    def test_t16_branch_code_takes_priority(self):
        page = MockPage()
        ok, info, branches = select_branch_robust(
            self.bot,
            page,
            preferred_branches=["معلم زاهدان"], # code 03447
            branch_code="03446", # code 03446
            stop_event=self.stop_event
        )
        self.assertTrue(ok)
        self.assertEqual(info["code"], "03446")

    # T17: Fallback to first available branch when preferred branch is missing
    def test_t17_fallback_to_first_branch(self):
        page = MockPage()
        ok, info, branches = select_branch_robust(
            self.bot,
            page,
            preferred_branches=["شعبه ناموجود در زاهدان"],
            stop_event=self.stop_event
        )
        self.assertTrue(ok)
        self.assertEqual(info["code"], "03445")

    # T18: Strict blocking of READY_TO_SUBMIT when branch is missing
    def test_t18_strict_blocking_ready_to_submit(self):
        page = MockPage(controls={"control_type": "none", "options_count": 0, "is_non_presential": False}, branches=[])
        with patch("bot_select_bank.select_branch_robust", return_value=(False, {}, [])):
            with patch("bot_select_bank._ensure_state_and_city_selected", return_value=True):
                with patch("bot_select_bank._read_dropdown_options", return_value=[{"text": "بانک قرض الحسنه رسالت", "value": "19"}]):
                    with patch("bot_select_bank._select_dropdown_with_postback", return_value=True):
                        with patch("bot_select_bank._verify_dropdown_selection", return_value=(True, "19", "بانک قرض الحسنه رسالت")):
                            with patch("bot_select_bank._select_bank_mode", return_value=("in_person", True)):
                                with patch("bot_select_bank.load_runtime_data", return_value={
                                    "priority_banks": ["بانک قرض الحسنه رسالت"],
                                    "mode": "in_person"
                                }):
                                    res = process_bank_selection_v2(self.bot, page, self.stop_event)
                                    self.assertEqual(res, "branch_selection_failed")

    # T19: Online mode skips branch selection cleanly without error
    def test_t19_online_mode_skips_branch(self):
        page = MockPage()
        with patch("bot_select_bank._ensure_state_and_city_selected", return_value=True):
            with patch("bot_select_bank._read_dropdown_options", return_value=[{"text": "بانک قرض الحسنه رسالت", "value": "19"}]):
                with patch("bot_select_bank._select_dropdown_with_postback", return_value=True):
                    with patch("bot_select_bank._verify_dropdown_selection", return_value=(True, "19", "بانک قرض الحسنه رسالت")):
                        with patch("bot_select_bank._select_bank_mode", return_value=("online", True)):
                            with patch("bot_select_bank.load_runtime_data", return_value={
                                "priority_banks": ["بانک قرض الحسنه رسالت"],
                                "mode": "online"
                            }):
                                res = process_bank_selection_v2(self.bot, page, self.stop_event)
                                self.assertEqual(res, "option_selected_waiting_user")

    # T20: High concurrency / re-entrant calls with stop_event
    def test_t20_stop_event_immediate_abort(self):
        page = MockPage()
        self.stop_event.set()
        ok, info, branches = select_branch_robust(self.bot, page, stop_event=self.stop_event)
        self.assertFalse(ok)
        self.assertEqual(info, {})


if __name__ == "__main__":
    unittest.main()
