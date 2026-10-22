"""Acceptance tests: real Chromium on an isolated fixture, no requests to the loan site."""
import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import database
from database import DBHandler, normalize_config
from bot_core import BotCore
from bot_select import BankSelectionBot
from bank_page_manager import BankPageState as State, analyze_bank_page_html, capture_dom_html
from bank_session_controller import BankSelectionSessionController
from live_sessions import LIVE_SESSIONS
from otp_transport import fresh_record, poll_once


class RepairTestCase(unittest.TestCase):
    nid = "0012345678"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = database.DB_PATH
        database.DB_PATH = str(Path(self.temp.name) / 'test.db')
        DBHandler._settings_cache.clear()
        self.env = patch.dict(os.environ, {
            "MAHANBOT_SAVED_PAGES_DIR": str(Path(self.temp.name) / 'snapshots'),
            "MAHANBOT_PREVIEW_DIR": str(Path(self.temp.name) / 'previews'),
            "MAHANBOT_PROFILE_ROOT": str(Path(self.temp.name) / 'profiles'),
            "MAHAN_OTP_RELAY_DISABLED": "1",
            "MAHANBOT_ADMIN_PASSWORD": "isolated-test-only",
        })
        self.env.start()
        DBHandler.init_db()
        self.data = {"priority_banks": ["صادرات"], "bank_mode": "online", "city": "خرم آباد",
                     "spouse_national_id": "0098765432", "zip_code": "0012345678"}
        with DBHandler._connect() as conn:
            conn.execute("INSERT INTO applicants(full_name,national_id,status,data) VALUES(?,?,'Ready',?)",
                         ('متقاضی آزمایشی', self.nid, json.dumps(self.data)))
            conn.commit()
        self.bot = BankSelectionBot(self.nid, normalize_config({}), captcha_service=Mock())
        self.bot.log = Mock()
        LIVE_SESSIONS.reset()
        self.stop = threading.Event()
        self.owner = LIVE_SESSIONS.acquire(self.nid, self.stop)
        self.bot.live_owner = self.owner

    def tearDown(self):
        try:
            if LIVE_SESSIONS.get(self.nid) is self.owner:
                LIVE_SESSIONS.release(self.owner)
        except Exception:
            pass
        LIVE_SESSIONS.reset()
        database.DB_PATH = self.old_db
        DBHandler._settings_cache.clear()
        self.env.stop()
        self.temp.cleanup()

