from __future__ import annotations

import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / "mahanbot.env"
DB_FILE = ROOT / "cbi_ultimate.db"
MODEL_FILE = ROOT / "my_captcha_model.pth"
BACKUP_DIR = ROOT / "backups"
PHONE_SETUP_FILE = ROOT / "PHONE_SETUP.txt"
DASHBOARD_URL = "http://127.0.0.1:8000/"
DASHBOARD_PORT = 8000
SMS_PORT = 8010
MAX_DATABASE_BACKUPS = 12
VALID_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"})
ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _read_env(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not ENV_KEY_PATTERN.fullmatch(key):
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def _write_env(path: Path, values: Dict[str, str]) -> None:
    lines = [
        "# MahanBot unified local configuration",
        "# Keep this file private.",
    ]
    for key in sorted(values):
        if not ENV_KEY_PATTERN.fullmatch(key):
            continue
        value = str(values[key]).replace("\r", "").replace("\n", "")
        lines.append(f"{key}={value}")
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temp, path)


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
    yield ROOT / "data" / "cbi_ultimate.db"

    desktop = user_profile / "Desktop"
    if desktop.is_dir():
        try:
            yield from desktop.glob("*/cbi_ultimate.db")
            yield from desktop.glob("*/*/cbi_ultimate.db")
        except OSError:
            pass


def _is_valid_sqlite(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 100:
        return False
    connection: Optional[sqlite3.Connection] = None
    try:
        connection = sqlite3.connect(str(path), timeout=5)
        connection.execute("PRAGMA query_only=ON")
        result = connection.execute("PRAGMA quick_check").fetchone()
        return bool(result and str(result[0]).lower() == "ok")
    except sqlite3.Error:
        return False
    finally:
        if connection is not None:
            connection.close()


def _import_database_once() -> Optional[Path]:
    if DB_FILE.is_file():
        return None
    seen: set[str] = set()
    for candidate in _candidate_databases():
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            continue
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            if resolved == DB_FILE.resolve(strict=False) or not _is_valid_sqlite(resolved):
                continue
        except OSError:
            continue

        temp = DB_FILE.with_suffix(".db.importing")
        try:
            shutil.copy2(resolved, temp)
            if not _is_valid_sqlite(temp):
                temp.unlink(missing_ok=True)
                continue
            os.replace(temp, DB_FILE)
            return resolved
        except OSError:
            temp.unlink(missing_ok=True)
            continue
    return None


def _backup_database(path: Path = DB_FILE) -> Optional[Path]:
    if not _is_valid_sqlite(path):
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = BACKUP_DIR / f"cbi_ultimate-{stamp}.db"
    source_conn: Optional[sqlite3.Connection] = None
    target_conn: Optional[sqlite3.Connection] = None
    try:
        source_conn = sqlite3.connect(str(path), timeout=10)
        target_conn = sqlite3.connect(str(destination), timeout=10)
        source_conn.backup(target_conn)
        target_conn.execute("PRAGMA quick_check")
        target_conn.close()
        target_conn = None
        if not _is_valid_sqlite(destination):
            destination.unlink(missing_ok=True)
            return None
    except (sqlite3.Error, OSError):
        destination.unlink(missing_ok=True)
        return None
    finally:
        if target_conn is not None:
            target_conn.close()
        if source_conn is not None:
            source_conn.close()

    try:
        backups = sorted(BACKUP_DIR.glob("cbi_ultimate-*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
        for old in backups[MAX_DATABASE_BACKUPS:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass
    return destination


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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def _wait_for_process(url: str, process: subprocess.Popen, timeout: float = 60.0) -> Tuple[bool, Optional[int]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            return False, int(exit_code)
        if _wait_for(url, timeout=0.8):
            return True, None
    return False, process.poll()


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.8):
            return True
    except OSError:
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
    values.setdefault("MAHANBOT_PORT", str(DASHBOARD_PORT))
    values.setdefault("MAHANBOT_SMS_HOST", "0.0.0.0")
    values.setdefault("MAHANBOT_SMS_PORT", str(SMS_PORT))
    values.setdefault("MAHANBOT_CAPTCHA_MODEL_PATH", str(MODEL_FILE))
    values.setdefault("MAHANBOT_MODEL_DEVICE", "auto")
    values.setdefault("MAHANBOT_TYPING_DELAY_MS", "70")
    values["MAHANBOT_LOG_LEVEL"] = _normalize_log_level(values.get("MAHANBOT_LOG_LEVEL"))
    values["MAHANBOT_DB_PATH"] = str(DB_FILE)
    # The dashboard remains bound to localhost in the one-click package.
    values["MAHANBOT_API_KEY"] = ""
    _write_env(ENV_FILE, values)

    env = os.environ.copy()
    env.update(values)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")
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
    temp = PHONE_SETUP_FILE.with_suffix(".txt.tmp")
    temp.write_text(content, encoding="utf-8")
    os.replace(temp, PHONE_SETUP_FILE)
    return endpoint


def _configured_model_path(env: Dict[str, str]) -> Path:
    raw = os.path.expandvars(env["MAHANBOT_CAPTCHA_MODEL_PATH"])
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve(strict=False)


def main() -> int:
    os.chdir(ROOT)

    if _port_is_open("127.0.0.1", DASHBOARD_PORT):
        if _wait_for(DASHBOARD_URL, timeout=2.0):
            print("MahanBot is already running. Opening the existing dashboard.")
            webbrowser.open(DASHBOARD_URL)
            return 0
        print(f"Port {DASHBOARD_PORT} is already in use by another application.")
        return 2
    if _port_is_open("127.0.0.1", SMS_PORT):
        print(f"Port {SMS_PORT} is already in use. Stop the conflicting service and retry.")
        return 2

    imported = _import_database_once()
    backup = _backup_database()
    env = _prepare_environment()
    phone_endpoint = _write_phone_setup(env)
    model_path = _configured_model_path(env)

    print("=" * 68)
    print("MahanBot unified launcher")
    print(f"Database: {DB_FILE}")
    if imported:
        print(f"Imported previous database from: {imported}")
    elif DB_FILE.exists():
        print("Database is ready in the project folder.")
    else:
        print("A new database will be created automatically.")
    if backup:
        print(f"Database backup: {backup}")
    print(f"Browser runtime: {env['MAHANBOT_BROWSER_CHANNEL']}")
    if model_path.is_file():
        print(f"Local model: detected ({model_path})")
    else:
        print(f"Local model: not found ({model_path})")
        print("Place my_captcha_model.pth beside unified_server.py and restart MahanBot.")
    print("Model output is available for local operator review; browser autofill is disabled.")
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
        ready, early_exit = _wait_for_process(DASHBOARD_URL, process, timeout=60)
        if ready:
            webbrowser.open(DASHBOARD_URL)
        elif early_exit is not None:
            print(f"MahanBot server exited before startup completed (code {early_exit}).")
            return int(early_exit or 1)
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
