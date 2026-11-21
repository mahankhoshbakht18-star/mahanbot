"""
ASP.NET WebForms helpers for MAHANBOT.

Domain truth (ve.cbi.ir):
- Bank quotas are PROVINCE-level (province chosen at registration).
- On Page 3, changing ddlCity does NOT refresh bank quotas.
- When preferred bank is missing on Page 3, the official refresh is page.reload()
  (or GET re-entry to SelectBnkShb.aspx if the document was loaded via POST).
- Never use ddlCity / synthetic bank postback as a quota sniper.
- page.reload() remains FORBIDDEN on OTP / national-id / UNKNOWN recovery paths.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple
from urllib.parse import urlsplit

_DISARM_JS = """() => {
    try {
        window.onbeforeunload = null;
        window.CloseSession = function () {};
        if (typeof window.onunload !== 'undefined') window.onunload = null;
        if (window.jQuery) {
            try { jQuery(window).off('beforeunload'); } catch (e) {}
            try { jQuery(window).off('unload'); } catch (e) {}
        }
    } catch (e) {}
}"""

_READ_VIEWSTATE_JS = """() => {
    const el = document.getElementById('__VIEWSTATE');
    return el ? String(el.value || '') : '';
}"""


def read_viewstate(page: Any) -> str:
    if not page:
        return ""
    try:
        return str(page.evaluate(_READ_VIEWSTATE_JS) or "")
    except Exception:
        return ""


def disarm_logout_trap(page: Any) -> bool:
    if not page:
        return False
    try:
        page.evaluate(_DISARM_JS)
        return True
    except Exception:
        return False


def wait_for_viewstate_change(page: Any, previous: str, timeout_ms: int = 8000) -> bool:
    if not page:
        return False
    try:
        page.wait_for_function(
            """(prev) => {
                const el = document.getElementById('__VIEWSTATE');
                const cur = el ? String(el.value || '') : '';
                if (!prev) return !!cur;
                return cur && cur !== prev;
            }""",
            arg=previous or "",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


# The one ASP.NET "postback finished" check. Runs inside the page so a single
# round-trip covers both phases:
#   1. up to graceMs: wait for the postback to START (busy, or __VIEWSTATE changed);
#      returns at once when it starts, so the grace only costs time when nothing posts.
#   2. until timeoutMs: wait for the page to be idle again.
_POSTBACK_WAIT_JS = """async ({prev, graceMs, timeoutMs, pollMs}) => {
    const busy = () => {
        try {
            if (window.Sys && Sys.WebForms && Sys.WebForms.PageRequestManager) {
                const prm = Sys.WebForms.PageRequestManager.getInstance();
                if (prm && prm.get_isInAsyncPostBack()) return true;
            }
            const up = document.getElementById('ctl00_ContentPlaceHolder1_updateProgress');
            if (up && up.style && up.style.display !== 'none' && up.offsetParent !== null) return true;
            const wd = document.getElementById('waitDiv');
            if (wd && wd.style && wd.style.visibility === 'visible') return true;
            return document.readyState === 'loading';
        } catch (e) { return false; }
    };
    const viewstate = () => { const el = document.getElementById('__VIEWSTATE'); return el ? String(el.value || '') : ''; };
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const start = Date.now();
    while (Date.now() - start < graceMs && !busy() && !(prev !== null && viewstate() !== prev)) await sleep(Math.min(pollMs, 10));
    while (busy()) {
        if (Date.now() - start > timeoutMs) return 'timeout';
        await sleep(pollMs);
    }
    return 'idle';
}"""


def wait_for_postback(
    page: Any,
    settings: Any = None,
    *,
    previous_viewstate: Optional[str] = None,
    grace_ms: Optional[int] = None,
    timeout_ms: Optional[int] = None,
    poll_ms: int = 25,
) -> bool:
    """Wait for the postback an action just triggered. False only on timeout."""
    if not page:
        return False
    from core.timings import timing
    grace = timing(settings, "bank_postback_start_grace_ms") if grace_ms is None else grace_ms
    limit = timing(settings, "bank_postback_timeout_ms") if timeout_ms is None else timeout_ms
    args = {"prev": previous_viewstate, "graceMs": int(max(0, grace)),
            "timeoutMs": int(max(0, limit)), "pollMs": int(max(5, poll_ms))}
    try:
        return page.evaluate(_POSTBACK_WAIT_JS, args) != "timeout"
    except Exception:
        # A full (non-async) postback navigates and destroys the evaluate context.
        try:
            page.wait_for_load_state("domcontentloaded", timeout=int(max(1000, limit)))
        except Exception:
            pass
        return True


def wait_for_aspnet_idle(page: Any, bot: Any = None, timeout_ms: int = 8000) -> bool:
    """Wait until no postback is running (no start grace)."""
    del bot
    return wait_for_postback(page, grace_ms=0, timeout_ms=timeout_ms)


def _safe_page3_get_url(page: Any, bot: Any = None) -> Optional[str]:
    try:
        target = urlsplit(str(getattr(page, "url", "") or ""))
    except Exception:
        return None
    if target.hostname not in {"ve.cbi.ir", "127.0.0.1", "localhost"}:
        return None
    if target.scheme not in {"http", "https"}:
        return None
    entry = getattr(bot, "_workflow_entry_path", "/SelectBnkShb.aspx") if bot is not None else "/SelectBnkShb.aspx"
    if not str(entry).startswith("/"):
        entry = "/SelectBnkShb.aspx"
    return f"{target.scheme}://{target.netloc}{entry}"


def refresh_page3_banks_via_official_reload(
    page: Any,
    bot: Any = None,
    timeout_ms: int = 15000,
    last_navigation_method: str = "GET",
) -> Tuple[bool, str]:
    """
    Official Page-3 bank-list refresh.

    Province quotas only update after a real page refresh / GET re-entry.
    ddlCity / ddlBankName synthetic postbacks do NOT refresh province quotas.
    """
    if not page:
        return False, "no_page"
    try:
        if getattr(page, "is_closed", lambda: False)():
            return False, "target_closed"
    except Exception:
        return False, "target_closed"

    if not disarm_logout_trap(page):
        # Still attempt reload; trap disarm failure is non-fatal on some mocks
        pass

    try:
        method = str(last_navigation_method or "GET").upper()
        if method == "POST":
            target_url = _safe_page3_get_url(page, bot=bot)
            if not target_url:
                return False, "unsafe_navigation_target"
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
            reason = "official_get_reentry"
        else:
            page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
            reason = "official_page_reload"
    except Exception as exc:
        return False, str(exc)

    # reload/goto already returned a fresh document; a GET of the same page can keep
    # an identical __VIEWSTATE, so waiting for it to change only burned up to 3s per reload.
    wait_for_aspnet_idle(page, timeout_ms=min(timeout_ms, 8000))
    return True, reason


# Backward-compatible alias — MUST NOT use ddlCity. Routes to official reload.
def refresh_quota_via_dopostback(
    page: Any,
    bot: Any = None,
    timeout_ms: int = 15000,
) -> Tuple[bool, str]:
    """Deprecated name. Province quota refresh is official Page-3 reload, not postback."""
    method = "GET"
    try:
        flow = getattr(bot, "_loan_workflow", None) or getattr(bot, "workflow", None)
        if flow is not None:
            method = getattr(flow, "last_navigation_method", "GET") or "GET"
    except Exception:
        method = "GET"
    return refresh_page3_banks_via_official_reload(
        page, bot=bot, timeout_ms=timeout_ms, last_navigation_method=method
    )
