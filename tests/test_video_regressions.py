"""Video-derived regressions. Chromium is routed to a local fixture; SMS is mocked."""
import time
import threading
from unittest.mock import Mock, patch

try:
    import test_critical_repair as repair
except ModuleNotFoundError:
    from tests import test_critical_repair as repair
from database import DBHandler
from bank_page_manager import BankPageState as State
from live_sessions import LIVE_SESSIONS
from otp_transport import fresh_record, poll_once


class VideoBankTests(repair.RepairTestCase):
    def setUp(self):
        super().setUp()
        from playwright.sync_api import sync_playwright
        from bank_session_controller import BankSelectionSessionController
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        html = (repair.Path(__file__).parent / 'fixtures/bank_page_webforms.html').read_text(encoding='utf-8')
        self.context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=html))
        self.page.goto('http://127.0.0.1/SelectBnkShb.aspx')
        self.owner.bind(self.context, self.page)
        self.controller = BankSelectionSessionController(self.bot, self.owner)

    def tearDown(self):
        self.browser.close()
        self.pw.stop()
        super().tearDown()

    def enable_final(self):
        cfg = DBHandler.get_config()
        cfg.update(auto_final_submit=True, final_submit=True)
        DBHandler.update_config(cfg)

    def test_capacity_rejection_never_reclicks_unchanged_form(self):
        self.enable_final()
        self.page.evaluate("""() => {
            document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick = () => {
                fixtureEvents.submits++;
                document.querySelector('#ctl00_ContentPlaceHolder1_labErrMsg').textContent = 'ظرفیت ثبت نام غیرحضوری تکمیل شده است';
            };
        }""")
        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.CAPACITY_FULL)
        # An alert disappearing is not evidence that a bank has gained capacity.
        self.page.locator('#ctl00_ContentPlaceHolder1_labErrMsg').evaluate('(e) => e.textContent = ""')
        self.owner.commands.put('pause')
        self.controller.tick(force=True)
        self.owner.commands.put('resume')
        for _ in range(3):
            self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)

    def test_province_mismatch_has_actionable_reason_and_recovers(self):
        DBHandler.update_applicant_data(self.nid, {'state': 'سیستان و بلوچستان', 'city': 'سراوان'})
        self.page.evaluate("""() => {
            const span = document.createElement('span');
            span.id = 'ctl00_ContentPlaceHolder1_labStateName';
            span.textContent = 'لرستان'; document.body.appendChild(span);
        }""")
        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.MANUAL_ACTION_REQUIRED)
        self.assertEqual(DBHandler.get_checkpoint(self.nid)['last_error_reason'], 'province_mismatch')
        self.assertEqual(self.page.evaluate('fixtureEvents.postbacks.length'), 0)
        DBHandler.update_applicant_data(self.nid, {'state': 'لرستان', 'city': 'خرم آباد'})
        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.FINAL_SUBMIT_READY)
        self.assertEqual(self.page.locator('#ctl00_ContentPlaceHolder1_ddlBankName').input_value(), '19')
        self.assertTrue(self.page.locator('#ctl00_ContentPlaceHolder1_rbtnGheireHozori').is_checked())
        self.assertEqual(DBHandler.get_runtime(self.nid)['check_method'], 'live_dom_only')

    def test_wrong_applicant_dom_cannot_select_or_submit(self):
        self.enable_final()
        self.page.locator('#ctl00_ContentPlaceHolder1_labIDNo').evaluate('(e) => e.textContent="0098765432"')
        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.MANUAL_ACTION_REQUIRED)
        self.assertEqual(DBHandler.get_checkpoint(self.nid)['last_error_reason'], 'applicant_identity_mismatch')
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 0)
        self.assertFalse(self.owner.bank_page_active)

    def test_offline_html_cannot_be_a_live_bank_session(self):
        fixture = repair.Path(self.temp.name) / 'offline.html'
        fixture.write_text((repair.Path(__file__).parent / 'fixtures/bank_page_webforms.html').read_text(encoding='utf-8'), encoding='utf-8')
        self.page.goto(fixture.as_uri())
        self.assertEqual(self.bot.detect_actual_page_stage(self.page), 'OFFLINE_SNAPSHOT')
        self.controller.tick(force=True)
        self.assertFalse(self.owner.bank_page_active)
        self.assertEqual(self.page.evaluate('fixtureEvents.postbacks.length'), 0)

    def test_cancel_before_tick_prevents_all_mutation(self):
        self.enable_final()
        self.stop.set()
        self.assertEqual(self.controller.tick(force=True), 'stopped')
        self.assertEqual(self.page.evaluate('fixtureEvents.postbacks.length'), 0)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 0)

    def test_postback_timeout_never_advances_to_submit(self):
        self.enable_final()
        with patch.object(self.bot, 'wait_for_aspnet_postback', return_value=False):
            self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 0)
        self.assertEqual(self.page.evaluate('fixtureEvents.postbacks.length'), 0)

    def test_unauthorized_navigation_requires_reauthentication(self):
        response = Mock(status=401)
        response.request.is_navigation_request.return_value = True
        self.controller._on_response(response)
        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.SESSION_EXPIRED)
        self.assertFalse(self.owner.bank_page_active)
        self.assertEqual(self.page.evaluate('fixtureEvents.postbacks.length'), 0)

    def test_access_denied_preserves_page_and_stops_automated_requests(self):
        response = Mock(status=403)
        response.request.is_navigation_request.return_value = True
        self.controller._on_response(response)
        for _ in range(3):
            self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.MANUAL_ACTION_REQUIRED)
        self.assertEqual(self.page.evaluate('fixtureEvents.postbacks.length'), 0)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 0)

    def test_enabling_final_after_preparation_uses_verified_selection(self):
        self.controller.tick(force=True)
        self.enable_final()
        self.controller.tick(force=True)
        for _ in range(2):
            self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)


