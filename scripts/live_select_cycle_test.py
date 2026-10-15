"""Live start/stop regression for the bank-selection bot.

This script never reads or enters a CAPTCHA. Each run must stop at the manual
CAPTCHA handoff, then it cancels the job and verifies prompt browser cleanup.
"""

import json
import statistics
import time

import requests


BASE_URL = "http://127.0.0.1:8012"
TARGET_FULL_NAME = "فاطمه ودکار"
RUN_COUNT = 5


def applicant(session, nid):
    response = session.get(f"{BASE_URL}/applicants", timeout=5)
    response.raise_for_status()
    return next(item for item in response.json() if item["national_id"] == nid)


def main():
    session = requests.Session()
    response = session.post(
        f"{BASE_URL}/login", json={"username": "admin", "password": "admin"}, timeout=5
    )
    response.raise_for_status()
    users = session.get(f"{BASE_URL}/applicants", timeout=5).json()
    target = next(item for item in users if item["full_name"] == TARGET_FULL_NAME)
    nid = target["national_id"]
    results = []

    for run_number in range(1, RUN_COUNT + 1):
        started = time.perf_counter()
        response = session.post(
            f"{BASE_URL}/jobs/start", json={"bot_name": "select", "nid": nid}, timeout=5
        )
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "queued":
            raise AssertionError(f"run {run_number}: expected queued, got {body.get('status')}")
        job_id = body["job"]["id"]

        duplicate = session.post(
            f"{BASE_URL}/jobs/start", json={"bot_name": "select", "nid": nid}, timeout=5
        )
        duplicate.raise_for_status()
        if duplicate.json().get("status") != "already_running":
            raise AssertionError(f"run {run_number}: duplicate start was not rejected")

        deadline = time.monotonic() + 45
        runtime = {}
        while time.monotonic() < deadline:
            runtime = applicant(session, nid).get("runtime") or {}
            if runtime.get("status") == "WAITING_USER_INPUT" and runtime.get("current_step") == "WAIT_FOR_CAPTCHA_INPUT":
                break
            if runtime.get("status") == "ERROR":
                raise AssertionError(f"run {run_number}: runtime error {runtime.get('last_error')}")
            time.sleep(0.15)
        else:
            raise AssertionError(f"run {run_number}: manual CAPTCHA handoff timeout ({runtime})")

        handoff_ms = round((time.perf_counter() - started) * 1000, 1)
        stop_started = time.perf_counter()
        stop = session.post(f"{BASE_URL}/jobs/cancel/{job_id}", json={}, timeout=5)
        stop.raise_for_status()
        stop_deadline = time.monotonic() + 10
        final_runtime = {}
        while time.monotonic() < stop_deadline:
            active = session.get(f"{BASE_URL}/jobs/active", timeout=5).json()
            final_runtime = applicant(session, nid).get("runtime") or {}
            if (
                not any(item["nid"] == nid for item in active)
                and final_runtime.get("browser_status") == "CLOSED"
            ):
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"run {run_number}: stop did not finish within 10 seconds")
        stop_ms = round((time.perf_counter() - stop_started) * 1000, 1)
        results.append(
            {
                "run": run_number,
                "handoff_ms": handoff_ms,
                "stop_ms": stop_ms,
                "browser_startup_ms": (runtime.get("metrics") or {}).get("browser_startup_ms"),
                "page_load_ms": (runtime.get("metrics") or {}).get("page_load_ms"),
                "nid_field_ready_ms": (runtime.get("metrics") or {}).get("nid_field_ready_ms"),
                "nid_fill_ms": (runtime.get("metrics") or {}).get("nid_fill_ms"),
                "captcha_handoff_metric_ms": (runtime.get("metrics") or {}).get("captcha_handoff_ms"),
                "duplicate_start": "rejected",
                "final_status": final_runtime.get("status"),
            }
        )
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
        time.sleep(0.2)

    print(
        json.dumps(
            {
                "runs": results,
                "handoff_median_ms": round(statistics.median(item["handoff_ms"] for item in results), 1),
                "stop_median_ms": round(statistics.median(item["stop_ms"] for item in results), 1),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
