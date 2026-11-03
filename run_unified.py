"""
run_unified.py — One-Click Python Launcher for MAHANBOT Unified Selector.

Usage:
    python run_unified.py
"""

import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

from setup_and_verify import run_checks


def main():
    print("=" * 60)
    print("  راه‌اندازی سامانه یکپارچه انتخاب بانک وام ازدواج MAHANBOT")
    print("=" * 60)

    # 1. Run Pre-flight Checks
    checks_passed = run_checks()
    if not checks_passed:
        print("\n[خطا] برخی پیش‌نیازها برقرار نیستند. لطفاً خطاهای فوق را برطرف کنید.")
        sys.exit(1)

    # 2. Check if server is already running
    server_url = "http://127.0.0.1:8000/unified-selector"
    server_process = None
    server_running = False

    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=1.5) as resp:
            if resp.status in (200, 303):
                server_running = True
    except Exception:
        server_running = False

    if server_running:
        print("\n[1/2] سرور MAHANBOT از قبل فعال است.")
    else:
        print("\n[1/2] در حال راه‌اندازی سرور MAHANBOT...")
        server_process = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )

        # Wait for server to become responsive
        print("در انتظار پاسخ‌گویی سرور...")
        for _ in range(20):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=1.0) as resp:
                    if resp.status in (200, 303):
                        server_running = True
                        break
            except Exception:
                pass

    # 3. Open browser
    print(f"\n[2/2] باز کردن داشبورد یکپارچه در مرورگر: {server_url}")
    time.sleep(1.0)
    webbrowser.open(server_url)

    print("\n" + "=" * 60)
    print(f"🎉 سامانه فعال است: {server_url}")
    print("برای توقف سرور، کلیدهای Ctrl+C را فشار دهید.")
    print("=" * 60 + "\n")

    if server_process:
        try:
            server_process.wait()
        except KeyboardInterrupt:
            print("\nدر حال بستن سرور MAHANBOT...")
            server_process.terminate()
            try:
                server_process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                server_process.kill()
            print("سرور با موفقیت متوقف شد.")


if __name__ == "__main__":
    main()
