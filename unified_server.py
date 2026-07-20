from __future__ import annotations

import json
import os
import threading
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Request

from model_lab_api import install_model_lab, warmup_model_lab
from server_sms_bridge import app as dashboard_app, require_api_key
from sms_ingress import app as sms_ingress_app
from ui_v2 import install_ui_v2


def _disabled_healthcheck(*_args: Any, **_kwargs: Any) -> bool:
    """Compatibility no-op: legacy bots may still call this name."""

    return True


def _disable_legacy_healthcheck() -> None:
    # bot_core and bot_status imported the function into module scope, therefore
    # patch their local references as well. No page navigation or wait remains.
    import browser_launcher
    import bot_core
    import bot_status

    browser_launcher.open_healthcheck_page = _disabled_healthcheck
    bot_core.open_healthcheck_page = _disabled_healthcheck
    bot_status.open_healthcheck_page = _disabled_healthcheck


def _env_port(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    if not 1 <= value <= 65535:
        return default
    return value


def _env_log_level() -> str:
    allowed = {"critical", "error", "warning", "info", "debug", "trace"}
    value = str(os.getenv("MAHANBOT_LOG_LEVEL", "warning")).strip().lower()
    return value if value in allowed else "warning"


def _origin_is_local(origin: str, dashboard_port: int) -> bool:
    value = str(origin or "").strip()
    if not value:
        return True
    try:
        parsed = urlsplit(value)
        hostname = str(parsed.hostname or "").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except (TypeError, ValueError):
        return False
    return parsed.scheme in {"http", "https"} and hostname in {"127.0.0.1", "localhost", "::1"} and port == dashboard_port


class LocalDashboardOriginGuard:
    """Reject browser cross-origin access to the localhost dashboard.

    Android SMS ingress is served by a separate app and is authenticated with a
    device key. This guard protects both dashboard HTTP and WebSocket traffic.
    """

    def __init__(self, app: Any, dashboard_port: int) -> None:
        self.app = app
        self.dashboard_port = dashboard_port

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        origin = headers.get("origin", "")
        if _origin_is_local(origin, self.dashboard_port):
            await self.app(scope, receive, send)
            return

        if scope.get("type") == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "Origin not allowed"})
            return

        payload = json.dumps({"detail": "Origin not allowed"}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


def _install_common_headers(app: FastAPI) -> None:
    if getattr(app.state, "mahanbot_common_headers_installed", False):
        return
    app.state.mahanbot_common_headers_installed = True

    @app.middleware("http")
    async def add_common_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith("/api/") or request.url.path in {"/applicants", "/settings"}:
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response


def _serve_ingress(server: uvicorn.Server) -> None:
    server.run()


def _warm_model_runtime() -> None:
    status = warmup_model_lab()
    if status.get("loaded"):
        print(
            "MahanBot local model ready for operator review: "
            f"{status.get('model_file')} on {status.get('device')}"
        )
    elif status.get("available"):
        print(
            "MahanBot local model was found but could not be loaded: "
            f"{status.get('load_error') or 'unknown error'}"
        )
    else:
        print(
            "MahanBot local model not found. Place my_captcha_model.pth "
            "beside unified_server.py."
        )


def main() -> None:
    dashboard_host = str(os.getenv("MAHANBOT_DASHBOARD_HOST", "127.0.0.1")).strip() or "127.0.0.1"
    dashboard_port = _env_port("MAHANBOT_PORT", 8000)
    sms_host = str(os.getenv("MAHANBOT_SMS_HOST", "0.0.0.0")).strip() or "0.0.0.0"
    sms_port = _env_port("MAHANBOT_SMS_PORT", 8010)
    log_level = _env_log_level()

    if dashboard_port == sms_port and dashboard_host in {sms_host, "0.0.0.0"}:
        raise RuntimeError("Dashboard and SMS ingress cannot use the same host and port")

    _disable_legacy_healthcheck()
    install_model_lab(dashboard_app, require_api_key)
    install_ui_v2(dashboard_app)
    _install_common_headers(dashboard_app)
    _install_common_headers(sms_ingress_app)

    model_warmup_thread = threading.Thread(
        target=_warm_model_runtime,
        name="mahanbot-model-warmup",
        daemon=True,
    )
    model_warmup_thread.start()

    ingress_config = uvicorn.Config(
        sms_ingress_app,
        host=sms_host,
        port=sms_port,
        log_level=log_level,
        access_log=False,
        timeout_keep_alive=15,
    )
    ingress_server = uvicorn.Server(ingress_config)
    ingress_thread = threading.Thread(
        target=_serve_ingress,
        args=(ingress_server,),
        name="mahanbot-sms-ingress",
        daemon=True,
    )
    ingress_thread.start()

    guarded_dashboard_app = LocalDashboardOriginGuard(dashboard_app, dashboard_port)
    dashboard_config = uvicorn.Config(
        guarded_dashboard_app,
        host=dashboard_host,
        port=dashboard_port,
        log_level=log_level,
        access_log=False,
        timeout_keep_alive=15,
    )
    dashboard_server = uvicorn.Server(dashboard_config)

    try:
        dashboard_server.run()
    finally:
        ingress_server.should_exit = True
        ingress_thread.join(timeout=5)


if __name__ == "__main__":
    main()
