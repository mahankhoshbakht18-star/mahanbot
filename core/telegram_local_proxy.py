"""Local SOCKS inbound for Telegram only. The bank browser stays direct."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Dict, List
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger("mahanbot.telegram_proxy")

LOCAL_PORT = 10808
_PROCESS: subprocess.Popen | None = None


def proxy_url() -> str:
    return f"socks5h://127.0.0.1:{LOCAL_PORT}"


def _port_open(port: int) -> bool:
    sock = socket.socket()
    sock.settimeout(0.2)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False
    finally:
        sock.close()


def _subscription_urls() -> List[str]:
    log_path = Path(os.environ.get("LOCALAPPDATA", "")) / "Happ" / "logs" / "subscription_log.txt"
    if not log_path.exists():
        return []
    text = log_path.read_text(encoding="utf-8", errors="replace")
    found = re.findall(r"starting update from: (https://\S+)", text)
    unique: List[str] = []
    for item in reversed(found):
        if item not in unique:
            unique.append(item)
    return unique[:4]


def _fetch_links(url: str) -> List[str]:
    raw = urlopen(Request(url, headers={"User-Agent": "MAHANBOT"}), timeout=12).read()
    text = raw.decode("utf-8", "replace").strip()
    try:
        text = base64.b64decode(text + "=" * (-len(text) % 4)).decode("utf-8", "replace")
    except Exception:
        pass
    return [line.strip() for line in text.splitlines() if line.strip().startswith("vless://")]


def _outbound(link: str) -> Dict:
    parsed = urlparse(link)
    query = parse_qs(parsed.query)

    def field(name: str, default: str = "") -> str:
        return unquote((query.get(name) or [default])[0])

    network = field("type", "tcp") or "tcp"
    security = field("security", "none") or "none"
    stream: Dict = {"network": network, "security": security}
    if security == "reality":
        stream["realitySettings"] = {
            "serverName": field("sni") or (parsed.hostname or ""),
            "fingerprint": field("fp", "chrome") or "chrome",
            "publicKey": field("pbk"),
            "shortId": field("sid"),
            "spiderX": field("spx", "/") or "/",
        }
    if network == "ws":
        stream["wsSettings"] = {"path": field("path", "/") or "/"}
    elif network == "httpupgrade":
        stream["httpupgradeSettings"] = {"path": field("path", "/") or "/"}
    return {
        "protocol": "vless",
        "settings": {
            "vnext": [{
                "address": parsed.hostname,
                "port": parsed.port or 443,
                "users": [{"id": parsed.username, "encryption": field("encryption", "none") or "none"}],
            }]
        },
        "streamSettings": stream,
    }


def _rank(link: str) -> int:
    query = parse_qs(urlparse(link).query)
    security = (query.get("security") or [""])[0]
    network = (query.get("type") or ["tcp"])[0]
    if security == "reality":
        return 0
    if network == "tcp":
        return 1
    if network in ("ws", "httpupgrade"):
        return 2
    return 3


def _xray_path() -> Path:
    return Path(os.environ.get("MAHANBOT_XRAY", r"C:\Program Files\FlyFrogLLC\Happ\core\xray.exe"))


def _config_path() -> Path:
    folder = Path(os.environ.get("LOCALAPPDATA", ".")) / "MAHANBOT"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "telegram-xray.json"


def _write_config(outbound: Dict) -> Path:
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "listen": "127.0.0.1",
            "port": LOCAL_PORT,
            "protocol": "socks",
            "settings": {"udp": False},
        }],
        "outbounds": [outbound],
    }
    path = _config_path()
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _stop() -> None:
    global _PROCESS
    if _PROCESS and _PROCESS.poll() is None:
        _PROCESS.terminate()
        try:
            _PROCESS.wait(timeout=3)
        except Exception:
            _PROCESS.kill()
    _PROCESS = None


def _start(config: Path) -> None:
    global _PROCESS
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _PROCESS = subprocess.Popen(
        [str(_xray_path()), "run", "-c", str(config)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def _probe(token: str) -> bool:
    if not token:
        return _port_open(LOCAL_PORT)
    import requests
    session = requests.Session()
    session.trust_env = False
    try:
        resp = session.get(
            f"https://api.telegram.org/bot{token}/getMe",
            proxies={"http": proxy_url(), "https": proxy_url()},
            timeout=12,
        )
        data = resp.json()
        return bool(data.get("ok"))
    except Exception:
        return False


def ensure_telegram_proxy(token: str = "") -> str:
    """Start a localhost SOCKS inbound. Does not change the system route."""
    if _port_open(LOCAL_PORT) and (not token or _probe(token)):
        return proxy_url()
    if _port_open(LOCAL_PORT):
        return ""
    xray = _xray_path()
    if not xray.exists():
        return ""
    links: List[str] = []
    for url in _subscription_urls():
        try:
            links.extend(_fetch_links(url))
        except Exception as exc:
            logger.warning("telegram subscription fetch failed: %s", exc)
    links = sorted(links, key=_rank)
    for link in links[:4]:
        try:
            _stop()
            _start(_write_config(_outbound(link)))
            for _ in range(12):
                if _port_open(LOCAL_PORT):
                    break
                time.sleep(0.25)
            if _port_open(LOCAL_PORT) and _probe(token):
                logger.info("telegram local socks ready on 127.0.0.1:%s", LOCAL_PORT)
                return proxy_url()
        except Exception as exc:
            logger.warning("telegram local socks failed: %s", exc)
        _stop()
    return ""
