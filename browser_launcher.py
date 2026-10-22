def apply_page_zoom(page, zoom_level: float = 1.0) -> None:
    if page is None:
        return
    try:
        if zoom_level is None:
            zoom_level = 1.0
        setattr(page, "_mahan_page_zoom", zoom_level)
        if abs(float(zoom_level) - 1.0) < 0.01:
            # 100% natural zoom - clean up any CSS zoom style so content is 100% crisp and readable
            page.evaluate("""() => {
                try {
                    const st = document.getElementById('__mahan_zoom_style__');
                    if (st) st.remove();
                    if (document.body) document.body.style.zoom = '';
                    if (document.documentElement) document.documentElement.style.zoom = '';
                } catch(e) {}
            }""")
            return
        zoom_pct = f"{int(round(float(zoom_level) * 100))}%"
        page.evaluate(f"""() => {{
            try {{
                const styleId = '__mahan_zoom_style__';
                let st = document.getElementById(styleId);
                if (!st) {{
                    st = document.createElement('style');
                    st.id = styleId;
                    (document.head || document.documentElement).appendChild(st);
                }}
                st.textContent = 'html, body {{ zoom: {zoom_pct} !important; }}';
                if (document.body) document.body.style.zoom = '{zoom_pct}';
                if (document.documentElement) document.documentElement.style.zoom = '{zoom_pct}';
            }} catch(e) {{}}
        }}""")
    except Exception:
        pass


# browser_launcher.py
import ctypes
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

try:
    from playwright.sync_api import sync_playwright, Error as PlaywrightError
except ImportError:  # pragma: no cover - fallback for test environments without playwright binary
    sync_playwright = None  # type: ignore[assignment]

    class PlaywrightError(Exception):  # type: ignore[no-redef]
        pass

from allowlist import is_url_allowed, get_allowed_domains
from network_diagnostics import check_rejection, SiteRejectedError

ALLOWED_BROWSERS = {"chromium", "chrome", "edge", "firefox", "webkit"}
ALLOWED_WINDOW_POSITIONS = {"top_right", "top_left", "bottom_right", "bottom_left", "center", "custom"}
DEFAULT_TIMEOUT_MS = 30000
DEFAULT_WINDOW_WIDTH = 900
DEFAULT_WINDOW_HEIGHT = 620
DEFAULT_WINDOW_POSITION = "top_right"
DEFAULT_VIEWPORT = {"width": DEFAULT_WINDOW_WIDTH, "height": DEFAULT_WINDOW_HEIGHT}
PROFILE_ROOT = Path(os.getenv("MAHANBOT_PROFILE_ROOT", Path(__file__).resolve().parent / "chrome_profiles")).resolve()

# Memory of last user-moved window coordinates when remember_position=True
_LAST_REMEMBERED_BOUNDS: Dict[str, Optional[int]] = {"x": None, "y": None, "width": None, "height": None}

# Hardcoded never-block allowlist (exact URLs requested)
HARD_ALLOWED_URLS = {
    "https://ve.cbi.ir/Register.aspx",
    "https://ve.cbi.ir/SelectBnkShb.aspx",
    "https://ve.cbi.ir/SelEditCase.aspx",
    "https://ve.cbi.ir/EditShb.aspx",
    "https://ve.cbi.ir/TasReqDelete.aspx",
    "https://ve.cbi.ir/TasTrace.aspx",
    "https://ve.cbi.ir/GetTraceCD.aspx",
    "https://ve.cbi.ir/DefaultVE.aspx",
    "https://ve.cbi.ir/ChildRegister.aspx",
    "https://ve.cbi.ir/ChildSelectBnkShb.aspx",
}


class BrowserLaunchError(Exception):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


