"""Replay bank CAPTCHA failures without contacting CBI or opening saved profiles."""
import base64
import io
import threading
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from playwright.sync_api import sync_playwright

from bot_core import BotCore
from bot_select import BankSelectionBot
from bot_select_captcha import wait_for_manual_captcha_handoff


CASES = {
    '6450700394': {'tracking_code': '', 'mobile': 'present', 'state': 'present'},
    '7040099756': {'tracking_code': '', 'mobile': '', 'state': ''},
    '4849936334': {'tracking_code': 'present', 'mobile': 'present', 'state': 'present'},
    '6450699140': {'tracking_code': 'present', 'mobile': '', 'state': ''},
    '7050030546': {'tracking_code': '', 'mobile': '', 'state': 'present'},
    '6680311926': {'tracking_code': '', 'mobile': '', 'state': 'present'},
}


class SelectCaptchaDifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.context.route('**/*', lambda route: route.abort())
        self.page = self.context.new_page()
        self.page.set_default_timeout(500)
        image = io.BytesIO()
        Image.new('RGB', (250, 40), 'white').save(image, format='PNG')
        src = 'data:image/png;base64,' + base64.b64encode(image.getvalue()).decode()
        self.page.set_content(f"""
          <img class='BDC_CaptchaImage' id='fixture_CaptchaImage' src='{src}'>
          <a class='BDC_ReloadLink' href='#' onclick="event.preventDefault();counts.refresh++;
            document.querySelector('img').src='{src}#'+counts.refresh">Reload</a>
          <input id='ctl00_ContentPlaceHolder1_tbIDNo'>
          <input id='ctl00_ContentPlaceHolder1_tbCaptcha1' oninput='counts.input++'
            onchange='counts.change++' onkeyup='counts.keyup++'>
          <button id='ctl00_ContentPlaceHolder1_btnSendConfirmCode' onclick="
            if(Page_ClientValidate()){{counts.submit++;this.disabled=true;
              document.body.insertAdjacentHTML('beforeend',
                '&lt;input id=ctl00_ContentPlaceHolder1_tbMobileConfCode&gt;')}}">Send</button>
          <script>window.counts={{refresh:0,input:0,change:0,keyup:0,submit:0}};
            window.Page_ClientValidate=()=>/^\\d{{10}}$/.test(
              document.getElementById('ctl00_ContentPlaceHolder1_tbIDNo').value);
          </script>""")
        self.page.locator('img').evaluate('e=>e.decode()')
        self.clock = [100.0]
        self.patches = [
            patch('bot_select_captcha.time.time', side_effect=lambda: self.clock[0]),
            patch('bot_select_captcha.time.sleep', side_effect=self.advance),
            patch('bot_select_captcha.DBHandler.update_status'),
            patch('bot_core.DBHandler.append_captcha_attempt'),
            patch('event_logger.emit_structured_event'),
        ]
        for item in self.patches:
            item.start()
        original_wait = self.page.wait_for_timeout
        def wait(ms):
            self.advance(ms / 1000)
            original_wait(1)
        self.page.wait_for_timeout = wait

    def advance(self, seconds):
        self.clock[0] += seconds

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.context.close()

    def bot(self, nid):
        bot = BankSelectionBot.__new__(BankSelectionBot)
        bot.nid = nid
        bot.data = CASES[nid]
        bot.settings = {'captcha_max_retries': 3, 'pause_for_manual_captcha': True,
                        'captcha_manual_timeout': 8}
        bot.captcha_service = Mock()
        bot.captcha_service.solve_element.return_value = 'AB12CD'
        for name in ('log', 'set_runtime_state', 'mark_progress', 'suspend_watchdog',
                     'clear_otp_locks', 'wait_for_aspnet_postback'):
            setattr(bot, name, Mock())
        bot._firewall_gate = Mock(return_value=False)
        bot.consume_recovery_flag = Mock(return_value=False)
        self.page.locator('#ctl00_ContentPlaceHolder1_tbIDNo').fill(nid)
        return bot

    def test_failing_cases_recover_after_primary_and_first_assist_ocr_failures(self):
        for nid in list(CASES)[:3]:
            with self.subTest(nid=nid):
                bot = self.bot(nid)
                attempts = [0]
                def solve(*args, **kwargs):
                    attempts[0] += 1
                    if attempts[0] <= 7:
                        return False
                    return BotCore.solve_captcha_with_model(bot, *args, **kwargs)
                bot.solve_captcha_with_model = Mock(side_effect=solve)
                result = bot._solve_primary_captcha_fast(self.page, threading.Event())
                self.assertTrue(result, 'OCR failure must not leave the assist loop on one challenge')
                self.assertEqual(attempts[0], 8)
                counts = self.page.evaluate('counts')
                self.assertEqual(counts['submit'], 1)
                for kind in ('input', 'change', 'keyup'):
                    self.assertGreater(counts[kind], 0)
                self.assertEqual(bot.detect_actual_page_stage(self.page), 'STEP_2_OTP')
                self.page.locator('#ctl00_ContentPlaceHolder1_tbMobileConfCode').evaluate('e=>e.remove()')
                self.page.locator('#ctl00_ContentPlaceHolder1_tbCaptcha1').fill('')
                self.page.locator('button').evaluate('e=>e.disabled=false')
                self.page.evaluate('counts.submit=0')

    def test_control_cases_still_submit_once_without_refresh(self):
        for nid in list(CASES)[3:]:
            with self.subTest(nid=nid):
                bot = self.bot(nid)
                self.assertTrue(bot._solve_primary_captcha_fast(self.page, threading.Event()))
                self.assertEqual(self.page.evaluate('counts.refresh'), 0)
                self.assertEqual(self.page.evaluate('counts.submit'), 1)
                self.page.locator('#ctl00_ContentPlaceHolder1_tbMobileConfCode').evaluate('e=>e.remove()')
                self.page.locator('#ctl00_ContentPlaceHolder1_tbCaptcha1').fill('')
                self.page.locator('button').evaluate('e=>e.disabled=false')
                self.page.evaluate('counts.submit=0')

    def test_bank_assist_preserves_manual_text_and_does_not_submit_it(self):
        bot = self.bot(next(iter(CASES)))
        self.page.locator('#ctl00_ContentPlaceHolder1_tbCaptcha1').fill('USER')
        self.assertFalse(wait_for_manual_captcha_handoff(
            bot, self.page, '#ctl00_ContentPlaceHolder1_tbCaptcha1',
            '#ctl00_ContentPlaceHolder1_btnSendConfirmCode', threading.Event(),
            refresh_on_failure=True))
        self.assertEqual(self.page.locator('#ctl00_ContentPlaceHolder1_tbCaptcha1').input_value(), 'USER')
        self.assertEqual(self.page.evaluate('counts.refresh'), 0)
        self.assertEqual(self.page.evaluate('counts.submit'), 0)

    def test_bank_assist_logs_a_transient_exception_and_retries(self):
        bot = self.bot(next(iter(CASES)))
        attempts = [0]
        def solve(*args, **kwargs):
            attempts[0] += 1
            if attempts[0] == 1:
                raise RuntimeError('fixture transient screenshot failure')
            return BotCore.solve_captcha_with_model(bot, *args, **kwargs)
        bot.solve_captcha_with_model = Mock(side_effect=solve)
        self.assertTrue(wait_for_manual_captcha_handoff(
            bot, self.page, '#ctl00_ContentPlaceHolder1_tbCaptcha1',
            '#ctl00_ContentPlaceHolder1_btnSendConfirmCode', threading.Event(),
            refresh_on_failure=True))
        self.assertEqual(attempts[0], 2)
        self.assertTrue(any('RuntimeError' in str(call) for call in bot.log.call_args_list))
        self.assertEqual(self.page.evaluate('counts.submit'), 1)

    def test_bank_assist_stop_does_not_run_ocr_or_refresh(self):
        bot = self.bot(next(iter(CASES)))
        bot.solve_captcha_with_model = Mock()
        stop = threading.Event()
        stop.set()
        self.assertFalse(wait_for_manual_captcha_handoff(
            bot, self.page, '#ctl00_ContentPlaceHolder1_tbCaptcha1',
            '#ctl00_ContentPlaceHolder1_btnSendConfirmCode', stop, refresh_on_failure=True))
        bot.solve_captcha_with_model.assert_not_called()
        self.assertEqual(self.page.evaluate('counts.refresh'), 0)

    def test_bank_assist_browser_close_returns_without_waiting_for_timeout(self):
        bot = self.bot(next(iter(CASES)))
        def close(*args, **kwargs):
            self.page.close()
            return False
        bot.solve_captcha_with_model = Mock(side_effect=close)
        self.assertFalse(wait_for_manual_captcha_handoff(
            bot, self.page, '#ctl00_ContentPlaceHolder1_tbCaptcha1',
            '#ctl00_ContentPlaceHolder1_btnSendConfirmCode', threading.Event(), refresh_on_failure=True))
        self.assertEqual(bot.solve_captcha_with_model.call_count, 1)

    def test_primary_captcha_pixels_survive_each_applicants_saved_browser_zoom(self):
        import json
        from pathlib import Path
        import tempfile

        zooms = [-2.2239010857415455, -1.2239010857415447, -1.5778829311823859, 0, 0, 0]
        image = Image.new('RGB', (250, 40))
        image.putdata([((x*7+y*11)%256, (x*13+y*3)%256, (x+y*17)%256)
                       for y in range(40) for x in range(250)])
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        src = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
        for nid, zoom in zip(CASES, zooms):
            with self.subTest(nid=nid, zoom=zoom), tempfile.TemporaryDirectory() as temp:
                default = Path(temp) / 'Default'
                default.mkdir()
                (default/'Preferences').write_text(json.dumps({'partition': {'per_host_zoom_levels': {
                    'x': {'ve.cbi.ir': {'zoom_level': zoom, 'last_modified': '13437850454224833'}}}}}))
                context = self.pw.chromium.launch_persistent_context(temp, channel='chrome', headless=True,
                    viewport={'width':620,'height':730})
                try:
                    context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=
                        f'<img id="fixture_CaptchaImage" src="{src}"><input id="tbCaptcha">'
                        '<button id="send">Send</button>'))
                    page = context.pages[0]
                    page.goto('https://ve.cbi.ir/SelectBnkShb.aspx')
                    self.assertAlmostEqual(page.evaluate('devicePixelRatio'), 1.2**zoom, places=4)
                    bot = self.bot(nid)
                    bot.captcha_service.solve.return_value = 'AB12CD'
                    self.assertTrue(bot.solve_captcha_with_model(page, '#tbCaptcha', '#send',
                        source='primary', captcha_element=page.locator('img')))
                    actual = Image.open(io.BytesIO(bot.captcha_service.solve.call_args.args[0]))
                    self.assertEqual(actual.size, image.size)
                    self.assertEqual(actual.convert('RGB').tobytes(), image.tobytes())
                finally:
                    context.close()

    def diagnostic_module(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location('select_diagnostic',
            Path(__file__).resolve().parents[1] / 'scripts/diagnose_select_captcha.py')
        diagnostic = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(diagnostic)
        return diagnostic

    def test_diagnostic_urls_and_logs_remove_identifiers(self):
        diagnostic = self.diagnostic_module()
        self.assertEqual(diagnostic.public_url('https://ve.cbi.ir/test?token=secret'), 'https://ve.cbi.ir/test')
        for nid in CASES:
            self.assertNotIn(nid, diagnostic.redact(nid))

    def test_diagnostic_copies_profile_and_db_and_records_events_on_local_site(self):
        import json
        from pathlib import Path
        import sqlite3
        import tempfile
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import closing
        import database

        diagnostic = self.diagnostic_module()
        markup = (self.page.content() + '<script>setTimeout(()=>alert("fixture dialog"),100)</script>').encode()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(markup)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        old_db = database.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                nid = next(iter(CASES))
                (root / 'chrome_profiles' / nid).mkdir(parents=True)
                source_db = root / 'source.db'
                with closing(sqlite3.connect(old_db)) as source, closing(sqlite3.connect(source_db)) as target:
                    source.backup(target)
                    target.execute('INSERT OR REPLACE INTO applicants(national_id,full_name,status,data) VALUES(?,?,?,?)',
                                   (nid, 'Fixture', 'Ready', '{}'))
                    target.commit()
                before = source_db.read_bytes()
                with patch.object(diagnostic, 'ROOT', root), patch.object(diagnostic, 'TARGET',
                    f'http://127.0.0.1:{server.server_port}/SelectBnkShb.aspx'):
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        outcome = executor.submit(diagnostic.run_case, 'F1', nid, root, source_db).result(timeout=40)
                self.assertEqual(outcome['outcome']['event'], 'outcome')
                self.assertEqual(source_db.read_bytes(), before)
                self.assertEqual(list((root / 'chrome_profiles' / nid).iterdir()), [])
                events = json.loads((root / 'F1/events.json').read_text(encoding='utf-8'))
                self.assertTrue(any(e['event'] == 'dialog' for e in events))
                self.assertTrue(any(e['event'] == 'input_event' and e['valueLength'] == 10 for e in events))
                self.assertTrue(any(e['event'] == 'original_profile_unchanged' and e['unchanged'] for e in events))
        finally:
            database.DB_PATH = old_db
            database.DBHandler._settings_cache.clear()
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
