"""
tests/test_database_and_bank_pipeline.py — Comprehensive test suite for MAHANBOT
Relational Database Architecture, Normalization, ApplicantConfigurationService,
and Bank Selection Pipeline (Scenarios 1-16).
"""
import copy
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock

from core.applicant_config_service import (
    ApplicantConfig,
    ApplicantConfigurationService,
    BankPreference,
)
from core.db_migration import (
    check_database_health,
    create_database_backup,
    restore_database_backup,
    run_database_migration,
)
from core.persian_normalizer import (
    is_special_location_case,
    match_location_name,
    normalize_compact_key,
    normalize_location_key,
    normalize_national_id,
    normalize_persian_text,
    normalize_postal_code,
)


class MockPage:
    """Mock Playwright Page for bank and location selection testing."""

    def __init__(self, options=None, state_name="سیستان و بلوچستان", city_options=None):
        self.options = options or []
        self.state_name = state_name
        self.city_options = city_options or [{"v": "793", "t": "مهرستان", "s": True}]
        self.selected_options = {}
        self.clicked_selectors = []
        self._listeners = {}

    def locator(self, selector):
        mock_loc = MagicMock()
        if "labStateName" in selector:
            mock_loc.count.return_value = 1
            mock_loc.first.is_visible.return_value = True
            mock_loc.first.inner_text.return_value = self.state_name
        elif "ddlCity" in selector:
            mock_loc.count.return_value = 1
            mock_loc.first.is_visible.return_value = True
        elif "ddlBankName" in selector:
            mock_loc.count.return_value = 1
            mock_loc.first.is_visible.return_value = True
        elif "rbtnGheireHozori" in selector:
            mock_loc.count.return_value = 1
            mock_loc.first.is_visible.return_value = True
            mock_loc.first.is_checked.return_value = True
        elif "ddlBranch" in selector:
            mock_loc.count.return_value = 1
            mock_loc.first.is_visible.return_value = True
        else:
            mock_loc.count.return_value = 0
            mock_loc.first.is_visible.return_value = False
        return mock_loc

    def evaluate(self, script, arg=None):
        if "ddlCity" in script or (isinstance(arg, str) and "ddlCity" in arg):
            return self.city_options
        if "ddlBankName" in script or (isinstance(arg, str) and "ddlBankName" in arg):
            return self.options
        if "rbtn" in script:
            return [{"id": "ctl00_ContentPlaceHolder1_rbtnGheireHozori", "text": "غیرحضوری", "checked": True}]
        if "ddlBranch" in script:
            return [{"v": "101", "t": "شعبه مرکزی مهرستان", "s": True}]
        return []

    def select_option(self, selector, value=None):
        self.selected_options[selector] = value
        for opt in self.options:
            if str(opt.get("value") or opt.get("v")) == str(value):
                opt["s"] = True
                opt["selected"] = True
            else:
                opt["s"] = False
                opt["selected"] = False
        return [value]

    def click(self, selector, **kwargs):
        self.clicked_selectors.append(selector)

    def wait_for_timeout(self, ms):
        pass

    def on(self, event, handler):
        self._listeners[event] = handler


class TestDatabaseAndBankPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_cbi.db")
        # Create minimal sqlite structure
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """CREATE TABLE applicants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT,
                national_id TEXT UNIQUE,
                status TEXT DEFAULT 'Ready',
                last_log TEXT DEFAULT '-',
                data TEXT
            )"""
        )
        c.execute(
            """CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )"""
        )
        # Seed 2 initial test applicants
        app1_data = {
            "state": "سیستان و بلوچستان",
            "city": "مهرستان",
            "priority_banks": ["صادرات"],
            "bank_mode": "prefer_online",
            "allow_reserve": True,
        }
        app2_data = {
            "state": "لرستان",
            "city": "ازنا",
            "priority_banks": ["تجارت", "ملی"],
            "bank_mode": "only_online",
            "allow_reserve": False,
        }
        c.execute(
            "INSERT INTO applicants (full_name, national_id, data) VALUES (?, ?, ?)",
            ("سعید دادکاریان", "6450700394", json.dumps(app1_data, ensure_ascii=False)),
        )
        c.execute(
            "INSERT INTO applicants (full_name, national_id, data) VALUES (?, ?, ?)",
            ("حسین هبودی", "4840192286", json.dumps(app2_data, ensure_ascii=False)),
        )
        conn.commit()
        conn.close()

        # Run migration on the test db
        run_database_migration(self.db_path)
        self.svc = ApplicantConfigurationService(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # 1. استان و شهر صحیح
    def test_01_correct_province_and_city(self):
        cfg = self.svc.get_applicant_config("6450700394")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.province_name, "سیستان و بلوچستان")
        self.assertEqual(cfg.city_name, "مهرستان")
        valid, status, msg = self.svc.validate_site_location(cfg, "سیستان و بلوچستان", "مهرستان")
        self.assertTrue(valid)
        self.assertEqual(status, "VERIFIED")

    # 2. شهر متعلق به استان دیگر
    def test_02_cross_province_city_mismatch(self):
        cfg = self.svc.get_applicant_config("6450700394")
        # Site shows Tehran instead of Sistan & Baluchestan
        valid, status, msg = self.svc.validate_site_location(cfg, "تهران", "تهران")
        self.assertFalse(valid)
        self.assertEqual(status, "LOCATION_MISMATCH")
        self.assertIn("مغایرت دارد", msg)

    # 3. اختلاف نام شهر بین دیتابیس و سایت (نرمال‌سازی و تطبیق موفق)
    def test_03_city_name_variation_and_aliases(self):
        cfg = self.svc.get_applicant_config("6450700394")
        # Site uses historical alias "زابلی" for "مهرستان"
        valid, status, msg = self.svc.validate_site_location(cfg, "سیستان و بلوچستان", "زابلی")
        self.assertTrue(valid)
        self.assertEqual(status, "VERIFIED")

        # Test Lorestan Khorramabad spelling variation
        k1 = normalize_location_key("خرم‌اباد")
        k2 = normalize_location_key("خرم آباد")
        self.assertEqual(k1, k2)

    # 4. نام‌های فارسی و عربی متفاوت (ي/ی، ك/ک، همزه)
    def test_04_arabic_persian_character_normalization(self):
        # Arabic Yeh and Kaf
        self.assertEqual(normalize_persian_text("سيستان و بلوچستان"), "سیستان و بلوچستان")
        self.assertEqual(normalize_persian_text("بانك ملّي ايران"), "بانک ملی ایران")
        self.assertEqual(normalize_location_key("كردستان"), "کردستان")
        self.assertEqual(normalize_compact_key("چهار محال و بختياري"), "چهارمحالوبختیاری")

    # 5. بانک اولویت اول موجود
    def test_05_first_priority_bank_available(self):
        cfg = self.svc.get_applicant_config("6450700394")
        dom_options = [
            {"value": "0", "text": "-- انتخاب کنيد --"},
            {"value": "19", "text": "بانک صادرات [دارای ظرفیت ثبت نام] - متقاضی در صف : 100 نفر"},
            {"value": "12", "text": "بانک ملی ایران [دارای ظرفیت ثبت نام] - متقاضی در صف : 50 نفر"},
        ]
        res = self.svc.match_priority_banks_to_dom_options(cfg, dom_options)
        self.assertEqual(res["status"], "MATCHED")
        self.assertEqual(res["chosen_bank"]["value"], "19")
        self.assertEqual(res["chosen_bank"]["bank_name"], "صادرات")
        self.assertFalse(res["is_reserve"])

    # 6. بانک اولویت اول ناموجود
    def test_06_first_priority_bank_missing(self):
        # App 2 prefers Tejarat then Melli
        cfg = self.svc.get_applicant_config("4840192286")
        dom_options = [
            {"value": "0", "text": "-- انتخاب کنيد --"},
            {"value": "12", "text": "بانک ملی ایران [دارای ظرفیت ثبت نام] - متقاضی در صف : 50 نفر"},
            {"value": "15", "text": "بانک ملت [دارای ظرفیت ثبت نام] - متقاضی در صف : 80 نفر"},
        ]
        # Tejarat is missing, so it should fall back to second priority (Melli)
        res = self.svc.match_priority_banks_to_dom_options(cfg, dom_options)
        self.assertEqual(res["status"], "MATCHED")
        self.assertEqual(res["chosen_bank"]["value"], "12")
        self.assertEqual(res["chosen_bank"]["bank_name"], "ملی")

        # When NONE of the priorities exist:
        dom_options_none = [
            {"value": "0", "text": "-- انتخاب کنيد --"},
            {"value": "15", "text": "بانک ملت [دارای ظرفیت ثبت نام] - متقاضی در صف : 80 نفر"},
        ]
        res_none = self.svc.match_priority_banks_to_dom_options(cfg, dom_options_none)
        self.assertEqual(res_none["status"], "BANK_NOT_AVAILABLE")
        self.assertIsNone(res_none["chosen_bank"])

    # 7. بانک در حالت ذخیره (تفاوت رفتار مجاز بودن vs غیرمجاز بودن)
    def test_07_reserve_bank_allow_vs_disallow(self):
        cfg_disallow = self.svc.get_applicant_config("4840192286")  # allow_reserve = False
        dom_options = [
            {"value": "18", "text": "بانک تجارت [در انتظار تخصیص ظرفیت جدید (ذخیره)] - متقاضی در صف : 200 نفر"},
        ]
        res_disallow = self.svc.match_priority_banks_to_dom_options(
            cfg_disallow, dom_options, allow_reserve_global=False
        )
        self.assertEqual(res_disallow["status"], "BANK_RESERVE_ONLY")
        self.assertIsNone(res_disallow["chosen_bank"])

        # When allowed:
        cfg_allow = self.svc.get_applicant_config("6450700394")  # allow_reserve = True
        dom_saderat_reserve = [
            {"value": "19", "text": "بانک صادرات [در انتظار تخصیص ظرفیت جدید (ذخیره)] - متقاضی در صف : 4048 نفر"},
        ]
        res_allow = self.svc.match_priority_banks_to_dom_options(
            cfg_allow, dom_saderat_reserve, allow_reserve_global=True
        )
        self.assertEqual(res_allow["status"], "MATCHED")
        self.assertEqual(res_allow["chosen_bank"]["value"], "19")
        self.assertTrue(res_allow["is_reserve"])

    # 8. بانک با شناسه متفاوت در DOM (تطبیق بر اساس متن و کانونیکال، نه مقدار هاردکد شده)
    def test_08_dynamic_dom_value_matching(self):
        cfg = self.svc.get_applicant_config("6450700394")
        # CBI site gave value "888" to Saderat on this dynamic render
        dom_options = [
            {"value": "888", "text": "بانک صادرات ایران [دارای ظرفیت ثبت نام]"},
        ]
        res = self.svc.match_priority_banks_to_dom_options(cfg, dom_options)
        self.assertEqual(res["status"], "MATCHED")
        self.assertEqual(res["chosen_bank"]["value"], "888")

    # 9. بانک غیرحضوری با ظرفیت تکمیل‌شده
    def test_09_online_mode_capacity_full_preservation(self):
        cfg = self.svc.get_applicant_config("4840192286")
        self.assertIn("online", cfg.priority_banks[0].visit_method)
        # Ensure that mode is strictly online and not flipped to in_person
        self.assertNotIn("in_person", cfg.priority_banks[0].visit_method)

    # 10. بانک حضوری با شعبه انتخابی
    def test_10_in_person_mode_with_branch_selection(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """UPDATE applicant_bank_preferences
               SET visit_method='in_person', preferred_branch_text='شعبه مرکزی مهرستان ۱۲۳۴'
               WHERE applicant_id = 1"""
        )
        conn.commit()
        conn.close()

        cfg = self.svc.get_applicant_config("6450700394")
        self.assertEqual(cfg.priority_banks[0].visit_method, "in_person")
        self.assertEqual(cfg.priority_banks[0].preferred_branch_text, "شعبه مرکزی مهرستان ۱۲۳۴")

    # 11. متقاضیان همزمان با استان‌های متفاوت (ایزولاسیون کامل)
    def test_11_concurrent_applicants_cross_province_isolation(self):
        cfg1 = self.svc.get_applicant_config("6450700394")
        cfg2 = self.svc.get_applicant_config("4840192286")
        self.assertNotEqual(cfg1.province_id, cfg2.province_id)
        self.assertEqual(cfg1.province_name, "سیستان و بلوچستان")
        self.assertEqual(cfg2.province_name, "لرستان")

        # Test independent site validations
        v1, _, _ = self.svc.validate_site_location(cfg1, "سیستان و بلوچستان", "مهرستان")
        v2, _, _ = self.svc.validate_site_location(cfg2, "لرستان", "ازنا")
        self.assertTrue(v1)
        self.assertTrue(v2)

        # Cross check fails cleanly
        v1_cross, s1, _ = self.svc.validate_site_location(cfg1, "لرستان", "ازنا")
        self.assertFalse(v1_cross)
        self.assertEqual(s1, "LOCATION_MISMATCH")

    # 12. تغییر تنظیمات یک متقاضی بدون تأثیر بر دیگران
    def test_12_applicant_mutation_isolation(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        # Change Applicant 1
        c.execute("UPDATE applicants SET full_name='سعید دادکاریان اصلاحی' WHERE id=1")
        conn.commit()
        conn.close()

        cfg1 = self.svc.get_applicant_config("6450700394")
        cfg2 = self.svc.get_applicant_config("4840192286")

        self.assertEqual(cfg1.full_name, "سعید دادکاریان اصلاحی")
        self.assertEqual(cfg2.full_name, "حسین هبودی")

    # 13. اجرای مجدد Migration (Idempotency)
    def test_13_migration_idempotency(self):
        # Run migration a 2nd and 3rd time
        res2 = run_database_migration(self.db_path)
        self.assertEqual(res2["status"], "success")
        self.assertEqual(res2["integrity"], "ok")

        res3 = run_database_migration(self.db_path)
        self.assertEqual(res3["status"], "success")

        # Confirm applicant count remains 2
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM applicants")
        count = c.fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)

    # 14. بازیابی دیتابیس از Backup
    def test_14_database_backup_and_restore(self):
        backup_file = create_database_backup(self.db_path, prefix="test_point")
        self.assertTrue(os.path.exists(backup_file))

        # Mutate current DB
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM applicants WHERE id=2")
        conn.commit()
        c.execute("SELECT COUNT(*) FROM applicants")
        self.assertEqual(c.fetchone()[0], 1)
        conn.close()

        # Restore from backup
        restore_database_backup(backup_file, self.db_path)

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM applicants")
        self.assertEqual(c.fetchone()[0], 2)
        conn.close()

    # 15. بررسی حفظ تعداد متقاضیان و اطلاعات اصلی
    def test_15_data_integrity_and_leading_zeros(self):
        nid_padded = normalize_national_id("12345678")
        self.assertEqual(len(nid_padded), 10)
        self.assertEqual(nid_padded, "0012345678")

        postal_padded = normalize_postal_code("98765")
        self.assertEqual(len(postal_padded), 10)
        self.assertEqual(postal_padded, "0000098765")

        health = check_database_health(self.db_path)
        self.assertTrue(health["is_healthy"])
        self.assertEqual(health["integrity"], "ok")
        self.assertEqual(len(health["fk_violations"]), 0)

    # 16. ثبت شواهد DOM بدون کلیک واقعی روی دکمه ثبت نهایی
    def test_16_dom_observation_recording(self):
        obs_id = self.svc.record_bank_page_observation(
            applicant_id=1,
            session_id="session_test_123",
            site_province="سیستان و بلوچستان",
            site_city="مهرستان",
            available_banks=[{"name": "بانک صادرات", "value": "19"}],
            raw_options=[{"text": "بانک صادرات", "value": "19", "selected": True, "is_reserve": True}],
            selected_bank="بانک صادرات",
            selected_mode="online",
            capacity_status="RESERVE_ONLY",
            verification_status="VERIFIED",
        )
        self.assertGreater(obs_id, 0)

        latest = self.svc.get_latest_observation(applicant_id=1, max_age_seconds=10.0)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["selected_bank"], "بانک صادرات")
        self.assertEqual(latest["capacity_status"], "RESERVE_ONLY")
        self.assertEqual(latest["verification_status"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()
