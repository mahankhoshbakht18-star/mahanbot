import json
import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import database
from bot_select import BankSelectionBot
from bot_select_otp import fast_otp_sync_and_inject, _detect_failure_type, handle_otp_dialog
from database import DBHandler
from otp_transport import fresh_record


class OtpExpiryAndManualTests(unittest.TestCase):
    def setUp(self):
        self.nid = "0012345678"
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = os.path.join(self.temp_dir.name, "test_otp.db")
        DBHandler._settings_cache.clear()
        DBHandler.init_db()

        with DBHandler._connect() as conn:
            conn.execute(
                "INSERT INTO applicants (full_name, national_id, status, data) VALUES (?, ?, 'Ready', ?)",
                (
                    "متقاضی تستی",
                    self.nid,
                    json.dumps({
                        "state": "تهران",
                        "city": "تهران",
                        "priority_banks": ["بانک صادرات ایران"],
                    }),
                ),
            )
            conn.commit()

        self.settings = DBHandler.get_config()
        self.bot = BankSelectionBot(self.nid, self.settings)

    def tearDown(self):
        database.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def test_01_detect_failure_type_recognizes_expired_alarm(self):
        page = Mock()
        page.inner_text.return_value = "خطا: کد تایید منقضی شده است. لطفا مجددا وارد شوید."
        self.assertEqual(_detect_failure_type(page), "otp_expired")

        page.inner_text.return_value = "کد تأیید نامعتبر است"
        self.assertEqual(_detect_failure_type(page), "otp")

        page.inner_text.return_value = "کد امنیتی اشتباه است"
        self.assertEqual(_detect_failure_type(page), "captcha")

    def test_02_dialog_otp_expired_sets_flag_and_accepts(self):
        dialog = Mock()
        dialog.message.return_value = "کد تایید منقضی شده است"
        handled = handle_otp_dialog(self.bot, dialog)
        self.assertTrue(handled)
        dialog.accept.assert_called_once()
        self.assertEqual(self.bot._last_dialog_action, "otp_expired")
        self.assertTrue(self.bot._recovery_flags.get("otp_expired"))

    def test_03_fresh_record_prioritizes_manual_otp_even_if_app_fails(self):
        # Even if SMS relay or app failed, manual entry is immediately accepted
        self.bot.mark_otp_entry_time(time.time() - 30)
        manual_record = {
            "nid": self.nid,
            "otp": "789123",
            "status": "manual",
            "ts": time.time(),
        }
        code = fresh_record(self.bot, manual_record)
        self.assertEqual(code, "789123")

    def test_04_manual_otp_in_browser_input_is_detected_and_accepted(self):
        locator = Mock()
        locator.input_value.return_value = "654321"
        page = Mock()
        stop = Mock()
        stop.is_set.return_value = False

        with patch("bot_select_otp.ensure_otp_captcha_prefilled", return_value=False), \
             patch("bot_select_otp._bank_select_visible_now", return_value=False), \
             patch("bot_select_otp._entry_form_visible_now", return_value=False), \
             patch("bot_select_otp._locate_otp_input", return_value=(locator, page)), \
             patch("bot_select_otp._detect_site_otp_ttl", return_value=120), \
             patch("bot_select_otp.get_otp_from_db_fresh", return_value=(None, None)), \
             patch("bot_select_otp._solve_otp_captcha_fast", return_value=True), \
             patch("bot_select_otp._post_submit_watch", return_value=("bank", None, None)), \
             patch.object(self.bot, "_firewall_gate", return_value=False):
            res = fast_otp_sync_and_inject(self.bot, page, stop, lambda *_: False)
            self.assertEqual(res, "bank_ready")
            rec = DBHandler.get_otp_record(self.nid)
            self.assertIsNotNone(rec)
            self.assertEqual(rec["otp"], "654321")

    def test_05_expired_otp_restarts_from_step1(self):
        locator = Mock()
        locator.input_value.return_value = ""
        page = Mock()
        stop = Mock()
        stop.is_set.return_value = False

        self.bot._last_dialog_action = "otp_expired"
        with patch("bot_select_otp._bank_select_visible_now", return_value=False), \
             patch("bot_select_otp._entry_form_visible_now", return_value=False), \
             patch("bot_select_otp._locate_otp_input", return_value=(locator, page)), \
             patch("bot_select_otp._detect_site_otp_ttl", return_value=120), \
             patch("bot_select_otp.get_otp_from_db_fresh", return_value=("112233", "test")), \
             patch("bot_select_otp._solve_otp_captcha_fast", return_value=True), \
             patch("bot_select_otp._detect_failure_type", return_value="otp_expired"), \
             patch.object(self.bot, "_firewall_gate", return_value=False), \
             patch.object(self.bot, "emit_event") as emit_event:
            res = fast_otp_sync_and_inject(self.bot, page, stop, lambda *_: False)
            self.assertEqual(res, "otp_expired")
            self.assertEqual(DBHandler.get_runtime(self.nid).get("otp_state"), "OTP_EXPIRED")
            emit_event.assert_any_call(
                "OTP_EXPIRED",
                step="STEP_2_OTP",
                reason="otp_expired_restart_step1",
                result="reauth_required",
                message="کد پیامک منقضی شد؛ بازگشت خودکار به مرحله ۱ برای صدور کد جدید.",
                level="warning",
                page=page,
            )

    def test_06_stale_otp_record_does_not_leave_active_otp_page(self):
        otp_locator = Mock()
        form = Mock(fields={"otp": {}}, reason="live_form_evidence")
        form.resolve.side_effect = lambda role: otp_locator if role == "otp" else None
        self.bot._otp_request_id = "request-1"
        self.bot._last_dialog_action = "otp_expired"
        self.bot._set_recovery_flag("otp_expired")
        page = Mock()
        stop = Mock()

        with patch("otp_form_detector.detect_otp_form", return_value=form), \
             patch("bot_select_otp.DBHandler.get_checkpoint", return_value={}), \
             patch("bot_select_otp.DBHandler.get_otp_record", return_value={
                 "status": "expired",
                 "request_id": "request-1",
             }), \
             patch("bot_select_otp._run_otp_step", return_value="manual_required") as run_step:
            result = fast_otp_sync_and_inject(self.bot, page, stop, lambda *_: False)

        self.assertEqual(result, "manual_required")
        run_step.assert_called_once()
        self.assertFalse(self.bot._recovery_flags.get("otp_expired"))
        self.assertIsNone(self.bot._last_dialog_action)

    def test_07_expired_session_still_requests_reauthentication(self):
        otp_locator = Mock()
        form = Mock(fields={}, reason="live_form_evidence")
        flow = Mock(http_status=401)
        flow.pump_commands.return_value = False
        page = Mock()
        stop = Mock()
        stop.is_set.return_value = False

        with patch("core.loan_workflow.workflow_for", return_value=flow), \
             patch("otp_form_detector.detect_otp_form", return_value=form), \
             patch("bot_select_otp._locate_otp_input", return_value=(otp_locator, page)), \
             patch("bot_select_otp._bank_select_visible_now", return_value=False), \
             patch("bot_select_otp._entry_form_visible_now", return_value=False):
            result = fast_otp_sync_and_inject(self.bot, page, stop, lambda *_: False)

        self.assertEqual(result, "otp_expired")
        flow.transition.assert_any_call("OTP_REAUTH_REQUIRED", "site_or_code_expiry")


if __name__ == "__main__":
    unittest.main()
