"""
scripts/package_release.py — Packages MAHANBOT 2.0 for Server Deployment & Direct Download.
Creates clean zip archives ready to be uploaded to /downloads/ on otp.mahanvip.ir
"""
import os
import sys
import zipfile
import hashlib
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DOWNLOADS_DIR = ROOT_DIR / "downloads"

EXCLUDED_DIRS = {
    ".git", ".github", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    ".system_generated", "updates_staging", "tmp", "temp", "node_modules",
    "chrome_profiles", "saved_pages", "backups", "logs", "diagnostics", "screenshots",
    "build", "dist", ".gradle", "مشاهده_وضعیت"
}

EXCLUDED_EXTS = {
    ".pyc", ".pyo", ".pyd", ".log", ".pem", ".key", ".sqlite", ".sqlite3",
    ".env", ".apk", ".aab", ".zip", ".exe", ".jks", ".keystore", ".p12", ".pfx"
}

EXCLUDED_FILES = {
    "cbi_ultimate.db", "cbi_ultimate.db-wal", "cbi_ultimate.db-shm",
    "otp_codes.db", ".relay.private.env", ".licenses.private.env",
    "mahan",  # Existing extensionless PKCS#12 signing keystore; keep it local.
}


def compute_sha256(filepath: Path) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def package_release(version: str = "2.0.1", edition: str = "CLIENT"):
    edition = edition.strip().upper()
    if edition not in {"ADMIN", "CLIENT"}:
        raise ValueError("Edition must be ADMIN or CLIENT")
    DOWNLOADS_DIR.mkdir(parents=True,exist_ok=True)
    zip_filename = f"mahanbot-{version}.zip"
    latest_filename = "mahanbot-latest.zip"
    
    target_zip = DOWNLOADS_DIR / zip_filename
    latest_zip = DOWNLOADS_DIR / latest_filename

    print(f"[*] Packaging MAHANBOT v{version} release...")

    total_files = 0
    total_size = 0

    with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(ROOT_DIR):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".")]
            if edition == 'CLIENT':
                dirs[:] = [d for d in dirs if d not in {'android','deploy','packaging','tests'}]

            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in EXCLUDED_EXTS or file in EXCLUDED_FILES:
                    continue
                if file.startswith('.env') and file != '.env.example':
                    continue
                if ('.db' in file.lower() and file != 'cbi_clean_template.db'):
                    continue

                full_path = Path(root) / file
                # Don't package files inside downloads dir itself
                if DOWNLOADS_DIR in full_path.parents:
                    continue

                rel_path = full_path.relative_to(ROOT_DIR)
                if rel_path.as_posix() in {'license_server/app.py', 'license_server/store.py'}:
                    continue
                if edition == 'CLIENT':
                    if rel_path.as_posix() == 'run_admin.bat':
                        continue
                    if rel_path.parts[0] == 'scripts' and file not in {
                        'create_clean_distribution_db.py', 'check_relay_connection.py'
                    }:
                        continue
                if rel_path.parts[:2] == ('static', 'live_browser') and file != '.gitkeep':
                    continue
                zf.write(full_path, arcname=str(rel_path))
                total_files += 1
                total_size += full_path.stat().st_size

        # Every fresh customer install starts in CLIENT mode. Never copy the manager's .env.
        bootstrap = (f'MAHANBOT_EDITION={edition}\n'
                     f'MAHANBOT_REQUIRE_LICENSE={"true" if edition == "CLIENT" else "false"}\n'
                     'MAHANBOT_LICENSE_SERVER=https://otp.mahanvip.ir/api/licensing/v1\n')
        zf.writestr('.env', bootstrap)
        total_files += 1

    # Copy to latest.zip
    import shutil
    shutil.copy2(target_zip, latest_zip)

    sha = compute_sha256(target_zip)
    size_mb = target_zip.stat().st_size / (1024 * 1024)

    print(f"[OK] Created: {target_zip.name} ({size_mb:.2f} MB, {total_files} files)")
    print(f"[OK] Created: {latest_zip.name}")
    print(f"[SHA256] {sha}")

    # Write release-metadata.json
    meta = {
        "version": version,
        "release_date": "2026-10-22",
        "archive": zip_filename,
        "size_mb": round(size_mb, 2),
        "sha256": sha,
        "total_files": total_files,
        "edition": edition
    }
    (DOWNLOADS_DIR / "release-metadata.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2), encoding="utf-8")
    return target_zip, sha


if __name__ == "__main__":
    ver = sys.argv[1] if len(sys.argv) > 1 else "2.0.1"
    package_release(ver)
