"""Step 3 policy regressions on isolated real Chromium, including expired local OTP."""
import time
from pathlib import Path
from test_critical_repair import RepairTestCase
from bank_session_controller import BankSelectionSessionController
from bank_page_manager import BankPageState as State
from database import DBHandler


class TestPiece456Puzzle(RepairTestCase):
    def setUp(self):
        super().setUp()
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.context = self.browser.new_context()
        html = (Path(__file__).parent / 'fixtures/bank_page_webforms.html').read_text(encoding='utf-8')
        self.context.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html'))
        self.page = self.context.new_page()
        self.page.goto('http://127.0.0.1/SelectBnkShb.aspx')
        self.owner.bind(self.context, self.page)
        self.ctrl = BankSelectionSessionController(self.bot, self.owner)
        self.ctrl._submit_response_timeout = .1

    def tearDown(self):
        self.browser.close()
        self.pw.stop()
        super().tearDown()

    def test_active_bank_auto_final_submit_false_does_not_submit(self):
        cfg = DBHandler.get_config()
        cfg.update(auto_final_submit=False, final_submit=False)
        DBHandler.update_config(cfg)
        self.ctrl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 0)
        self.assertEqual(self.ctrl.state, State.FINAL_SUBMIT_READY)
        self.assertTrue(self.owner.bank_page_active)

    def test_reserve_bank_requires_both_allow_reserve_and_auto_submit(self):
        self.page.locator('option[value="19"]').evaluate("e=>e.textContent='بانک صادرات [در انتظار تخصیص ظرفیت جدید (ذخیره)]'")
        cfg = DBHandler.get_config()
        cfg.update(auto_final_submit=True, final_submit=True, allow_reserve_bank=False)
        DBHandler.update_config(cfg)
        self.ctrl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 0)
        cfg['allow_reserve_bank'] = True
        DBHandler.update_config(cfg)
        self.ctrl._prepared = False
        self.ctrl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)
        for _ in range(3):
            self.ctrl.tick(force=True)
        self.assertEqual(self.page.evaluate('fixtureEvents.submits'), 1)

    def test_live_bank_does_not_open_a_parallel_tab_when_local_otp_ttl_expires(self):
        self.bot._otp_entry_time = time.time() - 130
        before = list(self.context.pages)
        self.ctrl.tick(force=True)
        self.assertEqual(self.context.pages, before)
        self.assertIs(self.owner.page, self.page)
        self.assertNotEqual(self.ctrl.state, State.SESSION_EXPIRED)
