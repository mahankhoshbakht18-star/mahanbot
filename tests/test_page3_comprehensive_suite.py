"""
tests/test_page3_comprehensive_suite.py — Mandatory 20-Scenario Test Suite.

Verifies with Mocks / Fake Data:
1. Reload Page 3 and retain stage.
2. Reload and return to SMS/OTP stage.
3. Re-entry within OTP validity window (<120s).
4. OTP expired after validity window (>120s).
5. Server disconnect / network failure.
6. Maintenance error banner detection.
7. New province bank added.
8. Bank removed from live list.
9. Bank present only in old HTML (STALE_ONLY).
10. Non-presential capacity full error.
11. In-person reserve available.
12. Reserve converted to confirmed registration.
13. Stale error message extracted from saved HTML.
14. Multi-applicant concurrency isolation.
15. Pause and resume after error.
16. Duplicate submission prevention.
17. Prevention of infinite reload loops.
18. Accurate request and transition reporting.
19. Strict distinction between local file:/// and live network.
20. No false success claim without proof.
"""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.page3_state_machine import ApplicantPage3StateMachine, Page3State
from core.province_bank_monitor import ProvinceBankMonitor
from core.saved_page_analyzer import SavedPage3Analyzer
from core.session_diagnostics import Page3SessionDiagnostics, SessionStatus
from core.page3_refresh_controller import Page3OfficialRefreshController


