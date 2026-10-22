"""
tests/test_otp_auth_system.py — Comprehensive tests for real OTP, mobile auth, rate limiting, and password reset.
"""
import time
import unittest
from fastapi.testclient import TestClient
from server import app
from database import DBHandler
from core.sms_gateway import normalize_mobile, mask_mobile, MockSMSGateway, get_sms_gateway


class OTPAuthSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        DBHandler.init_db()
        cls.client = TestClient(app)
        # Create a test admin user and client user
        cls.admin_user = "test_admin_auth"
        cls.admin_pass = "AdminPass123!"
        cls.admin_mobile = "09121112233"
        DBHandler.save_app_user(cls.admin_user, cls.admin_pass, 30, is_admin=True)
        DBHandler.link_mobile_to_user(cls.admin_user, cls.admin_mobile)

        cls.client_user = "test_client_otp"
        cls.client_pass = "ClientPass123!"
        cls.client_mobile = "09359998877"
        DBHandler.save_app_user(cls.client_user, cls.client_pass, 30, is_admin=False)
        DBHandler.link_mobile_to_user(cls.client_user, cls.client_mobile)

    def setUp(self):
        # Clean up test OTPs so tests are isolated and don't trip cooldowns
        with DBHandler._connect() as conn:
            conn.execute("DELETE FROM auth_otps")
            conn.commit()
        MockSMSGateway.last_sent.clear()

    def test_01_mobile_normalization_and_masking(self):
        """Verify Persian digits, leading +98/0098, and masking format."""
        self.assertEqual(normalize_mobile("۰۹۱۲۱۱۱۲۲۳۳"), "09121112233")
        self.assertEqual(normalize_mobile("+989121112233"), "09121112233")
        self.assertEqual(normalize_mobile("00989121112233"), "09121112233")
        self.assertEqual(normalize_mobile("9121112233"), "09121112233")
        self.assertIsNone(normalize_mobile("123456"))
        self.assertIsNone(normalize_mobile("08123456789"))

        self.assertEqual(mask_mobile("09121112233"), "0912***2233")

    def test_02_password_login_success_and_failure(self):
        """Verify standard username/password login and wrong credential rejection."""
        # Success
        resp = self.client.post("/login", json={"username": self.admin_user, "password": self.admin_pass})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("username"), self.admin_user)
        self.assertTrue(data.get("is_admin"))

        # Wrong password
        bad_resp = self.client.post("/login", json={"username": self.admin_user, "password": "WrongPassword"})
        self.assertEqual(bad_resp.status_code, 401)

    def test_03_otp_send_and_verify_success(self):
        """Test full OTP send -> mock dispatch -> verify -> session cookie issued."""
        mobile = "09191234567"
        # 1. Send OTP
        resp = self.client.post("/auth/otp/send", json={"mobile": mobile, "purpose": "login"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertIn("0919***4567", data.get("masked_mobile"))

        # Retrieve mock code dispatched
        code = MockSMSGateway.last_sent.get(mobile)
        self.assertIsNotNone(code)
        self.assertEqual(len(code), 6)

        # 2. Verify OTP
        v_resp = self.client.post("/auth/otp/verify", json={"mobile": mobile, "code": code, "purpose": "login"})
        self.assertEqual(v_resp.status_code, 200)
        v_data = v_resp.json()
        self.assertEqual(v_data.get("status"), "ok")

        # Session cookie must be set
        self.assertIn("mahan_session", v_resp.cookies)

    def test_04_otp_cooldown_and_rate_limiting(self):
        """Ensure requesting OTP repeatedly triggers 120s cooldown and 429 status."""
        test_mobile = "09187654321"
        # First send -> ok
        r1 = self.client.post("/auth/otp/send", json={"mobile": test_mobile, "purpose": "login"})
        self.assertEqual(r1.status_code, 200)

        # Immediate second send -> rejected due to cooldown (429)
        r2 = self.client.post("/auth/otp/send", json={"mobile": test_mobile, "purpose": "login"})
        self.assertEqual(r2.status_code, 429)
        self.assertIn("شکیبا", r2.json().get("detail", ""))

    def test_05_otp_wrong_code_and_brute_force_lock(self):
        """Verify entering wrong codes decrements attempts and locks after 5 tries."""
        mobile = "09371112233"
        # Dispatch code
        r = self.client.post("/auth/otp/send", json={"mobile": mobile, "purpose": "login"})
        self.assertEqual(r.status_code, 200)

        # Try wrong code 4 times
        for _ in range(4):
            bad_v = self.client.post("/auth/otp/verify", json={"mobile": mobile, "code": "000000", "purpose": "login"})
            self.assertEqual(bad_v.status_code, 400)
            self.assertIn("تلاش دیگر", bad_v.json().get("detail", ""))

        # 5th attempt invalidates code
        bad_v5 = self.client.post("/auth/otp/verify", json={"mobile": mobile, "code": "000000", "purpose": "login"})
        self.assertEqual(bad_v5.status_code, 400)
        self.assertIn("باطل شد", bad_v5.json().get("detail", ""))

        # Even if correct code entered now, it is invalidated
        correct_code = MockSMSGateway.last_sent.get(mobile)
        late_v = self.client.post("/auth/otp/verify", json={"mobile": mobile, "code": correct_code, "purpose": "login"})
        self.assertEqual(late_v.status_code, 400)

    def test_06_otp_replay_prevention(self):
        """Verify code cannot be reused once consumed."""
        mobile = "09215556677"
        self.client.post("/auth/otp/send", json={"mobile": mobile, "purpose": "login"})
        code = MockSMSGateway.last_sent.get(mobile)

        # Consume code
        v1 = self.client.post("/auth/otp/verify", json={"mobile": mobile, "code": code, "purpose": "login"})
        self.assertEqual(v1.status_code, 200)

        # Replay attempt
        v2 = self.client.post("/auth/otp/verify", json={"mobile": mobile, "code": code, "purpose": "login"})
        self.assertEqual(v2.status_code, 400)

    def test_07_password_reset_flow_via_otp(self):
        """Verify requesting reset OTP, validating code, and resetting password."""
        # 1. Request reset OTP for admin_mobile
        r = self.client.post("/auth/otp/send", json={"mobile": self.admin_mobile, "purpose": "reset_password"})
        self.assertEqual(r.status_code, 200)

        code = MockSMSGateway.last_sent.get(self.admin_mobile)
        self.assertIsNotNone(code)

        # 2. Verify reset OTP -> returns reset_token
        v = self.client.post("/auth/otp/verify", json={"mobile": self.admin_mobile, "code": code, "purpose": "reset_password"})
        self.assertEqual(v.status_code, 200)
        reset_token = v.json().get("reset_token")
        self.assertIsNotNone(reset_token)

        # 3. Change password
        new_pass = "BrandNewSecretPass999!"
        rp = self.client.post("/auth/reset-password", json={"reset_token": reset_token, "new_password": new_pass})
        self.assertEqual(rp.status_code, 200)

        # 4. Login with new password
        login_new = self.client.post("/login", json={"username": self.admin_user, "password": new_pass})
        self.assertEqual(login_new.status_code, 200)

    def test_08_admin_sms_config_endpoints(self):
        """Verify admin can view, update, and test SMS gateway settings."""
        # 1. Login as admin
        admin_login = self.client.post("/login", json={"username": self.admin_user, "password": "BrandNewSecretPass999!"})
        self.assertEqual(admin_login.status_code, 200)

        # 2. Get SMS config
        cfg_resp = self.client.get("/admin/sms-config")
        self.assertEqual(cfg_resp.status_code, 200)
        self.assertIn("sms_provider", cfg_resp.json())

        # 3. Update SMS config
        up_resp = self.client.post("/admin/sms-config", json={
            "otp_auth_enabled": True,
            "sms_provider": "kavenegar",
            "sms_api_key": "dummy_secret_key_12345",
            "sms_sender_line": "1000888",
            "sms_pattern_code": "mahan_otp",
            "sms_otp_template": "کد ورود شما: {code}",
        })
        self.assertEqual(up_resp.status_code, 200)

        # Verify DB config updated
        db_cfg = DBHandler.get_config()
        self.assertEqual(db_cfg.get("sms_provider"), "kavenegar")
        self.assertEqual(db_cfg.get("sms_api_key"), "dummy_secret_key_12345")

        # 4. Switch back to mock for safe testing
        self.client.post("/admin/sms-config", json={
            "otp_auth_enabled": True,
            "sms_provider": "mock",
        })

    def test_09_admin_sms_test_dispatch(self):
        """Verify admin test SMS dispatch."""
        self.client.post("/login", json={"username": self.admin_user, "password": "BrandNewSecretPass999!"})
        test_resp = self.client.post("/admin/sms-test", json={"mobile": "09129990000"})
        self.assertEqual(test_resp.status_code, 200)
        self.assertEqual(MockSMSGateway.last_sent.get("09129990000"), "123456")

    def test_10_otp_persian_digits_normalization(self):
        """Verify verification accepts Persian digits (e.g. ۵۸۲۴۱۹)."""
        mobile = "09361114455"
        self.client.post("/auth/otp/send", json={"mobile": mobile, "purpose": "login"})
        code = MockSMSGateway.last_sent.get(mobile)

        # Convert to Persian digits
        fa_digits = "۰۱۲۳۴۵۶۷۸۹"
        fa_code = "".join(fa_digits[int(d)] for d in code)

        v = self.client.post("/auth/otp/verify", json={"mobile": mobile, "code": fa_code, "purpose": "login"})
        self.assertEqual(v.status_code, 200)


if __name__ == "__main__":
    unittest.main()
