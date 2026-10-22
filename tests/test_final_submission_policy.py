"""Acceptance scenarios for the requested October 21 submission policy, with no live service."""
import json
from pathlib import Path
import threading
from unittest.mock import Mock

import test_critical_repair as repair
from bank_session_controller import BankSelectionSessionController
from bank_page_manager import BankPageState as State
from bot_select import BankSelectionBot
from database import DBHandler
from live_sessions import LIVE_SESSIONS

FULL = 'ظرفیت ثبت نام بانک تکمیل شده است'


class FinalSubmissionPolicyTests(repair.RepairTestCase):
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
        self.ctl = BankSelectionSessionController(self.bot, self.owner)
        self.clock = 100.0
        self.ctl._monotonic = lambda: self.clock
        self.ctl._submit_response_timeout = .15
        self.extra = []
        self.config(False, 7)

    def tearDown(self):
        for ctl, owner in self.extra:
            ctl.capacity_monitor.cleanup()
            LIVE_SESSIONS.release(owner)
        self.ctl.capacity_monitor.cleanup()
        self.browser.close()
        self.pw.stop()
        super().tearDown()

    def config(self, enabled, interval=7):
        cfg = DBHandler.get_config()
        cfg.update(auto_final_submit=enabled, final_submit=enabled, bank_capacity_check_interval=interval,
                   bank_capacity_recheck_seconds=interval, sniper_mode_enabled=False, fallback_to_in_person=True)
        DBHandler.update_config(cfg)

    def applicant(self, **updates):
        self.assertTrue(DBHandler.update_applicant_data(self.nid, updates))

    def reject(self, page=None):
        (page or self.page).evaluate('''text=>{
            document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick=()=>{
                fixtureEvents.submits++;
                document.querySelector('#ctl00_ContentPlaceHolder1_labErrMsg').textContent=text;
            };
        }''', FULL)

    def in_person(self):
        self.applicant(bank_mode='in_person', priority_branches=['77'])
        self.page.evaluate('''()=>document.body.insertAdjacentHTML('beforeend',
            '<select id="ctl00_ContentPlaceHolder1_ddlBranch"><option value="0">انتخاب کنید</option><option value="77">شعبه آزمایشی ۷۷</option><option value="88">شعبه دیگر</option></select>')''')

    def test_global_off_prepares_even_when_bank_is_available(self):
        self.ctl.tick(force=True)
        self.assertEqual(self.ctl.state, State.FINAL_SUBMIT_READY)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 0)
        self.assertTrue(self.page.locator('#ctl00_ContentPlaceHolder1_rbtnGheireHozori').is_checked())

    def test_global_on_submits_immediately_in_first_tick_without_sniper_switch(self):
        self.config(True)
        self.reject()
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.assertEqual(self.ctl.state, State.CAPACITY_FULL)

    def test_explicit_applicant_off_overrides_global_on(self):
        self.config(True)
        self.applicant(auto_final_submit=False)
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 0)

    def test_applicant_on_overrides_global_off_and_custom_interval_is_used(self):
        self.applicant(auto_final_submit=True, final_submit_interval_seconds=3)
        self.reject()
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.clock += 2.99
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.clock += .01
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 2)
        self.assertEqual(DBHandler.get_runtime(self.nid)['check_interval'], 3)

    def test_in_person_retries_and_preserves_branch_city_and_mode(self):
        self.config(True, 4)
        self.in_person()
        self.reject()
        self.ctl.tick(force=True)
        self.clock += 4
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 2)
        self.assertEqual(self.page.locator('#ctl00_ContentPlaceHolder1_ddlBranch').input_value(), '77')
        self.assertTrue(self.page.locator('#ctl00_ContentPlaceHolder1_rbtnHozori').is_checked())
        self.assertFalse(self.page.locator('#ctl00_ContentPlaceHolder1_rbtnGheireHozori').is_checked())

    def test_old_capacity_label_does_not_prevent_in_person_preparation(self):
        self.config(True)
        self.in_person()
        self.page.locator('#ctl00_ContentPlaceHolder1_labErrMsg').evaluate('(el,text)=>el.textContent=text', FULL)
        self.reject()
        self.ctl.tick(force=True)
        self.assertEqual(self.page.locator('#ctl00_ContentPlaceHolder1_ddlBranch').input_value(), '77')
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)

    def test_preferred_method_can_fallback_if_control_is_unavailable_before_submission(self):
        self.applicant(bank_mode='prefer_in_person')
        self.page.locator('#ctl00_ContentPlaceHolder1_rbtnHozori').evaluate('el => el.disabled=true')
        self.ctl.tick(force=True)
        self.assertEqual(self.ctl.state, State.FINAL_SUBMIT_READY)
        self.assertTrue(self.page.locator('#ctl00_ContentPlaceHolder1_rbtnGheireHozori').is_checked())
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 0)

    def test_checkbox_off_stops_next_attempt_without_closing_page(self):
        self.config(True)
        self.reject()
        self.ctl.tick(force=True)
        self.applicant(auto_final_submit=False)
        self.clock += 20
        for _ in range(2):
            self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.assertFalse(self.page.is_closed())

    def test_enabling_prepared_form_submits_immediately_without_force(self):
        self.ctl.tick(force=True)
        self.reject()
        self.applicant(auto_final_submit=True)
        self.ctl.tick()
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)

    def test_unknown_result_has_no_durable_pending_user_lock_and_observes_late_success(self):
        self.config(True)
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.assertNotEqual(DBHandler.get_checkpoint(self.nid).get('final_submit_status'), 'pending')
        self.clock += 30
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.page.evaluate("document.body.insertAdjacentHTML('beforeend','<span id=\"ctl00_ContentPlaceHolder1_lblTrackingCode\">123456789012</span>')")
        self.assertEqual(self.ctl.tick(force=True), 'confirmed')

    def test_no_reentrant_double_click_during_request(self):
        self.config(True)
        self.page.expose_function('nestedTick', lambda: self.ctl.tick(force=True))
        self.page.evaluate('''text=>{
            document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick=()=>{
                fixtureEvents.submits++; window.nestedTick();
                document.querySelector('#ctl00_ContentPlaceHolder1_labErrMsg').textContent=text;
            };
        }''', FULL)
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)

    def test_checkbox_disabled_during_response_stops_subsequent_clicks(self):
        self.config(True)
        self.page.expose_function('disableAutomaticSubmit', lambda: self.applicant(auto_final_submit=False))
        self.page.evaluate('''text=>document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick=()=>{
            fixtureEvents.submits++;disableAutomaticSubmit();
            document.querySelector('#ctl00_ContentPlaceHolder1_labErrMsg').textContent=text;
        }''', FULL)
        self.ctl.tick(force=True)
        self.clock += 30
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.assertEqual(DBHandler.get_runtime(self.nid)['next_refresh'], 0)

    def test_reset_form_is_reapplied_before_retry_and_online_leaves_branch_to_bank(self):
        self.config(True, 3)
        self.page.evaluate('''()=>{
            document.querySelector('#ctl00_ContentPlaceHolder1_ddlCity').add(new Option('انتخاب کنید','0'));
            document.body.insertAdjacentHTML('beforeend','<select id="ctl00_ContentPlaceHolder1_ddlBranch"><option value="0">انتخاب کنید</option><option value="77">شعبه آزمایشی</option></select>');
        }''')
        self.reject()
        self.ctl.tick(force=True)
        self.page.select_option('#ctl00_ContentPlaceHolder1_ddlCity', '0')
        self.page.select_option('#ctl00_ContentPlaceHolder1_ddlBankName', '0')
        self.page.locator('#ctl00_ContentPlaceHolder1_rbtnHozori').check()
        self.clock += 3
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 2)
        self.assertNotEqual(self.page.locator('#ctl00_ContentPlaceHolder1_ddlCity').input_value(), '0')
        self.assertEqual(self.page.locator('#ctl00_ContentPlaceHolder1_ddlBankName').input_value(), '19')
        self.assertTrue(self.page.locator('#ctl00_ContentPlaceHolder1_rbtnGheireHozori').is_checked())
        self.assertEqual(self.page.locator('#ctl00_ContentPlaceHolder1_ddlBranch').input_value(), '0')

    def test_two_applicants_keep_independent_enable_flags_and_timers(self):
        self.applicant(auto_final_submit=False)
        nid2 = '0098765432'
        data2 = dict(self.data, auto_final_submit=True, final_submit_interval_seconds=2)
        with DBHandler._connect() as conn:
            conn.execute("INSERT INTO applicants(full_name,national_id,status,data) VALUES(?,?,'Ready',?)", ('متقاضی دوم آزمایشی', nid2, json.dumps(data2)))
            conn.commit()
        bot2 = BankSelectionBot(nid2, DBHandler.get_config(), captcha_service=Mock())
        bot2.log = Mock()
        page2 = self.context.new_page()
        page2.goto('http://127.0.0.1/SelectBnkShb.aspx')
        page2.locator('#ctl00_ContentPlaceHolder1_labIDNo').evaluate('(el,nid)=>el.textContent=nid', nid2)
        owner2 = LIVE_SESSIONS.acquire(nid2, threading.Event())
        bot2.live_owner = owner2
        owner2.bind(self.context, page2)
        ctl2 = BankSelectionSessionController(bot2, owner2)
        ctl2._monotonic = lambda: self.clock
        ctl2._submit_response_timeout = .15
        self.extra.append((ctl2, owner2))
        self.reject(page2)
        self.ctl.tick(force=True)
        ctl2.tick(force=True)
        self.clock += 2
        self.ctl.tick(force=True)
        ctl2.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 0)
        self.assertEqual(page2.evaluate('fixtureEvents.submits'), 2)

    def test_pause_resume_and_cancel_are_local(self):
        self.config(True)
        self.reject()
        self.ctl.tick(force=True)
        self.owner.commands.put('pause')
        self.clock += 7
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.owner.commands.put('resume')
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 2)
        self.stop.set()
        self.clock += 7
        self.assertEqual(self.ctl.tick(force=True), 'stopped')
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 2)

    def test_late_post_response_is_reconciled_without_an_overlapping_request(self):
        self.config(True, 3)
        held = []
        self.context.route('**/delayed-save', lambda route: held.append(route))
        self.page.evaluate('''()=>{
            document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick=()=>{
                fixtureEvents.submits++;
                fetch('/delayed-save',{method:'POST',body:'ctl00%24ContentPlaceHolder1%24btnSave=save'});
            };
        }''')
        self.ctl.tick(force=True)
        self.assertEqual(len(held), 1)
        self.clock += 30
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        held[0].fulfill(content_type='text/html', body=f'<span id="ctl00_ContentPlaceHolder1_labErrMsg">{FULL}</span>')
        self.page.wait_for_timeout(100)
        self.ctl.tick(force=True)
        self.assertEqual(self.ctl.state, State.CAPACITY_FULL)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.assertFalse(DBHandler.get_runtime(self.nid)['submission_in_flight'])
        self.clock += 3
        self.ctl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 2)
        self.assertEqual(len(held), 2)
        held[1].fulfill(content_type='text/html', body='<span id="ctl00_ContentPlaceHolder1_lblMessage">درخواست رزرو شما با موفقیت ثبت شد</span>')
        self.page.wait_for_timeout(100)
        self.ctl.tick(force=True)
        cp = DBHandler.get_checkpoint(self.nid) or {}
        self.assertTrue(cp.get('reserve_confirmed'))
        self.assertFalse(DBHandler.has_final_submission(self.nid))
        self.clock += 30
        self.ctl.tick(force=True)
        self.assertEqual(self.ctl.state, State.RESERVE_PENDING)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 2)

    def test_late_registration_response_marks_success_and_preserves_updated_preferences(self):
        self.config(True)
        held = []
        self.context.route('**/delayed-save', lambda route: held.append(route))
        self.page.evaluate('''()=>{
            document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick=()=>{
                fixtureEvents.submits++;
                fetch('/delayed-save',{method:'POST',body:'ctl00%24ContentPlaceHolder1%24btnSave=save'});
            };
        }''')
        self.ctl.tick(force=True)
        self.applicant(auto_final_submit=False, final_submit_interval_seconds=2)
        held[0].fulfill(content_type='text/html', body='<span id="ctl00_ContentPlaceHolder1_lblTrackingCode">123456789012</span>')
        self.page.wait_for_timeout(100)
        self.assertEqual(self.ctl.tick(force=True), 'confirmed')
        self.assertEqual(DBHandler.get_runtime(self.nid)['status'], 'SUCCESS')
        data = json.loads(DBHandler.get_applicant(self.nid)['data'])
        self.assertIs(data['auto_final_submit'], False)
        self.assertEqual(data['final_submit_interval_seconds'], 2)
        self.clock += 30
        self.assertEqual(self.ctl.tick(force=True), 'confirmed')
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)


