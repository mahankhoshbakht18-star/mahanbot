import os, sys, subprocess, glob

test_files = sorted(glob.glob('tests/test_*.py'))
print(f"Total test files to evaluate: {len(test_files)}", flush=True)

passed = 0
failed = 0
passed_details = []
failed_details = []

for tf in test_files:
    mod_name = os.path.basename(tf)[:-3]
    cmd = [sys.executable, 'scripts/run_tests.py', mod_name]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding='utf-8', errors='replace')
        out = (res.stdout or '') + (res.stderr or '')
        ran_line = ''
        for line in out.splitlines():
            if line.startswith('Ran '):
                ran_line = line.strip()
        if res.returncode == 0 and 'OK' in out:
            passed += 1
            passed_details.append((mod_name, ran_line))
            print(f"[PASS] {mod_name:40s} | {ran_line}", flush=True)
        else:
            failed += 1
            fail_msg = ''
            for line in out.splitlines():
                if line.startswith('FAILED') or line.startswith('FAIL:') or line.startswith('ERROR:'):
                    fail_msg = line.strip()
                    break
            failed_details.append((mod_name, fail_msg or "Failed"))
            print(f"[FAIL] {mod_name:40s} | {fail_msg or 'Failed'}", flush=True)
    except subprocess.TimeoutExpired:
        failed += 1
        failed_details.append((mod_name, "Timeout (>30s)"))
        print(f"[TIMEOUT] {mod_name:37s} | Timeout (>30s)", flush=True)

print(f"\n==========================================", flush=True)
print(f"TOTAL: {passed} PASSED, {failed} FAILED out of {len(test_files)}", flush=True)
print(f"==========================================", flush=True)