class TestPage3ComprehensiveSuite(unittest.TestCase):

    # ── Test 1: Reload Page 3 and Retain Stage ───────────────────────────
    def test_01_reload_page3_retains_stage(self):
        mock_bot = MagicMock(nid="1112223334", state_name="تهران")
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.url = "https://ve.cbi.ir/SelectBnkShb.aspx"
        mock_page.content.return_value = "<select id='ctl00_ContentPlaceHolder1_ddlBankName'></select><input id='ctl00_ContentPlaceHolder1_btnSave' type='submit' />"

        ctrl = Page3OfficialRefreshController(mock_bot, mock_page)
        ctrl.create_checkpoint()
        ok, _ = ctrl.execute_controlled_reload()
        self.assertTrue(ok)
        dest = ctrl.detect_destination_stage()
        self.assertEqual(dest["destination"], "PAGE3_ACTIVE")
        self.assertEqual(ctrl.fsm.current_state, Page3State.PAGE3_READY)

    # ── Test 2: Reload and Return to SMS/OTP Stage ───────────────────────
    def test_02_reload_returns_to_otp_step(self):
        mock_bot = MagicMock(nid="1112223334", state_name="تهران", _otp_entry_time=time.time() - 30.0)
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.url = "https://ve.cbi.ir/SelTasCase.aspx"
        mock_page.content.return_value = "<input id='ctl00_ContentPlaceHolder1_tbPass' type='text' />"

        ctrl = Page3OfficialRefreshController(mock_bot, mock_page)
        ctrl.create_checkpoint()
        ok, _ = ctrl.execute_controlled_reload()
        self.assertTrue(ok)
        dest = ctrl.detect_destination_stage()
        self.assertEqual(dest["destination"], "OTP_FORM")
        self.assertEqual(ctrl.fsm.current_state, Page3State.OFFICIAL_REENTRY_REQUIRED)

    # ── Test 3: Re-entry Within OTP Validity Window (<120s) ──────────────
    def test_03_reentry_within_otp_validity_window(self):
        mock_bot = MagicMock(nid="1112223334", state_name="فارس", _otp_entry_time=time.time() - 40.0)
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.url = "https://ve.cbi.ir/SelTasCase.aspx"
        mock_page.content.return_value = "<input id='ctl00_ContentPlaceHolder1_tbPass' />"

        ctrl = Page3OfficialRefreshController(mock_bot, mock_page, otp_validity_window_sec=120.0)
        dest = ctrl.detect_destination_stage()
        self.assertTrue(dest.get("can_reenter_with_cached_otp"))

    # ── Test 4: OTP Expired After Validity Window (>120s) ────────────────
    def test_04_otp_expired_after_validity_window(self):
        mock_bot = MagicMock(nid="1112223334", state_name="فارس", _otp_entry_time=time.time() - 135.0)
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.url = "https://ve.cbi.ir/SelTasCase.aspx"
        mock_page.content.return_value = "<input id='ctl00_ContentPlaceHolder1_tbPass' />"

        ctrl = Page3OfficialRefreshController(mock_bot, mock_page, otp_validity_window_sec=120.0)
        dest = ctrl.detect_destination_stage()
        self.assertEqual(dest["destination"], "OTP_EXPIRED")
        self.assertEqual(ctrl.fsm.current_state, Page3State.SESSION_EXPIRED)

    # ── Test 5: Server Disconnect / Network Failure ──────────────────────
    def test_05_server_disconnect_network_failure(self):
        mock_bot = MagicMock(nid="1112223334", state_name="تهران")
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.url = "https://ve.cbi.ir/SelectBnkShb.aspx"
        # Official Page-3 refresh uses disarm evaluate + page.reload
        mock_page.evaluate.return_value = ""
        mock_page.reload.side_effect = RuntimeError("net::ERR_CONNECTION_RESET")

        ctrl = Page3OfficialRefreshController(mock_bot, mock_page)
        ctrl.create_checkpoint()
        ok, msg = ctrl.execute_controlled_reload()
        self.assertFalse(ok)
        self.assertIn("ERR_CONNECTION_RESET", msg)
        self.assertEqual(ctrl.fsm.current_state, Page3State.ERROR)

    # ── Test 6: Maintenance Error Banner Detection ───────────────────────
    def test_06_maintenance_error_detected(self):
        mock_bot = MagicMock(nid="1112223334", state_name="اصفهان")
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.url = "https://ve.cbi.ir/SelectBnkShb.aspx"
        mock_page.content.return_value = "<div>سامانه موقتاً از دسترس خارج می‌باشد؛ لطفاً در ساعات کاری مراجعه فرمایید.</div>"

        ctrl = Page3OfficialRefreshController(mock_bot, mock_page)
        dest = ctrl.detect_destination_stage()
        self.assertEqual(dest["destination"], "MAINTENANCE")
        self.assertEqual(ctrl.fsm.current_state, Page3State.MAINTENANCE_PAUSED)

    # ── Test 7: New Province Bank Added ──────────────────────────────────
    def test_07_new_province_bank_added(self):
        monitor = ProvinceBankMonitor("کرمان")
        monitor.update_live_banks([{"value": "10", "text": "بانک ملت [تکمیل ظرفیت]" }])
        diff = monitor.update_live_banks([
            {"value": "10", "text": "بانک ملت [تکمیل ظرفیت]"},
            {"value": "12", "text": "بانک تجارت [دارای ظرفیت ثبت نام] - متقاضی در صف: 50 نفر"}
        ])
        self.assertIn("بانک تجارت", diff["added_banks"])
        self.assertTrue(diff["has_changes"])

    # ── Test 8: Bank Removed from Live List ──────────────────────────────
    def test_08_bank_removed_from_live_list(self):
        monitor = ProvinceBankMonitor("خراسان رضوی")
        monitor.update_live_banks([
            {"value": "14", "text": "بانک مسکن [دارای ظرفیت ثبت نام]"},
            {"value": "19", "text": "بانک صادرات [دارای ظرفیت ثبت نام]"}
        ])
        diff = monitor.update_live_banks([
            {"value": "14", "text": "بانک مسکن [دارای ظرفیت ثبت نام]"}
        ])
        self.assertIn("بانک صادرات", diff["removed_banks"])

    # ── Test 9: Bank Stale-Only Discrepancy (Present only in old HTML) ────
    def test_09_bank_stale_only_discrepancy(self):
        monitor = ProvinceBankMonitor("یزد")
        # Ingest historical snapshot
        monitor.register_historical_snapshot([{"text": "بانک سپه [دارای ظرفیت ثبت نام]"}])
        # Live page only has بانک مسکن
        monitor.update_live_banks([{"value": "14", "text": "بانک مسکن [دارای ظرفیت]" }])

        res = monitor.classify_bank_discrepancy("بانک سپه")
        self.assertEqual(res["status"], "STALE_ONLY")
        self.assertFalse(res["is_actionable"])
        self.assertIn("فقط در فایل‌های ذخیره‌شده قدیمی", res["warning"])

    # ── Test 10: Non-Presential Capacity Full Error ──────────────────────
    def test_10_non_presential_capacity_full_handling(self):
        fsm = ApplicantPage3StateMachine("1234567890", Page3State.BANK_AVAILABLE)
        ok = fsm.transition(Page3State.NON_PRESENTIAL_CAPACITY_FULL, reason="non_presential_full_banner_seen")
        self.assertTrue(ok)
        self.assertEqual(fsm.current_state, Page3State.NON_PRESENTIAL_CAPACITY_FULL)

    # ── Test 11: In-Person Reserve Available ─────────────────────────────
    def test_11_in_person_reserve_available(self):
        fsm = ApplicantPage3StateMachine("1234567890", Page3State.BANK_LIST_COMPARED)
        ok = fsm.transition(Page3State.BANK_RESERVATION_AVAILABLE, reason="bank_reserve_only")
        self.assertTrue(ok)
        self.assertEqual(fsm.current_state, Page3State.BANK_RESERVATION_AVAILABLE)

    # ── Test 12: Reserve Converted to Confirmed Registration ─────────────
    def test_12_reserve_conversion_to_confirmed(self):
        fsm = ApplicantPage3StateMachine("1234567890", Page3State.RESERVATION_CONFIRMED)
        ok = fsm.transition(Page3State.REGISTRATION_CONFIRMED, reason="final_tracking_code_received")
        self.assertTrue(ok)
        self.assertEqual(fsm.current_state, Page3State.REGISTRATION_CONFIRMED)

    # ── Test 13: Stale Error in Saved HTML ───────────────────────────────
    def test_13_stale_error_in_saved_html(self):
        sample_html = """
        <html><body>
            <form action="SelectBnkShb.aspx" method="post">
                <select id="ctl00_ContentPlaceHolder1_ddlBankName">
                    <option value="19">بانک صادرات [دارای ظرفیت]</option>
                </select>
                <input type="submit" id="ctl00_ContentPlaceHolder1_btnSave" />
                <span id="ctl00_ContentPlaceHolder1_labErrMsg">ظرفیت شعبه مورد نظر تکمیل می‌باشد</span>
            </form>
        </body></html>
        """
        analysis = SavedPage3Analyzer.analyze_html_content(sample_html, source_path="C:\\test\\banks.html")
        self.assertTrue(analysis["valid"])
        self.assertIn("ظرفیت شعبه مورد نظر تکمیل می‌باشد", analysis["stale_errors"])

    # ── Test 14: Multi-Applicant Non-Interference ────────────────────────
    def test_14_multi_applicant_non_interference(self):
        fsm_a = ApplicantPage3StateMachine("1111111111", Page3State.PAGE3_READY)
        fsm_b = ApplicantPage3StateMachine("2222222222", Page3State.PAGE3_READY)

        fsm_a.transition(Page3State.REFRESH_REQUESTED, reason="A_refresh")
        fsm_b.transition(Page3State.BANK_AVAILABLE, reason="B_bank_found")

        self.assertEqual(fsm_a.current_state, Page3State.REFRESH_REQUESTED)
        self.assertEqual(fsm_b.current_state, Page3State.BANK_AVAILABLE)
        self.assertEqual(fsm_a.applicant_nid, "1111111111")
        self.assertEqual(fsm_b.applicant_nid, "2222222222")

    # ── Test 15: Pause and Resume After Error ────────────────────────────
    def test_15_pause_and_resume_after_error(self):
        fsm = ApplicantPage3StateMachine("1234567890", Page3State.PAGE3_READY)
        fsm.pause(reason="maintenance_window")
        self.assertTrue(fsm.paused)
        self.assertEqual(fsm.current_state, Page3State.MAINTENANCE_PAUSED)

        # Transition blocked while paused
        blocked = fsm.transition(Page3State.REFRESH_REQUESTED)
        self.assertFalse(blocked)

        # Resume restores operation
        resumed = fsm.resume(reason="maintenance_ended")
        self.assertTrue(resumed)
        self.assertFalse(fsm.paused)
        self.assertEqual(fsm.current_state, Page3State.PAGE3_READY)

    # ── Test 16: Duplicate Submission Prevention ─────────────────────────
    def test_16_duplicate_submission_prevention(self):
        mock_bot = MagicMock(nid="1234567890", user_data={"tracking_code": "987654321"})
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False

        ctrl = Page3OfficialRefreshController(mock_bot, mock_page)
        ctrl.create_checkpoint()
        can_continue, msg = ctrl.can_continue_monitoring()
        self.assertFalse(can_continue)
        self.assertIn("قبلاً کد رهگیری", msg)

    # ── Test 17: No Infinite Reload Cycle ────────────────────────────────
    def test_17_no_infinite_reload_cycle(self):
        mock_bot = MagicMock(nid="1234567890", state_name="تهران")
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.url = "https://ve.cbi.ir/SelectBnkShb.aspx"
        mock_page.content.return_value = "<select id='ctl00_ContentPlaceHolder1_ddlBankName'></select><input id='ctl00_ContentPlaceHolder1_btnSave' type='submit'/>"

        ctrl = Page3OfficialRefreshController(mock_bot, mock_page, max_reload_attempts=2)
        ctrl.create_checkpoint()
        # Attempt 1
        ctrl.execute_controlled_reload()
        # Attempt 2
        ctrl.execute_controlled_reload()
        # Attempt 3 (exceeds max of 2)
        ok, msg = ctrl.execute_controlled_reload()
        self.assertFalse(ok)
        self.assertIn("تکمیل شده است", msg)
        self.assertEqual(ctrl.fsm.current_state, Page3State.ERROR)

    # ── Test 18: Accurate Request Result Reporting ───────────────────────
    def test_18_accurate_request_result_reporting(self):
        fsm = ApplicantPage3StateMachine("1234567890", Page3State.PAGE3_READY)
        fsm.transition(Page3State.REFRESH_REQUESTED, reason="timer_pulse")
        fsm.transition(Page3State.CHECKPOINT_SAVED, reason="checkpoint_ok")
        d = fsm.to_dict()
        self.assertEqual(d["current_state"], Page3State.CHECKPOINT_SAVED.value)
        self.assertEqual(d["applicant_nid"], "1234567890")
        self.assertEqual(d["history_count"], 3)

    # ── Test 19: Distinguish Real Server Response from Local File ────────
    def test_19_distinguish_real_server_response_from_local_js(self):
        # Local file URI
        status_local = Page3SessionDiagnostics.evaluate_session("file:///C:/Users/saved/banks.html")
        self.assertEqual(status_local["status"], SessionStatus.LOCAL_SAVED_HTML.value)

        # Live URI
        status_live = Page3SessionDiagnostics.evaluate_session(
            "https://ve.cbi.ir/SelectBnkShb.aspx",
            dom_html="<select id='ctl00_ContentPlaceHolder1_ddlBankName'></select><input id='ctl00_ContentPlaceHolder1_btnSave' />",
            last_http_status=200
        )
        self.assertEqual(status_live["status"], SessionStatus.PAGE3_LIVE_VERIFIED.value)

    # ── Test 20: No False Success Claim Without Proof ────────────────────
    def test_20_no_false_success_claim(self):
        # Unclear page without proper elements or HTTP verification
        res = Page3SessionDiagnostics.evaluate_session("https://ve.cbi.ir/UnknownPage.aspx", dom_html="<div>Nothing</div>")
        self.assertEqual(res["status"], SessionStatus.SESSION_UNKNOWN.value)
        self.assertNotEqual(res["status"], SessionStatus.PAGE3_LIVE_VERIFIED.value)


if __name__ == "__main__":
    unittest.main()