class CriticalRepairTests(RepairTestCase):
    def test_otp_received_before_handler_is_fresh_and_request_mismatch_rejected(self):
        issued = self.bot.mark_otp_entry_time(time.time() - 1)
        DBHandler.set_otp(self.nid, '123456', ts=issued + .1)
        self.assertEqual(self.bot.get_otp_code(timeout=1), '123456')
        self.assertIsNone(fresh_record(self.bot, {'otp': '123456', 'ts': time.time(), 'nid': '0099999999'}))
        self.assertIsNone(fresh_record(self.bot, {'otp': '123456', 'ts': time.time(), 'request_id': 'previous-request'}))
        self.assertIsNone(fresh_record(self.bot, {'otp': '123456', 'ts': time.time() - 300}))
        self.assertIsNone(fresh_record(self.bot, {'otp': '123456', 'ts': 0}))
        self.assertIsNone(fresh_record(self.bot, {'otp': '123456', 'ts': time.time(), 'status': 'consumed'}))

    def test_otp_transport_retries_and_redacts_failure_details(self):
        import requests
        DBHandler.clear_otp(self.nid)
        self.bot.mark_otp_entry_time(time.time() - 1)
        response = Mock(status_code=200, headers={})
        response.json.return_value = {'otp': '۲۳۴۵۶۷', 'ts': time.time(), 'nid': self.nid}
        with patch('otp_transport.requests.get', side_effect=[requests.ConnectionError('private'), response]), \
             patch.dict(os.environ, {'MAHANBOT_OTP_SECRET': 'test-secret', 'MAHANBOT_API_KEY': 'test-key', 'MAHAN_OTP_RELAY_DISABLED': '1'}):
            self.bot.api_base_url = 'http://relay.invalid'
            self.assertEqual(poll_once(self.bot, self.stop), '234567')
        self.assertEqual(DBHandler.get_runtime(self.nid).get('otp_transport_error'), '')

    def test_timeout_preserves_form_and_manual_fallback(self):
        from bot_select_otp import fast_otp_sync_and_inject
        page, locator = Mock(), Mock()
        locator.input_value.return_value = ''
        self.bot.mark_otp_entry_time(time.time() - 121)
        count = [0]
        def sleep(stop, seconds):
            count[0] += 1
            locator.input_value.return_value = '345678'
            return False
        with patch('bot_select_otp._bank_select_visible_now', return_value=False), \
             patch('bot_select_otp._entry_form_visible_now', return_value=False), \
             patch('bot_select_otp._locate_otp_input', return_value=(locator, page)), \
             patch('bot_select_otp._detect_site_otp_ttl', return_value=120), \
             patch('bot_select_otp.get_otp_from_db_fresh', return_value=(None, None)), \
             patch('bot_select_otp._solve_otp_captcha_fast', return_value=True), \
             patch('bot_select_otp._post_submit_watch', return_value=('bank', None, None)), \
             patch.object(self.bot, '_firewall_gate', return_value=False):
            self.assertEqual(fast_otp_sync_and_inject(self.bot, page, self.stop, sleep), 'bank_ready')
        self.assertGreater(count[0], 0)
        page.reload.assert_not_called()
        page.goto.assert_not_called()
        self.assertEqual(DBHandler.get_runtime(self.nid)['otp_state'], 'OTP_ACCEPTED')

    def test_single_browser_owner_and_no_cross_thread_operation(self):
        with self.assertRaises(RuntimeError):
            LIVE_SESSIONS.acquire(self.nid, self.stop)
        errors = []
        def other_thread():
            try:
                self.owner.assert_owner()
            except RuntimeError as exc:
                errors.append(str(exc))
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)

    def test_final_submission_latch_survives_unknown_result_and_concurrency(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(DBHandler.claim_final_submission(self.nid))) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count(True), 1)
        self.assertEqual(DBHandler.get_checkpoint(self.nid)['final_submit_attempts'], 1)
        self.assertFalse(DBHandler.claim_final_submission(self.nid))

    def test_unknown_submission_does_not_restart_browser_or_request_another_otp(self):
        DBHandler.claim_final_submission(self.nid)
        LIVE_SESSIONS.release(self.owner)
        with patch.object(self.bot, 'setup_browser') as setup:
            self.bot.run(self.stop)
        setup.assert_not_called()
        self.assertEqual(DBHandler.get_runtime(self.nid)['current_step'], 'PREVIOUS_SUBMISSION_UNRESOLVED')

    def test_resume_requires_live_session_and_never_enqueues_new_otp(self):
        from routes.saved_pages import resume_applicant_checkpoint
        self.owner.current_state = State.FINAL_SUBMIT_READY
        self.owner.bank_page_active = True
        response = asyncio.run(resume_applicant_checkpoint(self.nid, True))
        self.assertTrue(response['validation_pending'])
        self.assertEqual(self.owner.commands.get_nowait(), 'resume')
        self.owner.current_state = State.SESSION_EXPIRED
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(resume_applicant_checkpoint(self.nid, True))
        self.assertEqual(caught.exception.status_code, 409)
        LIVE_SESSIONS.release(self.owner)
        with self.assertRaises(HTTPException):
            asyncio.run(resume_applicant_checkpoint(self.nid, True))

    def test_saved_pages_snapshot_does_not_imply_live_readiness(self):
        from routes import saved_pages
        folder = Path(self.temp.name) / 'snapshots' / 'متقاضی آزمایشی'
        folder.mkdir(parents=True)
        fixture = Path(__file__).parent / 'fixtures' / 'bank_page_webforms.html'
        (folder / 'banks-test.html').write_text(fixture.read_text(encoding='utf-8'), encoding='utf-8')
        (folder / 'banks_seen.json').write_text('{"snapshots": [], "banks": []}')
        DBHandler.save_checkpoint(self.nid, {'bank_state': State.CAPACITY_FULL, 'session_active': True})
        with patch.object(saved_pages, 'SAVED_PAGES_DIR', str(folder.parent)):
            res = saved_pages.list_saved_pages(True)
            self.assertEqual(res['resumable_count'], 0)
            self.assertEqual(res['waiting_capacity_count'], 1)
            self.assertFalse(res['saved_pages'][0]['can_resume'])
            html_response = saved_pages.get_snapshot_file(folder.name, 'banks-test.html', True)
            self.assertIn('sandbox', html_response.headers['content-security-policy'])
            saved_pages.repair_saved_page_single(folder.name, True)
            self.assertEqual(self.owner.commands.get_nowait(), 'repair_checkpoint')

    def test_interval_aliases_and_settings_endpoint_agree(self):
        from routes.settings import update_settings
        from core.models import SettingsRequest
        cfg = update_settings(SettingsRequest(bank_capacity_check_interval=20, bank_list_check_interval=20), True)['config']
        for key in ('bank_capacity_check_interval', 'capacity_check_interval_seconds', 'bank_capacity_recheck_seconds',
                    'bank_list_check_interval', 'bank_list_refresh_seconds', 'bank_refresh_interval'):
            self.assertEqual(cfg[key], 20)
        cfg = update_settings(SettingsRequest(capacity_check_interval_seconds=35), True)['config']
        self.assertEqual(cfg['bank_capacity_check_interval'], 35)

    def test_android_webhook_auth_ingestion_poll_and_manual_endpoint(self):
        from fastapi.testclient import TestClient
        from server import app
        self.bot.mark_otp_entry_time(time.time() - 1)
        with TestClient(app) as client, patch.dict(os.environ, {'MAHANBOT_OTP_SECRET': 'fixture-secret'}):
            payload = {'text': f'کد تایید شما: 456789 کدملی {self.nid}'}
            self.assertEqual(client.post('/api/sms/webhook', json=payload).status_code, 401)
            received = client.post('/api/sms/webhook', json=payload, headers={'X-OTP-Secret': 'fixture-secret'})
            self.assertEqual(received.status_code, 200)
            polled = client.get(f'/wait_otp/{self.nid}', params={'timeout': 1, 'min_ts': self.bot._otp_entry_time})
            self.assertEqual(polled.json()['otp'], '456789')
            self.assertEqual(self.bot.get_otp_code(timeout=1), '456789')
            manual = client.post('/otp/manual', json={'nid': self.nid, 'code': '۵۶۷۸۹۰'})
            self.assertEqual(manual.status_code, 200)
            self.assertEqual(self.bot.get_otp_code(timeout=1), '567890')

    def test_ambiguous_waiters_and_unknown_explicit_applicant_are_rejected(self):
        from sms_service import find_active_otp_waiter, route_incoming_sms
        waiters = [{'national_id': nid, 'status': 'منتظر پیامک'} for nid in (self.nid, '0098765432')]
        with patch('sms_service.DBHandler.get_all_applicants', return_value=waiters), \
             patch('sms_service.DBHandler.get_runtime', return_value={'current_step': 'OTP_FORM'}):
            self.assertIsNone(find_active_otp_waiter())
        ok, result = route_incoming_sms('کد تایید شما: 123456', explicit_nid='0099999999')
        self.assertFalse(ok)
        self.assertEqual(result['error'], 'explicit_applicant_not_found')

    def test_window_resize_uses_only_owned_cdp_target_and_fits_small_screen(self):
        import browser_launcher
        page, context = Mock(), Mock()
        context.new_cdp_session.return_value.send.side_effect = [{'windowId': 7}, {}, {}]
        geometry = browser_launcher.compute_window_geometry({}, (0, 0, 280, 240))
        self.assertLessEqual(geometry['x'] + geometry['width'], 280)
        self.assertLessEqual(geometry['y'] + geometry['height'], 240)
        browser_launcher._apply_compact_window_bounds(page, context, geometry, {})
        context.new_cdp_session.assert_called_once_with(page)
        changes = context.new_cdp_session.return_value.send.call_args_list[1:]
        self.assertTrue(all(call.args[1]['windowId'] == 7 for call in changes))
        self.assertNotIn('EnumWindows', Path(browser_launcher.__file__).read_text(encoding='utf-8'))

    def test_saved_panel_commands_are_real_and_reauth_uses_existing_queue_api(self):
        from fastapi.testclient import TestClient
        from server import app
        import core.state as state
        from routes import saved_pages
        folder = Path(self.temp.name) / 'panel' / 'متقاضی آزمایشی'
        folder.mkdir(parents=True)
        (folder / 'banks-test.html').write_text((Path(__file__).parent / 'fixtures/bank_page_webforms.html').read_text(encoding='utf-8'), encoding='utf-8')
        with TestClient(app) as client, patch.object(saved_pages, 'SAVED_PAGES_DIR', str(folder.parent)):
            self.owner.bank_page_active = True
            self.owner.current_state = State.FINAL_SUBMIT_READY
            self.assertEqual(client.post(f'/saved-pages/pause/{self.nid}').status_code, 200)
            self.assertEqual(self.owner.commands.get_nowait(), 'pause')
            self.assertEqual(client.post(f'/saved-pages/resume/{self.nid}').status_code, 200)
            self.assertEqual(self.owner.commands.get_nowait(), 'resume')
            self.assertEqual(client.get(f'/saved-pages/logs/{self.nid}').status_code, 200)
            self.assertEqual(client.post(f'/saved-pages/{folder.name}/health-check').status_code, 200)
            self.assertEqual(client.post(f'/saved-pages/reauthenticate/{self.nid}').status_code, 409)
            LIVE_SESSIONS.release(self.owner)
            queue = Mock()
            queue.enqueue.return_value = Mock(id='fixture-job')
            with patch.object(state, 'JOB_QUEUE', queue):
                response = client.post(f'/saved-pages/reauthenticate/{self.nid}')
            self.assertEqual(response.status_code, 200)
            queue.enqueue.assert_called_once_with('select', self.nid, {})


