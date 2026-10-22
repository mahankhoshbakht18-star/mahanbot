"""
Page 3 Sensitive Acceptance, Final Submission Rules, and Quota Sniping Verification.
Runs fully in an isolated temp environment.
"""
import sys
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from submission_policy import validate_submission_settings, submission_policy
from bank_page_manager import BankPageState as State, analyze_bank_page_html
from bank_quota_sniper import form_ready

def run_page3_sniping_audit():
    results = []

    # 1. Validation of final_submit_interval_seconds
    valid_intervals = [1, 2.5, 5, 10, 60, 600]
    for val in valid_intervals:
        try:
            validate_submission_settings({"auto_final_submit": True, "final_submit_interval_seconds": val})
            results.append({
                "test_id": f"P3-INTERVAL-VALID-{val}",
                "category": "Interval Validation",
                "input": val,
                "status": "PASS",
                "detail": f"Valid interval {val}s accepted"
            })
        except Exception as e:
            results.append({
                "test_id": f"P3-INTERVAL-VALID-{val}",
                "category": "Interval Validation",
                "input": val,
                "status": "FAIL",
                "detail": f"Valid interval {val}s rejected: {e}"
            })

    invalid_intervals = [
        (0, "Zero"),
        (-1, "Negative"),
        (-50, "Negative large"),
        (601, "Over 600s max"),
        (10000, "Extreme high"),
        ("abc", "String non-numeric"),
        (True, "Boolean value"),
        (False, "Boolean False"),
        (float("nan"), "NaN"),
        (float("inf"), "Infinity")
    ]
    for val, label in invalid_intervals:
        try:
            validate_submission_settings({"auto_final_submit": True, "final_submit_interval_seconds": val})
            results.append({
                "test_id": f"P3-INTERVAL-INVALID-{label}",
                "category": "Interval Validation",
                "input": str(val),
                "status": "FAIL",
                "detail": f"VULNERABILITY: Invalid interval {val} ({label}) was mistakenly accepted!"
            })
        except (ValueError, TypeError):
            results.append({
                "test_id": f"P3-INTERVAL-INVALID-{label}",
                "category": "Interval Validation",
                "input": str(val),
                "status": "PASS",
                "detail": f"Invalid interval {val} ({label}) properly rejected"
            })

    # 2. Inheritance & Preference Precedence: Applicant-level vs Global
    # Case A: Global OFF, Applicant ON -> Expected: ON
    enabled, interval = submission_policy({"auto_final_submit": False}, {"auto_final_submit": True})
    results.append({
        "test_id": "P3-PRECEDENCE-APP-ON-GLOBAL-OFF",
        "category": "Preference Precedence",
        "description": "Applicant ON must override Global OFF",
        "status": "PASS" if enabled is True else "FAIL",
        "detail": f"Applicant-specific setting took precedence (resolved: {enabled})"
    })

    # Case B: Global ON, Applicant OFF -> Expected: OFF
    enabled_b, interval_b = submission_policy({"auto_final_submit": True}, {"auto_final_submit": False})
    results.append({
        "test_id": "P3-PRECEDENCE-APP-OFF-GLOBAL-ON",
        "category": "Preference Precedence",
        "description": "Applicant explicit OFF must override Global ON",
        "status": "PASS" if enabled_b is False else "FAIL",
        "detail": f"Explicit applicant OFF was respected (resolved: {enabled_b})"
    })

    # Case C: Global ON, Applicant None/unspecified -> Expected: Inherit ON
    enabled_c, interval_c = submission_policy({"auto_final_submit": True, "bank_capacity_check_interval": 15}, {})
    results.append({
        "test_id": "P3-PRECEDENCE-INHERIT-GLOBAL",
        "category": "Preference Precedence",
        "description": "Empty applicant preference inherits Global value",
        "status": "PASS" if (enabled_c is True and interval_c == 15.0) else "FAIL",
        "detail": f"Inherited successfully from global setting (enabled={enabled_c}, interval={interval_c})"
    })

    # 3. HTML State Analysis via analyze_bank_page_html
    html_samples = [
        ("<span id='ctl00_ContentPlaceHolder1_lblTrackingCode'>8877665544</span>", State.REGISTRATION_CONFIRMED, "Confirmation (Pure Digits)"),
        ("<span id='ctl00_ContentPlaceHolder1_lblTrackingCode'>کد رهگیری: 8877665544</span>", State.REGISTRATION_CONFIRMED, "Confirmation (With Persian Prefix)"),
        ("<span id='ctl00_ContentPlaceHolder1_labErrMsg'>ظرفیت ثبت نام در این بانک تکمیل گردیده است</span>", State.CAPACITY_FULL, "Capacity Full"),
        ("<span id='ctl00_ContentPlaceHolder1_labErrMsg'>مهلت زمانی نشست شما به پایان رسیده است</span>", State.SESSION_EXPIRED, "Session Expired"),
    ]
    for html, expected_state, label in html_samples:
        analyzed = analyze_bank_page_html(html)
        actual_state = analyzed.get("state")
        is_matched = (actual_state == expected_state)
        results.append({
            "test_id": f"P3-ANALYZE-{label}",
            "category": "Bank Page State Analysis",
            "input_label": label,
            "status": "PASS" if is_matched else "FAIL",
            "detail": f"State: {actual_state} (Expected: {expected_state})"
        })

    return results

if __name__ == "__main__":
    rep = run_page3_sniping_audit()
    out_file = Path(__file__).resolve().parent / "page3_sniping_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, ensure_ascii=False)
    print("PAGE3_SNIPING_AUDIT_COMPLETE")
    total = len(rep)
    passed = sum(1 for r in rep if r["status"] == "PASS")
    print(f"Total Page 3 Rules Tested: {total}, Passed: {passed}, Failed: {total - passed}")
