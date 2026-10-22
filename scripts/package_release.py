"""
scripts/package_release.py — Packages MAHANBOT 2.0 for Server Deployment & Direct Download.
Creates clean zip archives ready to be uploaded to /downloads/ on otp.mahanvip.ir
"""
import os
import sys
import zipfile
import hashlib
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DOWNLOADS_DIR = ROOT_DIR / "downloads"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

EXCLUDED_DIRS = {
    ".git", ".github", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    ".system_generated", "updates_staging", "tmp", "temp", "node_modules"
}

EXCLUDED_EXTS = {
    ".pyc", ".pyo", ".pyd", ".log"
}

EXCLUDED_FILES = {
    "cbi_ultimate.db", "cbi_ultimate.db-wal", "cbi_ultimate.db-shm",
    "otp_codes.db", ".relay.private.env"
}


def compute_sha256(filepath: Path) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def package_release(version: str = "2.0.1"):
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

            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in EXCLUDED_EXTS or file in EXCLUDED_FILES:
                    continue

                full_path = Path(root) / file
                # Don't package files inside downloads dir itself
                if DOWNLOADS_DIR in full_path.parents:
                    continue

                rel_path = full_path.relative_to(ROOT_DIR)
                zf.write(full_path, arcname=str(rel_path))
                total_files += 1
                total_size += full_path.stat().st_size

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
        "total_files": total_files
    }
    (DOWNLOADS_DIR / "release-metadata.json").write_text(str(meta), encoding="utf-8")
    return target_zip, sha


if __name__ == "__main__":
    ver = sys.argv[1] if len(sys.argv) > 1 else "2.0.1"
    package_release(ver)
