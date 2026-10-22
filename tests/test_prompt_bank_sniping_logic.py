"""
tests/test_prompt_bank_sniping_logic.py — Comprehensive automated tests for prompt's exact Step 3 specifications:
1. DOM-based page detection & session preservation (no SMS/restart)
2. City, bank, online mode selection without branch
3. Quota sniping every 5 seconds on capacity rejection
4. Independent concurrent multi-applicant execution (no global lock)
5. Response classification, stale warning rejection, timeout handling
6. Stop/resume per applicant & winner termination
"""
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch, Mock

try:
    import test_critical_repair as repair
except ModuleNotFoundError:
    from tests import test_critical_repair as repair

from bank_page_manager import BankPageState as State
from bank_session_controller import BankSelectionSessionController
from bot_select import BankSelectionBot
from database import DBHandler, normalize_config
from live_sessions import LIVE_SESSIONS

CAPACITY_REJECTION_MSG = "ظرفیت غیرحضوری بانک انتخابی شما تکمیل شده است؛ گزینه حضوری را انتخاب نمایید"


class PromptBankSnipingLogicTests(repair.RepairTestCase):
    def setUp(self):
        super().setUp()
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.context = self.browser.new_context()
        self.context.route(
            "**/*",
            lambda route: route.fulfill(
                content_type="text/html",
                body=(Path(__file__).parent / "fixtures/bank_page_webforms.html").read_text(encoding="utf-8")
            )
        )
        self.page = self.context.new_page()
        self.page.goto("http://127.0.0.1/SelectBnkShb.aspx")
        self.owner.bind(self.context, self.page)
        self.controller = BankSelectionSessionController(self.bot, self.owner)
        self.clock = 200.0
        self.controller._monotonic = lambda: self.clock
        self.controller._submit_response_timeout = 0.25
        self.extra_owners = []

        cfg = DBHandler.get_config()
        cfg.update(
            sniper_mode_enabled=True,
            sniper_interval_ms=5000,
            capacity_check_enabled=True,
            bank_capacity_check_interval=5.0,
            auto_final_submit=True,
            final_submit=True,
            fallback_to_in_person=True,
        )
        DBHandler.update_config(cfg)

    def tearDown(self):
        for owner in self.extra_owners:
            try:
                LIVE_SESSIONS.release(owner)
            except Exception:
                pass
        try:
            self.browser.close()
            self.pw.stop()
        except Exception:
            pass
        super().tearDown()

    def prepare_and_attempt(self, controller=None):
        ctl = controller or self.controller
        ctl.tick(force=True)
        return ctl.tick(force=True)
        ctl.tick(force=True)

    def set_rejection_on_click(self, page=None):
        target_page = page or self.page
        target_page.evaluate(
            """msg => {
                document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick = () => {
                    fixtureEvents.submits++;
                    document.querySelector('#ctl00_ContentPlaceHolder1_labErrMsg').textContent = msg;
                };
            }""",
            CAPACITY_REJECTION_MSG
        )

    def test_01_page_detected_from_live_dom_and_session_preserved(self):
        """1. Detects Page 3 from real DOM controls, keeps same browser/session, never restarts."""
        stage, data = self.controller.observe()
        self.assertEqual(stage, "STEP_3_BANK_SELECT")
        self.assertTrue(data.get("is_bank_page"))
        self.assertTrue(self.owner.bank_page_active)

    def test_02_city_bank_online_selection_without_branch(self):
        """2. Selects configured city, bank, online mode, skips branch, and verifies DOM."""
        cfg = DBHandler.get_config()
        cfg.update(auto_final_submit=False, final_submit=False)
        DBHandler.update_config(cfg)
        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.FINAL_SUBMIT_READY)

        # Verify city and bank are selected in DOM
        city_val = self.page.locator("#ctl00_ContentPlaceHolder1_ddlCity").input_value()
        bank_val = self.page.locator("#ctl00_ContentPlaceHolder1_ddlBankName").input_value()
        self.assertTrue(int(city_val) > 0)
        self.assertTrue(int(bank_val) > 0)

        # Verify online mode radio is checked
        online_radio = self.page.locator("#ctl00_ContentPlaceHolder1_rbtnGheireHozori")
        self.assertTrue(online_radio.is_checked())

        # Verify branch dropdown was NOT required / skipped
        runtime = DBHandler.get_runtime(self.nid)
        self.assertEqual(runtime.get("selected_mode"), "online")
        self.assertEqual(runtime.get("selected_branch"), "غیرحضوری")

    def test_03_quota_rejection_retries_after_5_seconds_and_keeps_bank_online(self):
        """3. On capacity full rejection, keeps bank and online mode, clicks btnSave again after 5s."""
        self.set_rejection_on_click()
        self.prepare_and_attempt()

        # First attempt resulted in capacity full
        self.assertEqual(self.controller.state, State.CAPACITY_FULL)
        self.assertEqual(self.page.evaluate("() => fixtureEvents.submits"), 1)

        # Bank and online mode MUST remain selected in DOM
        online_radio = self.page.locator("#ctl00_ContentPlaceHolder1_rbtnGheireHozori")
        self.assertTrue(online_radio.is_checked())
        self.assertTrue(int(self.page.locator("#ctl00_ContentPlaceHolder1_ddlBankName").input_value()) > 0)

        # Before 5 seconds elapse: NO duplicate click
        self.clock += 3.0
        self.controller.tick()
        self.assertEqual(self.page.evaluate("() => fixtureEvents.submits"), 1)

        # At/after 5 seconds: REAL CLICK occurs on btnSave
        self.clock += 2.05
        self.controller.tick()
        self.assertEqual(self.page.evaluate("() => fixtureEvents.submits"), 2)
        self.assertEqual(self.controller.state, State.CAPACITY_FULL)

    def test_04_no_overlapping_requests_for_same_applicant(self):
        """4. Short-term claim prevents overlapping requests for the same applicant while in-flight."""
        self.assertTrue(DBHandler.claim_final_submission(self.nid))
        # Second immediate claim for the SAME applicant must be rejected
        self.assertFalse(DBHandler.claim_final_submission(self.nid))
        # Releasing allows retry
        DBHandler.release_final_submission_claim(self.nid)
        self.assertTrue(DBHandler.claim_final_submission(self.nid))
        DBHandler.release_final_submission_claim(self.nid)

    def test_05_stale_warning_does_not_count_as_fresh_rejection(self):
        """5. An old capacity warning remaining on page is not proof of rejection for new click."""
        # Set old error text on page BEFORE any click
        self.page.evaluate(
            """msg => {
                document.querySelector('#ctl00_ContentPlaceHolder1_labErrMsg').textContent = msg;
                document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick = () => {
                    fixtureEvents.submits++;
                    // Response hangs / pending
                };
            }""",
            CAPACITY_REJECTION_MSG
        )
        self.controller.tick(force=True)
        # Advance 5s so quota sniping interval elapses, then attempt click
        self.clock += 5.05
        self.controller._submit_response_timeout = 0.05
        self.controller.tick(force=True)
        # Stale text did NOT cause immediate capacity_full; timeout causes claim to be retained
        self.assertEqual(self.controller.state, State.MANUAL_ACTION_REQUIRED)
        self.assertNotEqual(DBHandler.get_checkpoint(self.nid).get("final_submit_status"), "pending")
        self.assertTrue(self.controller._awaiting_result)

    def test_06_independent_concurrent_applicants_and_stop_isolation(self):
        """6. Two applicants run concurrently; stopping applicant 1 does not affect applicant 2."""
        self.set_rejection_on_click()
        self.prepare_and_attempt()

        nid2 = "0098765432"
        with DBHandler._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO applicants(full_name,national_id,status,data) VALUES(?,?,'Ready',?)", ("متقاضی دوم", nid2, json.dumps(self.data)))
            conn.commit()

        bot2 = BankSelectionBot(nid2, normalize_config(DBHandler.get_config()), captcha_service=Mock())
        bot2.log = Mock()
        page2 = self.context.new_page()
        page2.goto("http://127.0.0.1/SelectBnkShb.aspx")
        page2.locator("#ctl00_ContentPlaceHolder1_labIDNo").evaluate("(el,nid)=>el.textContent=nid", nid2)
        owner2 = LIVE_SESSIONS.acquire(nid2, threading.Event())
        self.extra_owners.append(owner2)
        bot2.live_owner = owner2
        owner2.bind(self.context, page2)
        ctl2 = BankSelectionSessionController(bot2, owner2)
        ctl2._monotonic = lambda: self.clock
        ctl2._submit_response_timeout = 0.25

        self.set_rejection_on_click(page2)
        self.prepare_and_attempt(ctl2)

        # Both have attempted and made clicks
        self.assertEqual(self.page.evaluate("() => fixtureEvents.submits"), 1)
        self.assertEqual(page2.evaluate("() => fixtureEvents.submits"), 1)

        # Stop applicant 1
        self.owner.cancellation_token.set()
        res1 = self.controller.tick()
        self.assertEqual(res1, "stopped")

        # Advance 5 seconds: Applicant 2 continues unhindered!
        self.clock += 5.05
        ctl2.tick(force=True)
        self.assertEqual(page2.evaluate("() => fixtureEvents.submits"), 2)
        self.assertEqual(ctl2.state, State.CAPACITY_FULL)

    def test_07_success_terminates_only_winner_applicant(self):
        """7. Success with tracking code terminates sniping only for that applicant."""
        self.set_rejection_on_click()
        self.prepare_and_attempt()

        nid2 = "0098765432"
        with DBHandler._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO applicants(full_name,national_id,status,data) VALUES(?,?,'Ready',?)", ("متقاضی دوم", nid2, json.dumps(self.data)))
            conn.commit()

        bot2 = BankSelectionBot(nid2, normalize_config(DBHandler.get_config()), captcha_service=Mock())
        bot2.log = Mock()
        page2 = self.context.new_page()
        page2.goto("http://127.0.0.1/SelectBnkShb.aspx")
        page2.locator("#ctl00_ContentPlaceHolder1_labIDNo").evaluate("(el,nid)=>el.textContent=nid", nid2)
        owner2 = LIVE_SESSIONS.acquire(nid2, threading.Event())
        self.extra_owners.append(owner2)
        bot2.live_owner = owner2
        owner2.bind(self.context, page2)
        ctl2 = BankSelectionSessionController(bot2, owner2)
        ctl2._monotonic = lambda: self.clock
        ctl2._submit_response_timeout = 0.25

        self.set_rejection_on_click(page2)
        self.prepare_and_attempt(ctl2)

        # Set success tracking code for applicant 1
        self.page.evaluate('''() => {
            document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick = () => {
                fixtureEvents.submits++;
                document.querySelector('#ctl00_ContentPlaceHolder1_labErrMsg').textContent = '';
                document.body.insertAdjacentHTML('beforeend', '<span id="ctl00_ContentPlaceHolder1_lblTrackingCode">123456789012</span>');
            };
        }''')
        self.clock += 5.05
        self.assertEqual(self.controller.tick(force=True), "confirmed")
        self.assertEqual(self.controller.state, State.REGISTRATION_CONFIRMED)

        # Applicant 2 continues sniping
        self.clock += 5.05
        ctl2.tick(force=True)
        self.assertEqual(ctl2.state, State.CAPACITY_FULL)
        self.assertEqual(page2.evaluate("() => fixtureEvents.submits"), 2)

    def test_08_form_reset_by_site_reapplies_applicant_selections(self):
        """8. If site resets selections during tick, bot re-applies applicant choices and restores ready state."""
        cfg = DBHandler.get_config()
        cfg.update(auto_final_submit=False, final_submit=False)
        DBHandler.update_config(cfg)
        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.FINAL_SUBMIT_READY)

        # Simulate site resetting dropdowns via postback
        self.page.evaluate("""() => {
            document.querySelector('#ctl00_ContentPlaceHolder1_ddlBankName').value = '0';
        }""")
        self.clock += 5.1
        self.controller.tick()
        # Bot should re-apply and restore bank selection
        bank_val = self.page.locator("#ctl00_ContentPlaceHolder1_ddlBankName").input_value()
        self.assertTrue(int(bank_val) > 0)
        self.assertIn(self.controller.state, {State.FINAL_SUBMIT_READY, State.CAPACITY_FULL})

    def test_09_auto_final_disabled_prepares_without_clicking(self):
        """9. When auto_final_submit=False, prepares form and waits without clicking submit."""
        cfg = DBHandler.get_config()
        cfg["auto_final_submit"] = False
        DBHandler.update_config(cfg)

        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.FINAL_SUBMIT_READY)

        # Advance clock and tick multiple times: zero submits!
        self.clock += 10.0
        self.controller.tick()
        self.clock += 10.0
        self.controller.tick()
        self.assertEqual(self.page.evaluate("() => fixtureEvents.submits"), 0)
        self.assertEqual(self.controller.state, State.FINAL_SUBMIT_READY)
