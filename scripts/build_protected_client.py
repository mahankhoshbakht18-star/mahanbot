"""
scripts/build_protected_client.py — Build Closed-Source Standalone App for MAHANBOT
Bundles only compiled binary MahanBot.exe, Chromium browser, static assets, and AI models.
NO .py PYTHON SOURCE FILES ARE EXPOSED TO THE CLIENT.
"""
import os
import shutil
import zipfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
EXE_FILE = DIST_DIR / "MahanBot.exe"
SECURE_APP_DIR = DIST_DIR / "MAHANBOT_App"
SECURE_ZIP = DIST_DIR / "MAHANBOT_App_Standalone.zip"

def wait_for_exe():
    print("[*] Checking for compiled MahanBot.exe...")
    # Wait up to 30 minutes if PyInstaller is still packing
    for _ in range(360):
        if EXE_FILE.exists() and EXE_FILE.stat().st_size > 50_000_000:
            # Check if file is still locked by PyInstaller
            try:
                with open(EXE_FILE, "rb") as f:
                    f.read(1024)
                print(f"[+] Found compiled MahanBot.exe ({EXE_FILE.stat().st_size / (1024*1024):.1f} MB)")
                return True
            except (IOError, PermissionError):
                time.sleep(5)
        else:
            time.sleep(5)
    return False

def package_protected_app():
    print(f"[*] Creating protected application directory: {SECURE_APP_DIR}")
    if SECURE_APP_DIR.exists():
        shutil.rmtree(SECURE_APP_DIR, ignore_errors=True)
    SECURE_APP_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Copy the compiled executable
    print("[*] Copying compiled MahanBot.exe...")
    shutil.copy2(EXE_FILE, SECURE_APP_DIR / "MahanBot.exe")

    # 2. Copy static web UI assets (HTML, CSS, JS, fonts)
    print("[*] Copying static web interface...")
    shutil.copytree(ROOT / "static", SECURE_APP_DIR / "static", dirs_exist_ok=True)

    # 3. Copy OCR model weights and clean database
    print("[*] Copying AI CAPTCHA weights and database...")
    shutil.copy2(ROOT / "my_captcha_model.pth", SECURE_APP_DIR / "my_captcha_model.pth")
    shutil.copy2(ROOT / "cbi_ultimate.db", SECURE_APP_DIR / "cbi_ultimate.db")

    # 4. Copy bundled Chromium browser
    local_app_data = os.getenv("LOCALAPPDATA", "")
    playwright_dir = Path(local_app_data) / "ms-playwright" / "chromium-1194"
    target_browsers = SECURE_APP_DIR / "browsers" / "chromium-1194"
    if playwright_dir.exists():
        print("[*] Bundling Playwright Chromium browser...")
        shutil.copytree(playwright_dir, target_browsers, dirs_exist_ok=True)

    # 5. Create Desktop Shortcut Installer (.vbs / .bat)
    installer_vbs = SECURE_APP_DIR / "ایجاد میانبر روی دسکتاپ.vbs"
    installer_vbs.write_text("""Set WshShell = CreateObject("WScript.Shell")
strDesktop = WshShell.SpecialFolders("Desktop")
strAppPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
Set oShortcut = WshShell.CreateShortcut(strDesktop & "\\ماهان بات (MAHANBOT).lnk")
oShortcut.TargetPath = strAppPath & "\\MahanBot.exe"
oShortcut.WorkingDirectory = strAppPath
oShortcut.WindowStyle = 1
oShortcut.Description = "سامانه ثبت‌نام تسهیلات بانکی ماهان بات"
oShortcut.Save
MsgBox "میانبر برنامه ماهان‌بات با موفقیت روی دسکتاپ شما ایجاد شد.", 64, "نصب ماهان‌بات"
""", encoding="utf-8")

    readme = SECURE_APP_DIR / "راهنما.txt"
    readme.write_text("""══════════════════════════════════════════════════════════════
   سامانه هوشمند ثبت‌نام تسهیلات بانکی — MAHANBOT (نسخه مستقل)
══════════════════════════════════════════════════════════════

این برنامه کاملاً کامپایل‌شده، مستقل و بدون نیاز به نصب هیچ پیش‌نیازی می‌باشد.

نحوه اجرا:
۱. کافیست روی فایل MahanBot.exe دو بار کلیک کنید.
۲. سامانه اجرا شده و صفحه ورود در مرورگر شما باز می‌شود.

برای دسترسی سریع:
می‌توانید فایل «ایجاد میانبر روی دسکتاپ.vbs» را اجرا کنید تا میانبر
برنامه روی دسکتاپ ویندوز قرار گیرد.
══════════════════════════════════════════════════════════════
""", encoding="utf-8")

    # 6. Verify: Absolutely NO .py files in SECURE_APP_DIR!
    py_files = list(SECURE_APP_DIR.rglob("*.py"))
    if py_files:
        print(f"[!] Warning: Found {len(py_files)} .py files! Removing...")
        for p in py_files:
            p.unlink()
    print("[+] Verified: 0 source .py files present. Source code is 100% protected!")

    # 7. Zip into standalone distribution
    print(f"[*] Compressing into {SECURE_ZIP}...")
    with zipfile.ZipFile(SECURE_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(SECURE_APP_DIR):
            for file in files:
                file_path = Path(root) / file
                archive_name = file_path.relative_to(DIST_DIR)
                zf.write(file_path, archive_name)
    size_mb = SECURE_ZIP.stat().st_size / (1024 * 1024)
    print(f"[+] Standalone Protected Package created: {SECURE_ZIP} ({size_mb:.1f} MB)")

if __name__ == "__main__":
    if wait_for_exe():
        package_protected_app()
    else:
        print("[!] MahanBot.exe compilation timed out or failed.")
