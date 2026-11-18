"""Real Chromium regression fixtures: no traffic to the loan service."""
import hashlib
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from test_critical_repair import RepairTestCase
from otp_transport import fresh_record
from bank_session_controller import BankSelectionSessionController
from bot_select_otp import _locate_otp_input
from bot_select import LoanTypeAdapter
from database import DBHandler
from core.loan_workflow import workflow_for, detect_current_workflow_state
from otp_form_detector import detect_otp_form, fill_otp_form
from bot_select_otp import OTPWorkflowController
from live_sessions import LIVE_SESSIONS


class UnifiedOTPWorkflowTests(RepairTestCase):
    def setUp(self):
        super().setUp()
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.context = self.browser.new_context()
        self.context.route('**/*', lambda route: route.fulfill(
            body='<html><body></body></html>', content_type='text/html'))
        self.page = self.context.new_page()
        self.page.goto('http://127.0.0.1/SelectBnkShb.aspx')
        self.owner.bind(self.context, self.page)
        self.bot.page = self.page

    def tearDown(self):
        self.browser.close()
        self.pw.stop()
        super().tearDown()

    def otp_form(self, field_id='newSmsChallenge'):
        self.page.set_content(f'''<form><label for="{field_id}">کد تأیید پیامکی</label>
          <input id="{field_id}" autocomplete="one-time-code" maxlength="6">
          <label for="visualAnswer">کد امنیتی</label><input id="visualAnswer">
          <button type="submit">ادامه</button></form>''')

    def test_redetect_otp_after_ids_change(self):
        self.otp_form('ctl00_ContentPlaceHolder1_tbMobileConfCode')
        self.assertIsNotNone(_locate_otp_input(self.page)[0])
        self.otp_form('challenge_after_postback')
        self.assertIsNotNone(_locate_otp_input(self.page)[0])
        self.assertEqual(self.bot.detect_actual_page_stage(self.page), 'STEP_2_OTP')

    def test_return_to_otp_is_not_session_expiry(self):
        self.otp_form('ctl00_ContentPlaceHolder1_tbMobileConfCode')
        ctrl = BankSelectionSessionController(self.bot, self.owner)
        stage, _ = ctrl.observe()
        self.assertEqual(stage, 'STEP_2_OTP')

    def test_consumed_code_never_reaccepted_with_local_ttl(self):
        self.bot.mark_otp_entry_time(time.time() - 1)
        self.bot._last_retained_otp = '123456'
        self.bot._consumed_otp_digests = {hashlib.sha256(b'123456').hexdigest()}
        for status in ('consumed', 'manual', 'received'):
            with self.subTest(status=status):
                self.assertIsNone(fresh_record(self.bot, {
                    'otp': '123456', 'ts': time.time(), 'status': status,
                    'request_id': self.bot._otp_request_id, 'nid': self.nid}))

    def install_server(self, stage='otp'):
        self.server_stage = stage
        self.challenge = 1
        self.posts = []
        self.expected_code = '123456'
        self.server_status = 200
        self.reject_code = False
        self.auto_sms = False
        self.template = (Path(__file__).parent / 'fixtures/workflow_otp.html').read_text(encoding='utf-8')
        self.bank_html = '''<select id="ctl00_ContentPlaceHolder1_ddlCity"><option value="1">تهران</option></select>
          <select id="ctl00_ContentPlaceHolder1_ddlBankName"><option value="19">بانک صادرات</option></select>
          <button id="ctl00_ContentPlaceHolder1_btnSave">ثبت</button>'''
        def serve(route):
            request = route.request
            if urlsplit(request.url).hostname != '127.0.0.1':
                return route.abort()
            path = urlsplit(request.url).path
            error = ''
            if request.method == 'POST':
                self.posts.append(path)
                if path == '/request':
                    self.challenge += 1
                    self.server_stage = 'otp'
                    if self.auto_sms:
                        DBHandler.set_otp(self.nid, self.expected_code, request_id=self.bot._otp_request_id)
                elif path == '/continue':
                    values = parse_qs(request.post_data or '')
                    if values.get('sms_code') == [self.expected_code] and not self.reject_code:
                        if values.get('captcha_answer') == ['TEST']:
                            self.server_stage = 'bank'
                        else:
                            error = 'کد امنیتی اشتباه است'
                    else:
                        error = 'کد تایید منقضی شده است'
            if self.server_stage == 'bank':
                html = self.bank_html
            elif self.server_stage == 'entry':
                html = '''<form method="post" action="/request"><input id="ctl00_ContentPlaceHolder1_tbIDNo">
                    <button>درخواست پیامک</button></form>'''
            else:
                html = self.template.replace('CHALLENGE_FIELD', f'sms_{self.challenge}').replace('SERVER_ERROR', error)
            route.fulfill(body=html, status=self.server_status, content_type='text/html; charset=utf-8')
        self.context.unroute('**/*')
        self.context.route('**/*', serve)
        self.page.goto('http://127.0.0.1/SelectBnkShb.aspx')

    def deliver_and_run(self, code=None, manual=False, delayed=False):
        code = code or self.expected_code
        self.page.locator('#currentCaptcha').fill('TEST')
        if manual:
            fill_otp_form(self.page, code)
        elif not delayed:
            DBHandler.set_otp(self.nid, code, request_id=self.bot._otp_request_id)
        polls = [0]
        def wait(stop, seconds):
            polls[0] += 1
            if delayed and polls[0] == 1:
                DBHandler.set_otp(self.nid, code, request_id=self.bot._otp_request_id)
            if polls[0] >= 25:
                stop.set()
                return True
            self.page.wait_for_timeout(min(100, seconds * 1000))
            return False
        with patch('bot_select_otp._refresh_captcha_image', return_value=False):
            return OTPWorkflowController(self.bot).run(self.page, self.stop, wait)

    def test_initial_entry_otp_and_server_confirmed_bank_transition(self):
        self.install_server('entry')
        self.assertEqual(self.bot.detect_actual_page_stage(self.page), 'STEP_1_ENTRY_FORM')
        self.page.locator('input').fill(self.nid)
        self.bot.mark_otp_entry_time()
        self.page.locator('button').click()
        self.assertEqual(self.deliver_and_run(), 'bank_ready')
        self.assertEqual(self.bot.detect_actual_page_stage(self.page), 'STEP_3_BANK_SELECT')
        self.assertEqual(DBHandler.get_otp_record(self.nid)['status'], 'bank_ready')
        self.assertEqual(self.posts, ['/request', '/continue'])

    def test_native_reload_stays_on_bank_without_authentication(self):
        self.install_server('bank')
        self.bot.settings['bank_refresh_interval'] = 1
        controller = BankSelectionSessionController(self.bot, self.owner)
        self.assertEqual(controller.reload_and_redetect(), 'waiting')
        self.assertTrue(self.owner.bank_page_active)
        self.assertEqual(self.posts, [])
        self.assertEqual(workflow_for(self.bot).state, 'STEP3_CHECKING_BANKS')

    def test_reload_otp_changed_dom_new_challenge_and_reentry(self):
        self.install_server('otp')
        self.bot.mark_otp_entry_time()
        self.assertEqual(self.deliver_and_run(), 'bank_ready')
        controller = BankSelectionSessionController(self.bot, self.owner)
        self.server_stage = 'otp'
        result = controller.reload_and_redetect()
        self.assertEqual(result, 'cycle_otp', (workflow_for(self.bot).state,
                         workflow_for(self.bot).reason, controller._awaiting_result,
                         self.bot.detect_actual_page_stage(self.page)))
        self.assertEqual(OTPWorkflowController(self.bot).run(self.page, self.stop, lambda *_: False), 'otp_expired')
        first_request = self.bot._otp_request_id
        self.context.add_cookies([{'name': 'fixture_session', 'value': 'retained', 'url': 'http://127.0.0.1'}])
        with patch('bot_select.sleep_with_stop', return_value=False):
            self.bot.recover_authentication(self.page, self.stop, LoanTypeAdapter(), 'consumed_code')
        self.assertNotEqual(first_request, self.bot._otp_request_id)
        self.assertEqual(self.page.locator('[autocomplete="one-time-code"]').get_attribute('id'), 'sms_2')
        self.expected_code = '654321'
        self.assertEqual(self.deliver_and_run(delayed=True), 'bank_ready')
        self.assertEqual(self.posts.count('/continue'), 2)
        self.assertEqual(self.context.cookies()[0]['value'], 'retained')
        self.assertEqual(workflow_for(self.bot).successful_reentries, 1)

    def test_expired_otp_uses_official_resend_and_new_code(self):
        self.install_server()
        self.bot.mark_otp_entry_time()
        self.reject_code = True
        self.assertEqual(self.deliver_and_run(), 'otp_expired')
        self.assertEqual(DBHandler.get_otp_record(self.nid)['status'], 'expired')
        self.reject_code = False
        with patch('bot_select.sleep_with_stop', return_value=False):
            self.bot.recover_authentication(self.page, self.stop, LoanTypeAdapter(), 'server_expired')
        self.expected_code = '456789'
        self.assertEqual(self.deliver_and_run(), 'bank_ready')

    def test_ambiguous_dom_never_enters_either_field(self):
        self.otp_form()
        self.page.locator('form').evaluate('(e) => { const i=document.createElement("input"); i.autocomplete="one-time-code"; e.appendChild(i); }')
        self.assertEqual(detect_otp_form(self.page).reason, 'ambiguous_otp_fields')
        self.assertFalse(fill_otp_form(self.page, '123456'))
        self.assertEqual(self.page.locator('#newSmsChallenge').input_value(), '')
        self.page.set_content('''<label for="ctl00_ContentPlaceHolder1_tbMobileConfCode">کد امنیتی</label>
            <input id="ctl00_ContentPlaceHolder1_tbMobileConfCode">''')
        self.assertIsNone(_locate_otp_input(self.page)[0])
        self.assertNotEqual(self.bot.detect_actual_page_stage(self.page), 'STEP_2_OTP')

    def test_android_disconnect_manual_persian_code_in_current_form(self):
        self.install_server()
        self.bot.mark_otp_entry_time(time.time() - 1)
        with patch('bot_select_otp.get_otp_from_db_fresh', return_value=(None, None)):
            self.assertEqual(self.deliver_and_run('۱۲۳۴۵۶', manual=True), 'bank_ready')
        self.assertEqual(self.posts, ['/continue'])

    def test_wrong_applicant_challenge_and_operation_are_rejected(self):
        self.bot.mark_otp_entry_time()
        base = {'otp': '123456', 'ts': time.time(), 'nid': self.nid,
                'request_id': self.bot._otp_request_id}
        for changes in ({'nid': '0098765432'}, {'request_id': 'different'},
                        {'otp_type': 'REGISTRATION_DELETE_OTP'}, {'otp_type': 'REGISTRATION_RECOVERY_OTP'}):
            self.assertIsNone(fresh_record(self.bot, dict(base, **changes)))

    def test_target_closed_preserves_checkpoint_without_actions(self):
        self.install_server()
        DBHandler.save_checkpoint(self.nid, {'selected_bank': 'صادرات', 'city': 'تهران'})
        self.page.close()
        self.assertEqual(detect_current_workflow_state(self.page, self.bot).reason, 'target_closed')
        self.assertEqual(DBHandler.get_checkpoint(self.nid)['selected_bank'], 'صادرات')

    def test_unknown_final_result_blocks_reload_reauth_and_otp(self):
        self.install_server('bank')
        DBHandler.save_checkpoint(self.nid, {'final_submit_status': 'pending'})
        controller = BankSelectionSessionController(self.bot, self.owner)
        self.assertEqual(controller.reload_and_redetect(), 'waiting')
        with patch.object(self.bot, 'mark_otp_entry_time') as issue:
            self.assertEqual(self.bot.recover_authentication(self.page, self.stop, LoanTypeAdapter(), 'unknown'), 'manual_required')
            issue.assert_not_called()
        self.assertEqual(self.posts, [])
        from routes.jobs import start_job, restart_job
        from routes.queue import start_queued_applicant
        from core.models import JobStartRequest
        from fastapi import HTTPException
        import core.state as state
        from unittest.mock import Mock
        queue = Mock()
        queue.find_active.return_value = None
        with patch.object(state, 'JOB_QUEUE', queue):
            request = JobStartRequest(nid=self.nid, bot_name='select')
            for action in (lambda: start_job(request, True), lambda: restart_job(request, True),
                           lambda: start_queued_applicant(self.nid, True)):
                with self.assertRaises(HTTPException) as caught:
                    action()
                self.assertEqual(caught.exception.status_code, 409)
        queue.enqueue.assert_not_called()
        self.assertEqual(DBHandler.get_checkpoint(self.nid)['final_submit_status'], 'pending')

    def test_rate_limit_stops_before_otp_or_reload(self):
        self.install_server('bank')
        flow = workflow_for(self.bot)
        flow.bind(self.page)
        self.server_status = 429
        controller = BankSelectionSessionController(self.bot, self.owner)
        self.assertEqual(controller.reload_and_redetect(), 'waiting')
        self.assertEqual(flow.state, 'PAUSED')
        self.assertEqual(self.posts, [])

    def test_postback_waits_for_fresh_dom_and_disabled_continue(self):
        self.install_server()
        self.page.evaluate('''() => { window.Sys={WebForms:{PageRequestManager:{getInstance:()=>({get_isInAsyncPostBack:()=>window.busy})}}};
            window.busy=true; setTimeout(()=>{window.busy=false; document.querySelector('[autocomplete]').id='afterAjax';},120); }''')
        form = detect_otp_form(self.page, timeout_ms=700)
        self.assertEqual(form.fields['otp']['id'], 'afterAjax')
        self.page.locator('#confirmCurrentChallenge').evaluate('(e)=>e.disabled=true')
        self.assertIsNone(detect_otp_form(self.page).resolve('continue'))
        from core.step3_bank_page import Step3BankPage
        self.page.set_content('''<select id="ctl00_ContentPlaceHolder1_ddlCity" onchange="window.cityChanges=(window.cityChanges||0)+1">
            <option value="1">تهران</option><option value="2">کرج</option></select>''')
        self.assertFalse(Step3BankPage().switch_city(self.page, 'missing'))
        self.assertTrue(Step3BankPage().switch_city(self.page, '2'))
        self.assertEqual(self.page.evaluate('cityChanges'), 1)
        self.page.evaluate('''() => { window.Sys={WebForms:{PageRequestManager:{getInstance:()=>({get_isInAsyncPostBack:()=>true})}}}; }''')
        controller = BankSelectionSessionController(self.bot, self.owner)
        self.assertEqual(controller.reload_and_redetect(), 'waiting')
        self.assertEqual(self.page.locator(Step3BankPage.CITY_SELECTOR).input_value(), '2')

    def test_two_applicants_use_independent_contexts_and_challenges(self):
        import json
        import threading
        from bot_select import BankSelectionBot
        other_nid = '0098765432'
        with DBHandler._connect() as conn:
            conn.execute("INSERT INTO applicants(full_name,national_id,status,data) VALUES(?,?,'Ready',?)",
                         ('متقاضی دوم آزمایشی', other_nid, json.dumps({})))
            conn.commit()
        other_context = self.browser.new_context()
        other_context.route('**/*', lambda route: route.fulfill(body='<input autocomplete="one-time-code" id="other">', content_type='text/html'))
        other_page = other_context.new_page()
        other_page.goto('http://127.0.0.1/SelectBnkShb.aspx')
        owner = LIVE_SESSIONS.acquire(other_nid, threading.Event())
        try:
            owner.bind(other_context, other_page)
            other_bot = BankSelectionBot(other_nid, self.bot.settings)
            other_bot.live_owner = owner
            self.install_server()
            self.bot.mark_otp_entry_time()
            other_bot.mark_otp_entry_time()
            code = {'otp': '123456', 'nid': self.nid, 'request_id': self.bot._otp_request_id, 'ts': time.time()}
            self.assertIsNone(fresh_record(other_bot, code))
            self.assertTrue(fill_otp_form(self.page, '123456'))
            self.assertEqual(other_page.locator('input').input_value(), '')
            self.assertIsNot(self.owner.context, owner.context)
        finally:
            LIVE_SESSIONS.release(owner)
            other_context.close()

    def test_request_ids_agree_between_owner_queue_and_android_api(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.otp import router
        from core.otp_queue import OTPQueueManager
        self.bot.mark_otp_entry_time()
        request = OTPQueueManager.get_active_request(self.nid)
        self.assertEqual(request['otp_request_id'], self.bot._otp_request_id)
        self.assertEqual(request['session_id'], self.owner.session_id)
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client, patch.dict(__import__('os').environ, {'MAHANBOT_OTP_SECRET': 'isolated'}):
            headers = {'X-OTP-Secret': 'isolated'}
            self.assertEqual(client.post('/receive_sms', headers=headers, json={
                'nid': self.nid, 'request_id': 'stale', 'code': '123456'}).status_code, 409)
            response = client.post('/receive_sms', headers=headers, json={
                'nid': self.nid, 'request_id': self.bot._otp_request_id, 'code': '۱۲۳۴۵۶'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.bot.get_otp_code(timeout=1), '123456')
            self.assertEqual(client.post('/receive_sms', headers=headers, json={
                'nid': self.nid, 'code': '123456', 'otp_type': 'REGISTRATION_DELETE_OTP'}).status_code, 409)

    def test_recovery_is_bounded_and_preserves_the_existing_context(self):
        self.install_server()
        self.bot.settings['workflow_max_recoveries'] = 2
        with patch('bot_select.sleep_with_stop', return_value=False):
            for _ in range(2):
                self.assertEqual(self.bot.recover_authentication(self.page, self.stop, LoanTypeAdapter(), 'test'), 'redetect')
            self.assertEqual(self.bot.recover_authentication(self.page, self.stop, LoanTypeAdapter(), 'test'), 'manual_required')
        self.assertTrue(self.owner.paused)
        self.assertIs(self.owner.context, self.context)
        self.assertEqual(self.posts, ['/request', '/request'])

    def test_unknown_otp_submission_cannot_be_sent_again_on_reentry(self):
        self.install_server()
        self.bot.mark_otp_entry_time()
        DBHandler.set_otp(self.nid, '123456', status='submitted', request_id=self.bot._otp_request_id)
        self.assertEqual(OTPWorkflowController(self.bot).run(self.page, self.stop, lambda *_: False), 'manual_required')
        self.assertEqual(self.posts, [])

    def test_hidden_error_does_not_expire_live_otp_form(self):
        self.install_server()
        self.page.locator('body').evaluate('e=>e.insertAdjacentHTML("beforeend", "<div role=alert style=display:none>نشست منقضی شده</div>")')
        self.assertNotEqual(detect_current_workflow_state(self.page, self.bot).state, 'SESSION_EXPIRED')

    def test_diagnostic_contains_no_otp_captcha_or_cookie_values(self):
        import json
        self.install_server()
        self.page.locator('[autocomplete]').fill('123456')
        self.page.locator('#currentCaptcha').fill('TEST')
        evidence = workflow_for(self.bot).diagnostic(self.page, 'fixture_error')
        serialized = json.dumps(evidence)
        for secret in ('123456', 'TEST', 'fixture_session'):
            self.assertNotIn(secret, serialized)

    def test_changed_captcha_clears_previous_answer_without_submitting(self):
        import base64
        import io
        from PIL import Image
        from bot_select_otp import _solve_otp_captcha_fast
        from otp_form_detector import captcha_fingerprint
        def picture(color):
            buffer = io.BytesIO()
            Image.new('RGB', (100, 40), color).save(buffer, format='PNG')
            return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
        self.install_server()
        self.bot.mark_otp_entry_time()
        self.page.locator('body').evaluate('''(body, src) => {
            const image=document.createElement('img'); image.id='otpCaptchaImage'; image.src=src; body.appendChild(image);
        }''', picture('red'))
        self.page.locator('img').evaluate('img=>img.decode()')
        self.bot._otp_captcha_challenge = (self.bot._otp_request_id, captcha_fingerprint(detect_otp_form(self.page)))
        self.page.locator('img').evaluate('(img,src)=>{img.src=src; return img.decode()}', picture('blue'))
        fill_otp_form(self.page, '123456')
        self.page.locator('#currentCaptcha').fill('OLD')
        self.assertFalse(_solve_otp_captcha_fast(self.bot, self.page))
        self.assertEqual(self.page.locator('#currentCaptcha').input_value(), '')
        self.assertEqual(self.posts, [])

    def test_detection_measurement_has_bounded_nonsecret_output(self):
        import json
        import statistics
        self.install_server()
        samples = [detect_otp_form(self.page).elapsed_ms for _ in range(10)]
        self.assertTrue(all(sample > 0 for sample in samples))
        print('WORKFLOW_BENCHMARK=' + json.dumps({'samples': len(samples),
              'otp_detector_median_ms': round(statistics.median(samples), 3),
              'otp_detector_max_ms': max(samples)}))

    def test_http_repairs_queue_commands_without_cross_thread_browser_access(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.saved_pages import router
        from core.deps import require_api_key
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_api_key] = lambda: True
        self.owner.bank_page_active = True
        with patch.object(self.page, 'is_closed', side_effect=AssertionError('cross-thread access')):
            with TestClient(app) as client:
                self.assertEqual(client.post(f'/saved-pages/switch-city/{self.nid}', json={'city_code': '793'}).status_code, 200)
                self.assertEqual(client.post(f'/saved-pages/live-repair/{self.nid}').status_code, 200)
        self.assertEqual(self.owner.commands.get_nowait(), {'action': 'switch_city', 'city_code': '793'})
        self.assertEqual(self.owner.commands.get_nowait(), 'redetect')

    def test_old_legacy_message_cannot_cross_new_request_time(self):
        self.bot.mark_otp_entry_time()
        for status in ('received', 'manual'):
            self.assertIsNone(fresh_record(self.bot, {'nid': self.nid, 'otp': '123456',
                'ts': self.bot._otp_entry_time - 1, 'status': status}))
        for timestamp in (float('nan'), float('inf')):
            self.assertIsNone(fresh_record(self.bot, {'otp': '123456', 'ts': timestamp, 'status': 'manual'}))

    def test_server_error_cannot_start_bank_selection_or_final_submit(self):
        self.install_server('bank')
        controller = BankSelectionSessionController(self.bot, self.owner)
        self.server_status = 503
        self.page.reload()
        with patch('bank_session_controller.process_bank_selection_v2') as select:
            self.assertEqual(controller.tick(), 'waiting')
            select.assert_not_called()
        self.assertEqual(workflow_for(self.bot).reason, 'server_error_requires_review')

    def test_tracking_placeholder_is_not_server_confirmation(self):
        self.page.set_content('<span id="ctl00_ContentPlaceHolder1_lblTrackingCode">در انتظار تأیید</span>')
        self.assertEqual(detect_current_workflow_state(self.page, self.bot).state, 'UNKNOWN')
        self.assertIsNone(DBHandler.has_final_submission(self.nid))

    def test_archive_uses_temporary_root_and_captures_dom_on_owner_thread(self):
        import os
        import threading
        from core.step3_bank_page import Step3BankPage
        self.install_server('bank')
        step = Step3BankPage()
        options = step.read_live_bank_options(self.page)
        step.archive_banks_seen_nonblocking(self.bot, options, self.page)
        for worker in threading.enumerate():
            if worker.name == 'mahan-bank-archive':
                worker.join(timeout=3)
        self.assertTrue((Path(os.environ['MAHANBOT_SAVED_PAGES_DIR']) / 'banks_seen.json').is_file())
        self.assertFalse(self.page.is_closed())

    def test_whole_worker_recovers_from_bank_to_otp_in_the_same_context(self):
        self.install_server('entry')
        self.auto_sms = True
        self.template = self.template.replace('name="captcha_answer" maxlength="4"',
                                              'name="captcha_answer" maxlength="4" value="TEST"')
        self.bank_html = (Path(__file__).parent / 'fixtures/bank_page_webforms.html').read_text(encoding='utf-8')
        cfg = DBHandler.get_config()
        cfg.update(auto_final_submit=False, final_submit=False)
        DBHandler.update_config(cfg)
        LIVE_SESSIONS.release(self.owner)
        observed = []
        original_tick = BankSelectionSessionController.tick
        def primary(page, stop):
            self.bot.mark_otp_entry_time()
            page.locator('button').click()
            return True
        def tick(controller, force=False):
            result = original_tick(controller, force)
            observed.append((controller.owner.page is self.page, controller.owner.context is self.context))
            if len(observed) == 1:
                self.expected_code = '654321'
                self.server_stage = 'otp'
                return controller.reload_and_redetect()
            self.stop.set()
            return 'stopped'
        with patch('bot_select.SELECT_BNK_SHB_URL', 'http://127.0.0.1/SelectBnkShb.aspx'), \
             patch.object(self.bot, 'setup_browser', return_value=(self.pw, self.browser, self.context, self.page)) as launch, \
             patch.object(self.bot, '_solve_primary_captcha_fast', side_effect=primary), \
             patch('bank_session_controller.BankSelectionSessionController.tick', tick), \
             patch('bot_select.close_browser'):
            self.bot.run(self.stop)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(observed, [(True, True), (True, True)])
        self.assertEqual(self.posts.count('/continue'), 2)
        self.assertEqual(DBHandler.get_otp_record(self.nid)['status'], 'bank_ready')
