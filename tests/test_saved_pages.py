"""
tests/test_saved_pages.py — Unit and Integration Tests for Bank Page 3 Management,
Saved Pages API, Capacity Check, and Recovery Logic.
"""
import os
import json
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from bank_page_manager import (
    BankPageState,
    STATE_METADATA,
    analyze_bank_page_html,
    parse_bank_dropdown_html,
    classify_capacity_or_error,
    is_reserve_text,
    normalize_persian,
)
from database import DBHandler
from core.models import SettingsRequest


SAMPLE_PAGE3_HTML = """
<!DOCTYPE html>
<html>
<head><title>فرم انتخاب بانک و شعبه پیشنهادی جهت دریافت تسهیلات قرض الحسنه ازدواج</title></head>
<body>
    <span id="ctl00_ContentPlaceHolder1_labIDNo">0012345678</span>
    <span id="ctl00_ContentPlaceHolder1_labFLName">علی محمدی</span>
    <span id="ctl00_ContentPlaceHolder1_labStateName">لرستان</span>
    <select id="ctl00_ContentPlaceHolder1_ddlCity" name="ctl00$ContentPlaceHolder1$ddlCity">
        <option value="0">-- انتخاب کنید --</option>
        <option value="424" selected="selected">خرم آباد</option>
    </select>
    <select id="ctl00_ContentPlaceHolder1_ddlBankName" name="ctl00$ContentPlaceHolder1$ddlBankName">
        <option value="0">-- انتخاب کنید --</option>
        <option value="53">بانک کارآفرین [دارای ظرفیت ثبت نام] - متقاضی در صف : 74 نفر</option>
        <option value="19" selected="selected">بانک صادرات [در انتظار تخصیص ظرفیت جدید (ذخیره)] - متقاضی در صف : 1370 نفر</option>
    </select>
    <div id="ctl00_ContentPlaceHolder1_divGheireHozori">
        <input type="radio" id="ctl00_ContentPlaceHolder1_rbtnGheireHozori" name="mode" checked="checked">
        <input type="radio" id="ctl00_ContentPlaceHolder1_rbtnHozori" name="mode">
    </div>
    <span id="ctl00_ContentPlaceHolder1_labErrMsg">ظرفیت ثبت نام غیرحضوری بانک صادرات در استان لرستان تکمیل شده است.</span>
    <input type="submit" id="ctl00_ContentPlaceHolder1_btnSave" value="ثبت درخواست">
</body>
</html>
"""


