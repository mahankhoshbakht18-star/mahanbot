"""
routes/settings.py — Settings, browser profile, allowed domains, saved pages.
"""
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from database import DBHandler
from core.deps import require_api_key
from core.helpers import load_browser_profile_settings, save_browser_profile_settings
from core.models import (
    BrowserProfileRequest,
    SettingsRequest,
    AllowedDomainsRequest,
    AllowlistCheckRequest,
)
from browser_launcher import (
    BrowserLaunchError,
    close_browser,
    launch_browser,
    merge_browser_profiles,
)
from allowlist import get_allowlist_snapshot, is_url_allowed, normalize_domain_list
from bot_select_bank import repair_saved_pages

router = APIRouter()


# ── Browser profile ───────────────────────────────────────────────────
@router.get("/settings/browser-profile")
def get_browser_profile():
    return load_browser_profile_settings()


@router.put("/settings/browser-profile")
def update_browser_profile(req: BrowserProfileRequest):
    data = req.dict(exclude_unset=True) if hasattr(req, "dict") else req.model_dump(exclude_unset=True)
    try:
        merged = merge_browser_profiles(load_browser_profile_settings(), data)
    except BrowserLaunchError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.to_dict())
    return save_browser_profile_settings(merged)


# ── App settings ──────────────────────────────────────────────────────
@router.get("/settings")
def get_settings():
    return DBHandler.get_config()


@router.post("/settings")
def update_settings(req: SettingsRequest, _: bool = Depends(require_api_key)):
    payload = req.dict(exclude_unset=True) if hasattr(req, "dict") else req.model_dump(exclude_unset=True)
    aliases = {
        "bank_list_check_interval": ("bank_list_refresh_seconds", "bank_refresh_interval"),
        "bank_capacity_check_interval": ("capacity_check_interval_seconds", "bank_capacity_recheck_seconds"),
        "loan_browser_width": ("browser_window_width",),
        "loan_browser_height": ("browser_window_height",),
        "loan_browser_position": ("browser_window_position",),
        "loan_browser_maximized": ("browser_start_maximized",),
        "loan_browser_always_on_top": ("browser_always_on_top",),
    }
    for canonical, legacy in aliases.items():
        supplied = next((key for key in (canonical, *legacy) if key in payload and payload[key] is not None), None)
        if supplied:
            for key in (canonical, *legacy):
                payload[key] = payload[supplied]
    payload["captcha_mode"] = "model"
    if "automation_mode" in payload and payload["automation_mode"] not in {"safe", "normal", "fast"}:
        raise HTTPException(status_code=422, detail="Invalid automation mode")
    for key, low, high in (
        ("captcha_poll_interval", 0.05, 2.0),
        ("captcha_retry_delay", 0.0, 10.0),
        ("bank_refresh_interval", 1.0, 300.0),
        ("bank_list_refresh_seconds", 1.0, 300.0),
        ("bank_capacity_recheck_seconds", 1.0, 600.0),
        ("retry_delay", 0.0, 30.0),
        ("proxy_health_check_timeout", 1.0, 30.0),
        ("browser_page_zoom", 0.25, 3.0),
    ):
        if key in payload and payload[key] is not None and not low <= float(payload[key]) <= high:
            raise HTTPException(status_code=422, detail=f"{key} out of range")
    for key, low, high in (
        ("retry_count", 1, 20),
        ("captcha_max_retries", 1, 10),
        ("captcha_manual_timeout", 10, 3600),
        ("otp_validity_seconds", 30, 600),
        ("otp_retry_limit", 1, 20),
        ("default_timeout_ms", 250, 60000),
        ("navigation_timeout_ms", 1000, 120000),
        ("browser_window_width", 360, 3840),
        ("browser_window_height", 320, 2160),
        ("max_concurrency", 1, 32),
    ):
        if key in payload and payload[key] is not None and not low <= int(payload[key]) <= high:
            raise HTTPException(status_code=422, detail=f"{key} out of range")

    if "bank_list_refresh_seconds" in payload and payload["bank_list_refresh_seconds"] is not None:
        payload["bank_refresh_interval"] = float(payload["bank_list_refresh_seconds"])
    elif "bank_refresh_interval" in payload and payload["bank_refresh_interval"] is not None:
        payload["bank_list_refresh_seconds"] = float(payload["bank_refresh_interval"])

    if "auto_final_submit" in payload and payload["auto_final_submit"] is not None:
        payload["final_submit"] = bool(payload["auto_final_submit"])
    elif "final_submit" in payload and payload["final_submit"] is not None:
        payload["auto_final_submit"] = bool(payload["final_submit"])

    current = DBHandler.get_config()
    current.update(payload)
    updated = DBHandler.update_config(current)

    # Dynamic scaling of job workers if max_concurrency updated
    if "max_concurrency" in payload:
        import core.state as _state
        if _state.JOB_QUEUE is not None:
            _state.JOB_QUEUE.set_max_concurrency(payload["max_concurrency"])

    # Reload proxy pool cache if proxy_list updated
    if "proxy_list" in payload:
        from proxy_manager import get_proxy_pool
        get_proxy_pool().load_from_text(str(payload.get("proxy_list") or ""))

    return {"status": "ok", "config": updated}


