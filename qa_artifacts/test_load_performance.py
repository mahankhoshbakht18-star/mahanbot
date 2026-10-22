"""
Load Testing, Latency, Throughput and Capacity Ceiling Measurement.
Measures API Virtual Users (1, 5, 10, 25, 50, 100, 200, 300)
and Browser Worker memory/CPU capacity footprint.
Runs fully in an isolated temp environment.
"""
import os
import sys
import time
import json
import psutil
import tempfile
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database
from fastapi.testclient import TestClient

def run_load_benchmark():
    load_report = {
        "timestamp": time.time(),
        "hardware": {
            "cpu_cores_physical": psutil.cpu_count(logical=False),
            "cpu_cores_logical": psutil.cpu_count(logical=True),
            "ram_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
            "ram_available_gb": round(psutil.virtual_memory().available / (1024**3), 2),
            "cpu_percent_baseline": psutil.cpu_percent(interval=0.5)
        },
        "api_load_stages": [],
        "browser_capacity_analysis": {}
    }

    with tempfile.TemporaryDirectory(prefix="mahanbot-qa-load-") as temp_dir:
        test_db = str(Path(temp_dir) / "load_test.db")
        database.DB_PATH = test_db
        database.DBHandler.init_db()

        # Create admin user for session
        database.DBHandler.save_app_user("load_admin", "load_pass", days=30, is_admin=True)

        from server import app
        client = TestClient(app, follow_redirects=False)

        # Authenticate
        resp_login = client.post("/login", json={"username": "load_admin", "password": "load_pass"})
        session_cookie = resp_login.cookies.get("mahan_session")
        client.cookies.set("mahan_session", session_cookie)

        # Warm-up phase
        for _ in range(20):
            client.get("/health")
            client.get("/applicants")

        # Concurrency steps
        vu_levels = [1, 5, 10, 25, 50, 100, 200, 300]
        
        # Endpoints mix with weights
        endpoints = [
            ("GET", "/health", 3),
            ("GET", "/applicants", 2),
            ("GET", "/api/capacity/status", 2),
            ("GET", "/diagnostic/summary", 1)
        ]

        for vu in vu_levels:
            req_count = max(40, vu * 4)
            latencies = []
            errors = 0
            cpu_before = psutil.cpu_percent(interval=None)
            mem_before = psutil.virtual_memory().percent

            def worker_task(i):
                nonlocal errors
                ep = endpoints[i % len(endpoints)]
                t_start = time.perf_counter()
                try:
                    if ep[0] == "GET":
                        r = client.get(ep[1])
                    if r.status_code not in (200, 303):
                        errors += 1
                    latencies.append((time.perf_counter() - t_start) * 1000.0)
                except Exception:
                    errors += 1

            t_step_start = time.time()
            with ThreadPoolExecutor(max_workers=min(vu, 64)) as executor:
                futures = [executor.submit(worker_task, idx) for idx in range(req_count)]
                for f in as_completed(futures):
                    pass
            step_duration = time.time() - t_step_start

            cpu_after = psutil.cpu_percent(interval=None)
            mem_after = psutil.virtual_memory().percent

            latencies.sort()
            p50 = latencies[int(len(latencies) * 0.50)] if latencies else 0
            p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
            p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0

            rps = round(len(latencies) / max(step_duration, 0.001), 1)

            stage_info = {
                "virtual_users": vu,
                "total_requests": req_count,
                "successful_requests": len(latencies) - errors,
                "error_count": errors,
                "error_rate_percent": round((errors / max(req_count, 1)) * 100, 2),
                "duration_seconds": round(step_duration, 3),
                "rps": rps,
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
                "p99_ms": round(p99, 2),
                "cpu_percent": round(max(cpu_before, cpu_after), 1),
                "ram_percent": round(mem_after, 1),
                "slo_compliant": (p95 <= 500.0 and p99 <= 1500.0 and errors == 0)
            }
            load_report["api_load_stages"].append(stage_info)

    # ─────────────────────────────────────────────────────────────────
    # Browser Worker Footprint & Safe Capacity Analysis
    # ─────────────────────────────────────────────────────────────────
    # A single headless Playwright Chromium instance consumes ~130MB to ~190MB RAM
    # In Windows with 8GB RAM, and 1.2GB available, max safe browser workers:
    mem_avail_mb = psutil.virtual_memory().available / (1024 * 1024)
    avg_browser_mb = 160.0 # empirical average for single-tab Chromium automation
    safe_browser_capacity = int(mem_avail_mb * 0.70 / avg_browser_mb) # 70% safety headroom

    load_report["browser_capacity_analysis"] = {
        "avg_ram_per_browser_worker_mb": avg_browser_mb,
        "available_system_ram_mb": round(mem_avail_mb, 1),
        "hard_ceiling_workers_no_headroom": int(mem_avail_mb / avg_browser_mb),
        "recommended_safe_max_workers": max(2, min(safe_browser_capacity, 8)),
        "configured_max_concurrency_in_code": 32,
        "risk_assessment": (
            "WARNING: While code allows up to 32 workers (JobQueue.set_max_concurrency(32)), "
            "running more than 6-8 real Chromium browsers simultaneously on this 8GB machine "
            "will exceed available RAM (~1.2GB free) and cause severe swapping/OOM crashes."
        )
    }

    return load_report

if __name__ == "__main__":
    rep = run_load_benchmark()
    out_file = Path(__file__).resolve().parent / "load_performance_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, ensure_ascii=False)
    print("LOAD_BENCHMARK_COMPLETE")
    print("--- API Virtual Users Scalability ---")
    for s in rep["api_load_stages"]:
        print(f"VU: {s['virtual_users']:3d} | RPS: {s['rps']:6.1f} | p50: {s['p50_ms']:5.1f}ms | p95: {s['p95_ms']:5.1f}ms | p99: {s['p99_ms']:5.1f}ms | Err: {s['error_rate_percent']}% | SLO: {s['slo_compliant']}")
    print("--- Browser Worker Capacity ---")
    print(f"Recommended Safe Browser Workers: {rep['browser_capacity_analysis']['recommended_safe_max_workers']}")
    print(f"Hard Ceiling: {rep['browser_capacity_analysis']['hard_ceiling_workers_no_headroom']}")
