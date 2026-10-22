"""Real Chromium against local assets and mocked HTTP; no running app or SMS relay."""
import json
import mimetypes
import unittest
import os
import zipfile
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


class DashboardUiTests(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.context = self.browser.new_context(viewport={'width': 720, 'height': 850})
        self.context.set_default_timeout(4000)
        self.page = self.context.new_page()
        self.users = [
            {'id': 1, 'national_id': '0012345678', 'full_name': 'متقاضی اول', 'status': 'ظرفیت تکمیل', 'data': {},
             'runtime': {'current_step': 'CAPACITY_FULL', 'status': 'BANK_CAPACITY_FULL'}},
            {'id': 2, 'national_id': '0098765432', 'full_name': 'متقاضی دوم', 'status': 'منتظر پیامک', 'data': {},
             'runtime': {'current_step': 'STEP_2_OTP', 'status': 'WAITING_USER_INPUT'}},
        ]
        self.requests = []
        self.manual_status = 200
        def route(request_route):
            req = request_route.request
            parsed = urlparse(req.url)
            if parsed.hostname != 'mahan.test':
                return request_route.abort()
            if req.method in {'POST', 'PATCH'}:
                self.requests.append((parsed.path, req.post_data_json))
            path = ROOT / ('static/index.html' if parsed.path == '/' else parsed.path.lstrip('/'))
            if path.is_file() and path.is_relative_to(ROOT / 'static'):
                body = path.read_bytes()
                baseline = os.getenv('MAHANBOT_UI_BASELINE_ZIP')
                if baseline:
                    with zipfile.ZipFile(baseline) as archive:
                        body = archive.read(path.relative_to(ROOT).as_posix())
                return request_route.fulfill(body=body, content_type=mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
            payload = self.users if parsed.path == '/applicants' else [] if parsed.path == '/jobs/active' else {}
            request_route.fulfill(json=payload, status=self.manual_status if parsed.path == '/manual_otp' else 200)
        self.context.route('**/*', route)
        self.page.add_init_script("window.WebSocket = class { static OPEN=1; readyState=0; send(){} close(){} }; window.maskSensitiveData=true;")
        self.page.goto('http://mahan.test/')
        self.page.add_style_tag(content='* { transition: none !important; animation: none !important; }')
        self.page.wait_for_function('typeof renderDashboard === "function"')
        self.page.evaluate('''users => {
            allUsersData = users;
            users.forEach(user => jobIdByNid.set(user.national_id, 'fixture-job'));
            renderDashboard(users);
        }''', self.users)

    def tearDown(self):
        self.browser.close()
        self.pw.stop()

    def test_manual_code_targets_second_waiter_while_first_monitors_bank(self):
        self.page.locator('#manualOtpInput').fill('۱۲۳۴۵۶')
        with self.page.expect_request('**/manual_otp'):
            self.page.locator('.otp-row button').click()
        self.assertEqual(self.requests, [('/manual_otp', {'nid': '0098765432', 'code': '123456'})])
        self.assertEqual(self.page.locator('#manualOtpRecipient').input_value(), '0098765432')
        self.assertEqual(self.page.locator('#activeApplicantName').inner_text(), 'متقاضی دوم')

    def test_two_waiters_require_explicit_recipient_and_keep_selection_on_update(self):
        self.users[0].update(status='منتظر پیامک', runtime={'current_step': 'STEP_2_OTP'})
        self.page.evaluate('''users => {
            manualOtpTargetNid=null; allUsersData=users; renderDashboard(users);
        }''', self.users)
        self.assertEqual(self.page.locator('#manualOtpRecipient').input_value(), '')
        self.assertTrue(self.page.locator('#manualOtpSend').is_disabled())
        self.page.locator('#manualOtpRecipient').select_option('0098765432')
        self.page.evaluate('renderDashboard([...allUsersData].reverse())')
        self.assertEqual(self.page.locator('#manualOtpRecipient').input_value(), '0098765432')
        self.assertEqual(self.requests, [])

    def test_rejected_manual_request_preserves_code_and_shows_failure(self):
        self.manual_status = 500
        self.page.locator('#manualOtpInput').fill('123456')
        self.page.locator('#manualOtpSend').click()
        self.page.wait_for_function('document.querySelector("#manualOtpStatus").textContent.includes("HTTP 500")')
        self.assertEqual(self.page.locator('#manualOtpInput').input_value(), '123456')
        self.assertFalse(self.page.locator('#manualOtpSend').is_disabled())
        self.assertEqual(self.requests[0][1]['nid'], '0098765432')

    def test_start_button_is_reachable_without_maximizing_or_horizontal_scroll(self):
        self.page.evaluate("switchView('bank-select'); renderBankSelectList(allUsersData)")
        for width in (480, 720, 1024, 1440):
            self.page.set_viewport_size({'width': width, 'height': 850})
            button = self.page.locator('#bankSelectListBody .btn-primary').first
            box = button.bounding_box()
            self.assertIsNotNone(box)
            self.assertGreaterEqual(box['x'], 0)
            self.assertLessEqual(box['x'] + box['width'], width)
            button.click(trial=True)
            self.assertTrue(button.evaluate('e => { const r=e.getBoundingClientRect(); return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)); }'))
        output = __import__('os').getenv('MAHANBOT_UI_EVIDENCE_DIR')
        if output:
            self.page.set_viewport_size({'width': 720, 'height': 850})
            self.page.screenshot(path=str(Path(output) / 'bank-panel-720.png'))

    def test_applicant_submission_controls_preserve_explicit_off_and_patch_only_target(self):
        self.users[0]['data'] = {'auto_final_submit': False, 'final_submit_interval_seconds': 2.5}
        self.page.evaluate('''users => {
            allUsersData=users; setSubmissionDefaults({auto_final_submit:true,bank_capacity_check_interval:7});
            switchView('bank-select'); renderBankSelectList(users);
        }''', self.users)
        first = self.page.locator('[data-final-submit-nid="0012345678"]')
        second = self.page.locator('[data-final-submit-nid="0098765432"]')
        self.assertFalse(first.is_checked())
        self.assertTrue(second.is_checked())
        with self.page.expect_request('**/0098765432/submission-settings'):
            second.uncheck()
        self.page.wait_for_function("allUsersData[1].data.auto_final_submit===false")
        self.assertEqual(self.requests[-1], ('/applicants/0098765432/submission-settings', {'auto_final_submit': False}))
        interval = self.page.locator('[data-final-interval-nid="0012345678"]')
        interval.fill('4.5')
        self.page.evaluate('renderBankSelectList(allUsersData)')
        self.assertTrue(interval.evaluate('el => el === document.activeElement'))
        with self.page.expect_request('**/0012345678/submission-settings'):
            interval.press('Tab')
        self.assertEqual(self.requests[-1][1], {'final_submit_interval_seconds': 4.5})
        self.assertFalse(second.is_checked())

    def test_edit_applicant_supports_inherited_and_explicit_submission_preferences(self):
        self.users[0]['data'] = {'auto_final_submit': False, 'final_submit_interval_seconds': 3}
        self.page.evaluate('''users => {
            allUsersData=users; setSubmissionDefaults({auto_final_submit:true,bank_capacity_check_interval:7}); editUser(0);
        }''', self.users)
        self.assertFalse(self.page.locator('#inpInheritFinalSubmit').is_checked())
        self.assertFalse(self.page.locator('#inpAutoFinalSubmit').is_checked())
        self.assertFalse(self.page.locator('#inpAutoFinalSubmit').is_disabled())
        self.assertEqual(self.page.locator('#inpFinalSubmitInterval').input_value(), '3')
        self.page.evaluate('editUser(1)')
        self.assertTrue(self.page.locator('#inpInheritFinalSubmit').is_checked())
        self.assertTrue(self.page.locator('#inpAutoFinalSubmit').is_checked())
        self.assertTrue(self.page.locator('#inpAutoFinalSubmit').is_disabled())
        self.assertEqual(self.page.locator('#inpFinalSubmitInterval').input_value(), '')

    def test_global_interval_panel_accepts_same_range_as_applicant_interval(self):
        self.page.on('dialog', lambda dialog: dialog.accept())
        self.page.evaluate("switchView('bank-select')")
        self.page.locator('#capIntervalInput').fill('1.5')
        with self.page.expect_request('**/api/capacity/interval'):
            self.page.evaluate('updateCapacityInterval()')
        self.assertEqual(self.requests[-1], ('/api/capacity/interval', {'interval_seconds': 1.5}))
