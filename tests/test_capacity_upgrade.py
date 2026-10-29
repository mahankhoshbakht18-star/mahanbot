"""
tests/test_capacity_upgrade.py — Unit Tests for PageStateClassifier, Zero-Overhead Critical Path,
Capacity Telemetry, and SelTasCase Navigation.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import glob
from core.page_state_classifier import PageStateClassifier, PageState
from database import DBHandler


SAMPLE_HTML_MIXED = """
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>انتخاب بانک و شعبه پیشنهادی</title></head>
<body>
<select name="ctl00$ContentPlaceHolder1$ddlBankName" id="ctl00_ContentPlaceHolder1_ddlBankName">
    <option selected="selected" value="0">-- انتخاب کنید --</option>
    <option value="10">بانک صادرات ایران [دارای ظرفیت ثبت نام] - متقاضی در صف : 1200 نفر</option>
    <option value="13">بانک رفاه کارگران [دارای ظرفیت ثبت نام] - متقاضی در صف : 850 نفر</option>
    <option value="12">بانک ملت [در انتظار تخصیص ظرفیت جدید (ذخیره)] - متقاضی در صف : 4300 نفر</option>
    <option value="14">بانک مسکن [در انتظار تخصیص ظرفیت جدید (ذخیره)] - متقاضی در صف : 2100 نفر</option>
</select>
</body>
</html>
"""

SAMPLE_HTML_RESERVE_ONLY = """
<html><body>
<select name="ctl00$ContentPlaceHolder1$ddlBankName" id="ctl00_ContentPlaceHolder1_ddlBankName">
    <option selected="selected" value="0">-- انتخاب کنید --</option>
    <option value="12">بانک ملت [در انتظار تخصیص ظرفیت جدید (ذخیره)] - متقاضی در صف : 4300 نفر</option>
    <option value="14">بانک مسکن [در انتظار تخصیص ظرفیت جدید (ذخیره)] - متقاضی در صف : 2100 نفر</option>
</select>
</body></html>
"""

SAMPLE_HTML_WAF_REJECTED = """
<html>
<head><title>Request Rejected</title></head>
<body>
The requested URL was rejected. Please consult with your administrator.<br><br>
Your support ID is: 18446744073709551615
</body>
</html>
"""

SAMPLE_HTML_MAINTENANCE = """
<html><body>
<span id="ctl00_lblMessage">سامانه در حال بروزرسانی ظرفیت بانک ها می باشد و تا پایان عملیات بروزرسانی امکان ثبت نام وجود ندارد.</span>
</body></html>
"""

SAMPLE_HTML_CITY_REQUIRED = """
<html><body>
<select name="ctl00$ContentPlaceHolder1$ddlCity" id="ctl00_ContentPlaceHolder1_ddlCity">
    <option selected="selected" value="0">-- انتخاب شهر --</option>
    <option value="101">زاهدان</option>
