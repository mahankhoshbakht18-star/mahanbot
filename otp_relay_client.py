from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests


class OtpRelayError(RuntimeError):
    pass


@dataclass(frozen=True)
class OtpRelayConfig:
    base_url: str = "https://otp.mahanvip.ir"
    bot_token: str = ""
    connect_timeout: float = 10.0
    read_timeout: float = 130.0

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and not self.bot_token.startswith("CHANGE_ME"))


class OtpRelayClient:
    """The only MahanBot boundary allowed to call OTP Relay endpoints."""

    def __init__(self, config: OtpRelayConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls) -> "OtpRelayClient":
        return cls(OtpRelayConfig(
            base_url=os.getenv("MAHAN_OTP_RELAY_URL", "https://otp.mahanvip.ir").rstrip("/"),
            bot_token=os.getenv("MAHAN_OTP_BOT_TOKEN", "").strip(),
            connect_timeout=float(os.getenv("MAHAN_OTP_CONNECT_TIMEOUT", "10")),
            read_timeout=float(os.getenv("MAHAN_OTP_READ_TIMEOUT", "130")),
        ))

    @property
    def configured(self) -> bool:
        return self.config.configured

    def _request(self, method: str, path: str, *, read_timeout: float | None = None, **kwargs) -> dict[str, Any]:
        if not self.configured:
            raise OtpRelayError("OTP Relay bot authentication is not configured")
        headers = {"Authorization": f"Bearer {self.config.bot_token}", "Accept": "application/json"}
        headers.update(kwargs.pop("headers", {}))
        response = self.session.request(method, self.config.base_url + path, headers=headers,
                                        timeout=(self.config.connect_timeout, read_timeout or self.config.read_timeout),
                                        **kwargs)
        if response.status_code == 202:
            return response.json()
        if not response.ok:
            raise OtpRelayError(f"relay request failed ({response.status_code})")
        return response.json()

    def health(self) -> dict[str, Any]:
        response = self.session.get(self.config.base_url + "/status", timeout=self.config.connect_timeout)
        response.raise_for_status()
        return response.json()

    def create_session(self, *, job_id: str, national_id: str, purpose: str,
                       expires_in_seconds: int = 180, device_id: str | None = None) -> dict[str, Any]:
        return self._request("POST", "/api/v2/bot/sessions", json={
            "job_id": job_id, "national_id": national_id, "purpose": purpose,
            "expires_in_seconds": expires_in_seconds, "device_id": device_id,
        })

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v2/bot/sessions/{session_id}")

    def wait_for_otp(self, session_id: str, job_id: str, timeout: int = 120) -> dict[str, Any] | None:
        payload = self._request("GET", f"/api/v2/bot/sessions/{session_id}/wait",
                                params={"job_id": job_id, "timeout_seconds": timeout},
                                read_timeout=min(timeout + 10, self.config.read_timeout))
        return payload if payload.get("ok") else None

    def consume(self, session_id: str, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v2/bot/sessions/{session_id}/consume", json={"job_id": job_id})

    def cancel(self, session_id: str, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v2/bot/sessions/{session_id}/cancel", json={"job_id": job_id})

    def extend(self, session_id: str, job_id: str, seconds: int) -> dict[str, Any]:
        return self._request("POST", f"/api/v2/bot/sessions/{session_id}/extend",
                             json={"job_id": job_id, "seconds": seconds})

    def create_pairing_code(self, created_by: str = "dashboard", digits: int = 6) -> dict[str, Any]:
        return self._request("POST", "/api/v2/pairing/create", json={"created_by": created_by, "digits": digits})

    def revoke_device(self, device_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v2/admin/devices/{device_id}/revoke")
