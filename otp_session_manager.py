from __future__ import annotations

import threading
from dataclasses import dataclass

from otp_relay_client import OtpRelayClient


@dataclass
class ManagedOtpSession:
    job_id: str
    session_id: str
    national_id_hash_hint: str


class OtpSessionManager:
    def __init__(self, client: OtpRelayClient):
        self.client = client
        self._guard = threading.RLock()
        self._sessions: dict[str, ManagedOtpSession] = {}
        self._locks: dict[str, threading.Lock] = {}

    def create(self, *, job_id: str, national_id: str, purpose: str,
               expires_in_seconds: int = 180) -> ManagedOtpSession:
        with self._guard:
            if job_id in self._sessions:
                return self._sessions[job_id]
            payload = self.client.create_session(job_id=job_id, national_id=national_id,
                                                 purpose=purpose, expires_in_seconds=expires_in_seconds)
            managed = ManagedOtpSession(job_id, payload["session_id"], "******" + national_id[-4:])
            self._sessions[job_id] = managed
            self._locks.setdefault(job_id, threading.Lock())
            return managed

    def wait(self, job_id: str, timeout: int, stop_event=None) -> str | None:
        managed = self._sessions.get(job_id)
        if managed is None:
            raise RuntimeError("OTP session must be created before wait")
        lock = self._locks[job_id]
        with lock:
            if stop_event is not None and stop_event.is_set():
                self.cancel(job_id)
                return None
            result = self.client.wait_for_otp(managed.session_id, job_id, timeout)
            return str(result["otp"]) if result else None

    def consume(self, job_id: str) -> None:
        managed = self._sessions.pop(job_id, None)
        if managed:
            self.client.consume(managed.session_id, job_id)

    def cancel(self, job_id: str) -> None:
        managed = self._sessions.pop(job_id, None)
        if managed:
            self.client.cancel(managed.session_id, job_id)
