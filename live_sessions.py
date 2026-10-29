"""Thread-owned browser sessions. HTTP handlers send commands; they never use Playwright."""
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class BrowserSessionOwner:
    applicant_id: str
    cancellation_token: threading.Event
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    thread_id: int = field(default_factory=threading.get_ident)
    context: object = None
    page: object = None
    current_state: str = "STARTING"
    last_valid_checkpoint: dict = field(default_factory=dict)
    last_successful_transition: float = 0.0
    validated_at: float = 0.0
    bank_page_active: bool = False
    paused: bool = False
    alive: bool = True
    commands: queue.Queue = field(default_factory=queue.Queue)
    operation_lock: threading.RLock = field(default_factory=threading.RLock)

    def assert_owner(self):
        if threading.get_ident() != self.thread_id:
            raise RuntimeError("Playwright operation outside browser owner thread")

    def bind(self, context, page):
        self.assert_owner()
        self.context, self.page = context, page

    def summary(self):
        # Do not probe Playwright from the HTTP/event-loop thread.
        return {"session_id": self.session_id, "state": self.current_state,
                "live": self.alive and not self.cancellation_token.is_set(),
                "bank_page_active": self.bank_page_active, "paused": self.paused,
                "validated_at": self.validated_at,
                "validated": self.validated_at > 0 and time.time() - self.validated_at < 10}


class LiveSessionRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}

    def acquire(self, nid, stop_event):
        with self._lock:
            if str(nid) in self._sessions:
                raise RuntimeError("Applicant already has a browser owner")
            owner = BrowserSessionOwner(str(nid), stop_event)
            self._sessions[str(nid)] = owner
            return owner

    def get(self, nid):
        with self._lock:
            return self._sessions.get(str(nid))

    def enqueue_command(self, nid, command):
        with self._lock:
            owner = self._sessions.get(str(nid))
            if owner and owner.alive:
                owner.commands.put(command)
                if command == "pause":
                    owner.paused = True
                elif command == "resume":
                    owner.paused = False
                return True
            return False

    def release(self, owner):
        owner.assert_owner()
        with self._lock:
            owner.alive = False
            owner.bank_page_active = False
            owner.validated_at = 0
            owner.page = owner.context = None
            if self._sessions.get(owner.applicant_id) is owner:
                self._sessions.pop(owner.applicant_id)

    def reset(self):
        with self._lock:
            for owner in list(self._sessions.values()):
                owner.alive = False
                owner.bank_page_active = False
                owner.validated_at = 0
                owner.page = owner.context = None
            self._sessions.clear()


LIVE_SESSIONS = LiveSessionRegistry()
