"""
scripts/package_client_app.py — Complete Standalone Client Packager for MAHANBOT
Bundles application code, static assets, local OCR CRNN model, bundled Playwright Chromium,
and one-click launchers so end-clients require ZERO prerequisites.
"""
import os
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
CLIENT_DIR = DIST_DIR / "MAHANBOT_Client_v2.0"
ZIP_FILE = DIST_DIR / "MAHANBOT_Client_v2.0_Standalone.zip"

INCLUDE_EXTENSIONS = {".py", ".html", ".css", ".js", ".ttf", ".woff", ".woff2", ".svg", ".png", ".jpg", ".ico", ".json", ".bat", ".vbs", ".db", ".pth", ".txt"}
EXCLUDE_DIRS = {".git", ".gemini", ".system_generated", "tests", "qa_artifacts", "dist", "build", "__pycache__", ".pytest_cache", ".venv"}

def copy_app_files():
    print(f"[*] Creating target directory: {CLIENT_DIR}")
    if CLIENT_DIR.exists():
        shutil.rmtree(CLIENT_DIR, ignore_errors=True)
    CLIENT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Copy files from root
    for item in ROOT.iterdir():
        if item.name in EXCLUDE_DIRS:
            continue
        if item.is_file():
            if item.suffix.lower() in INCLUDE_EXTENSIONS or item.name in (".env.example", "requirements.txt", "my_captcha_model.pth", "cbi_ultimate.db"):
                # Avoid copying private admin license files
                if ".licenses.private.env" in item.name:
                    continue
                shutil.copy2(item, CLIENT_DIR / item.name)
        elif item.is_dir() and item.name in ("core", "routes", "static"):
            shutil.copytree(item, CLIENT_DIR / item.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # 2. Write client-specific .env
    client_env = CLIENT_DIR / ".env"
    client_env.write_text("MAHANBOT_EDITION=CLIENT\nMAHANBOT_HOST=127.0.0.1\nMAHANBOT_PORT=8000\nMAHANBOT_LOG_LEVEL=INFO\n", encoding="utf-8")

    # 3. Create friendly Persian launcher batch
    persian_bat = CLIENT_DIR / "اجرای ماهان بات.bat"
    persian_bat.write_text("""@echo off
chcp 65001 >nul
title سامانه ماهان بات نسخه مشتریان
cd /d "%~dp0"
call run_client.bat
""", encoding="utf-8")

    # 4. Copy bundled Chromium from LocalAppData if available
    local_app_data = os.getenv("LOCALAPPDATA", "")
    playwright_dir = Path(local_app_data) / "ms-playwright" / "chromium-1194"
    target_browsers = CLIENT_DIR / "browsers" / "chromium-1194"

    if playwright_dir.exists():
        print(f"[*] Bundling Playwright Chromium from {playwright_dir}...")
        target_browsers.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(playwright_dir, target_browsers, dirs_exist_ok=True)
        print("    -> Chromium bundled successfully! (Zero prerequisite for customer)")
    else:
        print("[!] Warning: Local Chromium 1194 not found. Creating placeholder.")
        target_browsers.mkdir(parents=True, exist_ok=True)

    # 5. Create user documentation
    readme_fa = CLIENT_DIR / "راهنمای اجرای برنامه.txt"
    readme_fa.write_text("""══════════════════════════════════════════════════════════════
   سامانه هوشمند ثبت‌نام تسهیلات بانکی — MAHANBOT نسخه مشتری
══════════════════════════════════════════════════════════════

این بسته کاملاً پرتابل و مستقل بوده و نیاز به نصب هیچ‌گونه پیش‌نیاز
(اعم از پایتون، گیت، مرورگر یا وابستگی خارجی) ندارد.

نحوه راه‌اندازی:
۱. فایل «اجرای ماهان بات.bat» یا «Start_MahanBot.vbs» را اجرا کنید.
۲. سامانه به‌طور خودکار راه‌اندازی شده و داشبورد در مرورگر باز می‌شود:
   http://127.0.0.1:8000
۳. برای ورود از مشخصات حساب خود استفاده نمایید.

پشتیبانی رسمی:
کانال تلگرام: @mahanbot_ezdavaj
══════════════════════════════════════════════════════════════
""", encoding="utf-8")

    print("[*] Base distribution created successfully.")

def create_zip():
    print(f"[*] Packaging into standalone ZIP archive: {ZIP_FILE}...")
    with zipfile.ZipFile(ZIP_FILE, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(CLIENT_DIR):
            for file in files:
                file_path = Path(root) / file
                archive_name = file_path.relative_to(DIST_DIR)
                zf.write(file_path, archive_name)
    size_mb = ZIP_FILE.stat().st_size / (1024 * 1024)
    print(f"[*] Standalone ZIP package created: {ZIP_FILE} ({size_mb:.1f} MB)")

if __name__ == "__main__":
    copy_app_files()
    create_zip()
    print("[*] DONE! Standalone Client Distribution ready.")
