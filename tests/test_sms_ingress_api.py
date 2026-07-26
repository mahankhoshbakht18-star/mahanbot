import json
import time

import unittest

try:
    import aiosqlite  # noqa: F401
    import playwright  # noqa: F401
except ImportError as exc:
    raise unittest.SkipTest(f"full SMS ingress dependencies are not installed: {exc}")

import database
from database import DBHandler
from fastapi.testclient import TestClient
from sms_ingress import app


def _create_applicant(nid: str) -> None:
    DBHandler.init_db()
    with DBHandler._connect() as conn:
        conn.execute(
            "INSERT INTO applicants (full_name, national_id, data) VALUES (?, ?, ?)",
            ("API Test", nid, json.dumps({"national_id": nid})),
        )
        conn.commit()


def test_consented_sms_api_matches_waiting_applicant(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("MAHANBOT_SMS_DEVICE_KEY", "test-device-key")
    nid = "0012345678"
    _create_applicant(nid)
    waiting_since = time.time() - 1
    assert DBHandler.mark_otp_waiting(nid, waiting_since)

    client = TestClient(app)
    headers = {
        "X-DEVICE-KEY": "test-device-key",
        "X-DEVICE-ID": "android-test",
    }
    status_response = client.get(
        "/api/v1/sms/otp/status",
        params={"national_id": nid},
        headers=headers,
    )
    assert status_response.status_code == 200
    assert status_response.json()["applicant_found"] is True
    assert status_response.json()["waiting"] is True

    payload = {
        "device_id": "android-test",
        "message_id": "message-1234",
        "national_id": nid,
        "otp": "123456",
        "received_at": time.time(),
        "source": "android",
        "consent_version": "1",
    }
    response = client.post("/api/v1/sms/otp", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert DBHandler.get_otp_record(nid)["otp"] == "123456"

    duplicate = client.post("/api/v1/sms/otp", json=payload, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True


def test_sms_api_rejects_wrong_key_and_non_waiting_applicant(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "reject.db"))
    monkeypatch.setenv("MAHANBOT_SMS_DEVICE_KEY", "correct-key")
    nid = "0098765432"
    _create_applicant(nid)
    client = TestClient(app)
    payload = {
        "device_id": "android-test",
        "message_id": "message-5678",
        "national_id": nid,
        "otp": "654321",
        "received_at": time.time(),
        "source": "android",
        "consent_version": "1",
    }
    wrong_key = client.post(
        "/api/v1/sms/otp",
        json=payload,
        headers={"X-DEVICE-KEY": "wrong-key", "X-DEVICE-ID": "android-test"},
    )
    assert wrong_key.status_code == 401

    not_waiting = client.post(
        "/api/v1/sms/otp",
        json=payload,
        headers={"X-DEVICE-KEY": "correct-key", "X-DEVICE-ID": "android-test"},
    )
    assert not_waiting.status_code == 409
