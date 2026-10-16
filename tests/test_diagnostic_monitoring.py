"""
tests/test_diagnostic_monitoring.py — Comprehensive Unit & Integration Tests for Phase 4:
1. Deep Diagnostic System & Live Health Summary
2. One-Click In-Memory ZIP Exporter & Sanitization
3. Real-Time Typed WebSocket Event Broadcasting (favorite_found, registration_confirmed, otp_required, capacity_full)
4. Diagnostic Test Event Broadcaster
"""
import io
import json
import zipfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from server import app
from database import DBHandler
from event_logger import (
    EVENT_BROADCASTER,
    build_event,
    emit_structured_event,
)
from routes.diagnostic import get_diagnostic_summary_dict


class TestDiagnosticMonitoring(unittest.TestCase):
    """Test Phase 4 Live Diagnostic summary and ZIP export capabilities."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, cookies={"mahan_session": "test_token"})

    def setUp(self):
        self.session_patch = patch("server._session_user", return_value={"username": "admin", "is_admin": 1, "active": 1})
        self.session_patch.start()

    def tearDown(self):
        self.session_patch.stop()

    def test_diagnostic_summary_structure(self):
        summary = get_diagnostic_summary_dict()
        self.assertIsInstance(summary, dict)
        self.assertEqual(summary["status"], "healthy")
        self.assertIn("system", summary)
        self.assertIn("concurrency", summary)
        self.assertIn("applicants", summary)
        self.assertIn("proxy_pool", summary)
        self.assertIn("saved_pages", summary)
        self.assertIn("websocket", summary)

        # Check subfields
        self.assertIn("platform", summary["system"])
        self.assertIn("python_version", summary["system"])
        self.assertIn("max_concurrency", summary["concurrency"])
        self.assertIn("total_count", summary["applicants"])

    def test_diagnostic_summary_endpoint(self):
        res = self.client.get("/diagnostic/summary")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("uptime_seconds", data)

    def test_diagnostic_export_zip(self):
        res = self.client.get("/diagnostic/export-zip?include_saved_pages=true&max_saved_files=10")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "application/zip")
        self.assertIn("mahanbot-diagnostic-", res.headers.get("content-disposition", ""))

        # Verify ZIP archive contents in-memory
        zip_bytes = io.BytesIO(res.content)
        with zipfile.ZipFile(zip_bytes, "r") as zf:
            file_names = zf.namelist()
            self.assertIn("diagnostic_summary.json", file_names)
            self.assertIn("config/settings_snapshot.json", file_names)
            self.assertIn("database/applicants_snapshot.json", file_names)
            self.assertIn("events/event_history.json", file_names)
            self.assertIn("logs/readable_events.log", file_names)

            # Validate summary JSON
            summary_content = json.loads(zf.read("diagnostic_summary.json").decode("utf-8"))
            self.assertEqual(summary_content["status"], "healthy")

            # Validate sanitized config (no secrets leaked)
            cfg_content = json.loads(zf.read("config/settings_snapshot.json").decode("utf-8"))
            for secret_key in ("admin_password", "api_key", "secret", "password"):
                if secret_key in cfg_content:
                    self.assertEqual(cfg_content[secret_key], "********")

            # Validate sanitized applicants (masked NID)
            apps_content = json.loads(zf.read("database/applicants_snapshot.json").decode("utf-8"))
            for app_record in apps_content:
                nid = app_record.get("national_id", "")
                if nid and len(nid) > 4:
                    self.assertIn("*", nid)

    def test_diagnostic_broadcast_test_event_endpoint(self):
        payload = {
            "type": "favorite_found",
            "bank": "بانک صادرات ایران",
            "detail": "سهمیه غیرحضوری آماده ثبت شد",
        }
        res = self.client.post("/diagnostic/broadcast-test-event", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "broadcasted")
        self.assertEqual(data["event"]["type"], "favorite_found")
        self.assertEqual(data["event"]["bank"], "بانک صادرات ایران")

    def test_emit_structured_event_dispatches_ui_events(self):
        received_events = []

        # Temporarily hook into emit_event to inspect dispatched events
        orig_emit = EVENT_BROADCASTER.emit_event

        def mock_emit(ev):
            received_events.append(ev)
            orig_emit(ev)

        EVENT_BROADCASTER.emit_event = mock_emit
        try:
            # 1. BANK_OPTION_FOUND -> favorite_found
            emit_structured_event(
                "0012345678",
                "BANK_OPTION_FOUND",
                step="STEP_3_BANK_SELECT",
                reason="bank=بانک صادرات",
                result="بانک صادرات ایران",
            )
            favorite_ev = next((e for e in received_events if e.get("type") == "favorite_found"), None)
            self.assertIsNotNone(favorite_ev)
            self.assertIn("صادرات", favorite_ev.get("bank", ""))

            # 2. FINAL_SUBMIT_CONFIRMED -> registration_confirmed
            received_events.clear()
            emit_structured_event(
                "0012345678",
                "FINAL_SUBMIT_CONFIRMED",
                step="DONE",
                reason="bank=بانک صادرات",
                result="tracking=998877",
            )
            reg_ev = next((e for e in received_events if e.get("type") == "registration_confirmed"), None)
            self.assertIsNotNone(reg_ev)
            self.assertIn("tracking=998877", reg_ev.get("tracking_code", ""))

            # 3. OTP_REQUIRED -> otp_required
            received_events.clear()
            emit_structured_event(
                "0012345678",
                "OTP_REQUIRED",
                step="STEP_2_OTP",
                reason="waiting_user",
            )
            otp_ev = next((e for e in received_events if e.get("type") == "otp_required"), None)
            self.assertIsNotNone(otp_ev)

            # 4. BANK_CAPACITY_FULL -> capacity_full
            received_events.clear()
            emit_structured_event(
                "0012345678",
                "BANK_CAPACITY_FULL",
                step="STEP_3_BANK_SELECT",
                reason="ظرفیت تکمیل است",
            )
            cap_ev = next((e for e in received_events if e.get("type") == "capacity_full"), None)
            self.assertIsNotNone(cap_ev)
            self.assertIn("تکمیل است", cap_ev.get("detail", ""))

        finally:
            EVENT_BROADCASTER.emit_event = orig_emit


if __name__ == "__main__":
    unittest.main()