class ChromiumBankSessionTests(RepairTestCase):
    def setUp(self):
        super().setUp()
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.network = []
        html = (Path(__file__).parent / 'fixtures' / 'bank_page_webforms.html').read_text(encoding='utf-8')
        def route(request_route):
            self.network.append(request_route.request.url)
            request_route.fulfill(status=200, content_type='text/html', body=html)
        self.context.route('**/*', route)
        self.page.goto('http://127.0.0.1/SelectBnkShb.aspx')
        self.owner.bind(self.context, self.page)
        self.controller = BankSelectionSessionController(self.bot, self.owner)

    def tearDown(self):
        try:
            self.browser.close()
            self.pw.stop()
        except Exception:
            pass  # The full-worker acceptance test closes its own Playwright driver.
        super().tearDown()

    def test_snapshot_and_bank_selection_keep_same_page_context_and_session(self):
        original_page, original_context = self.page, self.context
        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.FINAL_SUBMIT_READY)
        self.assertEqual(self.page.locator('#ctl00_ContentPlaceHolder1_ddlBankName').input_value(), '19')
        self.assertTrue(self.page.locator('#ctl00_ContentPlaceHolder1_rbtnGheireHozori').is_checked())
        self.assertTrue(list(Path(self.temp.name).rglob('*.html')))
        self.assertTrue(list(Path(self.temp.name).rglob('*.png')))
        for _ in range(3):
            self.controller.tick(force=True)
        self.assertIs(self.owner.page, original_page)
        self.assertIs(self.owner.context, original_context)
        self.assertFalse(self.page.is_closed())
        events = self.page.evaluate('fixtureEvents')
        self.assertEqual(events['unloads'], 0)
        self.assertEqual(events['submits'], 0)
        self.assertEqual(len(events['postbacks']), 1)
        self.assertEqual(len(self.network), 1)
        self.assertFalse(self.bot._reload_guarded(self.page))
        self.assertFalse(self.bot._safe_goto_guarded(self.page, 'https://ve.cbi.ir/Register.aspx'))

    def test_missing_bank_remains_active_without_synthetic_refresh(self):
        self.page.evaluate("document.querySelector('#ctl00_ContentPlaceHolder1_ddlBankName').querySelector('[value=\"19\"]').remove()")
        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.PREFERRED_BANK_NOT_FOUND)
        for _ in range(3):
            self.controller.tick(force=True)
        self.assertTrue(self.owner.bank_page_active)
        self.assertEqual(self.page.evaluate('fixtureEvents.postbacks.length'), 0)
        self.assertEqual(len(self.network), 1)

    def test_capacity_full_preserves_online_and_pause_resume(self):
        self.page.evaluate("() => { document.querySelector('#ctl00_ContentPlaceHolder1_rbtnGheireHozori').onclick=()=>document.querySelector('#ctl00_ContentPlaceHolder1_labErrMsg').textContent='ظرفیت ثبت نام غیرحضوری بانک صادرات تکمیل شده است.'; }")
        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.CAPACITY_FULL)
        checkpoint = DBHandler.get_checkpoint(self.nid)
        self.assertEqual(checkpoint['bank_mode'], 'online')
        self.owner.commands.put('pause')
        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.PAUSED)
        self.owner.commands.put('resume')
        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.CAPACITY_FULL)
        self.assertTrue(self.page.locator('#ctl00_ContentPlaceHolder1_rbtnGheireHozori').is_checked())
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 0)

    def test_final_timeout_does_not_repeat_submit_even_after_resume(self):
        cfg = DBHandler.get_config()
        cfg['auto_final_submit'] = cfg['final_submit'] = True
        DBHandler.update_config(cfg)
        self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.owner.commands.put('resume')
        for _ in range(4):
            self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        self.assertNotEqual(DBHandler.get_checkpoint(self.nid).get('final_submit_status'), 'pending')
        self.assertTrue(self.controller._awaiting_result)

    def test_server_expiry_requires_reauthentication_without_navigation(self):
        self.controller.tick(force=True)
        self.page.evaluate("document.body.innerHTML='<input id=\"ctl00_ContentPlaceHolder1_tbIDNo\">'")
        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.SESSION_EXPIRED)
        self.assertFalse(self.owner.bank_page_active)
        self.assertEqual(len(self.network), 1)
        self.assertEqual(DBHandler.get_checkpoint(self.nid)['bank_mode'], 'online')

    def test_watchdog_cannot_reload_during_transient_dom_replacement(self):
        self.controller.tick(force=True)
        self.bot._recovery_flags['otp_expired'] = True
        self.bot._last_progress_time = time.time() - 1000
        self.page.evaluate("document.querySelector('#ctl00_ContentPlaceHolder1_ddlBankName').remove()")
        self.assertFalse(self.bot.watchdog_check(self.page, self.stop))
        self.assertFalse(self.bot._recovery_flags['otp_expired'])
        self.assertEqual(self.page.evaluate('fixtureEvents.unloads'), 0)

    def test_reservation_confirmation_is_distinct_and_cannot_resubmit(self):
        cfg = DBHandler.get_config()
        cfg.update(allow_reserve_bank=True, auto_final_submit=True, final_submit=True)
        DBHandler.update_config(cfg)
        self.page.evaluate("""() => {
            document.querySelector('#ctl00_ContentPlaceHolder1_ddlBankName option[value="19"]').textContent = 'بانک صادرات [در انتظار تخصیص ظرفیت جدید (ذخیره)]';
            document.querySelector('#ctl00_ContentPlaceHolder1_btnSave').onclick = () => {
                fixtureEvents.submits++;
                const label = document.createElement('span');
                label.id = 'ctl00_ContentPlaceHolder1_lblSuccess';
                label.textContent = 'درخواست رزرو شما با موفقیت ثبت شد';
                document.body.appendChild(label);
            };
        }""")
        self.controller.tick(force=True)
        self.controller.tick(force=True)
        self.assertEqual(self.controller.state, State.RESERVE_PENDING)
        self.assertTrue(DBHandler.get_checkpoint(self.nid)['reserve_confirmed'])
        self.assertFalse(DBHandler.has_final_submission(self.nid))
        for _ in range(3):
            self.controller.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)

    def test_dynamic_bank_value_is_read_from_dom(self):
        self.page.evaluate("document.querySelector('#ctl00_ContentPlaceHolder1_ddlBankName option[value=\"19\"]').value='127'")
        self.controller.tick(force=True)
        self.assertEqual(self.page.locator('#ctl00_ContentPlaceHolder1_ddlBankName').input_value(), '127')
        self.assertEqual(self.controller.state, State.FINAL_SUBMIT_READY)

    def test_entire_worker_remains_on_page_three_until_explicit_stop(self):
        LIVE_SESSIONS.release(self.owner)
        observed = []
        original_tick = BankSelectionSessionController.tick
        def tick(controller, force=False):
            result = original_tick(controller, force)
            observed.append((controller.state, self.page.is_closed(), controller.owner.page is self.page))
            if len(observed) == 2:
                self.assertTrue(list(Path(self.temp.name).rglob('*.png')))
                self.assertEqual(self.page.evaluate('fixtureEvents.unloads'), 0)
                self.stop.set()
            return result
        with patch.object(self.bot, 'setup_browser', return_value=(self.pw, self.browser, self.context, self.page)) as setup, \
             patch('bank_session_controller.BankSelectionSessionController.tick', tick):
            self.bot.run(self.stop)
        self.assertEqual(setup.call_count, 1)
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(state == State.FINAL_SUBMIT_READY and not closed and same for state, closed, same in observed))
        self.assertEqual(len(self.network), 1)