def get_primary_work_area() -> Tuple[int, int, int, int]:
    """
    Detect the usable work area (left, top, width, height) of the primary monitor on Windows
    (excluding the Windows Taskbar) using standard Win32 SystemParametersInfoW(SPI_GETWORKAREA).
    Falls back to (0, 0, 1920, 1040) on non-Windows environments.
    """
    if os.name == "nt":
        try:
            class _RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = _RECT()
            SPI_GETWORKAREA = 0x0030
            ok = ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
            if ok:
                w = int(rect.right - rect.left)
                h = int(rect.bottom - rect.top)
                if w >= 640 and h >= 480:
                    return int(rect.left), int(rect.top), w, h
        except Exception:
            pass
    return 0, 0, 1920, 1040


def compute_window_geometry(
    profile: Optional[Dict[str, Any]] = None,
    work_area: Optional[Tuple[int, int, int, int]] = None,
) -> Dict[str, int]:
    """
    Calculate compact browser window dimensions and position on the primary monitor's work area.
    Guarantees the window is never maximized, never off-screen, and never hidden under the Taskbar.
    """
    data = profile or {}
    w_left, w_top, w_width, w_height = work_area if work_area is not None else get_primary_work_area()
    margin = 12

    raw_w = data.get("window_width") or (data.get("viewport") or {}).get("width") or DEFAULT_WINDOW_WIDTH
    raw_h = data.get("window_height") or (data.get("viewport") or {}).get("height") or DEFAULT_WINDOW_HEIGHT
    try:
        req_w = int(raw_w)
    except (TypeError, ValueError):
        req_w = DEFAULT_WINDOW_WIDTH
    try:
        req_h = int(raw_h)
    except (TypeError, ValueError):
        req_h = DEFAULT_WINDOW_HEIGHT

    max_usable_w = max(1, w_width - (margin * 2))
    max_usable_h = max(1, w_height - (margin * 2))
    width = min(max(360, req_w), max_usable_w)
    height = min(max(320, req_h), max_usable_h)

    pos = str(data.get("window_position") or DEFAULT_WINDOW_POSITION).strip().lower()
    if pos not in ALLOWED_WINDOW_POSITIONS:
        pos = DEFAULT_WINDOW_POSITION

    remember = bool(data.get("remember_position", True))
    custom_x = data.get("window_x")
    custom_y = data.get("window_y")
    if remember and custom_x is None and _LAST_REMEMBERED_BOUNDS.get("x") is not None:
        custom_x = _LAST_REMEMBERED_BOUNDS["x"]
    if remember and custom_y is None and _LAST_REMEMBERED_BOUNDS.get("y") is not None:
        custom_y = _LAST_REMEMBERED_BOUNDS["y"]

    if pos == "custom" and custom_x is not None and custom_y is not None:
        try:
            x = int(custom_x)
            y = int(custom_y)
        except (TypeError, ValueError):
            x = w_left + w_width - width - margin
            y = w_top + margin
    elif pos == "top_left":
        x = w_left + margin
        y = w_top + margin
    elif pos == "bottom_right":
        x = w_left + w_width - width - margin
        y = w_top + w_height - height - margin
    elif pos == "bottom_left":
        x = w_left + margin
        y = w_top + w_height - height - margin
    elif pos == "center":
        x = w_left + (w_width - width) // 2
        y = w_top + (w_height - height) // 2
    else:
        # Default: top_right corner of primary monitor
        x = w_left + w_width - width - margin
        y = w_top + margin

    # Strictly clamp coordinates inside the primary monitor work area so it never goes under Taskbar or off-screen
    min_x = w_left
    max_x = max(w_left, w_left + w_width - width)
    min_y = w_top
    max_y = max(w_top, w_top + w_height - height)
    x = max(min_x, min(x, max_x))
    y = max(min_y, min(y, max_y))

    return {
        "x": int(x),
        "y": int(y),
        "width": int(width),
        "height": int(height),
        "work_left": int(w_left),
        "work_top": int(w_top),
        "work_width": int(w_width),
        "work_height": int(w_height),
    }


