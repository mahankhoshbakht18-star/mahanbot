from __future__ import annotations

import os
import threading

import uvicorn

from model_lab_api import install_model_lab
from server_sms_bridge import app as dashboard_app, require_api_key
from sms_ingress import app as sms_ingress_app
from ui_v2 import install_ui_v2


def _disabled_healthcheck(*_args, **_kwargs) -> bool:
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


def _serve_ingress(server: uvicorn.Server) -> None:
    server.run()


def main() -> None:
    dashboard_host = os.getenv("MAHANBOT_DASHBOARD_HOST", "127.0.0.1")
    dashboard_port = int(os.getenv("MAHANBOT_PORT", "8000"))
    sms_host = os.getenv("MAHANBOT_SMS_HOST", "0.0.0.0")
    sms_port = int(os.getenv("MAHANBOT_SMS_PORT", "8010"))
    log_level = os.getenv("MAHANBOT_LOG_LEVEL", "warning").lower()

    _disable_legacy_healthcheck()
    install_model_lab(dashboard_app, require_api_key)
    install_ui_v2(dashboard_app)

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

    dashboard_config = uvicorn.Config(
        dashboard_app,
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