class SubmissionSettingsApiTests(repair.RepairTestCase):
    def test_settings_patch_is_validated_preserves_other_data_and_supports_inheritance(self):
        from fastapi.testclient import TestClient
        from server import app
        with TestClient(app) as client:
            url = f'/applicants/{self.nid}/submission-settings'
            self.assertEqual(client.patch(url, json={'auto_final_submit': False, 'final_submit_interval_seconds': 2.5}).status_code, 200)
            data = json.loads(DBHandler.get_applicant(self.nid)['data'])
            self.assertIs(data['auto_final_submit'], False)
            self.assertEqual(data['final_submit_interval_seconds'], 2.5)
            self.assertEqual(data['city'], self.data['city'])
            self.assertEqual(data['priority_banks'], self.data['priority_banks'])
            for invalid in ({'auto_final_submit': 'false'}, {'final_submit_interval_seconds': 0}, {'final_submit_interval_seconds': 601}, {'national_id': '0098765432'}):
                self.assertEqual(client.patch(url, json=invalid).status_code, 422)
            self.assertEqual(client.patch(url, json={'auto_final_submit': None, 'final_submit_interval_seconds': None}).status_code, 200)
            data = json.loads(DBHandler.get_applicant(self.nid)['data'])
            self.assertIsNone(data['auto_final_submit'])
            self.assertEqual(client.patch('/applicants/0098765432/submission-settings', json={'auto_final_submit': True}).status_code, 404)
            self.assertEqual(client.post('/api/capacity/interval', json={'interval_seconds': 1.5}).status_code, 200)
            config = DBHandler.get_config()
            self.assertEqual(config['bank_capacity_check_interval'], 1.5)
            self.assertEqual(config['bank_capacity_recheck_seconds'], 1.5)


