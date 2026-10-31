"""
routes/browser_health.py — Browser Profile & Session Health Management Endpoints for MAHANBOT.
Allows administrators to inspect, diagnose, repair, and manage applicant browser profiles.
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from core.deps import require_api_key
from core.browser_profile_health import BrowserProfileHealthManager, ProfileHealthStatus
from core.browser_profile_recovery import BrowserProfileRecoveryManager
from database import DBHandler
from live_sessions import LIVE_SESSIONS

router = APIRouter(prefix="/browser-health", tags=["browser-health"])


class ActionResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


@router.get("", response_model=List[Dict[str, Any]])
def list_profile_health(user: Dict[str, Any] = Depends(require_api_key)):
    """List browser profile health status for all registered applicants."""
    reports = BrowserProfileHealthManager.list_all_reports()
    return [r.to_dict() for r in reports]


@router.get("/{nid}", response_model=Dict[str, Any])
def get_applicant_profile_health(nid: str, user: Dict[str, Any] = Depends(require_api_key)):
    """Get detailed health diagnostic report for a specific applicant."""
    report = BrowserProfileHealthManager.inspect_profile(nid)
    return report.to_dict()


@router.post("/{nid}/check", response_model=Dict[str, Any])
def trigger_health_check(nid: str, user: Dict[str, Any] = Depends(require_api_key)):
    """Trigger an immediate real-time diagnostic check on applicant's profile."""
    report = BrowserProfileHealthManager.inspect_profile(nid)
    BrowserProfileRecoveryManager.audit_log(
        nid=nid,
        action="manual_health_check",
        performed_by=str(user.get("username", "admin")),
        result="success",
        details=f"Status: {report.health_status.value}",
    )
    return report.to_dict()


@router.post("/{nid}/close-browser", response_model=ActionResponse)
def close_applicant_browser(nid: str, user: Dict[str, Any] = Depends(require_api_key)):
    """Gracefully close any running browser instance belonging to the applicant."""
    owner = LIVE_SESSIONS.get(nid)
    closed = False
    if owner and owner.page and not owner.page.is_closed():
        try:
            owner.page.close()
            closed = True
        except Exception:
            pass

    BrowserProfileRecoveryManager.audit_log(
        nid=nid,
        action="close_browser",
        performed_by=str(user.get("username", "admin")),
        result="success" if closed else "no_active_browser",
        details="Browser closed by administrator" if closed else "No active page found",
    )
    return ActionResponse(
        success=True,
        message="مرورگر متقاضی با موفقیت بسته شد." if closed else "مرورگر فعالی برای این متقاضی یافت نشد.",
    )


@router.post("/{nid}/reauth", response_model=ActionResponse)
def trigger_reauth(nid: str, user: Dict[str, Any] = Depends(require_api_key)):
    """Initiate official re-authentication workflow for an applicant."""
    ok, msg = BrowserProfileRecoveryManager.initiate_reauth_workflow(nid)
    BrowserProfileRecoveryManager.audit_log(
        nid=nid,
        action="initiate_reauth",
        performed_by=str(user.get("username", "admin")),
        result="success" if ok else "failed",
        details=msg,
    )
    return ActionResponse(success=ok, message=msg)


@router.post("/{nid}/backup", response_model=ActionResponse)
def backup_applicant_profile(nid: str, user: Dict[str, Any] = Depends(require_api_key)):
    """Create a secured snapshot backup of the applicant's profile."""
    ok, path, msg = BrowserProfileRecoveryManager.backup_profile(nid)
    BrowserProfileRecoveryManager.audit_log(
        nid=nid,
        action="backup_profile",
        performed_by=str(user.get("username", "admin")),
        result="success" if ok else "failed",
        details=msg,
    )
    return ActionResponse(success=ok, message=msg, data={"backup_path": path})


@router.post("/{nid}/purge-cookies", response_model=ActionResponse)
def purge_applicant_cookies(nid: str, user: Dict[str, Any] = Depends(require_api_key)):
    """Safely purge poisoned/expired CBI cookies while preserving profile structure."""
    p_dir = BrowserProfileHealthManager.get_applicant_profile_dir(nid)
    ok, count, msg = BrowserProfileRecoveryManager.purge_stale_cbi_cookies(p_dir, force_all_cbi=True)
    BrowserProfileRecoveryManager.audit_log(
        nid=nid,
        action="purge_cookies",
        performed_by=str(user.get("username", "admin")),
        result="success" if ok else "failed",
        details=msg,
    )
    return ActionResponse(success=ok, message=msg, data={"purged_count": count})


@router.post("/{nid}/fresh-profile", response_model=ActionResponse)
def create_fresh_applicant_profile(nid: str, user: Dict[str, Any] = Depends(require_api_key)):
    """Admin-confirmed action: Backs up old profile and initializes a pristine profile."""
    # Ensure user has admin privileges
    if not user.get("is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators are authorized to recreate browser profiles.",
        )
    ok, msg = BrowserProfileRecoveryManager.create_fresh_profile(nid, backup_first=True)
    BrowserProfileRecoveryManager.audit_log(
        nid=nid,
        action="create_fresh_profile",
        performed_by=str(user.get("username", "admin")),
        result="success" if ok else "failed",
        details=msg,
    )
    return ActionResponse(success=ok, message=msg)


@router.get("/audit-logs", response_model=List[Dict[str, Any]])
def get_repair_audit_logs(
    nid: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: Dict[str, Any] = Depends(require_api_key),
):
    """Retrieve audit trail of administrative profile repair operations."""
    return DBHandler.get_profile_repair_audit_logs(nid=nid, limit=limit)