def get_default_browser_profile() -> Dict[str, Any]:
    return {
        "browser": "chromium",
        "headless": False,
        "slow_mo_ms": 0,
        "viewport": DEFAULT_VIEWPORT.copy(),
        "window_width": DEFAULT_WINDOW_WIDTH,
        "window_height": DEFAULT_WINDOW_HEIGHT,
        "window_position": DEFAULT_WINDOW_POSITION,
        "window_x": None,
        "window_y": None,
        "start_maximized": False,
        "always_on_top": False,
        "remember_position": True,
        "page_zoom": 1.0,
        "user_data_dir": None,
        "proxy": None,
        "timeout_ms": DEFAULT_TIMEOUT_MS,
    }


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


def _normalize_viewport(viewport: Optional[Dict[str, Any]], fallback_w: int = DEFAULT_WINDOW_WIDTH, fallback_h: int = DEFAULT_WINDOW_HEIGHT) -> Dict[str, int]:
    if not isinstance(viewport, dict):
        return {"width": fallback_w, "height": fallback_h}
    width = int(viewport.get("width", fallback_w))
    height = int(viewport.get("height", fallback_h))
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
    data["start_maximized"] = bool(data.get("start_maximized", False))
    try:
        raw_zoom = data.get("page_zoom", 1.0)
        page_zoom = float(raw_zoom) if raw_zoom is not None else 1.0
    except (TypeError, ValueError):
        page_zoom = 1.0
    data["page_zoom"] = max(0.25, min(2.0, page_zoom))
    data["always_on_top"] = bool(data.get("always_on_top", False))
    data["remember_position"] = bool(data.get("remember_position", True))

    pos = str(data.get("window_position", DEFAULT_WINDOW_POSITION)).strip().lower()
    if pos not in ALLOWED_WINDOW_POSITIONS:
        pos = DEFAULT_WINDOW_POSITION
    data["window_position"] = pos

    try:
        win_w = int(data.get("window_width") or (data.get("viewport") or {}).get("width") or DEFAULT_WINDOW_WIDTH)
    except (TypeError, ValueError):
        win_w = DEFAULT_WINDOW_WIDTH
    try:
        win_h = int(data.get("window_height") or (data.get("viewport") or {}).get("height") or DEFAULT_WINDOW_HEIGHT)
    except (TypeError, ValueError):
        win_h = DEFAULT_WINDOW_HEIGHT

    win_w = max(360, min(3840, win_w))
    win_h = max(320, min(2160, win_h))
    data["window_width"] = win_w
    data["window_height"] = win_h

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

    # Keep viewport synchronized with window_width/window_height if viewport wasn't explicitly customized
    if profile and "viewport" in profile and isinstance(profile["viewport"], dict):
        data["viewport"] = _normalize_viewport(profile["viewport"], fallback_w=win_w, fallback_h=win_h)
        if "window_width" not in profile:
            data["window_width"] = data["viewport"]["width"]
        if "window_height" not in profile:
            data["window_height"] = data["viewport"]["height"]
    else:
        data["viewport"] = _normalize_viewport({"width": win_w, "height": win_h}, fallback_w=win_w, fallback_h=win_h)

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
    if isinstance(proxy, dict):
        data["proxy"] = proxy
    elif isinstance(proxy, str) and proxy.strip():
        data["proxy"] = proxy.strip()
    else:
        data["proxy"] = None

    data["anti_detection_enabled"] = bool(data.get("anti_detection_enabled", True))
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


