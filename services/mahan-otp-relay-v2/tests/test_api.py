from __future__ import annotations

import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

# Importing the production ASGI module is intentionally fail-closed, so tests provide
# non-production bootstrap values before import and use isolated settings per case below.
os.environ.update({
    "OTP_ENVIRONMENT": "test", "OTP_DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    "OTP_BOT_TOKEN": "bootstrap-bot", "OTP_ADMIN_TOKEN": "bootstrap-admin",
    "OTP_TOKEN_PEPPER": "bootstrap-pepper", "OTP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "OTP_FORCE_HTTPS": "false",
})

from app.config import Settings
from app.main import create_app


def settings(tmp_path):
    return Settings(environment="test", database_url=f"sqlite+aiosqlite:///{tmp_path / 'otp.db'}",
                    bot_token="bot-secret", admin_token="admin-secret", token_pepper="pepper-secret",
                    encryption_key=Fernet.generate_key().decode(), force_https=False)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_pair_session_delivery_is_single_use(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        pairing = client.post("/api/v2/pairing/create", headers=auth("bot-secret"),
                              json={"created_by": "test", "digits": 6}).json()
        claim = client.post("/api/v2/pairing/claim", json={
            "code": pairing["code"], "app_flavor": "consent", "consent": True,
            "consent_version": "1", "app_version": "1.0.0",
        }).json()
        created = client.post("/api/v2/bot/sessions", headers=auth("bot-secret"), json={
            "job_id": "job-1", "national_id": "0000000000", "purpose": "register",
            "expires_in_seconds": 180,
        }).json()
        message = {"session_id": created["session_id"], "message_id": "message-0001",
                   "national_id": "0000000000", "otp": "123456",
                   "received_at": datetime.now(timezone.utc).isoformat(), "sim_slot": 0,
                   "source": "android-consent", "consent_version": "1"}
        device_headers = auth(claim["device_token"]) | {"Idempotency-Key": "message-0001"}
        assert client.post("/api/v2/device/otp", headers=device_headers, json=message).status_code == 200
        delivered = client.get(f"/api/v2/bot/sessions/{created['session_id']}/wait",
                               headers=auth("bot-secret"), params={"job_id": "job-1", "timeout_seconds": 1})
        assert delivered.json()["otp"] == "123456"
        assert client.post(f"/api/v2/bot/sessions/{created['session_id']}/consume",
                           headers=auth("bot-secret"), json={"job_id": "job-1"}).status_code == 200
        again = client.get(f"/api/v2/bot/sessions/{created['session_id']}/wait",
                           headers=auth("bot-secret"), params={"job_id": "job-1", "timeout_seconds": 1})
        assert again.status_code in {202, 410}


def test_rejects_raw_sms_fields_and_missing_auth(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.post("/api/v2/bot/sessions", json={}).status_code == 401
        response = client.post("/api/v2/device/otp", headers={"Idempotency-Key": "message-0001"}, json={
            "session_id": "x", "message_id": "message-0001", "national_id": "0000000000",
            "otp": "1234", "received_at": datetime.now(timezone.utc).isoformat(),
            "source": "manual", "consent_version": "1", "sms_body": "forbidden",
        })
        assert response.status_code in {401, 422}


def test_security_headers_and_closed_cors(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.get("/status")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "access-control-allow-origin" not in response.headers
