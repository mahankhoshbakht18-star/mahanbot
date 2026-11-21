"""In-process wake-up for OTP waiters.

Every OTP write goes through DBHandler.set_otp, which calls notify(nid). The
bot thread waiting for that applicant wakes immediately instead of finding the
code on its next poll.
"""
import threading

_lock = threading.Lock()
_events = {}


def _event(nid) -> threading.Event:
    key = str(nid or "").strip()
    with _lock:
        event = _events.get(key)
        if event is None:
            event = _events[key] = threading.Event()
        return event


def notify(nid) -> None:
    _event(nid).set()


def wait(nid, timeout: float) -> bool:
    """Block up to timeout seconds; True when a new OTP was written for nid."""
    event = _event(nid)
    fired = event.wait(max(0.0, float(timeout)))
    if fired:
        event.clear()
    return fired
