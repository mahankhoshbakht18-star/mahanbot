import csv
import json
from pathlib import Path

qa_dir = Path("qa_artifacts")

rows = []

# Header: Test_ID, Category, Scenario_Description, Input_or_Load, Expected_Behavior, Observed_Behavior, Result, Severity_if_Failed
# 1. Baseline Suite
rows.append([
    "BASE-001",
    "Regression Baseline",
    "Full suite execution via scripts/run_tests.py",
    "267 unit & integration tests",
    "All tests PASS (exit code 0)",
    "254 PASS, 9 FAIL, 4 ERROR (exit code 1)",
    "FAIL",
    "P2"
])

# 2. Security Suite
with open(qa_dir / "sec_audit_results.json", "r", encoding="utf-8") as f:
    sec_data = json.load(f)
for item in sec_data:
    rows.append([
        item["test_id"],
        item["category"],
        item["description"],
        "HTTP Request / DB Query",
        "Denied or Handled safely",
        item["detail"],
        item["status"],
        "P1" if item["status"] != "PASS" else "N/A"
    ])

# 3. Concurrency Suite
with open(qa_dir / "concurrency_benchmark_results.json", "r", encoding="utf-8") as f:
    conc_data = json.load(f)
for w in conc_data["worker_scaling"]:
    rows.append([
        f"CONC-SCALE-W{w['configured_limit']}",
        "JobQueue Worker Scaling",
        f"Worker pool capacity scaled to {w['configured_limit']}",
        f"40 jobs submitted under {w['configured_limit']} max workers",
        f"Active concurrency <= {w['configured_limit']}",
        f"Peak {w['peak_observed_concurrency']} workers, Throughput: {w['throughput_jobs_per_sec']} jobs/s",
        "PASS" if w["concurrency_limit_honored"] else "FAIL",
        "P1" if not w["concurrency_limit_honored"] else "N/A"
    ])

rows.append([
    "CONC-MUTUAL-EXCLUSION",
    "Concurrency & Integrity",
    "Single active job per applicant constraint",
    "Applicant 9988776655 double start",
    "Second job rejected immediately with None/409",
    "Second job rejected=True; simultaneous=False",
    conc_data["applicant_mutual_exclusion"]["status"],
    "P0" if conc_data["applicant_mutual_exclusion"]["status"] != "PASS" else "N/A"
])

rows.append([
    "CONC-SQLITE-32T",
    "Database Concurrency",
    "SQLite 32 concurrent worker thread write contention",
    "320 write operations across 32 threads",
    "Zero database locked/busy errors (WAL mode)",
    f"0 errors, p95 latency: {conc_data['sqlite_lock_contention']['latency_ms']['p95']}ms",
    conc_data["sqlite_lock_contention"]["status"],
    "P1" if conc_data["sqlite_lock_contention"]["status"] != "PASS" else "N/A"
])

# 4. Page 3 Sniping Suite
with open(qa_dir / "page3_sniping_results.json", "r", encoding="utf-8") as f:
    p3_data = json.load(f)
for item in p3_data:
    rows.append([
        item["test_id"],
        item["category"],
        item.get("description", item.get("test_id")),
        str(item.get("input", item.get("input_label", "N/A"))),
        "Adhere to Page 3 submission policy",
        item["detail"],
        item["status"],
        "P2" if item["status"] != "PASS" else "N/A"
    ])

# 5. Load Performance Suite
with open(qa_dir / "load_performance_results.json", "r", encoding="utf-8") as f:
    load_data = json.load(f)
for s in load_data["api_load_stages"]:
    rows.append([
        f"PERF-LOAD-VU{s['virtual_users']}",
        "Load & Performance",
        f"API load step with {s['virtual_users']} virtual users",
        f"{s['virtual_users']} concurrent users, {s['total_requests']} requests",
        "p95 < 500ms, p99 < 1500ms, Error rate < 1.0%",
        f"RPS: {s['rps']}, p50: {s['p50_ms']}ms, p95: {s['p95_ms']}ms, p99: {s['p99_ms']}ms, Err: {s['error_rate_percent']}%",
        "PASS" if s["slo_compliant"] else "DEGRADED",
        "P3" if not s["slo_compliant"] else "N/A"
    ])

# Write CSV
csv_path = qa_dir / "QA_MATRIX.csv"
with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f)
    writer.writerow([
        "Test_ID", "Category", "Scenario_Description", "Input_or_Load",
        "Expected_Behavior", "Observed_Behavior", "Result", "Severity_if_Failed"
    ])
    writer.writerows(rows)

print(f"QA_MATRIX.csv written with {len(rows)} entries.")
