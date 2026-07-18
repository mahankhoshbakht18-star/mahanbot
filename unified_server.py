from __future__ import annotations

import os
import threading

import uvicorn

from server_sms_bridge import app as dashboard_app
from sms_ingress import app as sms_ingress_app


def _serve_ingress(server: uvicorn.Server) -> None:
    server.run()


def main() -> None:
    dashboard_host = os.getenv("MAHANBOT_DASHBOARD_HOST", "127.0.0.1")
    dashboard_port = int(os.getenv("MAHANBOT_PORT", "8000"))
    sms_host = os.getenv("MAHANBOT_SMS_HOST", "0.0.0.0")
    sms_port = int(os.getenv("MAHANBOT_SMS_PORT", "8010"))
    log_level = os.getenv("MAHANBOT_LOG_LEVEL", "info").lower()

    ingress_config = uvicorn.Config(
        sms_ingress_app,
        host=sms_host,
        port=sms_port,
        log_level=log_level,
        access_log=True,
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
        access_log=True,
    )
    dashboard_server = uvicorn.Server(dashboard_config)

    try:
        dashboard_server.run()
    finally:
        ingress_server.should_exit = True
        ingress_thread.join(timeout=5)


if __name__ == "__main__":
    main()
