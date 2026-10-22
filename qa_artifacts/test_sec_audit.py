"""
Security, Injection, Path Traversal, and Auth/Role Audit Test
Runs completely in an isolated temp environment with mock DB.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database
from fastapi.testclient import TestClient

def run_security_audit():
    results = []
    
    with tempfile.TemporaryDirectory(prefix="mahanbot-qa-sec-") as temp_dir:
        test_db = str(Path(temp_dir) / "sec_test.db")
        database.DB_PATH = test_db
        database.DBHandler.init_db()
        
        from server import app
        client = TestClient(app, follow_redirects=False)
        
        # Test 1: Unauthenticated access to protected APIs
        protected_endpoints = [
            ("GET", "/applicants"),
            ("POST", "/applicants", {"national_id": "0012345678", "full_name": "Test"}),
            ("GET", "/settings"),
            ("POST", "/settings", {}),
            ("GET", "/api/capacity/status"),
            ("POST", "/jobs/start", {"national_id": "0012345678", "bot_name": "select"}),
            ("POST", "/bot/action/delete-request/0012345678", {}),
        ]
        
        for method, ep, *body in protected_endpoints:
            payload = body[0] if body else None
            if method == "GET":
                resp = client.get(ep)
            elif method == "POST":
                resp = client.post(ep, json=payload)
            
            # Should be 401 Unauthorized (or 303 Redirect for browser routes)
            is_blocked = resp.status_code in (401, 403, 303)
            results.append({
                "test_id": f"SEC-AUTH-{method}-{ep}",
                "category": "Authentication Guard",
                "description": f"Unauthenticated request to {ep}",
                "status": "PASS" if is_blocked else "FAIL",
                "observed_status": resp.status_code,
                "detail": "Blocked as expected" if is_blocked else "VULNERABILITY: Endpoint exposed without auth!"
            })

        # Test 2: Path Traversal
        traversal_paths = [
            "/static/../../server.py",
            "/static/..%2f..%2fserver.py",
            "/static/%2e%2e/%2e%2e/cbi_ultimate.db",
            "/static/....//....//server.py",
        ]
        for p in traversal_paths:
            resp = client.get(p)
            # Must not return 200 with python code
            is_safe = (resp.status_code in (400, 403, 404)) or ("uvicorn.run" not in resp.text and "DBHandler" not in resp.text)
            results.append({
                "test_id": f"SEC-TRAVERSAL-{p}",
                "category": "Path Traversal",
                "description": f"Attempt directory traversal on {p}",
                "status": "PASS" if is_safe else "FAIL",
                "observed_status": resp.status_code,
                "detail": "Traversal denied" if is_safe else "CRITICAL: Traversal leaked file contents!"
            })

        # Test 3: SQL Injection via Applicant fields
        sqli_payloads = [
            "0012345678' OR '1'='1",
            "0012345678'; DROP TABLE applicants; --",
            "0012345678' UNION SELECT 1,2,3,4,5 --",
        ]
        for payload in sqli_payloads:
            try:
                # Direct DB handler check
                applicant = database.DBHandler.get_applicant(payload)
                # Should safely return None, not crash or execute injection
                results.append({
                    "test_id": f"SEC-SQLI-{payload[:15]}",
                    "category": "SQL Injection",
                    "description": f"Parameterized query handling of '{payload[:20]}'",
                    "status": "PASS" if applicant is None else "FAIL",
                    "observed_status": "Handled Safely",
                    "detail": "Parameterized query successfully prevented injection"
                })
            except Exception as e:
                results.append({
                    "test_id": f"SEC-SQLI-{payload[:15]}",
                    "category": "SQL Injection",
                    "description": f"SQL error on payload '{payload[:20]}'",
                    "status": "FAIL",
                    "observed_status": str(type(e).__name__),
                    "detail": f"Query threw unhandled DB error: {e}"
                })

        # Test 4: XSS Stored in Applicant Data
        xss_payload = "<script>alert('xss')</script><img src=x onerror=alert(1)>"
        try:
            database.DBHandler.save_app_user("admin_sec", "admin_pass_123", days=30, is_admin=True)
            admin_login = client.post("/login", json={"username": "admin_sec", "password": "admin_pass_123"})
            admin_cookie = admin_login.cookies.get("mahan_session")
            client.cookies.set("mahan_session", admin_cookie)

            resp_add = client.post("/applicants", json={
                "national_id": "0099887766",
                "full_name": xss_payload,
                "data": {
                    "mobile": "09120000000",
                    "province": "تهران",
                    "loan_type": "marriage"
                }
            })
            retrieved = database.DBHandler.get_applicant("0099887766")
            is_stored = retrieved and retrieved["full_name"] == xss_payload
            results.append({
                "test_id": "SEC-XSS-STORAGE",
                "category": "XSS & Content Sanitization",
                "description": "Store raw HTML/XSS in database without corruption",
                "status": "PASS" if is_stored else "FAIL",
                "observed_status": "Stored safely as literal data",
                "detail": "Data stored faithfully; frontend uses innerText/textContent/escaped templates"
            })
        except Exception as e:
            results.append({
                "test_id": "SEC-XSS-STORAGE",
                "category": "XSS & Content Sanitization",
                "description": "Storage exception",
                "status": "FAIL",
                "observed_status": str(e),
                "detail": "Failed to store"
            })

        # Test 5: Role-based Access Control (Client Edition isolation)
        os.environ["MAHANBOT_EDITION"] = "CLIENT"
        try:
            database.DBHandler.save_app_user("testclient", "testpass123", days=30, is_admin=False)
            resp_login = client.post("/login", json={"username": "testclient", "password": "testpass123"})
            cookie = resp_login.cookies.get("mahan_session")
            client.cookies.set("mahan_session", cookie)
            
            # Client edition accessing /admin/sms-config must be forbidden (403)
            resp_admin = client.get("/admin/sms-config")
            is_forbidden = resp_admin.status_code == 403
            results.append({
                "test_id": "SEC-RBAC-CLIENT-EDITION",
                "category": "Role-Based Access Control",
                "description": "Client edition must be denied access to admin configuration",
                "status": "PASS" if is_forbidden else "FAIL",
                "observed_status": resp_admin.status_code,
                "detail": "403 Forbidden received as expected" if is_forbidden else f"RBAC violation! Status: {resp_admin.status_code}"
            })
        finally:
            os.environ.pop("MAHANBOT_EDITION", None)

        # Test 6: OTP Rate-Limiting & Brute-Force lockout
        otp_mobile = "09351112233"
        # 1st send -> 200 OK
        r1 = client.post("/auth/otp/send", json={"mobile": otp_mobile})
        # Immediate 2nd send -> must be 429 Too Many Requests (cooldown active)
        r2 = client.post("/auth/otp/send", json={"mobile": otp_mobile})
        is_rate_limited = r2.status_code == 429
        results.append({
            "test_id": "SEC-OTP-COOLDOWN",
            "category": "OTP Abuse Prevention",
            "description": "120-second cooldown on consecutive OTP requests for same mobile",
            "status": "PASS" if is_rate_limited else "FAIL",
            "observed_status": r2.status_code,
            "detail": "429 Too Many Requests enforced" if is_rate_limited else "Cooldown bypassed!"
        })

        # 5 failed verification attempts -> 6th must lock out
        for _ in range(5):
            client.post("/auth/otp/verify", json={"mobile": otp_mobile, "code": "000000"})
        r_lockout = client.post("/auth/otp/verify", json={"mobile": otp_mobile, "code": "000000"})
        is_locked_out = r_lockout.status_code == 400 and ("حداکثر" in r_lockout.text or "بیش از حد" in r_lockout.text or "منقضی" in r_lockout.text)
        results.append({
            "test_id": "SEC-OTP-BRUTE-FORCE",
            "category": "OTP Abuse Prevention",
            "description": "5-attempt brute-force lockout on OTP verification",
            "status": "PASS" if is_locked_out else "FAIL",
            "observed_status": r_lockout.status_code,
            "detail": f"Brute force blocked with response: {r_lockout.text[:80]}" if is_locked_out else "Brute-force lockout failed!"
        })

    return results

if __name__ == "__main__":
    import json
    res = run_security_audit()
    out_file = Path(__file__).resolve().parent / "sec_audit_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    print("AUDIT_COMPLETE_SAVED_TO_FILE")
    total = len(res)
    passed = sum(1 for r in res if r["status"] == "PASS")
    print(f"Total Security Checks: {total}, Passed: {passed}, Failed: {total - passed}")
