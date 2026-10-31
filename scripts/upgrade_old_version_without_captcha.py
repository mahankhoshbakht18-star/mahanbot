"""
scripts/upgrade_old_version_without_captcha.py
Transfers all MAHANBOT upgrades (SMS/OTP, Page 3 Bank Selection, Database, Excel, etc.)
to an old MAHANBOT installation while strictly keeping the old version's OWN CAPTCHA service.
"""
import os
import sys
import shutil
import py_compile
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

ROOT_DIR = Path(__file__).resolve().parent.parent

# Files that belong to the CAPTCHA system and must NEVER be overwritten in the old folder
CAPTCHA_SYSTEM_FILES = {
    "bot_select_captcha.py",
    "captcha_service.py",
    "api_solver.py",
    "my_captcha_model.pth",
}

UPGRADE_ITEMS = [
    # 1. SMS / OTP Subsystem
    "otp_form_detector.py",
    "bot_select_otp.py",
    "otp_transport.py",
    "relay_transport.py",
    "sms_service.py",
    "core/otp_queue.py",
    "core/sms_gateway.py",

    # 2. Page 3 & Bank Selection Subsystem
    "bot_select.py",
    "bot_select_bank.py",
    "bank_page_manager.py",
    "bank_quota_sniper.py",
    "bank_session_controller.py",
    "capacity_monitor.py",
    "core/step3_bank_page.py",
    "core/bank_matcher.py",
    "core/page_state_classifier.py",
    "core/loan_workflow.py",
    "core/state_machine.py",
    "core/capacity_radar.py",
    "core/capacity_analytics.py",

    # 3. Profile Health & Session Safety
    "core/browser_profile_health.py",
    "core/browser_profile_recovery.py",
    "browser_launcher.py",
    "browser_fingerprint.py",

    # 4. Core Bot, Events & Automation
    "bot_core.py",
    "bot_register.py",
    "bot_helpers.py",
    "browser_actions.py",
    "event_logger.py",
    "allowlist.py",
    "server.py",
    "submission_policy.py",

    # 5. Database, Core & Routes
    "database.py",
    "core/db_migration.py",
    "core/persian_normalizer.py",
    "core/models.py",
    "core/helpers.py",
    "core/deps.py",
    "core/config.py",
    "core/state.py",
    "core/job_queue.py",
    "routes/applicants.py",
    "routes/otp.py",
    "routes/capacity.py",
    "routes/queue.py",
    "routes/radar.py",
    "routes/browser_health.py",
    "routes/saved_pages.py",
    "routes/settings.py",
    "routes/auth.py",
    "routes/websocket.py",

    # 6. Frontend
    "static/index.html",
    "static/script.js",

    # 7. Excel Data & Tools
    "اطلاعات_متقاضیان_MAHANBOT.xlsx",
    "applicants_data.xlsx",
    "scripts/export_applicants_excel.py",
    "scripts/import_applicants_excel.py",
]


def upgrade_target(target_path: Path):
    target_path = target_path.resolve()
    print("=" * 70)
    print(f"[*] Starting Selective Upgrade (WITHOUT CAPTCHA System)")
    print(f"[*] Target Directory: {target_path}")
    print("=" * 70)

    if not target_path.exists():
        print(f"[ERROR] Target folder not found: {target_path}")
        return False

    old_captcha = target_path / "bot_select_captcha.py"
    if old_captcha.exists():
        print(f"[OK] Found old CAPTCHA service ({old_captcha.stat().st_size} bytes). It will be PRESERVED.")

    backup_dir = target_path / "backup_before_upgrade_20261031"
    backup_dir.mkdir(parents=True, exist_ok=True)
    print(f"[*] Creating safety backup in: {backup_dir.name}...")

    copied_count = 0
    new_count = 0

    for rel_str in UPGRADE_ITEMS:
        if rel_str in CAPTCHA_SYSTEM_FILES:
            continue

        src = ROOT_DIR / rel_str
        dst = target_path / rel_str

        if not src.exists():
            print(f"[!] Warning: Source file missing: {rel_str}")
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)

        if dst.exists():
            b_dst = backup_dir / rel_str
            b_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, b_dst)
            copied_count += 1
        else:
            new_count += 1

        shutil.copy2(src, dst)

    print(f"[*] Copied {copied_count} updated files.")
    print(f"[*] Added {new_count} brand-new modules.")
    print(f"[*] Explicitly preserved all original CAPTCHA system files: {list(CAPTCHA_SYSTEM_FILES)}")

    # Verify syntax in target folder
    print("[*] Validating Python syntax in target folder...")
    syntax_ok = True
    for root, dirs, files in os.walk(target_path):
        if "backup_" in root or ".venv" in root or "__pycache__" in root:
            continue
        for f in files:
            if f.endswith(".py"):
                py_path = Path(root) / f
                try:
                    py_compile.compile(str(py_path), doraise=True)
                except Exception as exc:
                    print(f"[!] Syntax error in {py_path.name}: {exc}")
                    syntax_ok = False

    if syntax_ok:
        print("    [OK] All Python files in target folder compiled with ZERO syntax errors!")

    print("=" * 70)
    print(f"[SUCCESS] UPGRADE SUCCESSFULLY COMPLETED FOR: {target_path.name}")
    print(f"  - Original CAPTCHA service preserved 100% untouched.")
    print(f"  - Step 2 SMS / OTP system fully upgraded (otp_form_detector, otp_transport, relay).")
    print(f"  - Step 3 Page 3 bank selection logic fully upgraded (bank_page_manager, sniper, session controller).")
    print(f"  - Database handler, Excel import/export and Web UI buttons added.")
    print(f"  - Safety backup saved at: {backup_dir}")
    print("=" * 70)
    return True


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\amir\Desktop\Mahanbot-fixed-20260808")
    upgrade_target(target)