# ── Proxy endpoints ──────────────────────────────────────────────────
@router.post("/api/proxies/test")
def test_proxies_endpoint(req_body: Optional[Dict[str, Any]] = None):
    """Test proxy pool connectivity and latency in parallel."""
    from proxy_manager import get_proxy_pool
    pool = get_proxy_pool()
    proxy_text = ""
    if req_body and "proxy_list" in req_body:
        proxy_text = str(req_body["proxy_list"] or "")
    else:
        cfg = DBHandler.get_config()
        proxy_text = str(cfg.get("proxy_list") or "")
    pool.load_from_text(proxy_text)
    results = pool.test_all()
    return {
        "status": "ok",
        "total": len(results),
        "healthy_count": sum(1 for r in results if r["healthy"]),
        "results": results,
    }


@router.get("/api/proxies/pool")
def get_proxy_pool_status():
    from proxy_manager import get_proxy_pool
    cfg = DBHandler.get_config()
    pool = get_proxy_pool()
    pool.load_from_text(str(cfg.get("proxy_list") or ""))
    return pool.get_pool_status()



# ── Saved pages ───────────────────────────────────────────────────────
@router.post("/saved-pages/repair")
def repair_saved_pages_endpoint(_: bool = Depends(require_api_key)):
    from routes.saved_pages import repair_all_saved_pages_endpoint
    return repair_all_saved_pages_endpoint(True)


# ── Allowed domains ───────────────────────────────────────────────────
@router.get("/settings/allowed-domains")
def get_allowed_domains_settings():
    return get_allowlist_snapshot()


@router.put("/settings/allowed-domains")
def update_allowed_domains(req: AllowedDomainsRequest, _: bool = Depends(require_api_key)):
    normalized, invalid = normalize_domain_list(req.domains)
    if invalid:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_domains", "details": {"invalid_domains": invalid}},
        )
    DBHandler.update_setting("allowed_domains", normalized)
    return get_allowlist_snapshot()


@router.post("/settings/allowed-domains/check")
def check_allowed_domain(req: AllowlistCheckRequest):
    allowed, host, effective = is_url_allowed(req.url)
    return {"allowed": allowed, "host": host, "effective": effective}


# ── Browser test launch ──────────────────────────────────────────────
@router.post("/browser/test-launch")
def test_browser_launch(_: bool = Depends(require_api_key)):
    profile = load_browser_profile_settings()
    playwright = browser = context = page = None
    try:
        playwright, browser, context, page = launch_browser(profile)
        return {"status": "ok"}
    except BrowserLaunchError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.to_dict())
    finally:
        close_browser(playwright, browser, context, page)