</select>
</body></html>
"""


class TestCapacityUpgradeSuite(unittest.TestCase):
    def setUp(self):
        DBHandler.init_db()

    def test_parse_active_vs_reserve_banks(self):
        # 1. Target is Saderat (Active)
        res_saderat = PageStateClassifier.classify(SAMPLE_HTML_MIXED, target_banks=["بانک صادرات"])
        self.assertEqual(res_saderat.state, PageState.BANK_ACTIVE_AVAILABLE)
        self.assertEqual(res_saderat.target_bank_value, "10")
        self.assertEqual(res_saderat.target_bank_status, "active")

        # 2. Target is Refah (Active)
        res_refah = PageStateClassifier.classify(SAMPLE_HTML_MIXED, target_banks=["بانک رفاه کارگران"])
        self.assertEqual(res_refah.state, PageState.BANK_ACTIVE_AVAILABLE)
        self.assertEqual(res_refah.target_bank_value, "13")
        self.assertEqual(res_refah.target_bank_status, "active")

        # 3. Target is Mellat (Reserve)
        res_mellat = PageStateClassifier.classify(SAMPLE_HTML_MIXED, target_banks=["بانک ملت"])
        self.assertEqual(res_mellat.state, PageState.ONLY_RESERVE_AVAILABLE)
        self.assertEqual(res_mellat.target_bank_value, "12")
        self.assertEqual(res_mellat.target_bank_status, "reserve")

        # 4. Target is Maskan (Reserve)
        res_maskan = PageStateClassifier.classify(SAMPLE_HTML_MIXED, target_banks=["بانک مسکن"])
        self.assertEqual(res_maskan.state, PageState.ONLY_RESERVE_AVAILABLE)
        self.assertEqual(res_maskan.target_bank_value, "14")
        self.assertEqual(res_maskan.target_bank_status, "reserve")

        # 5. Target is Absent (Tejarat)
        res_absent = PageStateClassifier.classify(SAMPLE_HTML_MIXED, target_banks=["بانک تجارت"])
        self.assertEqual(res_absent.state, PageState.TARGET_BANK_ABSENT)
        self.assertIsNone(res_absent.target_bank_value)
        self.assertEqual(res_absent.target_bank_status, "absent")

        # Check counts
        self.assertEqual(len(res_saderat.active_banks), 2)
        self.assertEqual(len(res_saderat.reserve_banks), 2)

    def test_waf_rejection_and_support_id_extraction(self):
        res = PageStateClassifier.classify(SAMPLE_HTML_WAF_REJECTED, http_status=200)
        self.assertEqual(res.state, PageState.WAF_REJECTED)
        self.assertEqual(res.support_id, "18446744073709551615")
        self.assertTrue(len(res.errors) > 0)

    def test_maintenance_mode_and_503(self):
        res_html = PageStateClassifier.classify(SAMPLE_HTML_MAINTENANCE, http_status=200)
        self.assertEqual(res_html.state, PageState.MAINTENANCE_MODE)

        res_503 = PageStateClassifier.classify("<html>Service Unavailable</html>", http_status=503)
        self.assertEqual(res_503.state, PageState.MAINTENANCE_MODE)

    def test_province_city_required(self):
        res = PageStateClassifier.classify(SAMPLE_HTML_CITY_REQUIRED, http_status=200)
        self.assertEqual(res.state, PageState.PROVINCE_CITY_REQUIRED)

    def test_parse_real_saved_pages(self):
        saved_files = [
            f for f in glob.glob(os.path.join("saved_pages", "*", "banks-*.html"))
            if os.path.getsize(f) > 500
        ]
        if not saved_files:
            self.skipTest("No non-empty saved_pages HTML found.")

        tested = 0
        for fpath in saved_files[:8]:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                html = f.read()
            res = PageStateClassifier.classify(html)
            self.assertIn(
                res.state,
                [
                    PageState.BANK_ACTIVE_AVAILABLE,
                    PageState.ONLY_RESERVE_AVAILABLE,
                    PageState.TARGET_BANK_ABSENT,
                    PageState.MAINTENANCE_MODE,
                ],
            )
            tested += 1
        self.assertTrue(tested > 0)

    def test_capacity_telemetry_recording_and_retrieval(self):
        test_nid = "9876543210"
        row_id = DBHandler.record_capacity_telemetry(
            nid=test_nid,
            target_bank="بانک صادرات ایران",
            target_status="active",
            total_active_count=4,
            total_reserve_count=2,
            active_banks_json=["بانک صادرات", "بانک رفاه"],
            submission_latency_ms=18.5,
            submission_result="submitted",
        )
        self.assertIsNotNone(row_id)
        self.assertTrue(row_id > 0)

        rows = DBHandler.get_capacity_telemetry(nid=test_nid, limit=5)
        self.assertTrue(len(rows) >= 1)
        latest = rows[0]
        self.assertEqual(latest["nid"], test_nid)
        self.assertEqual(latest["target_bank"], "بانک صادرات ایران")
        self.assertEqual(latest["target_status"], "active")
        self.assertEqual(latest["total_active_count"], 4)
        self.assertEqual(latest["total_reserve_count"], 2)
        self.assertAlmostEqual(latest["submission_latency_ms"], 18.5, places=1)


if __name__ == "__main__":
    unittest.main()
