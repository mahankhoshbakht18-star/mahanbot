"""
gsm_modem_service.py — Hardware GSM Modem & USB Dongle Listener for MAHANBOT
Supports automated reading of incoming OTP messages directly from SIM cards via AT commands.
Compatible with Huawei, Quectel, SIM800, D-Link, and standard USB 3G/4G Dongles.
"""
import time
import threading
import logging
from typing import List, Dict, Any, Optional

from database import DBHandler
from sms_service import route_incoming_sms

logger = logging.getLogger("mahanbot.gsm")

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    serial = None
    SERIAL_AVAILABLE = False


class GSMModemService:
    """Non-blocking background service that polls a serial GSM modem for unread SMS."""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._serial = None
        self.port: Optional[str] = None
        self.baudrate: int = 115200
        self.is_connected: bool = False
        self.last_check_ts: float = 0.0
        self.last_sms_received: Optional[str] = None
        self.last_error: Optional[str] = None

    @staticmethod
    def list_available_ports() -> List[Dict[str, str]]:
        """List serial COM ports available on the host machine."""
        if not SERIAL_AVAILABLE:
            return [{"port": "COM3", "description": "پورت پیش‌فرض (pyserial نصب نیست)"}]

        ports = []
        try:
            for p in serial.tools.list_ports.comports():
                ports.append({
                    "port": p.device,
                    "description": p.description or p.device,
                    "hwid": getattr(p, "hwid", ""),
                })
        except Exception as e:
            logger.warning("Error listing serial ports: %s", e)

        if not ports:
            ports.append({"port": "COM3", "description": "COM3 (پیش‌فرض)"})
        return ports

    def start(self, port: Optional[str] = None, baudrate: int = 115200):
        """Start background GSM modem polling thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                logger.info("GSM Modem service is already running on %s", self.port)
                return

            self.port = port or DBHandler.get_config().get("gsm_modem_port", "COM3")
            self.baudrate = baudrate or int(DBHandler.get_config().get("gsm_modem_baudrate", 115200))
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="GSM-Modem-Listener")
            self._thread.start()
            logger.info("GSM Modem service started on port %s (baud: %s)", self.port, self.baudrate)

    def stop(self):
        """Stop background GSM modem listener."""
        with self._lock:
            self._stop_event.set()
            if self._serial:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
            self.is_connected = False
            logger.info("GSM Modem service stopped.")

    def _send_at_cmd(self, ser, cmd: str, timeout: float = 2.0) -> str:
        """Send an AT command and return response lines."""
        try:
            ser.reset_input_buffer()
            ser.write((cmd + "\r\n").encode("utf-8"))
            deadline = time.time() + timeout
            response = []
            while time.time() < deadline:
                if ser.in_waiting:
                    line = ser.readline().decode("utf-8", errors="replace").strip()
                    if line:
                        response.append(line)
                        if "OK" in line or "ERROR" in line:
                            break
                else:
                    time.sleep(0.05)
            return "\n".join(response)
        except Exception as e:
            logger.warning("AT command %s failed: %s", cmd, e)
            return ""

    def _init_modem(self, ser) -> bool:
        """Configure modem for SMS text mode."""
        resp = self._send_at_cmd(ser, "AT", timeout=1.0)
        if "OK" not in resp:
            return False

        self._send_at_cmd(ser, "ATE0", timeout=1.0)       # Echo off
        self._send_at_cmd(ser, "AT+CMGF=1", timeout=1.0)  # Text mode
        self._send_at_cmd(ser, 'AT+CPMS="SM","SM","SM"', timeout=1.0)  # SIM storage
        return True

    def _read_unread_sms(self, ser):
        """Check and parse unread incoming SMS messages."""
        raw_output = self._send_at_cmd(ser, 'AT+CMGL="REC UNREAD"', timeout=3.0)
        if not raw_output or "OK" not in raw_output:
            return

        lines = raw_output.splitlines()
        idx = 0
        while idx < len(lines):
            line = lines[idx].strip()
            if line.startswith("+CMGL:"):
                # Header format: +CMGL: index,"REC UNREAD","+989...",,"date,time"
                parts = line.split(",")
                msg_index = parts[0].replace("+CMGL:", "").strip()
                from_num = parts[2].replace('"', '').strip() if len(parts) > 2 else None

                # Next line is message body
                idx += 1
                body_lines = []
                while idx < len(lines) and not lines[idx].strip().startswith("+CMGL:") and lines[idx].strip() != "OK":
                    body_lines.append(lines[idx].strip())
                    idx += 1

                body = "\n".join(body_lines).strip()
                if body:
                    logger.info("GSM received SMS from %s: %s", from_num, body[:80])
                    self.last_sms_received = f"{from_num}: {body[:40]}"
                    route_incoming_sms(body, from_number=from_num)
                    # Mark as read or delete from SIM
                    if msg_index.isdigit():
                        self._send_at_cmd(ser, f"AT+CMGD={msg_index}", timeout=1.0)
                continue
            idx += 1

    def _run_loop(self):
        """Continuous polling loop."""
        if not SERIAL_AVAILABLE:
            self.last_error = "pyserial کتابخانه نصب نیست. لطفا pip install pyserial را اجرا کنید."
            logger.warning(self.last_error)
            return

        while not self._stop_event.is_set():
            if not self.is_connected or not self._serial:
                try:
                    self._serial = serial.Serial(
                        port=self.port,
                        baudrate=self.baudrate,
                        timeout=2.0,
                        write_timeout=2.0,
                    )
                    if self._init_modem(self._serial):
                        self.is_connected = True
                        self.last_error = None
                        logger.info("Connected to GSM Modem on %s", self.port)
                    else:
                        self.is_connected = False
                        self.last_error = "Modem did not respond with OK to AT commands."
                        self._serial.close()
                        self._serial = None
                except Exception as e:
                    self.is_connected = False
                    self.last_error = f"Cannot open port {self.port}: {e}"
                    if self._stop_event.wait(5.0):
                        break
                    continue

            # Read SMS
            try:
                self.last_check_ts = time.time()
                self._read_unread_sms(self._serial)
            except Exception as e:
                logger.warning("Error reading from GSM Modem: %s", e)
                self.is_connected = False
                try:
                    if self._serial:
                        self._serial.close()
                except Exception:
                    pass
                self._serial = None

            if self._stop_event.wait(3.0):
                break

    def get_status(self) -> Dict[str, Any]:
        """Return diagnostic status of the GSM Modem service."""
        return {
            "serial_library_available": SERIAL_AVAILABLE,
            "running": bool(self._thread and self._thread.is_alive()),
            "connected": self.is_connected,
            "port": self.port,
            "baudrate": self.baudrate,
            "last_check_ts": self.last_check_ts,
            "last_sms": self.last_sms_received,
            "last_error": self.last_error,
        }


# Singleton service
_GSM_SERVICE = GSMModemService()


def get_gsm_service() -> GSMModemService:
    return _GSM_SERVICE


GsmModemService = GSMModemService

