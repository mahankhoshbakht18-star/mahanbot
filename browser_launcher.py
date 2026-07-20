# browser_launcher.py
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import sync_playwright

from allowlist import is_url_allowed, get_allowed_domains

ALLOWED_BROWSERS = {"chromium", "firefox", "webkit"}
ALLOWED_SYSTEM_CHANNELS = {
    "chrome",
    "chrome-beta",
    "chrome-dev",
    "chrome-canary",
    "msedge",
    "msedge-beta",
    "msedge-dev",
    "msedge-canary",
}
DEFAULT_TIMEOUT_MS = 30000
DEFAULT_VIEWPORT = {"width": 1280, "height": 720}
PROFILE_ROOT = Path(os.getenv("MAHANBOT_PROFILE_ROOT", Path.cwd() / "browser_profiles")).resolve()
DEFAULT_HEALTHCHECK_URL = "http://127.0.0.1:8000/static/healthcheck.html"

HARD_ALLOWED_URLS = {
    "https://ve.cbi.ir/Register.aspx",
    "https://ve.cbi.ir/SelectBnkShb.aspx",
    "https://ve.cbi.ir/SelEditCase.aspx",
    "https://ve.cbi.ir/EditShb.aspx",
    "https://ve.cbi.ir/TasReqDelete.aspx",
    "https://ve.cbi.ir/TasTrace.aspx",
    "https://ve.cbi.ir/GetTraceCD.aspx",
    "https://ve.cbi.ir/DefaultVE.aspx",
}


class BrowserLaunchError(Exception):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


def _find_system_chromium() -> Optional[str]:
    configured = str(os.getenv("MAHANBOT_BROWSER_EXECUTABLE") or "").strip()
    if configured:
        candidate = Path(os.path.expandvars(configured)).expanduser()
        if candidate.is_file():
            return str(candidate)
        raise BrowserLaunchError(
            "browser_executable_missing",
            "Configured browser executable does not exist",
            {"path": str(candidate)},
        )

    if os.name != "nt":
        return None

    roots = [
        os.getenv("PROGRAMFILES"),
        os.getenv("PROGRAMFILES(X86)"),
        os.getenv("LOCALAPPDATA"),
    ]
    relative_paths = [
        Path("Microsoft/Edge/Application/msedge.exe"),
        Path("Google/Chrome/Application/chrome.exe"),
    ]
    for root in roots:
        if not root:
            continue
        for relative in relative_paths:
            candidate = Path(root) / relative
            if candidate.is_file():
                return str(candidate)
    return None


def get_default_browser_profile() -> Dict[str, Any]:
    return {
        "browser": "chromium",
        "headless": False,
        "slow_mo_ms": 0,
        "viewport": DEFAULT_VIEWPORT.copy(),
        "user_data_dir": None,
        "proxy": None,
        "timeout_ms": DEFAULT_TIMEOUT_MS,
    }


def get_healthcheck_url() -> str:
    return os.getenv("MAHANBOT_HEALTHCHECK_URL", DEFAULT_HEALTHCHECK_URL)


def _resolve_user_data_dir(user_data_dir: Optional[str]) -> Optional[str]:
    if not user_data_dir:
        return None
    candidate = Path(user_data_dir)
    if not candidate.is_absolute():
        candidate = PROFILE_ROOT / candidate
    resolved = candidate.resolve()
    if PROFILE_ROOT != resolved and PROFILE_ROOT not in resolved.parents:
        raise BrowserLaunchError(
            "invalid_user_data_dir",
            "user_data_dir must be within the profile root",
            {"profile_root": str(PROFILE_ROOT), "user_data_dir": str(resolved)},
        )
    resolved.mkdir(parents=True, exist_ok=True)
    return str(resolved)


