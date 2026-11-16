"""
Shared Playwright Browser pool for MAHANBOT.

- One Chromium Browser (non-persistent launches) reused across applicants.
- Isolated BrowserContext per applicant.
- Resource guards: abort font/media/images that are not CAPTCHA/ASP.NET essentials.
- Persistent chrome_profiles/{nid} still use launch_persistent_context (cannot share Browser),
  but attach the same resource guards.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("mahanbot.browser_pool")

_POOL_LOCK = threading.RLock()
_SHARED_PLAYWRIGHT = None
_SHARED_BROWSER = None
_SHARED_LAUNCH_KEY: Optional[str] = None
_CONTEXT_REFCOUNT = 0

# Block heavy assets; keep CAPTCHA / BotDetect / ASP.NET script+xhr+document.
_BLOCKED_RESOURCE_TYPES = {"font", "media"}
_CAPTCHA_URL_HINTS = ("captcha", "botdetect", "bdc_", "challenge", "getimage", "gdx")


def get_shared_playwright():
    """One process-wide sync_playwright() driver."""
    global _SHARED_PLAYWRIGHT
    with _POOL_LOCK:
        if _SHARED_PLAYWRIGHT is not None:
            return _SHARED_PLAYWRIGHT
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("playwright is not installed in this environment") from exc
        _SHARED_PLAYWRIGHT = sync_playwright().start()
        return _SHARED_PLAYWRIGHT


def _launch_key(launch_options: Dict[str, Any]) -> str:
    channel = launch_options.get("channel") or ""
    headless = bool(launch_options.get("headless", True))
    proxy = ""
    p = launch_options.get("proxy") or {}
    if isinstance(p, dict):
        proxy = str(p.get("server") or "")
    return f"h={headless}|c={channel}|p={proxy}"


def _should_abort_request(request) -> bool:
    try:
        rtype = str(getattr(request, "resource_type", "") or "").lower()
        url = str(getattr(request, "url", "") or "").lower()
    except Exception:
        return False
    if any(h in url for h in _CAPTCHA_URL_HINTS):
        return False
    # Keep stylesheets (ASP.NET UpdatePanel layout) and scripts
    if rtype in _BLOCKED_RESOURCE_TYPES:
        return True
    # Block large decorative images, but never captcha
    if rtype == "image":
        if any(h in url for h in _CAPTCHA_URL_HINTS):
            return False
        # Allow tiny icons / data URIs used by the form
        if url.startswith("data:"):
            return False
        # Block common static image CDNs / extensions that are not captcha
        if any(url.endswith(ext) for ext in (".woff", ".woff2", ".ttf", ".otf", ".mp4", ".webm", ".mp3")):
            return True
        # Soft-block non-cbi decorative images
        if "ve.cbi.ir" not in url and "localhost" not in url and "127.0.0.1" not in url:
            return True
    return False


def install_resource_guards(context: Any) -> None:
    """Attach route handler that aborts fonts/media and non-essential images."""
    if context is None:
        return
    if getattr(context, "_mahan_resource_guards", False):
        return

    def _handler(route):
        try:
            if _should_abort_request(route.request):
                return route.abort()
        except Exception:
            pass
        try:
            return route.continue_()
        except Exception:
            try:
                return route.abort()
            except Exception:
                return None

    try:
        context.route("**/*", _handler)
        setattr(context, "_mahan_resource_guards", True)
    except Exception as exc:
        logger.debug("resource guards not attached: %s", exc)


def get_shared_browser(playwright: Any, browser_type: Any, launch_options: Dict[str, Any]) -> Any:
    """
    Return a process-wide shared Chromium Browser for non-persistent contexts.
    Marks browser with _mahan_shared=True so close_browser can spare it.
    """
    global _SHARED_PLAYWRIGHT, _SHARED_BROWSER, _SHARED_LAUNCH_KEY
    key = _launch_key(launch_options)
    with _POOL_LOCK:
        alive = False
        try:
            alive = _SHARED_BROWSER is not None and getattr(_SHARED_BROWSER, "is_connected", lambda: True)()
        except Exception:
            alive = False
        if alive and _SHARED_LAUNCH_KEY == key:
            return _SHARED_BROWSER

        # Close previous shared browser if launch fingerprint changed
        if _SHARED_BROWSER is not None:
            try:
                _SHARED_BROWSER.close()
            except Exception:
                pass
            _SHARED_BROWSER = None

        opts = dict(launch_options)
        # Persistent-only keys must not reach browser.launch
        opts.pop("user_data_dir", None)
        browser = browser_type.launch(**opts)
        setattr(browser, "_mahan_shared", True)
        _SHARED_BROWSER = browser
        _SHARED_PLAYWRIGHT = playwright
        _SHARED_LAUNCH_KEY = key
        logger.info("shared browser launched key=%s", key)
        return browser


def acquire_context(
    playwright: Any,
    browser_type: Any,
    launch_options: Dict[str, Any],
    *,
    user_data_dir: Optional[str] = None,
    viewport: Optional[Dict[str, int]] = None,
) -> Tuple[Any, Any]:
    """
    Returns (browser_or_none, context).
    - persistent profile → (None, persistent_context)
    - otherwise → (shared_browser, new_context)
    """
    viewport = viewport or {"width": 900, "height": 620}
    if user_data_dir:
        context = browser_type.launch_persistent_context(
            user_data_dir,
            **launch_options,
            viewport=viewport,
        )
        install_resource_guards(context)
        setattr(context, "_mahan_shared_browser", False)
        return None, context

    browser = get_shared_browser(playwright, browser_type, launch_options)
    context = browser.new_context(viewport=viewport)
    install_resource_guards(context)
    setattr(context, "_mahan_shared_browser", True)
    return browser, context


def release_context(context: Any, page: Any = None, *, close_shared_browser: bool = False) -> None:
    """Close page/context; keep shared Browser unless explicitly requested."""
    try:
        if page is not None:
            page.close()
    except Exception:
        pass
    shared = bool(getattr(context, "_mahan_shared_browser", False)) if context is not None else False
    try:
        if context is not None:
            context.close()
    except Exception:
        pass
    if close_shared_browser:
        shutdown_shared_browser()


def shutdown_shared_browser() -> None:
    global _SHARED_BROWSER, _SHARED_PLAYWRIGHT, _SHARED_LAUNCH_KEY
    with _POOL_LOCK:
        if _SHARED_BROWSER is not None:
            try:
                _SHARED_BROWSER.close()
            except Exception:
                pass
        _SHARED_BROWSER = None
        _SHARED_LAUNCH_KEY = None
        # Playwright lifecycle is owned by launch_browser/close_browser callers
