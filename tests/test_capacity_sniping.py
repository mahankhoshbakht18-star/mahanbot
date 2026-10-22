"""Per-applicant quota submission, entirely on a routed local Chromium fixture."""
import json
from pathlib import Path
import threading
from unittest.mock import Mock

try:
    import test_critical_repair as repair
except ModuleNotFoundError:
    from tests import test_critical_repair as repair
from bank_page_manager import BankPageState as State
from bank_session_controller import BankSelectionSessionController
from bot_select import BankSelectionBot
from database import DBHandler, normalize_config
from live_sessions import LIVE_SESSIONS


FULL = 'ظرفیت غیرحضوری بانک انتخابی شما تکمیل شده و امکان ثبت نام به صورت غیرحضوری را ندارید. گزینه حضوری را انتخاب نمایید'


class CapacitySnipingTests(repair.RepairTestCase):
    def setUp(self):
        super().setUp()
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.context = self.browser.new_context()
        self.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=(Path(__file__).parent/'fixtures/bank_page_webforms.html').read_text(encoding='utf-8')))
        self.page = self.context.new_page()
        self.page.goto('http://127.0.0.1/SelectBnkShb.aspx')
        self.owner.bind(self.context, self.page)
        self.controller = BankSelectionSessionController(self.bot, self.owner)
        self.clock = 100.0
        self.controller._monotonic = lambda: self.clock
        self.controller._submit_response_timeout = .25
        self.extra_owners = []
        cfg = DBHandler.get_config()
        cfg.update(sniper_mode_enabled=True, sniper_interval_ms=5000, capacity_check_enabled=True,
                   bank_capacity_check_interval=5, auto_final_submit=True, final_submit=True,
                   fallback_to_in_person=True)
        DBHandler.update_config(cfg)

    def tearDown(self):
        for owner in self.extra_owners:
            LIVE_SESSIONS.release(owner)
        self.browser.close()
        self.pw.stop()
        super().tearDown()

    def reject_on_save(self, page=None):
        (page or self.page).evaluate('''message => {
            document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick = () => {
                fixtureEvents.submits++;
                document.querySelector('#ctl00_ContentPlaceHolder1_labErrMsg').textContent = message;
            };
        }''', FULL)

    def prepare_and_attempt(self, controller=None):
        ctl = controller or self.controller
        ctl.tick(force=True)
        return ctl.tick(force=True)

    def test_quota_retries_fresh_capacity_rejection_every_five_seconds(self):
        self.reject_on_save()
        self.prepare_and_attempt()
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.assertEqual(self.controller.state, State.CAPACITY_FULL)
        for _ in range(3):
            self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.clock += 4.99
        self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.clock += .01
        self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 2)
        self.assertEqual(DBHandler.get_checkpoint(self.nid)['final_submit_attempts'], 2)
        self.assertEqual(DBHandler.get_runtime(self.nid)['check_method'], 'native_submit_retry')
        self.assertTrue(self.page.locator('#ctl00_ContentPlaceHolder1_rbtnGheireHozori').is_checked())
        self.assertFalse(self.page.locator('#ctl00_ContentPlaceHolder1_rbtnHozori').is_checked())

    def test_quota_existing_capacity_message_does_not_hide_unknown_new_result(self):
        self.page.locator('#ctl00_ContentPlaceHolder1_labErrMsg').evaluate('(el,text)=>el.textContent=text', FULL)
        self.prepare_and_attempt()
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.assertNotEqual(DBHandler.get_checkpoint(self.nid).get('final_submit_status'), 'pending')
        self.assertTrue(self.controller._awaiting_result)
        self.clock += 30
        for _ in range(3):
            self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)

    def test_quota_capacity_after_selecting_online_keeps_bank_and_online(self):
        self.page.evaluate('''text => {
            document.querySelector('#ctl00_ContentPlaceHolder1_rbtnGheireHozori').onclick = () => {
                document.querySelector('#ctl00_ContentPlaceHolder1_labErrMsg').textContent=text;
            };
        }''', FULL)
        self.reject_on_save()
        self.prepare_and_attempt()
        self.assertEqual(self.page.locator('#ctl00_ContentPlaceHolder1_ddlBankName').input_value(), '19')
        self.assertTrue(self.page.locator('#ctl00_ContentPlaceHolder1_rbtnGheireHozori').is_checked())
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)

    def test_quota_pause_resume_and_cancel_do_not_bypass_attempt_timer(self):
        self.reject_on_save()
        self.prepare_and_attempt()
        self.owner.commands.put('pause')
        self.clock += 5
        self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.owner.commands.put('resume')
        self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 2)
        self.stop.set()
        self.clock += 5
        self.assertEqual(self.controller.tick(force=True), 'stopped')
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 2)

    def test_quota_success_stops_further_attempts(self):
        self.reject_on_save()
        self.prepare_and_attempt()
        self.page.evaluate('''() => {
            document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick=()=>{
                fixtureEvents.submits++;
                document.querySelector('#ctl00_ContentPlaceHolder1_labErrMsg').textContent='';
                document.body.insertAdjacentHTML('beforeend','<span id="ctl00_ContentPlaceHolder1_lblTrackingCode">123456789012</span>');
            };
        }''')
        self.clock += 5
        self.assertEqual(self.controller.tick(force=True), 'confirmed')
        self.clock += 5
        self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 2)

    def test_quota_confirmed_reservation_stops_further_attempts(self):
        self.page.evaluate('''() => {
            document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick=()=>{
                fixtureEvents.submits++;
                document.body.insertAdjacentHTML('beforeend','<span id="ctl00_ContentPlaceHolder1_lblSuccess">درخواست رزرو شما با موفقیت ثبت شد</span>');
            };
        }''')
        self.prepare_and_attempt()
        self.assertTrue(DBHandler.get_checkpoint(self.nid)['reserve_confirmed'])
        self.clock += 15
        self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)

    def test_quota_new_javascript_capacity_alert_allows_retry(self):
        self.bot.attach_dialog_handler(self.page)
        self.page.evaluate('''text => {
            document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick=()=>{fixtureEvents.submits++;alert(text);};
        }''', FULL)
        self.prepare_and_attempt()
        self.clock += 5
        self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 2)
        self.assertFalse(DBHandler.get_checkpoint(self.nid).get('final_submit_status'))

    def test_quota_fresh_post_response_can_reject_without_dom_replacement(self):
        body = f'<span id="ctl00_ContentPlaceHolder1_labErrMsg">{FULL}</span>'
        self.context.route('**/quota-submit', lambda route: route.fulfill(content_type='text/html', body=body))
        self.page.evaluate('''() => {
            document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick=()=>{
                fixtureEvents.submits++;
                fetch('/quota-submit',{method:'POST',body:'ctl00%24ContentPlaceHolder1%24btnSave=save'});
            };
        }''')
        self.prepare_and_attempt()
        self.assertEqual(self.controller.state, State.CAPACITY_FULL)
        self.clock += 5
        self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 2)

    def test_quota_http_error_with_capacity_text_reconciles_without_resubmitting(self):
        self.context.route('**/quota-submit', lambda route: route.fulfill(status=500, body=FULL))
        self.page.evaluate('''text => {
            document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick=()=>{
                fixtureEvents.submits++;
                document.querySelector('#ctl00_ContentPlaceHolder1_labErrMsg').textContent=text;
                fetch('/quota-submit',{method:'POST',body:'ctl00%24ContentPlaceHolder1%24btnSave=save'});
            };
        }''', FULL)
        self.prepare_and_attempt()
        self.assertNotEqual(DBHandler.get_checkpoint(self.nid).get('final_submit_status'), 'pending')
        self.assertTrue(self.controller._awaiting_result)
        self.clock += 5
        self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)

    def test_quota_changed_bank_is_reconciled_before_another_submission(self):
        self.reject_on_save()
        self.prepare_and_attempt()
        self.page.select_option('#ctl00_ContentPlaceHolder1_ddlBankName', value='13')
        self.clock += 5
        self.controller.tick(force=True)
        self.assertEqual(self.page.locator('#ctl00_ContentPlaceHolder1_ddlBankName').input_value(), '19')
        self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 2)

    def test_quota_auto_final_disabled_prepares_without_clicking_request(self):
        cfg = DBHandler.get_config()
        cfg.update(auto_final_submit=False, final_submit=False)
        DBHandler.update_config(cfg)
        self.prepare_and_attempt()
        self.clock += 5
        self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 0)
        self.assertTrue(self.page.locator('#ctl00_ContentPlaceHolder1_rbtnGheireHozori').is_checked())

    def test_quota_two_applicants_have_independent_responses_and_timers(self):
        self.prepare_and_attempt()  # First applicant is reconciling an unknown result in its own session.
        nid2 = '0098765432'
        with DBHandler._connect() as conn:
            conn.execute("INSERT INTO applicants(full_name,national_id,status,data) VALUES(?,?,'Ready',?)", ('متقاضی دوم آزمایشی', nid2, json.dumps(self.data)))
            conn.commit()
        bot2 = BankSelectionBot(nid2, normalize_config(DBHandler.get_config()), captcha_service=Mock())
        bot2.log = Mock()
        page2 = self.context.new_page()
        page2.goto('http://127.0.0.1/SelectBnkShb.aspx')
        page2.locator('#ctl00_ContentPlaceHolder1_labIDNo').evaluate('(el,nid)=>el.textContent=nid', nid2)
        owner2 = LIVE_SESSIONS.acquire(nid2, threading.Event())
        self.extra_owners.append(owner2)
        bot2.live_owner = owner2
        owner2.bind(self.context, page2)
        controller2 = BankSelectionSessionController(bot2, owner2)
        controller2._monotonic = lambda: self.clock
        controller2._submit_response_timeout = .25
        self.reject_on_save(page2)
        self.prepare_and_attempt(controller2)
        self.clock += 5
        self.controller.tick(force=True)
        controller2.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.assertEqual(page2.evaluate('fixtureEvents.submits'), 2)
        self.assertNotEqual(DBHandler.get_checkpoint(self.nid).get('final_submit_status'), 'pending')
        self.assertTrue(self.controller._awaiting_result)
        self.assertFalse(DBHandler.get_checkpoint(nid2).get('final_submit_status'))

    def test_quota_access_denied_stops_retries_even_with_old_capacity_label(self):
        self.reject_on_save()
        self.prepare_and_attempt()
        self.controller._on_response(Mock(status=403, request=Mock(method='POST')))
        self.clock += 5
        self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.assertEqual(self.controller.state, State.MANUAL_ACTION_REQUIRED)
