"""
scripts/create_clean_distribution_zip.py
Creates a pristine, production-ready, standalone zip archive of MAHANBOT.
Ready to be extracted and executed on any drive or machine.
"""
import os
import sys
import zipfile
import hashlib
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

EXCLUDED_DIRS = {
    ".git", ".github", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    ".system_generated", "updates_staging", "tmp", "temp", "node_modules",
    "chrome_profiles", "saved_pages", "backups", "logs", "diagnostics", "screenshots",
    "build", "dist", ".gradle", "مشاهده_وضعیت", "downloads", "qa_artifacts",
    "graphify-out", "reports", "scratch", "MagicMock"
}

EXCLUDED_EXTS = {
    ".pyc", ".pyo", ".pyd", ".log", ".tmp", ".mp4", ".avi", ".mkv",
    ".db-shm", ".db-wal"
}

EXCLUDED_FILES = {
    "cbi_ultimate.db",  # We provide a clean copy from cbi_clean_template.db
    "database.db", "mahan.db", "mahanbot.db", "otp_codes.db",
    "Screen Recording 2026-10-31 093304.mp4"
}

EMPTY_DIRS_TO_KEEP = [
    "chrome_profiles",
    "backups",
    "logs",
    "saved_pages",
    "مشاهده_وضعیت",
]


def compute_sha256(filepath: Path) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def create_clean_zip(output_filename: str = "MAHANBOT_Clean_v2.0.zip"):
    target_zip = ROOT_DIR / output_filename
    if target_zip.exists():
        target_zip.unlink()

    template_db = ROOT_DIR / "cbi_clean_template.db"
    if not template_db.exists():
        print("[!] Generating fresh cbi_clean_template.db first...")
        from create_clean_distribution_db import generate_clean_db
        generate_clean_db(str(template_db))

    total_files = 0
    total_uncompressed_bytes = 0

    print(f"[*] Creating clean distribution archive: {target_zip.name}...")

    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # 1. Walk and add source files
        for root, dirs, files in os.walk(ROOT_DIR):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".")]

            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in EXCLUDED_EXTS or file in EXCLUDED_FILES:
                    continue
                if "backup" in file.lower() and ".db" in file.lower():
                    continue
                if file.endswith(".zip"):
                    continue

                full_path = Path(root) / file
                rel_path = full_path.relative_to(ROOT_DIR)

                zf.write(full_path, arcname=str(rel_path))
                total_files += 1
                total_uncompressed_bytes += full_path.stat().st_size

        # 2. Add pristine clean cbi_ultimate.db copied from cbi_clean_template.db
        zf.write(template_db, arcname="cbi_ultimate.db")
        total_files += 1
        total_uncompressed_bytes += template_db.stat().st_size

        # 3. Add default .env file if not already added
        env_content = (
            "# ==========================================\n"
            "# MAHANBOT Clean Configuration (.env)\n"
            "# ==========================================\n"
            "MAHANBOT_HOST=127.0.0.1\n"
            "MAHANBOT_PORT=8000\n"
            "MAHANBOT_LOG_LEVEL=INFO\n"
            "MAHANBOT_ADMIN_USER=admin\n"
            "MAHANBOT_EDITION=ADMIN\n"
            "MAHANBOT_LICENSE_SERVER=https://otp.mahanvip.ir/api/licensing/v1\n"
            "MAHANBOT_ADMIN_PASSWORD=admin\n"
            "MAHANBOT_SECRET_KEY=mahanbot_clean_secret_key_2026\n"
            "MAHANBOT_DB=cbi_ultimate.db\n"
            "MAHANBOT_MAX_CONCURRENCY=2\n"
            "MAHAN_OTP_HOST=127.0.0.1\n"
            "MAHAN_OTP_PORT=8010\n"
            "MAHAN_OTP_DB=otp_codes.db\n"
            "MAHAN_OTP_TTL=180\n"
            "MAHANBOT_BROWSER_ZOOM=125\n"
            "MAHANBOT_BROWSER_WIDTH=540\n"
            "MAHANBOT_BROWSER_HEIGHT=680\n"
        )
        zf.writestr(".env", env_content)
        total_files += 1
        total_uncompressed_bytes += len(env_content.encode("utf-8"))

        # 4. Add empty placeholder directories with .gitkeep
        for ed in EMPTY_DIRS_TO_KEEP:
            zf.writestr(f"{ed}/.gitkeep", "")
            total_files += 1

    zip_size_mb = target_zip.stat().st_size / (1024 * 1024)
    sha256 = compute_sha256(target_zip)

    print("=" * 60)
    print(f"[SUCCESS] Clean archive created successfully!")
    print(f"  File: {target_zip}")
    print(f"  Total files in archive: {total_files}")
    print(f"  Uncompressed size: {total_uncompressed_bytes / (1024*1024):.2f} MB")
    print(f"  Compressed zip size: {zip_size_mb:.2f} MB")
    print(f"  SHA-256: {sha256}")
    print("=" * 60)

    # 5. Verify the created archive
    print("[*] Verifying zip integrity...")
    with zipfile.ZipFile(target_zip, "r") as zf:
        bad_file = zf.testzip()
        if bad_file:
            raise RuntimeError(f"Corrupt file found in zip: {bad_file}")
        names = zf.namelist()
        assert "server.py" in names
        assert "cbi_ultimate.db" in names
        assert "cbi_clean_template.db" in names
        assert "my_captcha_model.pth" in names
        assert "install.bat" in names
        assert "start.bat" in names
        assert "راهنمای_اجرا_در_درایو_جدید.txt" in names
        print(f"[VERIFIED] Zip integrity verified! All critical files present.")


if __name__ == "__main__":
    fname = sys.argv[1] if len(sys.argv) > 1 else "MAHANBOT_Clean_v2.0.zip"
    create_clean_zip(fname)