def _sanitize_chromium_preferences_window_placement(user_data_dir: Optional[str], geom: Dict[str, int]) -> None:
    """
    Ensure Chrome/Edge persistent user_data_dir Preferences does not force maximized=true from an old session.
    """
    if not user_data_dir:
        return
    prefs_path = Path(user_data_dir) / "Default" / "Preferences"
    if not prefs_path.is_file():
        return
    try:
        raw = prefs_path.read_text(encoding="utf-8")
        prefs = json.loads(raw) if raw else {}
        if not isinstance(prefs, dict):
            return
        browser_sec = prefs.setdefault("browser", {})
        if isinstance(browser_sec, dict):
            browser_sec["window_placement"] = {
                "bottom": geom["y"] + geom["height"],
                "left": geom["x"],
                "maximized": False,
                "right": geom["x"] + geom["width"],
                "top": geom["y"],
                "work_area_bottom": geom["work_top"] + geom["work_height"],
                "work_area_left": geom["work_left"],
                "work_area_right": geom["work_left"] + geom["work_width"],
                "work_area_top": geom["work_top"],
            }
            partition_sec = prefs.setdefault("partition", {})
            if isinstance(partition_sec, dict):
                partition_sec["default_zoom_level"] = -2.2239
            prefs_path.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _apply_compact_window_bounds(page, context, geom: Dict[str, int], profile: Dict[str, Any]) -> None:
    """
    Use Chromium CDP (Browser.getWindowForTarget + Browser.setWindowBounds) once at startup
    to ensure the browser window is un-maximized ('normal') and placed in the compact corner bounds.
    Does not lock the window, so the user can freely move or resize it afterward.
    """
    if profile.get("headless") or profile.get("start_maximized"):
        return
    if page is None or context is None or not hasattr(context, "new_cdp_session"):
        return
    try:
        cdp = context.new_cdp_session(page)
        target_info = cdp.send("Browser.getWindowForTarget")
        window_id = target_info.get("windowId")
        if window_id is not None:
            cdp.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window_id,
                    "bounds": {"windowState": "normal"},
                },
            )
            cdp.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window_id,
                    "bounds": {
                        "left": int(geom["x"]),
                        "top": int(geom["y"]),
                        "width": int(geom["width"]),
                        "height": int(geom["height"]),
                        "windowState": "normal",
                    },
                },
            )
        cdp.detach()
    except Exception:
        pass


def _remember_window_bounds_before_close(page, context) -> None:
    """If the user dragged the window during the session, remember its x/y for subsequent launches."""
    if page is None or context is None or not hasattr(context, "new_cdp_session"):
        return
    try:
        cdp = context.new_cdp_session(page)
        target_info = cdp.send("Browser.getWindowForTarget")
        bounds = target_info.get("bounds") or {}
        if bounds.get("windowState") == "normal":
            if isinstance(bounds.get("left"), int) and isinstance(bounds.get("top"), int):
                _LAST_REMEMBERED_BOUNDS["x"] = int(bounds["left"])
                _LAST_REMEMBERED_BOUNDS["y"] = int(bounds["top"])
        cdp.detach()
    except Exception:
        pass


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
        if port:
            netloc = f"{hostname}:{port}"
        else:
            netloc = hostname

    normalized = urlunsplit((scheme, netloc, parts.path or "", parts.query or "", parts.fragment or ""))
    return normalized


def _canonical_for_allowlist(url: str) -> str:
    normalized = _normalize_url(url)
    parts = urlsplit(normalized)
    scheme = "https"
    hostname = (parts.hostname or "").lower()
    path = parts.path or ""
    return urlunsplit((scheme, hostname, path, "", ""))


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
        response = goto(normalized, **kwargs)
        page.wait_for_load_state("domcontentloaded")
        current_zoom = getattr(page, "_mahan_page_zoom", None)
        if current_zoom is None:
            current_zoom = 1.0
        apply_page_zoom(page, current_zoom)
        check_rejection(page)
        return response
    except SiteRejectedError:
        raise
    except BrowserLaunchError as exc:
        if log_callback:
            try:
                log_callback(f"❌ URL Blocked: {exc.message}", "error", meta=exc.details)
            except TypeError:
                log_callback(f"❌ URL Blocked: {exc.message}", "error")
        raise
    except Exception as exc:
        # Check if page is already at target URL or target DOM elements are present despite timeout
        if "timeout" in str(exc).lower() and page is not None:
            try:
                curr_url = page.url or ""
                if curr_url and ("ve.cbi.ir" in curr_url or "SelectBnk" in curr_url or "Register" in curr_url):
                    has_elements = False
                    for sel in ("#ctl00_ContentPlaceHolder1_tbIDNo", "input[name$='tbIDNo']", "#ctl00_ContentPlaceHolder1_ddlBankName", "#ans", ".BDC_CaptchaImage"):
                        if page.locator(sel).count() > 0:
                            has_elements = True
                            break
                    if has_elements:
                        if log_callback:
                            try:
                                log_callback("⚠️ تاخیر در پایان بارگذاری، اما المان‌های فرم در صفحه مستقر شدند؛ ادامه فرآیند.", "info", page)
                            except Exception:
                                pass
                        check_rejection(page)
                        return None
            except Exception:
                pass
        if log_callback:
            try:
                log_callback(f"⚠️ Navigation error: {exc}", "warning")
            except TypeError:
                pass
        raise


