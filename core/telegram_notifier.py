"""
core/telegram_notifier.py — Smart Telegram Notification Engine (MAHANBOT 2.0).

Delivers capacity radar alerts, provincial/city digests, tariff/capacity posts, and system warnings to Telegram
subscribers and channels with persistent queue delivery, rate limiting, and exponential backoff retry.
"""
from datetime import datetime
import hashlib
import json
import logging
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import urllib.request

from database import DBHandler

logger = logging.getLogger("mahanbot.telegram_notifier")


def hashlib_key(
    province_name: str,
    city_name: str,
    active_banks: List[Dict[str, Any]],
    reserve_banks: List[Dict[str, Any]],
) -> str:
    """Stable digest signature for Page-3 bank list broadcasts."""
    def _norm_items(items: List[Dict[str, Any]]) -> List[str]:
        rows = []
        for item in items or []:
            name = str(item.get("bank_name") or item.get("clean_title") or item.get("text") or "").strip()
            wait = item.get("waiting_count", 0) or 0
            rows.append(f"{name}|{wait}")
        return sorted(rows)

    payload = "|".join(
        [
            str(province_name or "").strip(),
            str(city_name or "").strip(),
            "A:" + ",".join(_norm_items(active_banks)),
            "R:" + ",".join(_norm_items(reserve_banks)),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


class TelegramNotifierService:
    """
    Manages Telegram Bot API communication, rate-limiting (25 msg/s),
    offline delivery queue, channel broadcasts, and subscriber preference filtering.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_send_ts = 0.0
        self._min_send_interval = 0.04  # ~25 messages/sec max rate limit
        self._good_proxy = ""
        self._proxy_scan_at = 0.0
        self._proxy_scan: List[str] = []

    DEFAULT_BOT_TOKEN = "8988974593:AAHnlt1ZVf5WAMFm59fbLtenxppF9aHM_Fc"
    # Prefer public @username — numeric IDs go stale when channel is recreated.
    DEFAULT_CHANNEL_ID = "@Mahanvpn3"
    DEFAULT_ADMIN_CHAT_ID = "156475654"

    @classmethod
    def ensure_defaults_in_config(cls) -> None:
        """Seed empty telegram fields so radar/page3 posts always have a live channel target."""
        try:
            cfg = DBHandler.get_config()
            updates = {}
            if not str(cfg.get("telegram_bot_token") or "").strip():
                updates["telegram_bot_token"] = cls.DEFAULT_BOT_TOKEN
            if not str(cfg.get("telegram_channel_id") or "").strip():
                updates["telegram_channel_id"] = cls.DEFAULT_CHANNEL_ID
            if not str(cfg.get("telegram_admin_chat_id") or "").strip():
                updates["telegram_admin_chat_id"] = cls.DEFAULT_ADMIN_CHAT_ID
            if "telegram_notifications_enabled" not in cfg:
                updates["telegram_notifications_enabled"] = True
            if updates:
                cfg.update(updates)
                DBHandler.update_config(cfg)
        except Exception as exc:
            logger.warning("telegram defaults seed failed: %s", exc)

    @classmethod
    def get_token(cls) -> str:
        """Fetch bot token from DB config or settings."""
        cfg = DBHandler.get_config()
        token = cfg.get("telegram_bot_token", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            try:
                with DBHandler._connect() as conn:
                    c = conn.cursor()
                    c.execute("SELECT value FROM settings WHERE key='telegram_bot_token'")
                    row = c.fetchone()
                    if row and row[0]:
                        token = str(row[0]).strip()
            except Exception:
                pass
        return (token or cls.DEFAULT_BOT_TOKEN).strip()

    @staticmethod
    def set_token(token: str) -> bool:
        """Store bot token in DB settings."""
        clean_token = str(token).strip()
        try:
            with DBHandler._connect() as conn:
                c = conn.cursor()
                c.execute(
                    "INSERT INTO settings (key, value) VALUES ('telegram_bot_token', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (clean_token,),
                )
                conn.commit()
                return True
        except Exception:
            return False

    @classmethod
    def get_admin_chat_id(cls) -> str:
        cfg = DBHandler.get_config()
        return str(cfg.get("telegram_admin_chat_id", "") or cls.DEFAULT_ADMIN_CHAT_ID).strip()

    @classmethod
    def get_channel_id(cls) -> str:
        cfg = DBHandler.get_config()
        cid = str(cfg.get("telegram_channel_id", "") or "").strip()
        if not cid:
            cid = str(cfg.get("telegram_admin_chat_id", "") or "").strip()
        # Legacy numeric default may not be @Mahanvpn3 anymore — prefer username.
        if not cid or cid == "-1003720759140":
            cid = cls.DEFAULT_CHANNEL_ID
        return cid.strip()

    @staticmethod
    def notifications_enabled() -> bool:
        cfg = DBHandler.get_config()
        return bool(cfg.get("telegram_notifications_enabled", True))

    @staticmethod
    def _configured_proxy_only() -> str:
        """Explicit Telegram proxy only. Never inherit a system VPN proxy."""
        cfg = DBHandler.get_config()
        configured = cfg.get("telegram_proxy") or os.environ.get("TELEGRAM_PROXY") or ""
        return str(configured).strip()

    @staticmethod
    def _looks_like_network_error(detail: str) -> bool:
        text = str(detail or "").lower()
        return any(token in text for token in ("proxy", "timed out", "timeout", "connection", "refused", "unreachable"))

    @classmethod
    def _resolve_channel_target(cls, chat_id: str) -> str:
        """The old numeric channel id is dead. Tests and posts go to @Mahanvpn3."""
        cid = str(chat_id or "").strip()
        if not cid or cid == "-1003720759140":
            return cls.get_channel_id()
        return cid

    @staticmethod
    def _expand_proxy(raw: str) -> List[str]:
        text = str(raw or "").strip()
        if not text:
            return []
        if "://" not in text:
            return [f"socks5h://{text}", f"http://{text}"]
        if text.startswith("socks5://"):
            return ["socks5h://" + text[len("socks5://"):]]
        if text.startswith("socks://"):
            return ["socks5h://" + text[len("socks://"):]]
        return [text]

    def _local_proxy_candidates(self) -> List[str]:
        """Open local inbounds only. The browser to ve.cbi.ir stays direct."""
        now = time.time()
        if now - self._proxy_scan_at < 15:
            return list(self._proxy_scan)
        import socket
        endpoints = (
            (10808, "socks5h"),
            (10809, "http"),
            (1080, "socks5h"),
            (7890, "http"),
            (7891, "socks5h"),
            (2080, "socks5h"),
            (2081, "http"),
            (20170, "socks5h"),
            (20171, "http"),
            (9050, "socks5h"),
        )
        found: List[str] = []
        for port, scheme in endpoints:
            sock = socket.socket()
            sock.settimeout(0.15)
            try:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    found.append(f"{scheme}://127.0.0.1:{port}")
            except Exception:
                pass
            finally:
                sock.close()
        self._proxy_scan = found
        self._proxy_scan_at = now
        return found

    def _proxy_candidates(self) -> List[str]:
        local = ""
        try:
            from core.telegram_local_proxy import ensure_telegram_proxy
            local = ensure_telegram_proxy(self.get_token())
        except Exception as exc:
            logger.warning("telegram local proxy skipped: %s", exc)
        ordered: List[str] = []
        for item in [self._good_proxy, local, *self._expand_proxy(self._configured_proxy_only()), *self._local_proxy_candidates()]:
            if item and item not in ordered:
                ordered.append(item)
        return ordered

    def _public_error(self, detail: str) -> str:
        text = str(detail or "")
        try:
            token = self.get_token()
        except Exception:
            token = ""
        if token and token in text:
            text = text.replace(token, "***")
        import re
        text = re.sub(r"/bot[0-9]+:[A-Za-z0-9_-]+", "/bot***", text)
        low = text.lower()
        if any(key in low for key in ("10061", "timed out", "timeout", "refused", "newconnection", "telegram_dns", "telegram_direct", "failed to establish", "getaddrinfo")):
            return (
                "تلگرام از این خط مستقیم باز نمی‌شود و DNS آن را به آی‌پی داخلی می‌برد. "
                "وی‌پی‌ان سراسری را روشن نکن تا سامانه ازدواج باز بماند. "
                "Happ را در حالت پروکسی محلی و بدون تونل روشن کن، یا در فیلد پروکسی بنویس socks5://127.0.0.1:10808"
            )
        return (text[:240] or "ارسال تلگرام ناموفق بود.")

    @staticmethod
    def _resolve_telegram_ips() -> List[str]:
        """Ask 8.8.8.8 directly. The system resolver answers 10.10.34.35 for api.telegram.org."""
        import random
        import socket
        import struct
        packet = struct.pack(">HHHHHH", random.randint(0, 65535), 0x0100, 1, 0, 0, 0)
        for part in "api.telegram.org".split("."):
            packet += bytes([len(part)]) + part.encode()
        packet += b"\x00" + struct.pack(">HH", 1, 1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2)
        try:
            sock.sendto(packet, ("8.8.8.8", 53))
            data, _ = sock.recvfrom(2048)
        except Exception:
            return []
        finally:
            sock.close()
        if len(data) < 12:
            return []
        qdcount, ancount = struct.unpack_from(">HH", data, 4)

        def skip_name(pos: int) -> int:
            while pos < len(data):
                size = data[pos]
                if size == 0:
                    return pos + 1
                if size & 0xC0 == 0xC0:
                    return pos + 2
                pos += 1 + size
            return pos

        index = 12
        for _ in range(qdcount):
            index = skip_name(index) + 4
        ips: List[str] = []
        for _ in range(ancount):
            index = skip_name(index)
            if index + 10 > len(data):
                break
            typ, _cls, _ttl, rdlen = struct.unpack_from(">HHIH", data, index)
            index += 10
            rdata = data[index:index + rdlen]
            index += rdlen
            if typ == 1 and len(rdata) == 4:
                ip = ".".join(str(part) for part in rdata)
                if not ip.startswith(("10.", "127.", "0.", "192.168.")):
                    ips.append(ip)
        return ips

    def _post_requests(self, url: str, payload: Dict[str, Any], proxy: str, timeout: float) -> Tuple[int, Dict[str, Any]]:
        import requests
        session = requests.Session()
        session.trust_env = False
        proxies = {"http": proxy, "https": proxy} if proxy else None
        resp = session.post(url, json=payload, proxies=proxies, timeout=timeout)
        try:
            data = resp.json()
        except Exception:
            data = {}
        return resp.status_code, data if isinstance(data, dict) else {}

    def _post_pinned(self, path: str, body: bytes, timeout: float) -> Tuple[int, Dict[str, Any]]:
        import http.client
        import socket
        import ssl
        ips = self._resolve_telegram_ips()
        if not ips:
            raise OSError("telegram_dns_blocked")

        class _Pin(http.client.HTTPSConnection):
            def connect(self):
                raw = socket.create_connection((self._ip, 443), self.timeout)
                self.sock = self._context.wrap_socket(raw, server_hostname="api.telegram.org")

        last: Optional[Exception] = None
        for ip in ips[:3]:
            conn = _Pin("api.telegram.org", timeout=timeout, context=ssl.create_default_context())
            conn._ip = ip
            try:
                conn.request(
                    "POST",
                    path,
                    body=body,
                    headers={
                        "Host": "api.telegram.org",
                        "Content-Type": "application/json; charset=utf-8",
                        "Connection": "close",
                    },
                )
                resp = conn.getresponse()
                raw = resp.read()
                try:
                    data = json.loads(raw.decode("utf-8"))
                except Exception:
                    data = {}
                return resp.status, data if isinstance(data, dict) else {}
            except Exception as exc:
                last = exc
            finally:
                conn.close()
        raise last or OSError("telegram_direct_failed")

    @staticmethod
    def _telegram_ok_detail(data: Dict[str, Any], prefix: str = "OK") -> Tuple[bool, str]:
        """Only treat as success when Telegram returns ok + message_id (no false positives)."""
        if not isinstance(data, dict) or not data.get("ok"):
            return False, str((data or {}).get("description") or "Telegram API Error")
        result = data.get("result") or {}
        msg_id = result.get("message_id") if isinstance(result, dict) else None
        if not msg_id:
            return False, "telegram_ok_without_message_id"
        chat = result.get("chat") if isinstance(result, dict) else {}
        chat_ref = ""
        if isinstance(chat, dict):
            chat_ref = str(chat.get("username") or chat.get("id") or "")
        detail = f"{prefix} msg_id={msg_id}"
        if chat_ref:
            detail = f"{detail} chat={chat_ref}"
        return True, detail

    def send_raw_telegram(
        self,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """Send message directly via Telegram Bot API with proxy support and rate limiting."""
        # Never fake-success just because unittest was imported elsewhere in-process.
        if os.environ.get("MAHANBOT_TEST_MODE") == "1" and not os.environ.get("MAHANBOT_ENABLE_LIVE_TELEGRAM"):
            return True, "OK (test mode)"
        token = self.get_token()
        if not token:
            return False, "توکن ربات تلگرام تنظیم نشده است."

        clean_cid = self._resolve_channel_target(chat_id)
        if not clean_cid:
            return False, "شناسه چت (chat_id) نامعتبر است."

        # Telegram rejects oversized HTML posts; keep under hard limit.
        if len(text or "") > 4000:
            text = (text or "")[:3900] + "\n…\n<i>(ادامه لیست کوتاه شد)</i>"

        cfg = DBHandler.get_config()
        base_url = (cfg.get("telegram_api_base_url") or os.environ.get("TELEGRAM_API_BASE_URL") or "https://api.telegram.org").rstrip("/")
        url = f"{base_url}/bot{token}/sendMessage"

        payload = {
            "chat_id": clean_cid,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        # Rate-limiting enforcement
        with self._lock:
            elapsed = time.time() - self._last_send_ts
            if elapsed < self._min_send_interval:
                time.sleep(self._min_send_interval - elapsed)
            self._last_send_ts = time.time()

        path = f"/bot{token}/sendMessage"
        body = json.dumps(payload).encode("utf-8")
        last_err = ""
        attempts: List[Tuple[str, float]] = [(item, 10) for item in self._proxy_candidates()]
        attempts.append(("", 5))
        for proxy, timeout in attempts:
            try:
                if proxy:
                    status, data = self._post_requests(url, payload, proxy, timeout)
                    prefix = "OK"
                else:
                    status, data = self._post_pinned(path, body, timeout)
                    prefix = "OK(direct)"
                if status != 200 and "parse" in str((data or {}).get("description") or "").lower() and payload.get("parse_mode"):
                    plain = dict(payload)
                    plain.pop("parse_mode", None)
                    if proxy:
                        status, data = self._post_requests(url, plain, proxy, timeout)
                    else:
                        status, data = self._post_pinned(path, json.dumps(plain).encode("utf-8"), timeout)
                    prefix = "OK(plain)"
                if status == 200:
                    ok, detail = self._telegram_ok_detail(data, prefix)
                    if ok:
                        if proxy:
                            self._good_proxy = proxy
                        return True, detail + (f" via {proxy}" if proxy else "")
                    last_err = detail
                else:
                    last_err = str((data or {}).get("description") or f"HTTP {status}")
                if proxy and not self._looks_like_network_error(last_err):
                    self._good_proxy = proxy
                    return False, self._public_error(last_err)
            except Exception as exc:
                last_err = str(exc)
                if proxy and proxy == self._good_proxy:
                    self._good_proxy = ""
        return False, self._public_error(last_err)

    # ── MESSAGE BUILDERS (Persian Formatting / Phase 6) ───────────────
    @staticmethod
    def format_capacity_change(
        province_name: str,
        city_name: str,
        bank_name: str,
        old_status: str,
        new_status: str,
        observed_at: float,
        loan_type: str = "marriage",
        visit_method: str = "all",
    ) -> str:
        """Format capacity state change alert with emojis and disclaimers."""
        from core.bank_matcher import bank_identity
        _, bank_title, bank_emoji = bank_identity(bank_name)
        status_map = {
            "AVAILABLE_OBSERVED": "🟢 دارای ظرفیت ثبت‌نام (مشاهده‌شده)",
            "AVAILABLE_CONFIRMED": "🟢 دارای ظرفیت قطعی",
            "RESERVE": "🟠 در انتظار تخصیص ظرفیت جدید (صف ذخیره)",
            "FULL": "🔴 تکمیل ظرفیت",
            "MAINTENANCE": "⚠️ سامانه در حال بروزرسانی / اختلال",
            "SESSION_EXPIRED": "⚪ انقضای نشست",
            "UNKNOWN": "⚪ نامشخص",
        }
        old_text = status_map.get(old_status, old_status)
        new_text = status_map.get(new_status, new_status)
        time_str = datetime.fromtimestamp(observed_at).strftime("%Y/%m/%d — %H:%M:%S")
        loan_label = "ازدواج" if loan_type == "marriage" else "فرزندآوری"
        method_label = "غیرحضوری (شعبه مجازی)" if visit_method == "online" else "حضوری / عمومی"

        msg = (
            f"🔔 <b>تغییر وضعیت ظرفیت بانک‌ها | رادار MAHANBOT</b>\n\n"
            f"📍 <b>استان:</b> {province_name}\n"
            f"🏙 <b>شهر:</b> {city_name}\n"
            f"{bank_emoji} <b>بانک:</b> {bank_title}\n"
            f"💍 <b>تسهیلات:</b> {loan_label} ({method_label})\n\n"
            f"<b>وضعیت قبلی:</b> {old_text}\n"
            f"<b>وضعیت جدید:</b> <b>{new_text}</b>\n\n"
            f"🕒 <b>زمان مشاهده:</b> {time_str}\n"
            f"📡 <b>منبع:</b> سامانه رسمی تسهیلات بانک مرکزی\n"
            f"🌐 <b>پیوند سامانه:</b> https://ve.cbi.ir\n\n"
            f"⚠️ <i>توجه: مشاهده نام بانک به منزله تضمین ثبت موفق نیست؛ تکمیل ثبت‌نام بر مبنای سرعت عمل و تایید نهایی سامانه انجام می‌شود.</i>"
        )
        return msg

    @staticmethod
    def format_radar_digest(
        province_name: str,
        city_name: str,
        bank_items: List[Dict[str, Any]],
        loan_type: str = "marriage",
    ) -> str:
        """Format provincial/city radar digest summary."""
        loan_label = "ازدواج" if loan_type == "marriage" else "فرزندآوری"
        time_str = datetime.now().strftime("%Y/%m/%d — %H:%M:%S")

        status_emojis = {
            "AVAILABLE_OBSERVED": "🟢",
            "AVAILABLE_CONFIRMED": "🟢",
            "RESERVE": "🟠",
            "FULL": "🔴",
            "MAINTENANCE": "⚠️",
            "SESSION_EXPIRED": "⚪",
            "UNKNOWN": "⚪",
        }
        status_persian = {
            "AVAILABLE_OBSERVED": "دارای ظرفیت ثبت‌نام",
            "AVAILABLE_CONFIRMED": "سهمیه قطعی",
            "RESERVE": "صف ذخیره (رزرو)",
            "FULL": "تکمیل ظرفیت",
            "MAINTENANCE": "درحال بروزرسانی",
            "SESSION_EXPIRED": "منقضی",
            "UNKNOWN": "نامشخص",
        }

        lines = [
            f"🚀 <b>رادار جامع ظرفیت بانک‌ها | MAHANBOT 2.0</b>\n",
            f"📍 <b>استان:</b> {province_name or 'سراسری'}",
            f"🏙 <b>شهر:</b> {city_name or 'کل استان'}",
            f"💍 <b>نوع تسهیلات:</b> {loan_label}\n",
            f"🏦 <b>وضعیت بانک‌ها:</b>",
        ]

        if not bank_items:
            lines.append("⚪ <i>اطلاعات فعالی برای این محدوده ثبت نشده است.</i>")
        else:
            from core.bank_matcher import bank_identity, bank_sort_key
            for item in sorted(bank_items, key=lambda it: bank_sort_key(it.get("bank_name"))):
                _, b_name, bank_emoji = bank_identity(item.get("bank_name", "بانک"))
                st = item.get("current_status", "UNKNOWN")
                emoji = status_emojis.get(st, "⚪")
                st_label = status_persian.get(st, st)
                lines.append(f"{bank_emoji} <b>{b_name}</b> — {emoji} {st_label}")

        lines.extend([
            f"\n🕒 <b>آخرین بروزرسانی:</b> {time_str}",
            f"📡 <b>منبع:</b> نشست مجاز سامانه تسهیلات قرض‌الحسنه بانک مرکزی",
            f"\n⚠️ <i>مشاهده نام بانک به معنی تضمین ثبت موفق نیست.</i>",
        ])
        return "\n".join(lines)

    @staticmethod
    def format_beautiful_radar_message(
        province_name: str,
        city_name: str = "",
        active_banks: Optional[List[Dict[str, Any]]] = None,
        reserve_banks: Optional[List[Dict[str, Any]]] = None,
        applicant_nid: str = "",
        action_status: str = "پایش فعال ظرفیت",
        provincial_only: bool = False,
    ) -> str:
        """Channel digest of page-3 banks.

        Every bank sits on its own line with its own emoji, in the official CBI
        order, grouped into open capacity and reserve queue.
        """
        import html
        from core.bank_matcher import bank_identity, bank_sort_key
        from core.persian_normalizer import gregorian_to_jalali, to_persian_digits

        def ordered(banks: Optional[List[Dict[str, Any]]]) -> List[Tuple[str, str, int]]:
            merged: Dict[str, Tuple[str, str, int]] = {}
            for bank in banks or []:
                raw = bank.get("bank_name") or bank.get("clean_title") or bank.get("text") or ""
                code, official, emoji = bank_identity(raw)
                waiting = int(bank.get("waiting_count") or 0)
                key = code or official
                if key not in merged or waiting > merged[key][2]:
                    merged[key] = (official, emoji, waiting)
            return sorted(merged.values(), key=lambda item: bank_sort_key(item[0]))

        def bank_line(official: str, emoji: str, waiting: int) -> str:
            queue = f" ┊ 👥 {to_persian_digits(waiting, group=True)} نفر در صف" if waiting > 0 else ""
            return f"{emoji} <b>{html.escape(official)}</b>{queue}"

        active = ordered(active_banks)
        reserve = ordered(reserve_banks)
        now = datetime.now()
        jy, jm, jd = gregorian_to_jalali(now.year, now.month, now.day)
        date_text = to_persian_digits(f"{jy}/{jm:02d}/{jd:02d}")
        time_text = to_persian_digits(now.strftime("%H:%M"))
        rule = "━━━━━━━━━━━━━━━━━━"

        place = f"📍 استان <b>{html.escape(province_name or 'نامشخص')}</b>"
        if city_name:
            place += f" · 🏙 شهر <b>{html.escape(city_name)}</b>"
        lines = [
            "📡 <b>رادار لحظه‌ای سهمیه وام ازدواج</b>",
            rule,
            place,
            f"🗓 {date_text}  ⏰ {time_text}",
            rule,
            f"🟢 <b>ظرفیت آزاد ثبت‌نام</b> — {to_persian_digits(len(active))} بانک",
        ]
        lines += [bank_line(*item) for item in active] or ["<i>فعلاً بانکی با ظرفیت آزاد در این استان نیست.</i>"]
        lines += ["", f"🟡 <b>صف ذخیره (رزرو)</b> — {to_persian_digits(len(reserve))} بانک"]
        lines += [bank_line(*item) for item in reserve] or ["<i>بانکی در صف ذخیره نیست.</i>"]

        if not provincial_only:
            masked_nid = ""
            if applicant_nid:
                s_nid = str(applicant_nid).strip()
                masked_nid = f"{s_nid[:2]}******{s_nid[-2:]}" if len(s_nid) >= 8 else s_nid
            nid_line = f" ({masked_nid})" if masked_nid else ""
            lines += ["", f"🎯 <b>وضعیت اقدام متقاضی{nid_line}:</b> <code>{html.escape(action_status)}</code>"]

        total = len(active) + len(reserve)
        lines += [
            rule,
            f"📊 مجموع {to_persian_digits(total)} بانک: {to_persian_digits(len(active))} آزاد · {to_persian_digits(len(reserve))} رزرو",
            "⚠️ <i>دیده شدن نام بانک به معنی تضمین ثبت نیست؛ ثبت نهایی با تأیید سامانه است.</i>",
            "",
            "📢 @Mahanvpn3 · 🤖 @MahanVPN_Shop_Bot",
        ]
        return "\n".join(lines)

    @staticmethod
    def format_channel_post(
        title: str = "🚀 ظرفیت جدید سرویس‌های MahanBot و سهمیه‌های بانکی",
        body_text: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Format official post for @Mahanvpn3 with inline glass buttons to @MahanVPN_Shop_Bot.
        """
        time_str = datetime.now().strftime("%Y/%m/%d — %H:%M")
        content = body_text or (
            "ظرفیت جدید سهمیه‌های بانکی و سرویس‌های پایدار فعال گردید.\n"
            "هم‌اکنون می‌توانید از طریق ربات و پنل اختصاصی وضعیت را بررسی و ثبت نمایید."
        )

        msg = (
            f"✨ <b>{title}</b>\n\n"
            f"{content}\n\n"
            f"🕒 <b>تاریخ انتشار:</b> {time_str}\n"
            f"📢 <b>کانال اطلاع‌رسانی:</b> @Mahanvpn3\n"
            f"🤖 <b>ربات مدیریت و خرید:</b> @MahanVPN_Shop_Bot\n"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "🛍 ربات فروش و مدیریت اشتراک", "url": "https://t.me/MahanVPN_Shop_Bot"},
                ],
                [
                    {"text": "📢 کانال اطلاع‌رسانی @Mahanvpn3", "url": "https://t.me/Mahanvpn3"},
                    {"text": "🌐 سامانه تسهیلات", "url": "https://ve.cbi.ir"},
                ],
            ]
        }
        return msg, reply_markup

    def broadcast_page3_banks_to_channel(
        self,
        *,
        province_name: str,
        city_name: str = "",
        active_banks: Optional[List[Dict[str, Any]]] = None,
        reserve_banks: Optional[List[Dict[str, Any]]] = None,
        applicant_nid: str = "",
        action_status: str = "استخراج زنده صفحه ۳",
        provincial_only: bool = True,
        force: bool = False,
        dedupe_key: str = "",
    ) -> Tuple[bool, str]:
        """
        Send the full Page-3 bank extraction digest directly to the configured Telegram channel.
        Dedupes identical digests unless force=True.
        """
        active_banks = active_banks or []
        reserve_banks = reserve_banks or []
        if not active_banks and not reserve_banks:
            return False, "empty_bank_list"
        if not self.notifications_enabled():
            return False, "notifications_disabled"

        self.ensure_defaults_in_config()
        key = dedupe_key or hashlib_key(province_name, city_name, active_banks, reserve_banks)
        province_key = str(province_name or "سراسری")
        with self._lock:
            last = getattr(self, "_last_page3_digest_keys", {}) or {}
            if not force and last.get(province_key) == key:
                return False, "duplicate_digest"

        body = self.format_beautiful_radar_message(
            province_name=province_name or "سراسری",
            city_name="" if provincial_only else (city_name or ""),
            active_banks=active_banks,
            reserve_banks=reserve_banks,
            applicant_nid="" if provincial_only else applicant_nid,
            action_status=action_status,
            provincial_only=provincial_only,
        )
        channel_cid = self.get_channel_id()
        if not channel_cid:
            return False, "channel_not_configured"

        # Attach channel/shop buttons for channel posts
        _, reply_markup = self.format_channel_post(
            title="📡 رادار ظرفیت بانک‌ها",
            body_text=f"استان {province_name or 'سراسری'} — {len(active_banks)} ظرفیت قطعی / {len(reserve_banks)} رزرو",
        )
        self.start_background_worker()
        ok, detail = self.send_raw_telegram(channel_cid, body, reply_markup=reply_markup)
        if ok:
            with self._lock:
                last = getattr(self, "_last_page3_digest_keys", {}) or {}
                last[province_key] = key
                if len(last) > 64:
                    last = dict(list(last.items())[-32:])
                self._last_page3_digest_keys = last
            # Also mirror to admin if different from channel
            admin_cid = self.get_admin_chat_id()
            if admin_cid and str(admin_cid).lower() != str(channel_cid).lower():
                try:
                    self.send_raw_telegram(admin_cid, body)
                except Exception:
                    pass
            return True, f"sent:{channel_cid}:{detail}"
        # Fallback to durable queue if direct send failed
        try:
            DBHandler.enqueue_telegram_message(
                chat_id=channel_cid,
                message_text=body,
                message_type="page3_bank_digest",
                event_id=f"page3-{int(time.time())}",
                scheduled_at=int(time.time()),
            )
            self.start_background_worker()
            return False, f"queued:{channel_cid}:{detail}"
        except Exception as exc:
            return False, f"send_failed:{channel_cid}:{detail}:{exc}"

    # ── NOTIFICATION DISPATCHING & QUEUE (Phase 6 & 10) ───────────────
    def dispatch_capacity_event(
        self,
        event_id: str,
        province_name: str,
        city_name: str,
        bank_name: str,
        old_status: str,
        new_status: str,
        observed_at: float,
        loan_type: str = "marriage",
        visit_method: str = "all",
    ) -> int:
        """
        Filter subscribers by preferences and enqueue personalized notification messages.
        Returns count of enqueued messages.
        """
        msg_text = self.format_capacity_change(
            province_name=province_name,
            city_name=city_name,
            bank_name=bank_name,
            old_status=old_status,
            new_status=new_status,
            observed_at=observed_at,
            loan_type=loan_type,
            visit_method=visit_method,
        )

        subscribers = DBHandler.get_telegram_subscribers()
        enqueued_count = 0
        obs_time = int(observed_at)

        for sub in subscribers:
            if not sub.get("is_active"):
                continue

            # Preference filters:
            if new_status in ("AVAILABLE_OBSERVED", "AVAILABLE_CONFIRMED") and not sub.get("notify_available", 1):
                continue
            if new_status == "RESERVE" and not sub.get("notify_reserve", 1):
                continue

            # Province filter
            provinces = sub.get("favorite_provinces") or []
            if provinces and province_name not in provinces:
                continue

            # Bank filter
            favs = sub.get("favorite_banks") or []
            if favs and not any(f in bank_name or bank_name in f for f in favs):
                continue

            chat_id = str(sub.get("chat_id", "")).strip()
            if chat_id:
                DBHandler.enqueue_telegram_message(
                    chat_id=chat_id,
                    message_text=msg_text,
                    message_type="capacity_change",
                    event_id=event_id,
                    scheduled_at=obs_time,
                )
                enqueued_count += 1

        # Also notify Admin or Channel if configured
        admin_cid = self.get_admin_chat_id()
        channel_cid = self.get_channel_id()
        for cid in filter(None, [admin_cid, channel_cid]):
            if not any(str(sub.get("chat_id", "")) == cid for sub in subscribers):
                DBHandler.enqueue_telegram_message(
                    chat_id=cid,
                    message_text=msg_text,
                    message_type="admin_alert",
                    event_id=event_id,
                    scheduled_at=obs_time,
                )
                enqueued_count += 1

        # Trigger immediate pump of the delivery queue
        self.process_queue_batch(limit=25)
        return enqueued_count

    def send_test_message(
        self,
        chat_id: Optional[str] = None,
        custom_message: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Send a test verification message to target chat_id or configured admin/channel."""
        target_cid = self._resolve_channel_target(chat_id or "")
        if not target_cid:
            return False, "شناسه چت یا کانال تنظیم نشده است. لطفاً شناسه چت یا کانال تلگرام را وارد کنید."

        time_str = datetime.now().strftime("%Y/%m/%d — %H:%M:%S")
        test_text = custom_message or (
            f"🚀 <b>تست موفقیت‌آمیز اتصال ربات تلگرام | MAHANBOT 2.0</b>\n\n"
            f"✅ ربات @MahanVPN_Shop_Bot با موفقیت به سامانه متصل شد.\n"
            f"📢 کانال متصل: @Mahanvpn3 ({target_cid})\n"
            f"🕒 زمان تست: {time_str}\n\n"
            f"سیستم رادار آماده ارسال لحظه‌ای ظرفیت‌ها و هشدارهای ساعت ۱۸ می‌باشد."
        )

        _, reply_markup = self.format_channel_post(
            title="تست اتصال ربات تلگرام MAHANBOT",
            body_text=test_text,
        )

        ok, err = self.send_raw_telegram(
            chat_id=target_cid,
            text=test_text,
            reply_markup=reply_markup,
        )
        if ok:
            return True, f"پیام تست با موفقیت به شناسه {target_cid} ارسال شد."
        else:
            # Also enqueue it so when internet/proxy is available, it gets sent
            DBHandler.enqueue_telegram_message(
                chat_id=target_cid,
                message_text=test_text,
                message_type="test_alert",
            )
            return False, f"خطا در ارسال مستقیم ({err})؛ پیام در صف ارسال خودکار قرار گرفت."

    def process_queue_batch(self, limit: int = 25) -> int:
        """Process pending queue messages."""
        token = self.get_token()
        if not token:
            return 0

        pending = DBHandler.fetch_pending_telegram_messages(limit=limit)
        sent_count = 0

        for msg in pending:
            msg_id = msg["id"]
            chat_id = msg["chat_id"]
            text = msg["message_text"]

            reply_markup = None
            if str(chat_id).startswith("-100") or str(chat_id) == str(self.get_channel_id()):
                _, reply_markup = self.format_channel_post()

            ok, res = self.send_raw_telegram(chat_id=chat_id, text=text, reply_markup=reply_markup)
            if ok:
                DBHandler.mark_telegram_message_sent(msg_id)
                sent_count += 1
            else:
                logger.warning(f"Telegram send failed for {chat_id}: {res}")
                DBHandler.mark_telegram_message_failed(msg_id, error_message=res, retry=True)

        return sent_count

    def start_background_worker(self):
        """Start persistent background delivery thread if not already running."""
        with self._lock:
            if self._worker_thread and self._worker_thread.is_alive():
                return
            self._stop_event.clear()
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()

    def stop_background_worker(self):
        self._stop_event.set()

    def _worker_loop(self):
        """Background loop flushing delivery queue every 2 seconds."""
        while not self._stop_event.is_set():
            try:
                self.process_queue_batch(limit=25)
            except Exception as e:
                logger.error(f"Error in telegram worker loop: {e}")
            self._stop_event.wait(2.0)


TELEGRAM_NOTIFIER = TelegramNotifierService()