class TestBankPageManager(unittest.TestCase):

    def test_normalize_persian(self):
        self.assertEqual(normalize_persian("بانك ملّي"), "بانک ملی")
        self.assertEqual(normalize_persian("ثبت\u200cنام"), "ثبت نام")
        self.assertEqual(normalize_persian("  تست   فاصله  "), "تست فاصله")

    def test_is_reserve_text(self):
        self.assertTrue(is_reserve_text("بانک صادرات [در انتظار تخصیص ظرفیت جدید (ذخیره)] - متقاضی در صف : 1370 نفر"))
        self.assertTrue(is_reserve_text("ثبت نام به صورت رزرو در بانک"))
        self.assertTrue(is_reserve_text("نوبت رزرو شما"))
        self.assertFalse(is_reserve_text("بانک کارآفرین [دارای ظرفیت ثبت نام] - متقاضی در صف : 74 نفر"))
        self.assertFalse(is_reserve_text("بانک ملت"))

    def test_classify_capacity_or_error(self):
        # 1. Capacity messages MUST be classified as CAPACITY_FULL and NOT generic_error
        messages = [
            "ظرفیت ثبت نام غیرحضوری بانک صادرات در استان لرستان تکمیل شده است.",
            "امکان انتخاب روش غیرحضوری برای این بانک وجود ندارد",
            "ظرفیت پذیرش این بانک به اتمام رسیده است",
            "سهمیه ثبت نام این بانک در استان تکمیل می باشد",
            "سقف مجاز ثبت نام در این بانک تکمیل گردیده است",
            "عدم وجود ظرفیت در استان مورد نظر",
            "امکان ثبت نام به صورت غیرحضوری در این شعبه وجود ندارد",
        ]
        for msg in messages:
            kind, text = classify_capacity_or_error(msg)
            self.assertEqual(kind, BankPageState.CAPACITY_FULL, f"Failed for msg: {msg}")

        # 2. Session expired
        kind, _ = classify_capacity_or_error("مهلت زمانی نشست شما به پایان رسیده است. لطفاً مجدداً وارد شوید.")
        self.assertEqual(kind, BankPageState.SESSION_EXPIRED)

        # 3. WAF blocked
        kind, _ = classify_capacity_or_error("به دلیل تعداد دفعات زیاد، دسترسی شما مسدود گردید.")
        self.assertEqual(kind, "waf_blocked")

    def test_parse_bank_dropdown_html(self):
        banks = parse_bank_dropdown_html(SAMPLE_PAGE3_HTML)
        self.assertEqual(len(banks), 2)

        # Option 1: Karafarin (Active capacity)
        self.assertEqual(banks[0]["value"], "53")
        self.assertEqual(banks[0]["bank_name"], "بانک کارآفرین")
        self.assertFalse(banks[0]["is_reserve"])
        self.assertTrue(banks[0]["has_capacity"])
        self.assertEqual(banks[0]["queue_count"], 74)
        self.assertFalse(banks[0]["is_selected"])

        # Option 2: Saderat (Reserve)
        self.assertEqual(banks[1]["value"], "19")
        self.assertEqual(banks[1]["bank_name"], "بانک صادرات")
        self.assertTrue(banks[1]["is_reserve"])
        self.assertEqual(banks[1]["queue_count"], 1370)
        self.assertTrue(banks[1]["is_selected"])

    def test_analyze_bank_page_html_states(self):
        # Sample HTML has error message, so state should be CAPACITY_FULL
        res = analyze_bank_page_html(SAMPLE_PAGE3_HTML)
        self.assertTrue(res["is_bank_page"])
        self.assertEqual(res["applicant_nid"], "0012345678")
        self.assertEqual(res["applicant_name"], "علی محمدی")
        self.assertEqual(res["province"], "لرستان")
        self.assertEqual(res["city"], "خرم آباد")
        self.assertTrue(res["online_checked"])
        self.assertEqual(res["selected_mode"], "online")
        self.assertEqual(res["state"], BankPageState.CAPACITY_FULL)

        # HTML without error: selected bank is Saderat (reserve), so state should be RESERVE_PENDING
        clean_html = SAMPLE_PAGE3_HTML.replace("ظرفیت ثبت نام غیرحضوری بانک صادرات در استان لرستان تکمیل شده است.", "")
        res_clean = analyze_bank_page_html(clean_html)
        self.assertEqual(res_clean["state"], BankPageState.RESERVE_PENDING)

        # HTML with tracking code: state should be REGISTRATION_CONFIRMED
        confirmed_html = clean_html + '<span id="ctl00_ContentPlaceHolder1_lblTrackingCode">987654321</span>'
        res_conf = analyze_bank_page_html(confirmed_html)
        self.assertEqual(res_conf["state"], BankPageState.REGISTRATION_CONFIRMED)
        self.assertEqual(res_conf["tracking_code"], "987654321")


