"""
Concurrency, JobQueue Worker Scaling (2, 4, 8, 16, 32), Mutual Exclusion,
and SQLite Lock Contention Benchmark.
Runs fully in an isolated temp environment.
"""
import os
import sys
import time
import json
import sqlite3
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database
from core.job_queue import JobQueue, Job

def run_concurrency_benchmark():
    benchmark_report = {
        "timestamp": time.time(),
        "worker_scaling": [],
        "applicant_mutual_exclusion": {},
        "sqlite_lock_contention": {}
    }

    # ─────────────────────────────────────────────────────────────────
    # 1. JobQueue Worker Scaling Benchmark (2, 4, 8, 16, 32)
    # ─────────────────────────────────────────────────────────────────
    worker_levels = [2, 4, 8, 16, 32]
    for limit in worker_levels:
        q = JobQueue(max_concurrency=limit)
        peak_active = 0
        active_lock = threading.Lock()
        active_counter = [0]
        completed_count = [0]

        def dummy_worker_task(*args, **kwargs):
            nonlocal peak_active
            with active_lock:
                active_counter[0] += 1
                if active_counter[0] > peak_active:
                    peak_active = active_counter[0]
            time.sleep(0.04) # 40ms simulation
            with active_lock:
                active_counter[0] -= 1
                completed_count[0] += 1

        with patch("core.job_queue.RegistrationBot") as mock_bot:
            mock_instance = mock_bot.return_value
            mock_instance.run.side_effect = dummy_worker_task
            q.start()

            t0 = time.time()
            for i in range(40):
                nid = f"10000000{i:02d}"
                q.enqueue("register", nid, {"mock": True})

            # Wait for all to finish
            timeout = 8.0
            start_wait = time.time()
            while completed_count[0] < 40 and time.time() - start_wait < timeout:
                time.sleep(0.02)

            elapsed = time.time() - t0
            q.stop()

        compliance = peak_active <= limit
        benchmark_report["worker_scaling"].append({
            "configured_limit": limit,
            "jobs_submitted": 40,
            "jobs_completed": completed_count[0],
            "peak_observed_concurrency": peak_active,
            "duration_seconds": round(elapsed, 3),
            "throughput_jobs_per_sec": round(completed_count[0] / max(elapsed, 0.001), 2),
            "concurrency_limit_honored": compliance
        })

    # ─────────────────────────────────────────────────────────────────
    # 2. Mutual Exclusion per Applicant Test
    # ─────────────────────────────────────────────────────────────────
    q = JobQueue(max_concurrency=8)
    nid_single = "9988776655"
    simultaneous_hit = [False]

    def long_task(*args, **kwargs):
        time.sleep(0.5)

    with patch("core.job_queue.RegistrationBot") as mock_bot:
        mock_instance = mock_bot.return_value
        mock_instance.run.side_effect = long_task
        q.start()

        job1 = q.enqueue("register", nid_single, {})
        # Immediate attempt to enqueue same NID again
        job2 = q.enqueue("register", nid_single, {})

        second_job_rejected = (job2 is None)
        q.stop()

    benchmark_report["applicant_mutual_exclusion"] = {
        "applicant_id": nid_single,
        "first_job_id": job1.id if job1 else None,
        "second_job_rejected": second_job_rejected,
        "status": "PASS" if second_job_rejected else "FAIL"
    }

    # ─────────────────────────────────────────────────────────────────
    # 3. SQLite Lock Contention under 32 Concurrent Writers
    # ─────────────────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory(prefix="mahanbot-qa-sqlite-") as temp_dir:
        bench_db = str(Path(temp_dir) / "contention_test.db")
        database.DB_PATH = bench_db
        database.DBHandler.init_db()

        # Seed 50 applicants
        conn = sqlite3.connect(database.DB_PATH)
        for i in range(50):
            nid = f"50000000{i:02d}"
            conn.execute(
                "INSERT INTO applicants (national_id, full_name, data) VALUES (?, ?, ?)",
                (nid, f"User {i}", json.dumps({"province": "تهران", "status": "init"}))
            )
        conn.commit()
        conn.close()

        latencies = []
        lock_errors = 0
        success_writes = 0
        total_ops = 320 # 32 threads x 10 write ops each

        def write_worker(worker_idx):
            nonlocal lock_errors, success_writes
            for j in range(10):
                target_nid = f"50000000{(worker_idx + j) % 50:02d}"
                t_start = time.perf_counter()
                try:
                    database.DBHandler.update_applicant_data(target_nid, {
                        "status": f"busy_update_{worker_idx}_{j}",
                        "timestamp": time.time()
                    })
                    latencies.append((time.perf_counter() - t_start) * 1000.0)
                    success_writes += 1
                except sqlite3.OperationalError as oe:
                    if "locked" in str(oe).lower() or "busy" in str(oe).lower():
                        lock_errors += 1
                except Exception:
                    pass

        t_sqlite_start = time.time()
        with ThreadPoolExecutor(max_workers=32) as executor:
            futures = [executor.submit(write_worker, i) for i in range(32)]
            for f in as_completed(futures):
                pass
        total_sqlite_time = time.time() - t_sqlite_start

        latencies.sort()
        p50 = latencies[int(len(latencies) * 0.50)] if latencies else 0
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
        p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0

        benchmark_report["sqlite_lock_contention"] = {
            "concurrent_threads": 32,
            "total_write_operations": total_ops,
            "successful_writes": success_writes,
            "lock_or_busy_errors": lock_errors,
            "total_time_seconds": round(total_sqlite_time, 3),
            "writes_per_second": round(success_writes / max(total_sqlite_time, 0.001), 1),
            "latency_ms": {
                "min": round(min(latencies), 2) if latencies else 0,
                "p50": round(p50, 2),
                "p95": round(p95, 2),
                "p99": round(p99, 2),
                "max": round(max(latencies), 2) if latencies else 0
            },
            "status": "PASS" if lock_errors == 0 else "DEGRADED"
        }

    return benchmark_report

if __name__ == "__main__":
    rep = run_concurrency_benchmark()
    out_file = Path(__file__).resolve().parent / "concurrency_benchmark_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, ensure_ascii=False)
    print("CONCURRENCY_BENCHMARK_COMPLETE")
    for w in rep['worker_scaling']:
        print(f"Workers: {w['configured_limit']} -> Peak Concurrency: {w['peak_observed_concurrency']}, Throughput: {w['throughput_jobs_per_sec']} jobs/s (Compliant: {w['concurrency_limit_honored']})")
    print(f"Mutual exclusion per applicant: {rep['applicant_mutual_exclusion']['status']}")
    print(f"SQLite 32-thread lock errors: {rep['sqlite_lock_contention']['lock_or_busy_errors']} / {rep['sqlite_lock_contention']['total_write_operations']}")
    print(f"SQLite p95 latency: {rep['sqlite_lock_contention']['latency_ms']['p95']} ms")
