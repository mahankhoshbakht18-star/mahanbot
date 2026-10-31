"""
scripts/package_upgrade_patch.py
Packages all CAPTCHA fixes, SMS/OTP upgrades, and Page 3 bank selection logic into
a dedicated, standalone upgrade patch archive (MAHANBOT_UPGRADE_PATCH.zip).
"""
import os
import sys
import zipfile
import hashlib
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

PATCH_FILES = [
    # 1. CAPTCHA Subsystem
    "bot_select_captcha.py",
    "bot_core.py",
    "api_solver.py",
    "my_captcha_model.pth",

    # 2. SMS / OTP Subsystem
    "otp_form_detector.py",
    "bot_select_otp.py",
    "otp_transport.py",
    "relay_transport.py",
    "sms_service.py",
    "core/otp_queue.py",
    "core/sms_gateway.py",

    # 3. Page 3 & Bank Selection Subsystem
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

    # 4. Profile Health & Session Safety
    "core/browser_profile_health.py",
    "core/browser_profile_recovery.py",
    "browser_launcher.py",
    "browser_fingerprint.py",

    # 5. Database, Core & Routes
    "database.py",
    "submission_policy.py",
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

    # 6. Requirements & Scripts
    "requirements.txt",
    "requirements-otp.txt",
    "scripts/run_tests.py",
    "scripts/export_applicants_excel.py",
    "scripts/import_applicants_excel.py",
    "tests/test_bank_captcha_flow.py",
    "tests/test_browser_profile_health_and_recovery.py",
    "tests/test_navigation.py",

    # 7. Excel Data & Guide
    "اطلاعات_متقاضیان_MAHANBOT.xlsx",
    "applicants_data.xlsx",
    "راهنمای_انتقال_به_نسخه_قدیمی.txt",
]


def compute_sha256(filepath: Path) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def create_upgrade_patch(output_name: str = "MAHANBOT_UPGRADE_PATCH.zip"):
    target_zip = ROOT_DIR / output_name
    if target_zip.exists():
        target_zip.unlink()

    print(f"[*] Packaging Upgrade Patch: {target_zip.name}...")
    total_files = 0
    total_bytes = 0

    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel_path_str in PATCH_FILES:
            full_path = ROOT_DIR / rel_path_str
            if not full_path.exists():
                print(f"[!] Warning: Missing patch file {rel_path_str}")
                continue
            zf.write(full_path, arcname=rel_path_str)
            total_files += 1
            total_bytes += full_path.stat().st_size

        # Also add apply_upgrade_to_old_version.py inside the zip
        apply_py = ROOT_DIR / "apply_upgrade_to_old_version.py"
        if apply_py.exists():
            zf.write(apply_py, arcname="apply_upgrade.py")
            total_files += 1

    size_mb = target_zip.stat().st_size / (1024 * 1024)
    sha = compute_sha256(target_zip)
    print(f"[SUCCESS] Patch created: {target_zip.name} ({size_mb:.2f} MB, {total_files} files)")
    print(f"  SHA-256: {sha}")


if __name__ == "__main__":
    create_upgrade_patch()