class VideoStateTests(repair.RepairTestCase):
    def test_relay_access_denied_stops_repeated_publish_attempts(self):
        import relay_transport as transport
        self.bot._otp_entry_time = time.time() - 1
        self.bot._otp_request_id = 'fixture-request'
        env = {'MAHAN_OTP_RELAY_DISABLED': '0', 'RELAY_BOT_KEY': 'fixture-key', 'MAHAN_OTP_RELAY_URL': 'https://relay.invalid'}
        with patch.dict(repair.os.environ, env), patch('relay_transport.requests.post', return_value=Mock(status_code=403)) as post:
            self.assertFalse(transport.publish_wait(self.bot))
            self.assertFalse(transport.publish_wait(self.bot))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(DBHandler.get_runtime(self.nid)['otp_transport_error'], 'relay_access_denied')

    def test_relay_rate_limit_respects_retry_after(self):
        import relay_transport as transport
        self.bot._otp_entry_time = time.time() - 1
        self.bot._otp_request_id = 'fixture-request'
        env = {'MAHAN_OTP_RELAY_DISABLED': '0', 'RELAY_BOT_KEY': 'fixture-key', 'MAHAN_OTP_RELAY_URL': 'https://relay.invalid'}
        with patch.dict(repair.os.environ, env), patch('relay_transport.requests.post', return_value=Mock(status_code=429, headers={'Retry-After': '60'})) as post:
            self.assertFalse(transport.publish_wait(self.bot))
            self.assertFalse(transport.publish_wait(self.bot))
        self.assertEqual(post.call_count, 1)
        self.assertGreater(self.bot._relay_retry_at, time.monotonic() + 59)

    def test_relay_invalid_response_is_distinct_from_timeout(self):
        import relay_transport as transport
        import requests
        self.bot._otp_entry_time = time.time() - 1
        self.bot._otp_request_id = 'fixture-request'
        env = {'MAHAN_OTP_RELAY_DISABLED': '0', 'RELAY_BOT_KEY': 'fixture-key', 'MAHAN_OTP_RELAY_URL': 'https://relay.invalid'}
        with patch.dict(repair.os.environ, env), patch('relay_transport.requests.post', return_value=Mock(status_code=200, **{'json.return_value': []})):
            self.assertFalse(transport.publish_wait(self.bot))
        self.assertEqual(DBHandler.get_runtime(self.nid)['otp_transport_error'], 'relay_invalid_response')
        with patch.dict(repair.os.environ, env), patch('relay_transport.requests.post', side_effect=requests.Timeout('private')):
            self.assertFalse(transport.publish_wait(self.bot))
        self.assertEqual(DBHandler.get_runtime(self.nid)['otp_transport_error'], 'relay_network_timeout')

    def test_successful_relay_pull_is_request_bound(self):
        self.bot._otp_entry_time = time.time() - 1
        self.bot._otp_request_id = 'fixture-request'
        env = {'MAHAN_OTP_RELAY_DISABLED': '0', 'RELAY_BOT_KEY': 'fixture-key', 'MAHAN_OTP_RELAY_URL': 'https://relay.invalid'}
        with patch.dict(repair.os.environ, env), \
             patch('relay_transport.requests.post', return_value=Mock(status_code=200, **{'json.return_value': {'accepted': True}})), \
             patch('relay_transport.requests.get', return_value=Mock(status_code=200, headers={}, **{'json.return_value': {'otp': '123456', 'ts': time.time(), 'request_id': 'fixture-request'}})):
            self.assertEqual(poll_once(self.bot, self.stop), '123456')

    def test_checkpoint_merge_cannot_erase_concurrent_submission_claim(self):
        import json
        entered, release, claimed = threading.Event(), threading.Event(), threading.Event()
        original_dumps = json.dumps
        errors = []
        def dumps(payload, *args, **kwargs):
            if isinstance(payload, dict) and payload.get('test_writer') and threading.current_thread().name == 'checkpoint-writer':
                entered.set()
                release.wait(3)
            return original_dumps(payload, *args, **kwargs)
        def write():
            try:
                DBHandler.save_checkpoint(self.nid, {'test_writer': True})
            except Exception as exc:
                errors.append(type(exc).__name__)
        def claim():
            try:
                DBHandler.claim_final_submission(self.nid)
            finally:
                claimed.set()
        with patch('database.json.dumps', side_effect=dumps):
            writer = threading.Thread(target=write, name='checkpoint-writer')
            writer.start()
            self.assertTrue(entered.wait(2))
            latch = threading.Thread(target=claim)
            latch.start()
            claimed.wait(.2)
            release.set()
            writer.join(3)
            latch.join(3)
        self.assertFalse(errors)
        self.assertFalse(writer.is_alive() or latch.is_alive())
        self.assertEqual(DBHandler.get_checkpoint(self.nid)['final_submit_status'], 'pending')

    def test_aged_or_forced_pending_submission_never_restarts(self):
        DBHandler.claim_final_submission(self.nid)
        DBHandler.save_checkpoint(self.nid, {'final_submit_started_at': time.time() - 300})
        self.bot.force = True
        LIVE_SESSIONS.release(self.owner)
        with patch.object(self.bot, 'setup_browser', side_effect=RuntimeError('must not launch')) as setup:
            self.bot.run(self.stop)
        setup.assert_not_called()
        self.assertEqual(DBHandler.get_checkpoint(self.nid)['final_submit_status'], 'pending')

    def test_restart_route_preserves_unresolved_submission(self):
        from routes.jobs import restart_job
        from core.models import JobStartRequest
        from fastapi import HTTPException
        import core.state as state
        DBHandler.claim_final_submission(self.nid)
        queue = Mock()
        queue.find_active.return_value = None
        with patch.object(state, 'JOB_QUEUE', queue):
            with self.assertRaises(HTTPException) as caught:
                restart_job(JobStartRequest(nid=self.nid, bot_name='select'), True)
        self.assertEqual(caught.exception.status_code, 409)
        queue.enqueue.assert_not_called()
        self.assertEqual(DBHandler.get_checkpoint(self.nid)['final_submit_status'], 'pending')

    def test_late_http_response_after_cancel_is_not_consumed(self):
        self.bot.mark_otp_entry_time(time.time() - 1)
        def response(*args, **kwargs):
            self.stop.set()
            return Mock(status_code=200, headers={}, **{'json.return_value': {'otp': '123456', 'ts': time.time()}})
        with patch.dict(repair.os.environ, {'MAHAN_OTP_RELAY_DISABLED': '1'}), patch('otp_transport.requests.get', side_effect=response):
            self.assertIsNone(poll_once(self.bot, self.stop))
        self.assertIsNone(DBHandler.get_otp_record(self.nid))

    def test_receiving_late_code_does_not_extend_site_otp_validity(self):
        self.bot.mark_otp_entry_time(time.time() - 121)
        self.assertIsNone(fresh_record(self.bot, {'otp': '123456', 'ts': time.time()}))

    def test_unknown_otp_submit_waits_without_resending_form(self):
        from bot_select_otp import fast_otp_sync_and_inject
        self.bot.mark_otp_entry_time(time.time() - 1)
        locator = Mock()
        locator.input_value.return_value = '123456'
        watches = []
        def watch(**kwargs):
            watches.append(True)
            if len(watches) == 2:
                self.stop.set()
                return 'stopped', None, None
            return 'timeout', None, None
        with patch('bot_select_otp._bank_select_visible_now', return_value=False), \
             patch('bot_select_otp._entry_form_visible_now', return_value=False), \
             patch('bot_select_otp._locate_otp_input', return_value=(locator, Mock())), \
             patch('bot_select_otp._detect_site_otp_ttl', return_value=120), \
             patch('bot_select_otp.get_otp_from_db_fresh', return_value=('123456', 'test')), \
             patch('bot_select_otp._solve_otp_captcha_fast', return_value=True) as submit, \
             patch('bot_select_otp._post_submit_watch', side_effect=watch), \
             patch.object(self.bot, '_firewall_gate', return_value=False):
            fast_otp_sync_and_inject(self.bot, Mock(), self.stop, lambda *_: False)
        self.assertEqual(submit.call_count, 1)
