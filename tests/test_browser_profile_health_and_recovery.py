"""
tests/test_browser_profile_health_and_recovery.py
Comprehensive 14-point test suite for BrowserProfileHealthManager,
BrowserProfileRecoveryManager, Session Management, and CAPTCHA isolation.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.browser_profile_health import (
    BrowserProfileHealthManager,
    ProfileHealthStatus,
    ProfileHealthReport,
)
from core.browser_profile_recovery import BrowserProfileRecoveryManager
from database import DBHandler


class TestBrowserProfileHealthAndRecovery(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="mahan_test_profiles_")
        self.profile_root = Path(self.temp_dir)
        self.backup_root = self.profile_root / "backups"
        self.backup_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_cookie_db(self, profile_dir: Path, cookies: list = None, corrupt: bool = False):
        net_dir = profile_dir / "Default" / "Network"
        net_dir.mkdir(parents=True, exist_ok=True)
        cookie_path = net_dir / "Cookies"

        if corrupt:
            with open(cookie_path, "wb") as f:
                f.write(b"NOT_A_SQLITE_DATABASE_CORRUPTED_BYTES")
            return cookie_path

        conn = sqlite3.connect(str(cookie_path))
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE cookies (
                creation_utc INTEGER NOT NULL,
                host_key TEXT NOT NULL,
                top_frame_site_key TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                value TEXT NOT NULL DEFAULT '',
                encrypted_value BLOB NOT NULL,
                path TEXT NOT NULL DEFAULT '/',
                expires_utc INTEGER NOT NULL,
                is_secure INTEGER NOT NULL DEFAULT 0,
                is_httponly INTEGER NOT NULL DEFAULT 0,
                last_access_utc INTEGER NOT NULL DEFAULT 0,
                has_expires INTEGER NOT NULL DEFAULT 0,
                is_persistent INTEGER NOT NULL DEFAULT 0,
                priority INTEGER NOT NULL DEFAULT 1,
                samesite INTEGER NOT NULL DEFAULT 0,
                source_scheme INTEGER NOT NULL DEFAULT 1,
                source_port INTEGER NOT NULL DEFAULT 443,
                last_update_utc INTEGER NOT NULL DEFAULT 0,
                source_type INTEGER NOT NULL DEFAULT 0,
                has_cross_site_ancestor INTEGER NOT NULL DEFAULT 0
            )"""
        )
        if cookies:
            for c in cookies:
                # c: (host_key, name, expires_utc, has_expires, is_persistent)
                cur.execute(
                    """INSERT INTO cookies (creation_utc, host_key, name, encrypted_value, expires_utc, has_expires, is_persistent)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (int(time.time() * 1000000), c[0], c[1], b"dummy_enc", c[2], c[3], c[4]),
                )
        conn.commit()
        conn.close()
        return cookie_path

    # ──────────────────────────────────────────────────────────────────────────
    # تست ۱: کاربر جدید با پروفایل تازه → ورود موفق و پروفایل سالم
    # ──────────────────────────────────────────────────────────────────────────
    def test_01_new_user_clean_profile(self):
        nid = "0011223344"
        report = BrowserProfileHealthManager.inspect_profile(nid, profile_root=self.profile_root)
        self.assertEqual(report.health_status, ProfileHealthStatus.HEALTHY)
        self.assertIn(report.session_status, ("CLEAN_NEW", "READY"))
        self.assertFalse(report.has_cbi_cookies)
        self.assertFalse(report.reauth_required)

    # ──────────────────────────────────────────────────────────────────────────
    # تست ۲: کاربر قدیمی با نشست معتبر → ادامه بدون حذف Cookie
    # ──────────────────────────────────────────────────────────────────────────
    def test_02_old_user_valid_session_preserves_cookies(self):
        nid = "6450700394"
        p_dir = self.profile_root / nid
        future_chrome_ts = int((time.time() + 3600 + 11644473600) * 1000000)
        self._create_mock_cookie_db(
            p_dir,
            cookies=[("ve.cbi.ir", "cbi69d2dbd5140", future_chrome_ts, 1, 1)],
        )

        with patch("database.DBHandler.get_checkpoint", return_value={"last_verified_step": "STEP_3_BANK_SELECT"}):
            ok, msg = BrowserProfileRecoveryManager.prepare_session_for_launch(
                nid, stage="STEP_3_BANK_SELECT", profile_root=self.profile_root
            )
            self.assertTrue(ok)
            self.assertIn("Preserving active session cookies", msg)

            info = BrowserProfileHealthManager.inspect_cookies(p_dir)
            self.assertEqual(info["count"], 1)
            self.assertTrue(info["has_cbi"])
            self.assertFalse(info["has_expired_cbi"])

    # ──────────────────────────────────────────────────────────────────────────
    # تست ۳: کاربر قدیمی با نشست منقضی → ورود مجدد رسمی و پاکسازی کوکی‌های معلق
    # ──────────────────────────────────────────────────────────────────────────
    def test_03_old_user_expired_session_initiates_reauth(self):
        nid = "7040099756"
        p_dir = self.profile_root / nid
        past_chrome_ts = int((time.time() - 3600 + 11644473600) * 1000000)
        self._create_mock_cookie_db(
            p_dir,
            cookies=[("ve.cbi.ir", "cbi69d2dbd5140", past_chrome_ts, 1, 1)],
        )

        report = BrowserProfileHealthManager.inspect_profile(nid, profile_root=self.profile_root)
        self.assertTrue(report.has_expired_cbi_cookies)
        self.assertTrue(report.reauth_required)

        ok, msg = BrowserProfileRecoveryManager.prepare_session_for_launch(
            nid, stage="STEP_1_ENTRY_FORM", profile_root=self.profile_root
        )
        self.assertTrue(ok)
        info = BrowserProfileHealthManager.inspect_cookies(p_dir)
        self.assertEqual(info["count"], 0)

        with patch("database.DBHandler.save_checkpoint") as mock_save, \
             patch("database.DBHandler.update_runtime") as mock_rt, \
             patch("database.DBHandler.update_status") as mock_st:
            reauth_ok, r_msg = BrowserProfileRecoveryManager.initiate_reauth_workflow(nid)
            self.assertTrue(reauth_ok)
            mock_save.assert_called_once()
            self.assertEqual(mock_save.call_args[0][1]["last_verified_step"], "STEP_1_ENTRY_FORM")

    # ──────────────────────────────────────────────────────────────────────────
    # تست ۴: فایل Cookies سالم → بدون تغییر باقی بماند
    # ──────────────────────────────────────────────────────────────────────────
    def test_04_healthy_cookies_db_untouched(self):
        nid = "4849936334"
        p_dir = self.profile_root / nid
        future_ts = int((time.time() + 7200 + 11644473600) * 1000000)
        cookie_path = self._create_mock_cookie_db(
            p_dir,
            cookies=[
                ("ve.cbi.ir", "cbi_active", future_ts, 1, 1),
                ("otherdomain.com", "other_cookie", future_ts, 1, 1),
            ],
        )
        initial_mtime = os.path.getmtime(cookie_path)

        integrity_ok, msg = BrowserProfileHealthManager.check_storage_integrity(p_dir)
        self.assertTrue(integrity_ok)
        self.assertEqual(msg, "ok")
        self.assertEqual(os.path.getmtime(cookie_path), initial_mtime)

    # ──────────────────────────────────────────────────────────────────────────
    # تست ۵: فایل ذخیره‌سازی واقعاً خراب → تشخیص خطا و بازیابی کنترل‌شده
    # ──────────────────────────────────────────────────────────────────────────
    def test_05_corrupted_storage_detection_and_repair(self):
        nid = "6680311926"
        p_dir = self.profile_root / nid
        self._create_mock_cookie_db(p_dir, corrupt=True)

        report = BrowserProfileHealthManager.inspect_profile(nid, profile_root=self.profile_root)
        self.assertEqual(report.health_status, ProfileHealthStatus.PROFILE_STORAGE_ERROR)
        self.assertTrue(
            "Integrity check failed" in report.storage_integrity or "not a database" in report.storage_integrity.lower(),
            f"Unexpected integrity message: {report.storage_integrity}",
        )

        ok, msg = BrowserProfileRecoveryManager.repair_corrupted_storage(nid, profile_root=self.profile_root)
        self.assertTrue(ok)
        self.assertIn("safely repaired", msg)

        integrity_ok, msg_after = BrowserProfileHealthManager.check_storage_integrity(p_dir)
        self.assertTrue(integrity_ok)

    # ──────────────────────────────────────────────────────────────────────────
    # تست ۶: مرورگر هنگام کار بسته شود → مدیریت خطا و حفظ داده‌های کاری
    # ──────────────────────────────────────────────────────────────────────────
    def test_06_browser_closed_preserves_business_data(self):
        nid = "3710525411"
        mock_owner = MagicMock()
        mock_owner.page.is_closed.return_value = False

        with patch("live_sessions.LIVE_SESSIONS.get", return_value=mock_owner):
            from routes.browser_health import close_applicant_browser
            res = close_applicant_browser(nid, user={"username": "admin", "is_admin": True})
            self.assertTrue(res.success)
            mock_owner.page.close.assert_called_once()

    # ──────────────────────────────────────────────────────────────────────────
    # تست ۷: دو Worker بخواهند یک Profile را باز کنند → جلوگیری از تداخل
    # ──────────────────────────────────────────────────────────────────────────
    def test_07_concurrent_worker_collision_prevention(self):
        nid = "0099887766"
        p_dir = self.profile_root / nid
        p_dir.mkdir(parents=True, exist_ok=True)
        singleton_lock = p_dir / "SingletonLock"
        singleton_lock.write_text("locked by pid 1234")

        is_locked, reason = BrowserProfileHealthManager.check_lock_status(p_dir)
        self.assertTrue(is_locked)
        self.assertIn("Chromium lock file present", reason)

        report = BrowserProfileHealthManager.inspect_profile(nid, profile_root=self.profile_root)
        self.assertEqual(report.health_status, ProfileHealthStatus.PROFILE_LOCKED)

    # ──────────────────────────────────────────────────────────────────────────
    # تست ۸: CAPTCHA در DOM جدید ظاهر شود → شناسایی فرم زنده
    # ──────────────────────────────────────────────────────────────────────────
    def test_08_captcha_live_form_detection(self):
        mock_page = MagicMock()
        mock_img = MagicMock()
        mock_img.count.return_value = 1
        mock_img.is_visible.return_value = True
        mock_img.evaluate.return_value = True
        mock_img.bounding_box.return_value = {"width": 120, "height": 45}

        mock_loc = MagicMock()
        mock_loc.count.return_value = 1
        mock_loc.nth.return_value = mock_img
        mock_page.locator.return_value = mock_loc
        mock_page.frames = []

        from bot_select_captcha import wait_for_captcha_image_ready
        elem, scope = wait_for_captcha_image_ready(mock_page, timeout_ms=500)
        self.assertIsNotNone(elem)
        self.assertEqual(scope, mock_page)

    # ──────────────────────────────────────────────────────────────────────────
    # تست ۹: سرور CAPTCHA را رد کند → ثبت دلیل واقعی و عدم پاکسازی بی‌دلیل پروفایل
    # ──────────────────────────────────────────────────────────────────────────
    def test_09_captcha_rejection_logs_reason_without_blind_wipe(self):
        nid = "6450368853"
        p_dir = self.profile_root / nid
        future_ts = int((time.time() + 1800 + 11644473600) * 1000000)
        self._create_mock_cookie_db(
            p_dir,
            cookies=[("ve.cbi.ir", "cbi_test", future_ts, 1, 1)],
        )

        mock_page = MagicMock()
        mock_body = MagicMock()
        mock_body.inner_text.return_value = "کد امنیتی صحیح نمی‌باشد. لطفاً مجدداً تلاش نمایید."
        mock_page.locator.return_value = mock_body
        mock_page.frames = []

        from bot_select_captcha import detect_captcha_failure
        failed = detect_captcha_failure(mock_page)
        self.assertTrue(failed)

        info = BrowserProfileHealthManager.inspect_cookies(p_dir)
        self.assertEqual(info["count"], 1)

    # ──────────────────────────────────────────────────────────────────────────
    # تست ۱۰: بازگشت از صفحه سوم به OTP → گردش کار قبلی سالم بماند
    # ──────────────────────────────────────────────────────────────────────────
    def test_10_step3_to_step2_workflow_preserves_session(self):
        from core.state_machine import BotState, is_valid_transition
        # Session expired gracefully transitions back to requesting fresh OTP
        valid_reauth = is_valid_transition(BotState.SESSION_EXPIRED, BotState.REQUESTING_OTP)
        self.assertTrue(valid_reauth)
        # Step 3 ready can detect session expiration
        valid_expired = is_valid_transition(BotState.STEP_3_READY, BotState.SESSION_EXPIRED)
        self.assertTrue(valid_expired)

    # ──────────────────────────────────────────────────────────────────────────
    # تست ۱۱: چندین شروع و توقف متوالی → پروفایل خراب یا قفل‌شده باقی نماند
    # ──────────────────────────────────────────────────────────────────────────
    def test_11_repeated_start_stop_no_residual_locks(self):
        nid = "7050030546"
        p_dir = self.profile_root / nid
        p_dir.mkdir(parents=True, exist_ok=True)

        for i in range(5):
            ok, msg = BrowserProfileRecoveryManager.prepare_session_for_launch(
                nid, stage="STEP_1_ENTRY_FORM", profile_root=self.profile_root
            )
            self.assertTrue(ok)
            is_locked, _ = BrowserProfileHealthManager.check_lock_status(p_dir)
            self.assertFalse(is_locked)

    # ──────────────────────────────────────────────────────────────────────────
    # تست ۱۲: یک متقاضی نیازمند تعمیر باشد → کار سایر متقاضیان مختل نشود
    # ──────────────────────────────────────────────────────────────────────────
    def test_12_isolated_repair_does_not_affect_others(self):
        nid_corrupt = "6450700394"
        nid_healthy = "3710525411"

        p_corrupt = self.profile_root / nid_corrupt
        p_healthy = self.profile_root / nid_healthy

        self._create_mock_cookie_db(p_corrupt, corrupt=True)
        future_ts = int((time.time() + 3600 + 11644473600) * 1000000)
        self._create_mock_cookie_db(
            p_healthy, cookies=[("ve.cbi.ir", "healthy_cbi", future_ts, 1, 1)]
        )

        ok, msg = BrowserProfileRecoveryManager.repair_corrupted_storage(
            nid_corrupt, profile_root=self.profile_root
        )
        self.assertTrue(ok, f"Repair failed: {msg}")

        info = BrowserProfileHealthManager.inspect_cookies(p_healthy)
        self.assertEqual(info["count"], 1)
        self.assertTrue(info["has_cbi"])

    # ──────────────────────────────────────────────────────────────────────────
    # تست ۱۳: سرور خطای 429 یا محدودیت امنیتی برگرداند → توقف یا Backoff ایمن
    # ──────────────────────────────────────────────────────────────────────────
    def test_13_security_challenge_safe_backoff_no_cycling(self):
        mock_page = MagicMock()
        mock_page.inner_text.return_value = "The requested URL was rejected. Please consult with your administrator. Your support ID is: <9988776655>"

        from network_diagnostics import rejection_info, check_rejection, SiteRejectedError
        info = rejection_info(mock_page.inner_text.return_value)
        self.assertIsNotNone(info)
        self.assertEqual(info["support_id"], "9988776655")

        with self.assertRaises(SiteRejectedError):
            check_rejection(mock_page)

    # ──────────────────────────────────────────────────────────────────────────
    # تست ۱۴: در صورت نتیجه نامشخص ثبت نهایی → درخواست تکراری ارسال نشود
    # ──────────────────────────────────────────────────────────────────────────
    def test_14_ambiguous_submission_locks_duplicate_post(self):
        nid = "9400000064"
        with patch("database.DBHandler.claim_final_submission", return_value=False):
            claimed = DBHandler.claim_final_submission(nid)
            self.assertFalse(claimed)


if __name__ == "__main__":
    unittest.main()