class ConcurrentSubmissionTests(repair.RepairTestCase):
    def test_waiting_worker_does_not_block_a_second_browser_worker(self):
        LIVE_SESSIONS.release(self.owner)
        nids = [self.nid, '0098765432']
        with DBHandler._connect() as conn:
            conn.execute("INSERT INTO applicants(full_name,national_id,status,data) VALUES(?,?,'Ready',?)", ('متقاضی دوم آزمایشی', nids[1], json.dumps(self.data)))
            conn.commit()
        DBHandler.update_config({'auto_final_submit': True, 'final_submit': True, 'bank_capacity_check_interval': 3})
        ready = threading.Barrier(2)
        first_clicked, second_done = threading.Event(), threading.Event()
        stops = [threading.Event(), threading.Event()]
        results, errors = {}, []
        fixture = (Path(__file__).parent/'fixtures/bank_page_webforms.html').read_text(encoding='utf-8')

        def worker(index):
            from playwright.sync_api import sync_playwright
            ctl = owner = None
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    context = browser.new_context()
                    context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=fixture))
                    page = context.new_page()
                    page.goto('http://127.0.0.1/SelectBnkShb.aspx')
                    page.locator('#ctl00_ContentPlaceHolder1_labIDNo').evaluate('(el,nid)=>el.textContent=nid', nids[index])
                    bot = BankSelectionBot(nids[index], DBHandler.get_config(), captcha_service=Mock())
                    bot.log = Mock()
                    owner = LIVE_SESSIONS.acquire(nids[index], stops[index])
                    bot.live_owner = owner
                    owner.bind(context, page)
                    ctl = BankSelectionSessionController(bot, owner)
                    ctl._submit_response_timeout = 10
                    if index == 0:
                        page.expose_function('signalFirstClick', first_clicked.set)
                        page.evaluate("() => { document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick=()=>{fixtureEvents.submits++;signalFirstClick();}; }")
                    else:
                        page.evaluate('''text=>document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick=()=>{
                            fixtureEvents.submits++;document.querySelector('#ctl00_ContentPlaceHolder1_labErrMsg').textContent=text;
                        }''', FULL)
                    ready.wait(timeout=15)
                    if index == 1 and not first_clicked.wait(timeout=10):
                        raise AssertionError('First worker did not click')
                    outcome = ctl.tick(force=True)
                    results[index] = (outcome, ctl.state, page.evaluate('fixtureEvents.submits'))
                    if index == 1:
                        second_done.set()
                        stops[0].set()
                    else:
                        results['second_progressed_before_first_finished'] = second_done.is_set()
                    browser.close()
            except Exception as exc:
                errors.append(type(exc).__name__ + ': ' + str(exc))
                stops[0].set()
                ready.abort()
            finally:
                if ctl:
                    ctl.capacity_monitor.cleanup()
                if owner:
                    LIVE_SESSIONS.release(owner)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=25)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertTrue(results['second_progressed_before_first_finished'])
        self.assertEqual(results[0][0], 'stopped')
        self.assertEqual(results[1][1], State.CAPACITY_FULL)
        self.assertEqual([results[i][2] for i in range(2)], [1, 1])
