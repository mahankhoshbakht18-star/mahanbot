# core/queue_manager.py
"""
core/queue_manager.py — Persistent Priority Queue Manager for MAHANBOT Applicants

Features:
- Independent Queue Table with persistent positions and priorities
- Multi-priority support: HIGH (1), NORMAL (2), LOW (3)
- Atomic worker lease with automatic heartbeat expiry to prevent stale locks
- Reordering (drag & drop / move up/down)
- Pause / Resume individual applicant or all workers
- Real-time statistics aggregation for dashboard
"""
import json
import logging
import threading
import time
from typing import Optional, Dict, Any, List

from database import DBHandler
from core.helpers import _mask_identifier
from core.state_machine import BotState, STATE_TO_QUEUE_STATUS
from event_logger import EVENT_BROADCASTER, build_event

logger = logging.getLogger("mahanbot.queue_manager")

PRIORITY_WEIGHTS = {
    "HIGH": 1,
    "NORMAL": 2,
    "LOW": 3,
}

LEASE_DURATION_SECONDS = 90.0


class QueueManager:
    _lock = threading.RLock()

    @staticmethod
    def enroll(
        applicant_id: str,
        applicant_name: str = "",
        loan_type: str = "MARRIAGE",
        requested_bank: str = "all",
        city: str = "",
        priority: str = "NORMAL",
        initial_status: str = "WAITING",
        initial_bot_state: str = "QUEUED",
    ) -> bool:
        """Enroll an applicant directly into the queue."""
        nid = str(applicant_id).strip()
        p = str(priority).upper().strip()
        if p not in PRIORITY_WEIGHTS:
            p = "NORMAL"
        now = time.time()
        with QueueManager._lock:
            try:
                with DBHandler._connect() as conn:
                    exists = conn.execute("SELECT applicant_id FROM applicant_queue WHERE applicant_id = ?", (nid,)).fetchone()
                    if exists:
                        conn.execute(
                            "UPDATE applicant_queue SET queue_status = ?, bot_state = ?, priority = ?, updated_at = ? WHERE applicant_id = ?",
                            (initial_status, initial_bot_state, p, now, nid),
                        )
                    else:
                        row = conn.execute("SELECT COALESCE(MAX(queue_position), 0) FROM applicant_queue").fetchone()
                        max_pos = int(row[0]) if row else 0
                        q_id = f"q_{nid}_{int(now)}"
                        masked = _mask_identifier(nid)
                        conn.execute(
                            """INSERT INTO applicant_queue
                               (queue_id, applicant_id, applicant_display_name, masked_national_id, priority, queue_position,
                                queue_status, bot_state, current_stage, loan_type, requested_bank, selected_city,
                                created_at, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (q_id, nid, applicant_name or nid, masked, p, max_pos + 1,
                             initial_status, initial_bot_state, initial_bot_state, loan_type, requested_bank, city,
                             now, now),
                        )
                    conn.commit()
                QueueManager.emit_snapshot()
                return True
            except Exception as e:
                logger.error("Failed to enroll %s in queue: %s", nid, e)
                return False

    @staticmethod
    def sync_applicants():
        """
        Ensure every applicant in the `applicants` table exists in `applicant_queue`.
        Assigns default NORMAL priority and appends to end of queue if not present.
        """
        now = time.time()
        with QueueManager._lock:
            try:
                with DBHandler._connect() as conn:
                    # Get max queue position
                    row = conn.execute("SELECT COALESCE(MAX(queue_position), 0) FROM applicant_queue").fetchone()
                    max_pos = int(row[0]) if row else 0

                    apps = conn.execute("SELECT national_id, full_name, status, data FROM applicants").fetchall()
                    for app in apps:
                        nid = str(app[0]).strip()
                        name = str(app[1] or nid).strip()
                        st = str(app[2] or "Ready").strip()
                        raw_data = app[3]
                        d = {}
                        if raw_data:
                            try:
                                d = json.loads(raw_data)
                            except Exception:
                                d = {}

                        loan_type = d.get("loan_type", "marriage")
                        target_bank = d.get("bank_name") or d.get("preferred_bank") or d.get("primary_bank") or "-"
                        city = d.get("city") or "-"
                        mode = d.get("bank_mode") or d.get("mode") or "all"

                        # Check if already in queue
                        exists = conn.execute("SELECT applicant_id FROM applicant_queue WHERE applicant_id = ?", (nid,)).fetchone()
                        if not exists:
                            max_pos += 1
                            q_id = f"q_{nid}_{int(now)}"
                            masked_nid = _mask_identifier(nid)
                            init_q_status = "COMPLETED" if st.lower() in {"success", "completed", "registered"} else "WAITING"
                            init_bot_state = BotState.CONFIRMED.value if init_q_status == "COMPLETED" else BotState.QUEUED.value

                            conn.execute(
                                """INSERT INTO applicant_queue
                                   (queue_id, applicant_id, applicant_display_name, masked_national_id,
                                    loan_type, priority, queue_position, queue_status, bot_state,
                                    requested_bank, selected_city, preferred_mode, current_stage,
                                    created_at, updated_at, last_activity_at)
                                   VALUES (?, ?, ?, ?, ?, 'NORMAL', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (q_id, nid, name, masked_nid, loan_type, max_pos, init_q_status,
                                 init_bot_state, target_bank, city, mode, init_bot_state, now, now, now),
                            )
                    conn.commit()
            except Exception as e:
                logger.error("Failed to sync applicants to queue: %s", e)

    @staticmethod
    def get_queue(status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all queue items ordered by Priority (HIGH -> NORMAL -> LOW) and queue_position (FIFO).
        """
        QueueManager.sync_applicants()
        cols = [
            "queue_id", "applicant_id", "applicant_display_name", "masked_national_id",
            "loan_type", "priority", "queue_position", "queue_status", "bot_state",
            "assigned_worker", "worker_lease_until", "requested_bank", "selected_city",
            "preferred_mode", "current_stage", "retry_count", "last_error",
            "created_at", "started_at", "updated_at", "last_activity_at",
            "next_action_at", "checkpoint_reference"
        ]
        now = time.time()
        try:
            with DBHandler._connect() as conn:
                # First clean up expired worker leases
                conn.execute(
                    """UPDATE applicant_queue
                       SET queue_status = 'WAITING', assigned_worker = NULL, worker_lease_until = 0, updated_at = ?
                       WHERE queue_status = 'RUNNING' AND worker_lease_until > 0 AND worker_lease_until < ?""",
                    (now, now),
                )
                conn.commit()

                query = f"""
                    SELECT {','.join(cols)}
                    FROM applicant_queue
                    WHERE queue_status != 'CANCELLED'
                    ORDER BY
                        CASE priority WHEN 'HIGH' THEN 1 WHEN 'NORMAL' THEN 2 WHEN 'LOW' THEN 3 ELSE 4 END ASC,
                        queue_position ASC
                """
                rows = conn.execute(query).fetchall()
                results = []
                for idx, r in enumerate(rows):
                    item = dict(zip(cols, r))
                    item["rank"] = idx + 1
                    # Attach active runtime info
                    runtime = DBHandler.get_runtime(item["applicant_id"]) or {}
                    item["runtime_status"] = runtime.get("status") or "-"
                    item["runtime_step"] = runtime.get("current_step") or item["bot_state"]
                    item["otp_state"] = runtime.get("otp_state") or "-"
                    item["session_active"] = bool(runtime.get("session_id") or runtime.get("browser_status") == "OPEN")

                    if status_filter and item["queue_status"] != status_filter:
                        continue
                    results.append(item)
                return results
        except Exception as e:
            logger.error("Failed to fetch applicant_queue: %s", e)
            return []

    @staticmethod
    def get_stats() -> Dict[str, int]:
        """Aggregate summary counts for dashboard header cards."""
        stats = {
            "total": 0,
            "waiting": 0,
            "running": 0,
            "waiting_sms": 0,
            "step_3": 0,
            "waiting_bank": 0,
            "completed": 0,
            "failed": 0,
            "paused": 0,
        }
        try:
            with DBHandler._connect() as conn:
                rows = conn.execute("SELECT queue_status, bot_state, COUNT(*) FROM applicant_queue GROUP BY queue_status, bot_state").fetchall()
                for q_status, bot_state, cnt in rows:
                    q_st = str(q_status).upper()
                    b_st = str(bot_state).upper()
                    stats["total"] += cnt

                    if q_st == "WAITING":
                        stats["waiting"] += cnt
                    elif q_st in {"RUNNING", "ASSIGNED"}:
                        stats["running"] += cnt
                    elif q_st == "WAITING_SMS" or b_st == "WAITING_OTP":
                        stats["waiting_sms"] += cnt
                    elif q_st == "WAITING_BANK" or b_st in {"CAPACITY_FULL", "WAITING_AVAILABILITY"}:
                        stats["waiting_bank"] += cnt
                    elif q_st == "COMPLETED" or b_st == "CONFIRMED":
                        stats["completed"] += cnt
                    elif q_st == "FAILED" or b_st == "FAILED":
                        stats["failed"] += cnt
                    elif q_st == "PAUSED" or b_st == "PAUSED":
                        stats["paused"] += cnt

                    if b_st in {"STEP_3_READY", "SELECTING_BANK", "READY_TO_SUBMIT", "SUBMITTING"}:
                        stats["step_3"] += cnt
        except Exception as e:
            logger.warning("Failed to aggregate queue stats: %s", e)
        return stats

    @staticmethod
    def set_priority(applicant_id: str, priority: str) -> bool:
        """Update applicant priority (HIGH, NORMAL, LOW)."""
        nid = str(applicant_id).strip()
        p = str(priority).upper().strip()
        if p not in {"HIGH", "NORMAL", "LOW"}:
            p = "NORMAL"
        now = time.time()
        try:
            with DBHandler._connect() as conn:
                conn.execute(
                    "UPDATE applicant_queue SET priority = ?, updated_at = ? WHERE applicant_id = ?",
                    (p, now, nid),
                )
                conn.commit()
            QueueManager.emit_snapshot()
            return True
        except Exception as e:
            logger.error("Failed to set priority for %s: %s", nid, e)
            return False

    @staticmethod
    def reorder(queue_id: str, target_position: int) -> bool:
        """Change position of an applicant in the queue."""
        now = time.time()
        with QueueManager._lock:
            try:
                with DBHandler._connect() as conn:
                    item = conn.execute("SELECT applicant_id, queue_position FROM applicant_queue WHERE queue_id = ?", (queue_id,)).fetchone()
                    if not item:
                        return False
                    old_pos = item[1]
                    target_pos = max(1, int(target_position))

                    if target_pos > old_pos:
                        conn.execute(
                            """UPDATE applicant_queue
                               SET queue_position = queue_position - 1, updated_at = ?
                               WHERE queue_position > ? AND queue_position <= ?""",
                            (now, old_pos, target_pos),
                        )
                    elif target_pos < old_pos:
                        conn.execute(
                            """UPDATE applicant_queue
                               SET queue_position = queue_position + 1, updated_at = ?
                               WHERE queue_position >= ? AND queue_position < ?""",
                            (now, target_pos, old_pos),
                        )
                    conn.execute(
                        "UPDATE applicant_queue SET queue_position = ?, updated_at = ? WHERE queue_id = ?",
                        (target_pos, now, queue_id),
                    )
                    conn.commit()
                QueueManager.emit_snapshot()
                return True
            except Exception as e:
                logger.error("Failed to reorder queue item %s: %s", queue_id, e)
                return False

    @staticmethod
    def acquire_next_for_worker(worker_id: str) -> Optional[Dict[str, Any]]:
        """
        Atomically lease the next eligible waiting applicant for a worker.
        Selects highest priority (HIGH -> NORMAL -> LOW) and smallest queue_position.
        """
        now = time.time()
        lease_until = now + LEASE_DURATION_SECONDS
        with QueueManager._lock:
            try:
                with DBHandler._connect() as conn:
                    # Clean expired leases
                    conn.execute(
                        """UPDATE applicant_queue
                           SET queue_status = 'WAITING', assigned_worker = NULL, worker_lease_until = 0, updated_at = ?
                           WHERE queue_status = 'RUNNING' AND worker_lease_until > 0 AND worker_lease_until < ?""",
                        (now, now),
                    )
                    conn.commit()

                    # Find highest priority waiting applicant
                    query = """
                        SELECT queue_id, applicant_id, loan_type, requested_bank
                        FROM applicant_queue
                        WHERE queue_status IN ('WAITING', 'WAITING_BANK')
                        ORDER BY
                            CASE priority WHEN 'HIGH' THEN 1 WHEN 'NORMAL' THEN 2 WHEN 'LOW' THEN 3 ELSE 4 END ASC,
                            queue_position ASC
                        LIMIT 1
                    """
                    row = conn.execute(query).fetchone()
                    if not row:
                        return None

                    q_id, nid, loan_type, bank = row
                    conn.execute(
                        """UPDATE applicant_queue
                           SET queue_status = 'RUNNING', bot_state = 'STARTING',
                               assigned_worker = ?, worker_lease_until = ?,
                               started_at = COALESCE(started_at, ?),
                               last_activity_at = ?, updated_at = ?
                           WHERE queue_id = ?""",
                        (str(worker_id), lease_until, now, now, now, q_id),
                    )
                    conn.commit()
                    return {
                        "queue_id": q_id,
                        "applicant_id": nid,
                        "loan_type": loan_type,
                        "requested_bank": bank,
                        "worker_id": str(worker_id),
                    }
            except Exception as e:
                logger.error("Failed to acquire next queue item for worker %s: %s", worker_id, e)
                return None

    @staticmethod
    def heartbeat(applicant_id: str, worker_id: str) -> None:
        """Renew worker lease while actively processing."""
        now = time.time()
        lease_until = now + LEASE_DURATION_SECONDS
        try:
            with DBHandler._connect() as conn:
                conn.execute(
                    """UPDATE applicant_queue
                       SET worker_lease_until = ?, last_activity_at = ?, updated_at = ?
                       WHERE applicant_id = ? AND assigned_worker = ?""",
                    (lease_until, now, now, str(applicant_id), str(worker_id)),
                )
                conn.commit()
        except Exception:
            pass

    @staticmethod
    def pause_applicant(applicant_id: str) -> bool:
        """Pause processing for a specific applicant."""
        nid = str(applicant_id).strip()
        now = time.time()
        try:
            with DBHandler._connect() as conn:
                conn.execute(
                    """UPDATE applicant_queue
                       SET queue_status = 'PAUSED', bot_state = 'PAUSED',
                           assigned_worker = NULL, worker_lease_until = 0, updated_at = ?
                       WHERE applicant_id = ?""",
                    (now, nid),
                )
                conn.commit()
            DBHandler.update_runtime(nid, status="PAUSED", current_step="PAUSED")
            QueueManager.emit_snapshot()
            return True
        except Exception as e:
            logger.error("Failed to pause applicant %s: %s", nid, e)
            return False

    @staticmethod
    def resume_applicant(applicant_id: str) -> bool:
        """Resume a paused applicant back to WAITING."""
        nid = str(applicant_id).strip()
        now = time.time()
        try:
            with DBHandler._connect() as conn:
                conn.execute(
                    """UPDATE applicant_queue
                       SET queue_status = 'WAITING', bot_state = 'QUEUED',
                           assigned_worker = NULL, worker_lease_until = 0, updated_at = ?
                       WHERE applicant_id = ?""",
                    (now, nid),
                )
                conn.commit()
            DBHandler.update_runtime(nid, status="READY", current_step="QUEUED")
            QueueManager.emit_snapshot()
            return True
        except Exception as e:
            logger.error("Failed to resume applicant %s: %s", nid, e)
            return False

    @staticmethod
    def remove_from_queue(applicant_id: str) -> bool:
        """Remove applicant from active queue (marks CANCELLED, preserves applicant records)."""
        nid = str(applicant_id).strip()
        now = time.time()
        try:
            with DBHandler._connect() as conn:
                conn.execute(
                    """UPDATE applicant_queue
                       SET queue_status = 'CANCELLED', bot_state = 'FAILED',
                           assigned_worker = NULL, worker_lease_until = 0, updated_at = ?
                       WHERE applicant_id = ?""",
                    (now, nid),
                )
                conn.commit()
            QueueManager.emit_snapshot()
            return True
        except Exception as e:
            logger.error("Failed to cancel applicant %s from queue: %s", nid, e)
            return False

    @staticmethod
    def pause_all() -> int:
        """Pause all active applicants in the queue."""
        now = time.time()
        with QueueManager._lock:
            try:
                with DBHandler._connect() as conn:
                    cur = conn.execute(
                        """UPDATE applicant_queue
                           SET queue_status = 'PAUSED', bot_state = 'PAUSED',
                               assigned_worker = NULL, worker_lease_until = 0, updated_at = ?
                           WHERE queue_status IN ('WAITING', 'RUNNING', 'WAITING_SMS', 'WAITING_BANK')""",
                        (now,),
                    )
                    cnt = cur.rowcount
                    conn.commit()
                QueueManager.emit_snapshot()
                return cnt
            except Exception as e:
                logger.error("Failed to pause all in queue: %s", e)
                return 0

    @staticmethod
    def start_all() -> int:
        """Resume / start all paused applicants in the queue back to WAITING."""
        now = time.time()
        with QueueManager._lock:
            try:
                with DBHandler._connect() as conn:
                    cur = conn.execute(
                        """UPDATE applicant_queue
                           SET queue_status = 'WAITING', bot_state = 'QUEUED',
                               assigned_worker = NULL, worker_lease_until = 0, updated_at = ?
                           WHERE queue_status = 'PAUSED'""",
                        (now,),
                    )
                    cnt = cur.rowcount
                    conn.commit()
                QueueManager.emit_snapshot()
                return cnt
            except Exception as e:
                logger.error("Failed to start all in queue: %s", e)
                return 0

    @staticmethod
    def recover_on_startup() -> int:
        """
        On server boot, recover any orphaned leases left in RUNNING state from crashes or unclean shutdowns.
        Resets them to WAITING without losing queue rank or applicant data.
        """
        now = time.time()
        recovered = 0
        with QueueManager._lock:
            try:
                with DBHandler._connect() as conn:
                    # Sync any applicants in DB not yet enrolled in queue
                    QueueManager.sync_applicants()
                    cur = conn.execute(
                        """UPDATE applicant_queue
                           SET queue_status = 'WAITING', bot_state = 'QUEUED',
                               assigned_worker = NULL, worker_lease_until = 0, updated_at = ?
                           WHERE queue_status IN ('RUNNING', 'ASSIGNED')""",
                        (now,),
                    )
                    recovered = cur.rowcount
                    conn.commit()
                logger.info("Startup crash recovery: reset %d orphaned queue leases to WAITING", recovered)
                QueueManager.emit_snapshot()
            except Exception as e:
                logger.error("Startup crash recovery error: %s", e)
        return recovered

    @staticmethod
    def emit_snapshot() -> None:
        """Emit live WebSocket snapshot to all connected dashboards."""
        try:
            stats = QueueManager.get_stats()
            queue_items = QueueManager.get_queue()
            EVENT_BROADCASTER.emit_event(
                build_event(
                    "queue_snapshot",
                    stats=stats,
                    items=queue_items,
                    count=len(queue_items),
                )
            )
        except Exception:
            pass
