from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple


DEFAULT_EVENT_TTL_SECONDS = 15 * 60
DEFAULT_DEVICE_ONLINE_SECONDS = 90
DEFAULT_MAX_EVENTS = 200


def _clean_token(value: object, *, max_length: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("value is required")
    if len(text) > max_length:
        raise ValueError(f"value exceeds {max_length} characters")
    return text


def public_event_id(device_id: str, message_id: str) -> str:
    raw = f"{device_id}\x00{message_id}".encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True)
class SmsArrivalSignal:
    """Privacy-preserving signal that an SMS arrived.

    This object intentionally has no message-body or OTP field. The Android
    bridge only tells MahanBot that a new message exists; the user enters the
    verification code directly in the browser.
    """

    device_id: str
    message_id: str
    received_at: float
    source: str = "android"
    sender_hint: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        object.__setattr__(self, "device_id", _clean_token(self.device_id, max_length=128))
        object.__setattr__(self, "message_id", _clean_token(self.message_id, max_length=256))
        object.__setattr__(self, "source", _clean_token(self.source, max_length=32))

        received_at = float(self.received_at)
        created_at = float(self.created_at)
        if received_at <= 0 or created_at <= 0:
            raise ValueError("timestamps must be positive")
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "created_at", created_at)

        hint = str(self.sender_hint or "").strip()
        if len(hint) > 32:
            hint = hint[:32]
        object.__setattr__(self, "sender_hint", hint or None)

    @property
    def dedupe_key(self) -> str:
        return f"{self.device_id}\x00{self.message_id}"

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "event_id": public_event_id(self.device_id, self.message_id),
            "device_id": self.device_id,
            "received_at": self.received_at,
            "source": self.source,
            "sender_hint": self.sender_hint,
            "created_at": self.created_at,
        }


class SmsNotifyRegistry:
    def __init__(
        self,
        *,
        max_events: int = DEFAULT_MAX_EVENTS,
        event_ttl_seconds: int = DEFAULT_EVENT_TTL_SECONDS,
        device_online_seconds: int = DEFAULT_DEVICE_ONLINE_SECONDS,
    ) -> None:
        self.max_events = max(10, int(max_events))
        self.event_ttl_seconds = max(60, int(event_ttl_seconds))
        self.device_online_seconds = max(15, int(device_online_seconds))
        self._events: Deque[SmsArrivalSignal] = deque(maxlen=self.max_events)
        self._seen: Dict[str, float] = {}
        self._heartbeats: Dict[str, float] = {}
        self._lock = threading.RLock()

    def _prune_locked(self, now: float) -> None:
        event_cutoff = now - self.event_ttl_seconds
        while self._events and self._events[0].created_at < event_cutoff:
            expired = self._events.popleft()
            self._seen.pop(expired.dedupe_key, None)

        stale_keys = [key for key, ts in self._seen.items() if ts < event_cutoff]
        for key in stale_keys:
            self._seen.pop(key, None)

        device_cutoff = now - max(self.device_online_seconds * 10, self.event_ttl_seconds)
        stale_devices = [device_id for device_id, ts in self._heartbeats.items() if ts < device_cutoff]
        for device_id in stale_devices:
            self._heartbeats.pop(device_id, None)

    def add(self, signal: SmsArrivalSignal) -> Tuple[bool, Dict[str, Any]]:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            if signal.dedupe_key in self._seen:
                return False, signal.to_public_dict()
            self._seen[signal.dedupe_key] = signal.created_at
            self._heartbeats[signal.device_id] = now
            self._events.append(signal)
            return True, signal.to_public_dict()

    def heartbeat(self, device_id: str, *, at: Optional[float] = None) -> Dict[str, Any]:
        normalized = _clean_token(device_id, max_length=128)
        heartbeat_at = float(at if at is not None else time.time())
        if heartbeat_at <= 0:
            raise ValueError("heartbeat timestamp must be positive")
        with self._lock:
            self._heartbeats[normalized] = heartbeat_at
            self._prune_locked(time.time())
        return {"device_id": normalized, "last_seen_at": heartbeat_at, "online": True}

    def latest(self, *, since: float = 0.0, limit: int = 20) -> List[Dict[str, Any]]:
        since = max(0.0, float(since))
        limit = max(1, min(int(limit), 100))
        with self._lock:
            self._prune_locked(time.time())
            selected = [event for event in self._events if event.created_at > since]
            return [event.to_public_dict() for event in selected[-limit:]]

    def devices(self) -> List[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            rows = []
            for device_id, last_seen_at in sorted(self._heartbeats.items()):
                rows.append(
                    {
                        "device_id": device_id,
                        "last_seen_at": last_seen_at,
                        "online": (now - last_seen_at) <= self.device_online_seconds,
                    }
                )
            return rows

    def reset_for_tests(self) -> None:
        with self._lock:
            self._events.clear()
            self._seen.clear()
            self._heartbeats.clear()


SMS_NOTIFY_REGISTRY = SmsNotifyRegistry()
