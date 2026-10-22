"""
tests/test_sms_gsm_pipeline.py — Unit tests for Phase 1:
- Automated SMS receiving and routing service (sms_service.py)
- GSM dongle / USB modem AT listener (gsm_modem_service.py)
- Pre-warmed ultra-fast CAPTCHA memory solver (api_solver.py)
- FastAPI endpoints for SMS webhook and GSM management
"""
import os
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient

from sms_service import (
    normalize_digits,
    extract_otp_code,
    extract_national_id,
    extract_mobile_number,
    parse_incoming_sms,
    route_and_apply_sms,
)
from gsm_modem_service import GsmModemService, get_gsm_service
import api_solver


class TestSmsParser(unittest.TestCase):
    """Tests for regex parsing and digit normalization of incoming Persian SMS."""

    def test_normalize_digits(self):
        persian_nums = "۰۱۲۳۴۵۶۷۸۹"
        arabic_nums = "٠١٢٣٤٥٦٧٨٩"
        self.assertEqual(normalize_digits(persian_nums), "0123456789")
        self.assertEqual(normalize_digits(arabic_nums), "0123456789")
        self.assertEqual(normalize_digits("کد 123 و ۴۵۶"), "کد 123 و 456")

    def test_extract_otp_code_standard(self):
        text = "کد تایید شما در سامانه تسهیلات قرض الحسنه ازدواج: 582419"
        self.assertEqual(extract_otp_code(text), "582419")

    def test_extract_otp_code_persian_digits(self):
        text = "کد فعالسازی سامانه ve.cbi.ir: ۴۹۱۰۲۵ اعتبار تا ۲ دقیقه"
        self.assertEqual(extract_otp_code(text), "491025")

    def test_extract_otp_with_national_id(self):
        text = "متقاضی محترم کد ملی 0012345678، کد تایید شما 741852 می باشد."
        otp = extract_otp_code(text)
        nid = extract_national_id(text)
        self.assertEqual(otp, "741852")
        self.assertEqual(nid, "0012345678")

    def test_extract_mobile_number(self):
        text1 = "شماره همراه: 09123456789"
        text2 = "+989351234567"
        self.assertEqual(extract_mobile_number(text1), "09123456789")
        self.assertEqual(extract_mobile_number(text2), "09351234567")

    def test_parse_incoming_sms_full(self):
        text = "کد تایید وام فرزندآوری شما: 639201 برای کدملی 0451234567"
        parsed = parse_incoming_sms(text, from_number="09120000000")
        self.assertEqual(parsed["otp_code"], "639201")
        self.assertEqual(parsed["national_id"], "0451234567")
        self.assertEqual(parsed["sender"], "09120000000")


class TestSmsRouter(unittest.TestCase):
    """Tests for matching received SMS with active applicants in waiting states."""

    @patch("sms_service.DBHandler")
    def test_route_by_national_id(self, mock_db_class):
        mock_db_class.get_applicant.return_value = {"national_id": "0012345678", "full_name": "علی محمدی"}
        with patch("routes.otp._handle_receive_sms", return_value=True) as mock_handle, \
             patch("routes.otp._emit_applicants_snapshot"):
            res = route_and_apply_sms(text="کد ورود شما: 112233 کدملی 0012345678")
            self.assertTrue(res["success"])
            self.assertEqual(res["national_id"], "0012345678")
            self.assertEqual(res["code"], "112233")
            mock_handle.assert_called_once()

    @patch("sms_service.find_active_otp_waiter")
    @patch("sms_service.DBHandler")
    def test_route_by_active_waiter_fallback(self, mock_db_class, mock_find_waiter):
        mock_db_class.get_applicant.return_value = None
        mock_db_class.get_all_applicants.return_value = []
        mock_find_waiter.return_value = {"national_id": "0098765432", "full_name": "رضا رضایی"}

        with patch("routes.otp._handle_receive_sms", return_value=True) as mock_handle, \
             patch("routes.otp._emit_applicants_snapshot"):
            text = "کد تایید شما در سامانه تسهیلات ازدواج 889900 است."
            res = route_and_apply_sms(text=text)
            self.assertTrue(res["success"])
            self.assertEqual(res["national_id"], "0098765432")
            self.assertEqual(res["code"], "889900")
            mock_handle.assert_called_once()


class TestGsmModemService(unittest.TestCase):
    """Tests for GSM modem USB dongle background service."""

    def test_gsm_service_singleton(self):
        s1 = get_gsm_service()
        s2 = get_gsm_service()
        self.assertIs(s1, s2)

    def test_gsm_initial_status(self):
        service = GsmModemService()
        status = service.get_status()
        self.assertFalse(status["running"])
        self.assertEqual(status["baudrate"], 115200)

    def test_list_serial_ports(self):
        service = GsmModemService()
        ports = service.list_available_ports()
        self.assertIsInstance(ports, list)


class TestFastCaptcha(unittest.TestCase):
    """Tests for in-memory ultra-fast CAPTCHA solver and prewarm."""

    def test_prewarm_callable(self):
        # Prewarm should run gracefully without raising exceptions
        result = api_solver.prewarm()
        self.assertIsInstance(result, bool)

    def test_solve_element_in_memory_mock(self):
        mock_locator = MagicMock()
        # Mock screenshot returning bytes directly
        mock_locator.screenshot.return_value = b"\x89PNG\r\n\x1a\nfakeimagebytes"

        service = api_solver._get_default_service()
        with patch.object(service, "solve", return_value="12345") as mock_solve:
            code = api_solver.solve_element(mock_locator)
            self.assertEqual(code, "12345")
            mock_locator.screenshot.assert_called_once_with(type="png")
            mock_solve.assert_called_once_with(b"\x89PNG\r\n\x1a\nfakeimagebytes")


class TestSmsGsmEndpoints(unittest.TestCase):
    """Integration tests for SMS webhook and GSM FastAPI endpoints."""

    @classmethod
    def setUpClass(cls):
        from server import app
        cls.client = TestClient(app, cookies={"mahan_session": "test_token"})

    def setUp(self):
        self.session_patch = patch("server._session_user", return_value={"username": "admin", "is_admin": 1, "active": 1})
        self.session_patch.start()

    def tearDown(self):
        self.session_patch.stop()

    def test_test_parse_sms_endpoint(self):
        res = self.client.post(
            "/api/sms/test-parse",
            json={"text": "کد ورود شما در سامانه ازدواج: 334455 برای کد ملی 0021345678"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["parsed"]["otp_code"], "334455")
        self.assertEqual(data["parsed"]["national_id"], "0021345678")

    def test_gsm_ports_endpoint(self):
        res = self.client.get("/api/gsm/ports")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("ports", data)

    def test_gsm_status_endpoint(self):
        res = self.client.get("/api/gsm/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("running", data)
        self.assertIn("port", data)

    def test_sms_webhook_no_active_waiter(self):
        # An unrouteable SMS is rejected explicitly so Android can surface delivery failure.
        with patch("sms_service.find_active_otp_waiter", return_value=None):
            res = self.client.post("/api/sms/webhook", json={"text": "کد تایید شما: 654321"})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["detail"]["error"], "applicant_not_matched")
