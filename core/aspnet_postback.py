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

QUOTA_POLL_INTERVAL_SEC = 3.5

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


def wait_for_aspnet_idle(page: Any, bot: Any = None, timeout_ms: int = 8000) -> bool:
    if bot is not None and hasattr(bot, "wait_for_aspnet_postback"):
        try:
            return bool(bot.wait_for_aspnet_postback(page, timeout_ms=timeout_ms))
        except Exception:
            pass
    if not page:
        return False
    try:
        page.wait_for_function(
            """() => {
                try {
                    if (window.Sys && Sys.WebForms && Sys.WebForms.PageRequestManager) {
                        const prm = Sys.WebForms.PageRequestManager.getInstance();
                        if (prm && prm.get_isInAsyncPostBack()) return false;
                    }
                    const up = document.getElementById('ctl00_ContentPlaceHolder1_updateProgress');
                    if (up && up.style && up.style.display !== 'none' && up.offsetParent !== null) return false;
                    const wd = document.getElementById('waitDiv');
                    if (wd && wd.style && wd.style.visibility === 'visible') return false;
                    return document.readyState === 'complete' || document.readyState === 'interactive';
                } catch (e) {
                    return true;
                }
            }""",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


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

    previous_vs = read_viewstate(page)
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

    wait_for_aspnet_idle(page, bot=bot, timeout_ms=min(timeout_ms, 8000))
    wait_for_viewstate_change(page, previous_vs, timeout_ms=min(3000, timeout_ms))
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