def _normalize_viewport(viewport: Optional[Dict[str, Any]]) -> Dict[str, int]:
    if not isinstance(viewport, dict):
        return DEFAULT_VIEWPORT.copy()
    width = int(viewport.get("width", DEFAULT_VIEWPORT["width"]))
    height = int(viewport.get("height", DEFAULT_VIEWPORT["height"]))
    if width < 320 or height < 200:
        raise BrowserLaunchError(
            "invalid_viewport",
            "Viewport dimensions are too small",
            {"width": width, "height": height},
        )
    return {"width": width, "height": height}


def normalize_browser_profile(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = get_default_browser_profile()
    if profile:
        data.update({k: v for k, v in profile.items() if v is not None})

    browser = str(data.get("browser", "chromium")).lower().strip()
    if browser not in ALLOWED_BROWSERS:
        raise BrowserLaunchError(
            "invalid_browser",
            "Unsupported browser selection",
            {"allowed": sorted(ALLOWED_BROWSERS), "received": browser},
        )
    data["browser"] = browser
    data["headless"] = bool(data.get("headless", False))

    try:
        slow_mo_ms = int(data.get("slow_mo_ms", 0))
    except (TypeError, ValueError):
        raise BrowserLaunchError("invalid_slow_mo", "slow_mo_ms must be an integer")
    if slow_mo_ms < 0 or slow_mo_ms > 10000:
        raise BrowserLaunchError(
            "invalid_slow_mo",
            "slow_mo_ms out of range",
            {"min": 0, "max": 10000, "received": slow_mo_ms},
        )
    data["slow_mo_ms"] = slow_mo_ms
    data["viewport"] = _normalize_viewport(data.get("viewport"))

    try:
        timeout_ms = int(data.get("timeout_ms", DEFAULT_TIMEOUT_MS))
    except (TypeError, ValueError):
        raise BrowserLaunchError("invalid_timeout", "timeout_ms must be an integer")
    if timeout_ms < 1000 or timeout_ms > 120000:
        raise BrowserLaunchError(
            "invalid_timeout",
            "timeout_ms out of range",
            {"min": 1000, "max": 120000, "received": timeout_ms},
        )
    data["timeout_ms"] = timeout_ms

    proxy = data.get("proxy")
    data["proxy"] = proxy.strip() if isinstance(proxy, str) and proxy.strip() else None
    data["user_data_dir"] = _resolve_user_data_dir(data.get("user_data_dir"))
    return data


def merge_browser_profiles(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(base)
    if override:
        for key, value in override.items():
            if value is None:
                continue
            if key == "viewport" and isinstance(value, dict):
                viewport = dict(merged.get("viewport", {}))
                viewport.update(value)
                merged[key] = viewport
            else:
                merged[key] = value
    return normalize_browser_profile(merged)


def _normalize_url(url: str) -> str:
    if not isinstance(url, str):
        raise BrowserLaunchError("invalid_url", "URL must be a string", {"received_type": str(type(url))})
    raw = url.strip()
    if not raw:
        raise BrowserLaunchError("invalid_url", "URL is empty")

    parts = urlsplit(raw)
    if not parts.scheme:
        parts = urlsplit("https://" + raw.lstrip("/"))

    scheme = (parts.scheme or "https").lower()
    hostname = (parts.hostname or "").lower()
    netloc = parts.netloc
    if hostname:
        port = parts.port
        netloc = f"{hostname}:{port}" if port else hostname
    return urlunsplit((scheme, netloc, parts.path or "", parts.query or "", parts.fragment or ""))


def _canonical_for_allowlist(url: str) -> str:
    normalized = _normalize_url(url)
    parts = urlsplit(normalized)
    return urlunsplit(("https", (parts.hostname or "").lower(), parts.path or "", "", ""))


def ensure_allowed_url(url: str, allowed_domains: Optional[Iterable[str]] = None) -> None:
    normalized = _normalize_url(url)
    canonical = _canonical_for_allowlist(normalized)
    if canonical in HARD_ALLOWED_URLS:
        return

    domains = list(allowed_domains) if allowed_domains is not None else get_allowed_domains()
    if "ve.cbi.ir" not in domains:
        domains.append("ve.cbi.ir")

    allowed, host, effective = is_url_allowed(normalized, allowed_domains=domains)
    if not allowed:
        raise BrowserLaunchError(
            "url_not_allowed",
            "URL is blocked by internal allowlist",
            {"url": normalized, "host": host, "allowed_domains": effective},
        )


def safe_goto(page, url: str, *, allowed_domains: Optional[Iterable[str]] = None, log_callback=None, **kwargs):
    try:
        ensure_allowed_url(url, allowed_domains=allowed_domains)
        normalized = _normalize_url(url)
        goto = getattr(page, "_original_goto", page.goto)
        return goto(normalized, **kwargs)
    except BrowserLaunchError as exc:
        if log_callback:
            try:
                log_callback(f"❌ URL Blocked: {exc.message}", "error", meta=exc.details)
            except TypeError:
                log_callback(f"❌ URL Blocked: {exc.message}", "error")
        raise
    except Exception as exc:
        if log_callback:
            try:
                log_callback(f"⚠️ Navigation error: {exc}", "warning")
            except TypeError:
                pass
        raise


def open_healthcheck_page(page, *, allowed_domains: Optional[Iterable[str]] = None, log_callback=None) -> bool:
    url = get_healthcheck_url()
    goto = getattr(page, "_original_goto", page.goto)
    try:
        ensure_allowed_url(url, allowed_domains=allowed_domains)
        goto(_normalize_url(url), wait_until="load")
        return True
    except BrowserLaunchError as exc:
        if log_callback:
            try:
                log_callback("⚠️ بارگذاری صفحه داخلی سلامت مرورگر ناموفق بود.", "warning", meta=exc.details)
            except TypeError:
                log_callback("⚠️ بارگذاری صفحه داخلی سلامت مرورگر ناموفق بود.", "warning")
        return False
    except Exception as exc:
        if log_callback:
            try:
                log_callback(f"⚠️ خطا در بارگذاری صفحه سلامت: {exc}", "warning")
            except TypeError:
                log_callback(f"⚠️ خطا در بارگذاری صفحه سلامت: {exc}", "warning")
        return False


def launch_browser(profile: Dict[str, Any]) -> Tuple[Any, Any, Any, Any]:
    normalized = normalize_browser_profile(profile)
    playwright = None
    browser = None
    context = None
    page = None
    try:
        playwright = sync_playwright().start()
        browser_type = getattr(playwright, normalized["browser"])
        launch_options: Dict[str, Any] = {
            "headless": normalized["headless"],
            "slow_mo": normalized["slow_mo_ms"],
        }

        if normalized["browser"] == "chromium":
            channel = str(os.getenv("MAHANBOT_BROWSER_CHANNEL") or "").strip().lower()
            if channel in ALLOWED_SYSTEM_CHANNELS:
                launch_options["channel"] = channel
            else:
                system_browser = _find_system_chromium()
                if system_browser:
                    launch_options["executable_path"] = system_browser

        if normalized["proxy"]:
            launch_options["proxy"] = {"server": normalized["proxy"]}

        if normalized["user_data_dir"]:
            context = browser_type.launch_persistent_context(
                normalized["user_data_dir"],
                **launch_options,
                viewport=normalized["viewport"],
            )
        else:
            browser = browser_type.launch(**launch_options)
            context = browser.new_context(viewport=normalized["viewport"])

        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(normalized["timeout_ms"])
        page.set_default_navigation_timeout(normalized["timeout_ms"])
        return playwright, browser, context, page
    except BrowserLaunchError:
        raise
    except Exception as exc:
        raise BrowserLaunchError(
            "launch_failed",
            f"Failed to launch browser: {exc}",
            {"error": str(exc)},
        )
    finally:
        if playwright is None and browser is None and context is None and page is None:
            return


def close_browser(playwright, browser, context, page) -> None:
    try:
        if page:
            page.close()
    except Exception:
        pass
    try:
        if context:
            context.close()
    except Exception:
        pass
    try:
        if browser:
            browser.close()
    except Exception:
        pass
    try:
        if playwright:
            playwright.stop()
    except Exception:
        pass