def launch_browser(profile: Dict[str, Any]) -> Tuple[Any, Any, Any, Any]:
    normalized = normalize_browser_profile(profile)
    geom = compute_window_geometry(normalized)
    playwright = None
    browser = None
    context = None
    page = None
    try:
        if sync_playwright is None:
            raise RuntimeError("playwright is not installed in this environment")
        playwright = sync_playwright().start()
        selected_browser = normalized["browser"]
        browser_type = getattr(playwright, "chromium" if selected_browser in {"chrome", "edge"} else selected_browser)
        launch_options: Dict[str, Any] = {
            "headless": normalized["headless"],
            "slow_mo": normalized["slow_mo_ms"],
        }
        if selected_browser in {"chrome", "edge"}:
            launch_options["channel"] = "chrome" if selected_browser == "chrome" else "msedge"

        if selected_browser in {"chromium", "chrome", "edge"}:
            args = list(launch_options.setdefault("args", []))
            # Never allow fullscreen, kiosk, or forced maximize unless start_maximized is explicitly True
            forbidden_prefixes = ("--start-fullscreen", "--kiosk")
            if not normalized.get("start_maximized", False):
                forbidden_prefixes = forbidden_prefixes + ("--start-maximized",)
            args = [a for a in args if not any(a.startswith(fp) for fp in forbidden_prefixes)]

            stability_flags = [
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-search-engine-choice-screen",
                "--disable-infobars",
            ]
            for flag in stability_flags:
                if flag not in args:
                    args.append(flag)

            if normalized.get("anti_detection_enabled", True):
                from browser_fingerprint import get_stealth_args
                for sa in get_stealth_args():
                    if sa not in args:
                        args.append(sa)

            if not normalized["headless"] and not normalized.get("start_maximized", False):
                args = [a for a in args if not a.startswith("--window-size=") and not a.startswith("--window-position=")]
                args.append(f"--window-size={geom['width']},{geom['height']}")
                args.append(f"--window-position={geom['x']},{geom['y']}")

            launch_options["args"] = args

        if normalized["proxy"]:
            p = normalized["proxy"]
            if isinstance(p, dict):
                proxy_cfg = {"server": p.get("server") or p.get("raw")}
                if p.get("username") and p.get("password"):
                    proxy_cfg["username"] = p["username"]
                    proxy_cfg["password"] = p["password"]
                launch_options["proxy"] = proxy_cfg
            elif isinstance(p, str):
                from proxy_manager import parse_proxy_string
                parsed_p = parse_proxy_string(p)
                if parsed_p:
                    proxy_cfg = {"server": parsed_p["server"]}
                    if parsed_p.get("username") and parsed_p.get("password"):
                        proxy_cfg["username"] = parsed_p["username"]
                        proxy_cfg["password"] = parsed_p["password"]
                    launch_options["proxy"] = proxy_cfg
                else:
                    launch_options["proxy"] = {"server": p}

        effective_viewport = {
            "width": geom["width"],
            "height": geom["height"],
        }

        if normalized["user_data_dir"]:
            if not normalized.get("start_maximized", False):
                _sanitize_chromium_preferences_window_placement(normalized["user_data_dir"], geom)
            context = browser_type.launch_persistent_context(
                normalized["user_data_dir"],
                **launch_options,
                viewport=effective_viewport,
            )
        else:
            browser = browser_type.launch(**launch_options)
            context = browser.new_context(viewport=effective_viewport)

        zoom_level = normalized.get("page_zoom", 1.0)
        if zoom_level and zoom_level != 1.0:
            zoom_pct = f"{int(round(zoom_level * 100))}%"
            try:
                context.add_init_script(f"""
                (() => {{
                    const applyZoom = () => {{
                        try {{
                            const styleId = '__mahan_zoom_style__';
                            let st = document.getElementById(styleId);
                            if (!st) {{
                                st = document.createElement('style');
                                st.id = styleId;
                                st.textContent = 'html, body {{ zoom: {zoom_pct} !important; }}';
                                (document.head || document.documentElement).appendChild(st);
                            }}
                            if (document.body) {{
                                document.body.style.zoom = '{zoom_pct}';
                            }}
                            if (document.documentElement) {{
                                document.documentElement.style.zoom = '{zoom_pct}';
                            }}
                        }} catch (e) {{}}
                    }};
                    if (document.readyState === 'loading') {{
                        document.addEventListener('DOMContentLoaded', applyZoom);
                    }} else {{
                        applyZoom();
                    }}
                    window.addEventListener('load', applyZoom);
                }})();
                """)
            except Exception:
                pass

        if normalized.get("focus_view_enabled", True):
            try:
                context.add_init_script("""
                (() => {
                    const applyFocusView = () => {
                        try {
                            const styleId = '__mahan_focus_view_style__';
                            let st = document.getElementById(styleId);
                            if (!st) {
                                st = document.createElement('style');
                                st.id = styleId;
                                st.textContent = `
                                    body {
                                        overflow-x: hidden !important;
                                        font-family: Tahoma, 'Segoe UI', Arial, sans-serif !important;
                                    }
                                    #ctl00_ContentPlaceHolder1_UpdatePanel2 {
                                        max-width: 100% !important;
                                        margin: 0 auto !important;
                                        padding: 8px !important;
                                        box-sizing: border-box !important;
                                    }
                                    #ctl00_ContentPlaceHolder1_UpdatePanel2 table {
                                        width: 100% !important;
                                        max-width: 100% !important;
                                    }
                                    #ctl00_ContentPlaceHolder1_UpdatePanel2 select,
                                    #ctl00_ContentPlaceHolder1_UpdatePanel2 input[type="text"] {
                                        font-size: 13px !important;
                                        font-weight: 500 !important;
                                        padding: 4px 6px !important;
                                        border: 1px solid #7c9cbc !important;
                                        border-radius: 4px !important;
                                        max-width: 100% !important;
                                        box-sizing: border-box !important;
                                    }
                                    #ctl00_ContentPlaceHolder1_btnSave {
                                        font-size: 14px !important;
                                        font-weight: bold !important;
                                        padding: 6px 20px !important;
                                        background-color: #198754 !important;
                                        color: #ffffff !important;
                                        border-radius: 4px !important;
                                        cursor: pointer !important;
                                    }
                                `;
                                (document.head || document.documentElement).appendChild(st);
                            }
                        } catch (e) {}
                    };
                    if (document.readyState === 'loading') {
                        document.addEventListener('DOMContentLoaded', applyFocusView);
                    } else {
                        applyFocusView();
                    }
                    window.addEventListener('load', applyFocusView);
                })();
                """)
            except Exception:
                pass

        if normalized.get("anti_detection_enabled", True):
            from browser_fingerprint import apply_stealth_shield
            apply_stealth_shield(context)

        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(normalized["timeout_ms"])
        page.set_default_navigation_timeout(normalized["timeout_ms"])
        _apply_compact_window_bounds(page, context, geom, normalized)
        return playwright, browser, context, page
    except BrowserLaunchError:
        raise
    except Exception as exc:
        close_browser(playwright, browser, context, page)
        raise BrowserLaunchError("launch_failed", "Failed to launch browser", {"error": str(exc)})


def close_browser(playwright, browser, context, page) -> None:
    try:
        if page:
            setattr(page, "_mahan_closing_intentionally", True)
        if context:
            setattr(context, "_mahan_closing_intentionally", True)
        if browser:
            setattr(browser, "_mahan_closing_intentionally", True)
    except Exception:
        pass
    try:
        _remember_window_bounds_before_close(page, context)
    except Exception:
        pass
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
