"""
launcher.py — Standalone 1-Click Desktop Launcher for MAHANBOT
Runs in Client Edition, configures local Playwright browsers, and launches dashboard.
"""
import os
import sys
import time
import webbrowser
import threading
from pathlib import Path

def main():
    # If frozen (PyInstaller), use sys._MEIPASS or executable directory
    if getattr(sys, 'frozen', False):
        base_dir = Path(sys.executable).resolve().parent
    else:
        base_dir = Path(__file__).resolve().parent
        
    os.chdir(base_dir)
    
    # 1. Enforce Client Edition
    os.environ["MAHANBOT_EDITION"] = "CLIENT"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    
    # 2. Configure bundled Chromium for Playwright
    local_browsers = base_dir / "browsers"
    if (local_browsers / "chromium-1194").exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers.resolve())
    
    # 3. Open browser after server starts
    def _open_dashboard():
        time.sleep(2.5)
        webbrowser.open("http://127.0.0.1:8000")
        
    threading.Thread(target=_open_dashboard, daemon=True).start()
    
    # 4. Run uvicorn server
    import uvicorn
    from server import app
    
    port = int(os.getenv("MAHANBOT_PORT", "8000"))
    host = os.getenv("MAHANBOT_HOST", "127.0.0.1")
    
    print("========================================================")
    print("  MAHANBOT 2.0 — سامانه هوشمند ثبت‌نام تسهیلات بانکی")
    print("  نسخه مشتری (CLIENT EDITION) — بدون نیاز به پیش‌نیاز")
    print(f"  آدرس دسترسی: http://{host}:{port}")
    print("========================================================")
    
    uvicorn.run(app, host=host, port=port, log_level="info")

if __name__ == "__main__":
    main()