class TestSavedPagesAPI(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.patcher = patch("routes.saved_pages.SAVED_PAGES_DIR", self.test_dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_list_saved_pages_empty(self):
        from routes.saved_pages import list_saved_pages
        res = list_saved_pages(_=True)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["total_saved"], 0)
        self.assertEqual(len(res["saved_pages"]), 0)

    def test_list_saved_pages_with_sample_folder(self):
        from routes.saved_pages import list_saved_pages
        applicant_folder = os.path.join(self.test_dir, "تست متقاضی")
        os.makedirs(applicant_folder, exist_ok=True)

        manifest = {
            "version": 2,
            "banks": ["بانک کارآفرین", "بانک صادرات"],
            "last_signature": "abcdef123456",
            "snapshots": [
                {
                    "signature": "abcdef123456",
                    "banks": ["بانک کارآفرین", "بانک صادرات"],
                    "files": ["banks-20261016-010000-abcdef1234.html"],
                    "url": "https://ve.cbi.ir/SelectBnkShb.aspx",
                    "created_at": 1792076000.0
                }
            ]
        }
        with open(os.path.join(applicant_folder, "banks_seen.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)

        with open(os.path.join(applicant_folder, "banks-20261016-010000-abcdef1234.html"), "w", encoding="utf-8") as f:
            f.write(SAMPLE_PAGE3_HTML)

        res = list_saved_pages(_=True)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["total_saved"], 1)
        item = res["saved_pages"][0]
        self.assertEqual(item["folder"], "تست متقاضی")
        self.assertEqual(item["state"], BankPageState.CAPACITY_FULL)
        self.assertEqual(item["nid"], "0012345678")

    def test_health_check_saved_page(self):
        from routes.saved_pages import health_check_saved_page
        applicant_folder = os.path.join(self.test_dir, "متقاضی تست سلامت")
        os.makedirs(applicant_folder, exist_ok=True)

        # Missing manifest test
        res = health_check_saved_page("متقاضی تست سلامت", _=True)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["manifest_status"], "missing")
        self.assertFalse(res["healthy"])

        # With manifest & matching file
        manifest = {
            "version": 2,
            "banks": [],
            "snapshots": [{"files": ["test.html"]}]
        }
        with open(os.path.join(applicant_folder, "banks_seen.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        with open(os.path.join(applicant_folder, "test.html"), "w", encoding="utf-8") as f:
            f.write("<html>test</html>")

        res_ok = health_check_saved_page("متقاضی تست سلامت", _=True)
        self.assertEqual(res_ok["manifest_status"], "ok")
        self.assertEqual(len(res_ok["missing_files"]), 0)

    def test_security_path_traversal_prevention(self):
        from routes.saved_pages import get_snapshot_file
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            get_snapshot_file("../../../etc/passwd", "file.html", _=True)
        self.assertEqual(ctx.exception.status_code, 400)


class TestBankSelectionIntegration(unittest.TestCase):

    def test_detect_page_error_or_capacity_message_classifies_correctly(self):
        from bot_select_bank import _detect_page_error_or_capacity_message
        mock_bot = MagicMock()
        mock_page = MagicMock()
        mock_locator = MagicMock()
        mock_locator.count.return_value = 1
        mock_locator.first.is_visible.return_value = True
        mock_locator.first.inner_text.return_value = "ظرفیت ثبت نام غیرحضوری بانک صادرات در استان لرستان تکمیل شده است."
        mock_page.locator.return_value = mock_locator

        kind, txt = _detect_page_error_or_capacity_message(mock_bot, mock_page)
        self.assertEqual(kind, "bank_capacity_full")
        self.assertIn("ظرفیت ثبت نام غیرحضوری", txt)

    def test_watchdog_clears_otp_locks_on_page3(self):
        from bot_core import BotCore
        bot = BotCore(settings={}, nid="0012345678")
        bot._otp_wait_lock = True
        bot._safe_goto_locked = True
        bot._otp_guard_until = 9999999999.0
        bot._current_step = "OTP_MANUAL_REQUIRED"

        mock_page = MagicMock()
        # Mock detect_actual_page_stage to return STEP_3_BANK_SELECT
        bot.detect_actual_page_stage = MagicMock(return_value="STEP_3_BANK_SELECT")

        # Call watchdog check with expired progress time
        bot._last_progress_time = 0.0
        bot.watchdog_check(mock_page)

        # OTP locks MUST be cleared
        self.assertFalse(bot._otp_wait_lock)
        self.assertFalse(bot._safe_goto_locked)
        self.assertEqual(bot._otp_guard_until, 0.0)
        self.assertEqual(bot._current_step, "STEP_3_BANK_SELECT")

    def test_capacity_settings_and_models(self):
        cfg = DBHandler.get_config()
        self.assertIn("capacity_check_enabled", cfg)
        self.assertIn("capacity_check_interval_seconds", cfg)
        self.assertTrue(cfg["capacity_check_enabled"])
        self.assertEqual(cfg["capacity_check_interval_seconds"], 20.0)

        # Model validation
        req = SettingsRequest(capacity_check_enabled=True, capacity_check_interval_seconds=30.0)
        self.assertTrue(req.capacity_check_enabled)
        self.assertEqual(req.capacity_check_interval_seconds, 30.0)


if __name__ == "__main__":
    unittest.main()
