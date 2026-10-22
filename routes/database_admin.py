"""
routes/database_admin.py — Database health check, diagnostics, backup, and safe migration API.
Provides administration tools for MAHANBOT database consistency and applicant settings.
"""
import glob
import os
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from core.deps import require_api_key
from core.db_migration import (
    check_database_health,
    create_database_backup,
    restore_database_backup,
    run_database_migration,
)
from database import DB_PATH

router = APIRouter(prefix="/api/database", tags=["database_admin"])


class RepairRequest(BaseModel):
    dry_run: bool = False


class RestoreRequest(BaseModel):
    backup_filename: str


@router.get("/health")
def get_db_health(_: bool = Depends(require_api_key)):
    """Runs integrity check, foreign key check, and scans for applicant location/bank inconsistencies."""
    try:
        report = check_database_health(DB_PATH)
        return {"status": "ok", "health": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database health check failed: {str(e)}")


@router.post("/backup")
def trigger_db_backup(_: bool = Depends(require_api_key)):
    """Creates an immediate full copy backup of the active database."""
    try:
        backup_path = create_database_backup(DB_PATH, prefix="manual_backup")
        filename = os.path.basename(backup_path)
        size_bytes = os.path.getsize(backup_path)
        return {
            "status": "ok",
            "message": f"پشتیبان‌گیری با موفقیت انجام شد: {filename}",
            "filename": filename,
            "size_bytes": size_bytes,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup creation failed: {str(e)}")


@router.get("/backups")
def list_db_backups(_: bool = Depends(require_api_key)):
    """Lists all SQLite backups available on disk with size and timestamp."""
    try:
        base_dir = os.path.dirname(os.path.abspath(DB_PATH))
        backup_dir = os.path.join(base_dir, "backups")
        patterns = [
            os.path.join(backup_dir, "*.db"),
            os.path.join(backup_dir, "*.bak"),
            os.path.join(base_dir, "*.bak"),
        ]
        files = []
        for pat in patterns:
            for filepath in glob.glob(pat):
                fname = os.path.basename(filepath)
                if fname == os.path.basename(DB_PATH):
                    continue  # Skip active DB
                stat = os.stat(filepath)
                files.append({
                    "filename": fname,
                    "filepath": filepath,
                    "size_bytes": stat.st_size,
                    "modified_at": stat.st_mtime,
                })
        files.sort(key=lambda x: x["modified_at"], reverse=True)
        return {"status": "ok", "backups": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Listing backups failed: {str(e)}")


@router.post("/repair")
def repair_db(req: RepairRequest, _: bool = Depends(require_api_key)):
    """
    Applies non-destructive database repair and synchronization:
    Creates an automated pre-repair backup, runs migration, seeds reference tables,
    and returns updated health status.
    """
    try:
        if req.dry_run:
            health = check_database_health(DB_PATH)
            return {
                "status": "ok",
                "dry_run": True,
                "inconsistencies": health.get("inconsistencies", []),
                "message": "حالت پیش‌نمایش: هیچ تغییری اعمال نشد.",
            }

        migration_result = run_database_migration(DB_PATH, force=True)
        refreshed_health = check_database_health(DB_PATH)
        return {
            "status": "ok",
            "dry_run": False,
            "migration": migration_result,
            "health": refreshed_health,
            "message": "دیتابیس و تنظیمات متقاضیان با موفقیت یکپارچه‌سازی و تعمیر شدند.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Repair failed: {str(e)}")


@router.post("/restore")
def restore_db(req: RestoreRequest, _: bool = Depends(require_api_key)):
    """Restores database from a selected backup file after taking a safety backup."""
    base_dir = os.path.dirname(os.path.abspath(DB_PATH))
    target_backup = os.path.join(base_dir, os.path.basename(req.backup_filename))
    if not os.path.exists(target_backup):
        raise HTTPException(status_code=404, detail="فایل پشتیبان مشخص‌شده یافت نشد.")

    try:
        restore_database_backup(target_backup, DB_PATH)
        return {
            "status": "ok",
            "message": f"دیتابیس از فایل {req.backup_filename} با موفقیت بازیابی شد.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {str(e)}")
