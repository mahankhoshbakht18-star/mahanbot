from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional


class JobState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


@dataclass
class JobRecord:
    job_id: str
    bot_name: str
    nid: str
    applicant_name: Optional[str]
    state: JobState
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    last_error: Optional[str] = None
    cancel_requested: bool = False
    stop_event: threading.Event = field(default_factory=threading.Event)
    handler: Optional[Callable[[threading.Event], None]] = None

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "job_id": self.job_id,
            "bot_name": self.bot_name,
            "nid": self.nid,
            "applicant_name": self.applicant_name,
            "state": self.state.value,
            "created_at": self._format_ts(self.created_at),
            "started_at": self._format_ts(self.started_at),
            "finished_at": self._format_ts(self.finished_at),
            "last_error": self.last_error,
            "cancel_requested": self.cancel_requested,
        }

    @staticmethod
    def _format_ts(ts: Optional[float]) -> Optional[str]:
        if ts is None:
            return None
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class JobRunner:
    def __init__(
        self,
        max_workers: int,
        on_state_change: Optional[Callable[[JobRecord], None]] = None,
        max_history: int = 500,
    ) -> None:
        self.max_workers = max_workers
        self.on_state_change = on_state_change
        self.max_history = max_history
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._workers: List[threading.Thread] = []

    def start(self) -> None:
        for idx in range(self.max_workers):
            worker = threading.Thread(target=self._worker, daemon=True, name=f"job-worker-{idx}")
            worker.start()
            self._workers.append(worker)

    def enqueue(
        self,
        bot_name: str,
        nid: str,
        applicant_name: Optional[str],
        handler: Callable[[threading.Event], None],
        job_id: Optional[str] = None,
    ) -> JobRecord:
        with self._lock:
            if self._has_active_duplicate(bot_name, nid):
                raise ValueError("duplicate_job")
            final_job_id = job_id or uuid.uuid4().hex
            job = JobRecord(
                job_id=final_job_id,
                bot_name=bot_name,
                nid=nid,
                applicant_name=applicant_name,
                state=JobState.QUEUED,
                created_at=time.time(),
                handler=handler,
            )
            self._jobs[final_job_id] = job
            self._queue.put(final_job_id)
        self._emit_state(job)
        return job

    def cancel_job(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.state in {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELED}:
                return job
            job.cancel_requested = True
            job.stop_event.set()
            if job.state == JobState.QUEUED:
                job.state = JobState.CANCELED
                job.finished_at = time.time()
        if job:
            self._emit_state(job)
        return job

    def cancel_by_nid(self, nid: str) -> List[JobRecord]:
        canceled: List[JobRecord] = []
        with self._lock:
            job_ids = [job_id for job_id, job in self._jobs.items() if job.nid == nid]
        for job_id in job_ids:
            job = self.cancel_job(job_id)
            if job:
                canceled.append(job)
        return canceled

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> List[JobRecord]:
        with self._lock:
            return list(self._jobs.values())

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self.get_job(job_id)
            if not job:
                self._queue.task_done()
                continue
            if job.state == JobState.CANCELED:
                self._queue.task_done()
                continue
            with self._lock:
                job.state = JobState.RUNNING
                job.started_at = time.time()
            self._emit_state(job)
            try:
                if job.handler:
                    job.handler(job.stop_event)
                if job.stop_event.is_set():
                    with self._lock:
                        job.state = JobState.CANCELED
                else:
                    with self._lock:
                        job.state = JobState.SUCCEEDED
            except Exception as exc:
                with self._lock:
                    job.state = JobState.FAILED
                    job.last_error = str(exc)
            finally:
                with self._lock:
                    job.finished_at = time.time()
                    self._prune_history_locked()
                self._emit_state(job)
                self._queue.task_done()

    def _emit_state(self, job: JobRecord) -> None:
        if self.on_state_change:
            self.on_state_change(job)

    def _has_active_duplicate(self, bot_name: str, nid: str) -> bool:
        for job in self._jobs.values():
            if job.bot_name == bot_name and job.nid == nid and job.state in {JobState.QUEUED, JobState.RUNNING}:
                return True
        return False

    def _prune_history_locked(self) -> None:
        if len(self._jobs) <= self.max_history:
            return
        sorted_jobs = sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)
        keep_ids = {job.job_id for job in sorted_jobs[: self.max_history]}
        self._jobs = {job_id: job for job_id, job in self._jobs.items() if job_id in keep_ids}
