"""
core/job_queue.py — Job & JobQueue classes.

Moved out of server.py to keep the entry-point thin and testable.
"""
import asyncio
import json
import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

from database import DBHandler
from event_logger import create_job_id, job_status_event, log_event, build_log_callback
from browser_launcher import merge_browser_profiles
from core.helpers import load_browser_profile_settings
from core.state import CAPTCHA_SERVICE
from bot_register import RegistrationBot
from bot_recovery import RecoveryBot
from bot_select import BankSelectionBot
from bot_status import StatusBot


# ── Single job ────────────────────────────────────────────────────────
class Job:
    def __init__(self, bot_name: str, nid: str, payload: Dict[str, Any]):
        self.id = create_job_id()
        self.bot_name = bot_name
        self.nid = nid
        self.payload = payload
        self.status = "queued"
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.stop_event = threading.Event()
        self.cancel_requested = False
        self.error: Optional[Any] = None
        self.runner_thread: Optional[threading.Thread] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "bot_name": self.bot_name,
            "nid": self.nid,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "payload": self.payload,
            "error": self.error,
        }


# ── Queue ─────────────────────────────────────────────────────────────
class JobQueue:
    ACTIVE_STATUSES = {"queued", "running", "cancelling"}

    def __init__(self, max_concurrency: int):
        self._max_concurrency = max_concurrency
        self._jobs: Dict[str, Job] = {}
        self._queue: Deque[str] = deque()
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._workers: list[threading.Thread] = []
        self._shutting_down = False

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    @property
    def workers(self) -> list[threading.Thread]:
        return self._workers

    def start(self) -> None:
        for index in range(self._max_concurrency):
            t = threading.Thread(target=self._worker_loop, args=(index,), daemon=True)
            t.start()
            self._workers.append(t)

    def set_max_concurrency(self, new_limit: int) -> int:
        new_limit = max(1, min(32, int(new_limit)))
        with self._condition:
            old_limit = self._max_concurrency
            self._max_concurrency = new_limit
            if new_limit > old_limit:
                for index in range(old_limit, new_limit):
                    t = threading.Thread(target=self._worker_loop, args=(index,), daemon=True)
                    t.start()
                    self._workers.append(t)
            self._condition.notify_all()
        return self._max_concurrency

    def stop(self, timeout: float = 5.0) -> None:
        """Wake idle workers and let daemon threads exit on shutdown."""
        with self._condition:
            self._shutting_down = True
            self._condition.notify_all()
        deadline = time.time() + timeout
        for worker in self._workers:
            remaining = max(0.0, deadline - time.time())
            worker.join(timeout=remaining)

    def find_active(self, bot_name: str, nid: str) -> Optional[Job]:
        with self._lock:
            for job in self._jobs.values():
                if job.nid == nid and job.status in self.ACTIVE_STATUSES:
                    return job
        return None

    def enqueue(self, bot_name: str, nid: str, payload: Dict[str, Any]) -> Optional[Job]:
        with self._condition:
            if any(
                existing.nid == nid and existing.status in self.ACTIVE_STATUSES
                for existing in self._jobs.values()
            ):
                return None
            job = Job(bot_name, nid, payload)
            self._jobs[job.id] = job
            self._queue.append(job.id)
            self._condition.notify()
        job_status_event(nid, job.id, "queued")
        return job

    def cancel(self, job_id: str) -> Optional[Job]:
        with self._condition:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = time.time()
                try:
                    self._queue.remove(job_id)
                except ValueError:
                    pass
                job.stop_event.set()
                job_status_event(job.nid, job.id, "cancelled")
                return job
            if job.status in {"running", "cancelling"}:
                job.cancel_requested = True
                job.stop_event.set()
                if job.status != "cancelling":
                    job.status = "cancelling"
                    job_status_event(job.nid, job.id, "cancelling")
                return job
            return job

    def cancel_by_nid(self, nid: str) -> Optional[Job]:
        job_id = None
        with self._lock:
            for job in self._jobs.values():
                if job.nid == nid and job.status in self.ACTIVE_STATUSES:
                    job_id = job.id
                    break
        if not job_id:
            return None
        return self.cancel(job_id)

    def list_jobs(self) -> list[Dict[str, Any]]:
        with self._lock:
            return [j.to_dict() for j in self._jobs.values()]

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def stop_all(self, bot_name: Optional[str] = None) -> list[str]:
        target_ids = []
        with self._lock:
            for job in self._jobs.values():
                if job.status in self.ACTIVE_STATUSES:
                    if bot_name is None or job.bot_name.lower() == bot_name.lower():
                        target_ids.append(job.id)
        cancelled = []
        for jid in target_ids:
            res = self.cancel(jid)
            if res:
                cancelled.append(jid)
        return cancelled

    # ── Worker internals ──────────────────────────────────────────────

    def _worker_loop(self, worker_id: int) -> None:
        while True:
            with self._condition:
                while not self._queue:
                    if self._shutting_down:
                        return
                    self._condition.wait()
                job_id = self._queue.popleft()
                job = self._jobs.get(job_id)
                if not job or job.status != "queued":
                    continue
                job.status = "running"
                job.started_at = time.time()

            job_status_event(job.nid, job.id, "running")
            log_event(job.nid, job.id, f"Job started ({job.bot_name})", "info")

            try:
                self._run_job_isolated(job)
                if job.cancel_requested:
                    job.status = "cancelled"
                elif DBHandler.get_runtime(job.nid).get("status") == "ERROR":
                    job.status = "failed"
                    job.error = DBHandler.get_runtime(job.nid).get("last_error") or "bot_error"
                else:
                    job.status = "stopped"
            except Exception as e:
                job.status = "failed"
                job.error = str(e)
                log_event(job.nid, job.id, f"خطا: {e}", "error")
            finally:
                job.finished_at = time.time()
                runtime_status = "STOPPED" if job.status in {"stopped", "cancelled"} else "ERROR"
                current_runtime = DBHandler.get_runtime(job.nid)
                if current_runtime.get("status") != "SUCCESS":
                    DBHandler.update_runtime(
                        job.nid,
                        status=runtime_status,
                        browser_status="CLOSED",
                        current_step="STOPPED" if runtime_status == "STOPPED" else current_runtime.get("current_step") or "ERROR",
                        last_error=str(job.error or current_runtime.get("last_error") or "")[:300],
                    )
                job_status_event(job.nid, job.id, job.status)
                with self._lock:
                    finished = sorted(
                        (item for item in self._jobs.values() if item.finished_at),
                        key=lambda item: item.finished_at or 0,
                        reverse=True,
                    )
                    for stale in finished[200:]:
                        self._jobs.pop(stale.id, None)

    def _run_job_isolated(self, job: Job) -> None:
        """Run every bot in an isolated thread with a dedicated asyncio loop."""
        result: Dict[str, Any] = {}
        err: Dict[str, BaseException] = {}

        def _runner():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result["ok"] = True
                self._run_job(job)
            except BaseException as exc:
                err["exc"] = exc
            finally:
                try:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                finally:
                    asyncio.set_event_loop(None)
                    loop.close()

        runner = threading.Thread(target=_runner, name=f"job-{job.id}", daemon=True)
        job.runner_thread = runner
        runner.start()

        while runner.is_alive():
            if job.stop_event.is_set():
                runner.join(timeout=0.2)
            else:
                runner.join(timeout=0.5)

        if "exc" in err:
            raise err["exc"]

    def _run_job(self, job: Job) -> None:
        settings = DBHandler.get_config()
        applicant = DBHandler.get_applicant(job.nid)
        target_bank = ""
        loan_type = job.payload.get("loan_type")
        if applicant:
            try:
                applicant_data = json.loads(applicant["data"] or "{}")
                if not loan_type:
                    loan_type = applicant_data.get("loan_type")
                priorities = applicant_data.get("priority_banks") or applicant_data.get("banks") or []
                if priorities:
                    first = priorities[0]
                    target_bank = first.get("name", "") if isinstance(first, dict) else str(first)
            except Exception:
                pass
        DBHandler.reset_runtime(job.nid, run_id=job.id, target_bank=target_bank)
        base_profile = load_browser_profile_settings()
        override = job.payload.get("browser_profile_override")
        browser_profile = merge_browser_profiles(base_profile, override)
        browser_profile["timeout_ms"] = int(settings.get("default_timeout_ms", 5000))
        display_mode = str(settings.get("browser_display_mode") or "external").lower()
        browser_profile["headless"] = display_mode == "embedded" or bool(settings.get("browser_headless", settings.get("headless", False)))
        browser_profile["window_width"] = int(settings.get("browser_window_width", browser_profile.get("window_width", 540)))
        browser_profile["window_height"] = int(settings.get("browser_window_height", browser_profile.get("window_height", 680)))
        browser_profile["window_position"] = str(settings.get("browser_window_position", browser_profile.get("window_position", "top_right")))
        browser_profile["start_maximized"] = bool(settings.get("browser_start_maximized", browser_profile.get("start_maximized", False)))
        browser_profile["always_on_top"] = bool(settings.get("browser_always_on_top", browser_profile.get("always_on_top", False)))
        browser_profile["remember_position"] = bool(settings.get("browser_remember_position", browser_profile.get("remember_position", True)))
        browser_profile["viewport"] = {"width": browser_profile["window_width"], "height": browser_profile["window_height"]}
        browser_profile["anti_detection_enabled"] = bool(settings.get("anti_detection_enabled", True))

        if settings.get("use_proxy") and settings.get("proxy_list"):
            try:
                from proxy_manager import get_proxy_pool
                pool = get_proxy_pool()
                pool.load_from_text(str(settings.get("proxy_list") or ""))
                strat = str(settings.get("proxy_rotation_strategy") or "round_robin")
                assigned_proxy = pool.get_proxy(strategy=strat, applicant_id=job.nid)
                if assigned_proxy:
                    browser_profile["proxy"] = assigned_proxy
            except Exception:
                pass

        captcha_service = _get_captcha_service()
        log_callback = build_log_callback(job.id)

        if job.bot_name == "register":
            RegistrationBot(job.nid, settings, log_callback, captcha_service, browser_profile).run(
                job.stop_event, loan_type=loan_type
            )
        elif job.bot_name == "select":
            BankSelectionBot(job.nid, settings, log_callback, captcha_service, browser_profile).run(
                job.stop_event, loan_type or "rbtnNaghdi"
            )
        elif job.bot_name == "status":
            StatusBot(job.nid, settings, log_callback, captcha_service, browser_profile).run(job.stop_event)
        elif job.bot_name == "recovery":
            RecoveryBot(job.nid, settings, log_callback, captcha_service, browser_profile).run(job.stop_event)
        else:
            raise ValueError("Unsupported bot name")


# ── Lazy captcha-service accessor ────────────────────────────────────
def _get_captcha_service():
    from captcha_service import CaptchaService
    from core.state import CAPTCHA_SERVICE
    if CAPTCHA_SERVICE is None:
        return CaptchaService()
    return CAPTCHA_SERVICE


def get_max_concurrency() -> int:
    try:
        cfg = DBHandler.get_config()
        val = cfg.get("max_concurrency")
        if val is not None:
            return max(1, min(32, int(val)))
    except Exception:
        pass
    try:
        value = int(os.getenv("MAHANBOT_MAX_CONCURRENCY", "4"))
    except ValueError:
        value = 4
    return max(1, min(32, value))
