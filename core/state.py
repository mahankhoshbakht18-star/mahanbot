"""
core/state.py — Global mutable state shared across all route modules.

Every module that needs MAIN_LOOP, JOB_QUEUE, etc. imports from here
so there is exactly ONE source of truth.
"""
import asyncio
import os
from typing import Any, Dict, Optional

# ── Event-loop handle (set once at startup) ──────────────────────────
MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None

# ── Captcha service (lazy singleton) ─────────────────────────────────
CAPTCHA_SERVICE = None  # will be CaptchaService instance

# ── Job queue (created at startup) ───────────────────────────────────
JOB_QUEUE = None  # will be JobQueue instance

# ── OTP long-poll event store ────────────────────────────────────────
OTP_EVENTS: Dict[str, asyncio.Event] = {}
OTP_EVENT_LOCK = asyncio.Lock()

# ── Startup guard ─────────────────────────────────────────────────────
STARTUP_DONE = False
STARTUP_LOCK = asyncio.Lock()

# ── Auth session store ───────────────────────────────────────────────
AUTH_SESSIONS: Dict[str, Dict[str, Any]] = {}
AUTH_COOKIE = "mahan_session"

# ── Directory constants ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
