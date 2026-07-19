from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Dict, Iterable, Optional


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / "mahanbot.env"
DB_FILE = ROOT / "cbi_ultimate.db"
MODEL_FILE = ROOT / "my_captcha_model.pth"
PHONE_SETUP_FILE = ROOT / "PHONE_SETUP.txt"
DASHBOARD_URL = "http://127.0.0.1:8000/"
SMS_PORT = 8010
VALID_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"})


def _read_env(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _write_env(path: Path, values: Dict[str, str]) -> None:
    lines = [
        "# MahanBot unified local configuration",
        "# Keep this file private.",
    ]
    for key in sorted(values):
        lines.append(f"{key}={values[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _candidate_databases() -> Iterable[Path]:
    configured = os.getenv("MAHANBOT_IMPORT_DB", "").strip()
    if configured:
        yield Path(os.path.expandvars(configured)).expanduser()

    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        yield Path(local_app_data) / "MahanBot" / "cbi_ultimate.db"

    user_profile = Path(os.getenv("USERPROFILE", str(Path.home())))
    yield user_profile / "Desktop" / "mahanbot-old" / "cbi_ultimate.db"
    yield user_profile / "Desktop" / "mahanbot-complete-source" / "mahanbot" / "cbi_ultimate.db"

    old_root_db = ROOT / "data" / "cbi_ultimate.db"
    yield old_root_db

    desktop = user_profile / "Desktop"
    if desktop.is_dir():
        try:
            for path in desktop.glob("*/cbi_ultimate.db"):
                yield path
            for path in desktop.glob("*/*/cbi_ultimate.db"):
                yield path
        except OSError:
            pass


def _import_database_once() -> Optional[Path]:
    if DB_FILE.is_file():
        return None
    seen = set()
    for candidate in _candidate_databases():
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        if resolved == DB_FILE.resolve() or not resolved.is_file():
            continue
        try:
            shutil.copy2(resolved, DB_FILE)
            return resolved
        except OSError:
            continue
    return None


def _browser_channel() -> str:
    configured = os.getenv("MAHANBOT_BROWSER_CHANNEL", "").strip().lower()
    if configured:
        return configured
    program_files = [
        os.getenv("PROGRAMFILES", ""),
        os.getenv("PROGRAMFILES(X86)", ""),
        os.getenv("LOCALAPPDATA", ""),
    ]
    for root in program_files:
        if root and (Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe").is_file():
            return "msedge"
    for root in program_files:
        if root and (Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe").is_file():
            return "chrome"
    return "msedge"


def _lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


def _wait_for(url: str, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def _normalize_log_level(value: object) -> str:
    level = str(value or "INFO").strip().upper()
    return level if level in VALID_LOG_LEVELS else "INFO"


def _prepare_environment() -> Dict[str, str]:
    values = _read_env(ENV_FILE)
    values.setdefault("MAHANBOT_SMS_DEVICE_KEY", secrets.token_urlsafe(32))
    values.setdefault("MAHANBOT_BROWSER_CHANNEL", _browser_channel())
    values.setdefault("MAHANBOT_DASHBOARD_HOST", "127.0.0.1")
    values.setdefault("MAHANBOT_HOST", "127.0.0.1")
    values.setdefault("MAHANBOT_PORT", "8000")
    values.setdefault("MAHANBOT_SMS_HOST", "0.0.0.0")
    values.setdefault("MAHANBOT_SMS_PORT", str(SMS_PORT))
    values.setdefault("MAHANBOT_CAPTCHA_MODEL_PATH", str(MODEL_FILE))
    values["MAHANBOT_LOG_LEVEL"] = _normalize_log_level(values.get("MAHANBOT_LOG_LEVEL"))
    values["MAHANBOT_DB_PATH"] = str(DB_FILE)
    # Dashboard is bound only to localhost, so no API-key prompt is needed.
    values["MAHANBOT_API_KEY"] = ""
    _write_env(ENV_FILE, values)

    env = os.environ.copy()
    env.update(values)
    return env


def _write_phone_setup(env: Dict[str, str]) -> str:
    ip = _lan_ip()
    endpoint = f"http://{ip}:{env['MAHANBOT_SMS_PORT']}/api/v1/sms/notify"
    heartbeat = f"http://{ip}:{env['MAHANBOT_SMS_PORT']}/api/v1/sms/notify/heartbeat"
    content = (
        "MahanBot - Android SMS notification setup\n"
        "==========================================\n"
        f"Server URL: {endpoint}\n"
        f"Heartbeat URL: {heartbeat}\n"
        f"Device key: {env['MAHANBOT_SMS_DEVICE_KEY']}\n\n"
        "Only the arrival notification is accepted. Message text and OTP are not transferred.\n"
    )
    PHONE_SETUP_FILE.write_text(content, encoding="utf-8")
    return endpoint


def main() -> int:
    os.chdir(ROOT)
    imported = _import_database_once()
    env = _prepare_environment()
    phone_endpoint = _write_phone_setup(env)
    model_path = Path(os.path.expandvars(env["MAHANBOT_CAPTCHA_MODEL_PATH"])).expanduser()

    print("=" * 68)
    print("MahanBot unified launcher")
    print(f"Database: {DB_FILE}")
    if imported:
        print(f"Imported previous database from: {imported}")
    elif DB_FILE.exists():
        print("Database is ready in the project folder.")
    else:
        print("A new database will be created automatically.")
    print(f"Browser runtime: {env['MAHANBOT_BROWSER_CHANNEL']}")
    if model_path.is_file():
        print(f"Local model: detected and connected ({model_path})")
    else:
        print(f"Local model: not found ({model_path})")
        print("Place my_captcha_model.pth beside unified_server.py and restart MahanBot.")
    print(f"Dashboard: {DASHBOARD_URL}")
    print(f"Android notification endpoint: {phone_endpoint}")
    print(f"Phone setup file: {PHONE_SETUP_FILE}")
    print("=" * 68)

    process = subprocess.Popen(
        [sys.executable, str(ROOT / "unified_server.py")],
        cwd=str(ROOT),
        env=env,
    )
    try:
        if _wait_for(DASHBOARD_URL, timeout=60):
            webbrowser.open(DASHBOARD_URL)
        else:
            print("Dashboard did not become ready. Check the server logs above.")
        return process.wait()
    except KeyboardInterrupt:
        print("\nStopping MahanBot...")
        process.terminate()
        try:
            return process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
