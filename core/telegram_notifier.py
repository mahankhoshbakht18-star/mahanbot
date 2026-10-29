"""
core/telegram_notifier.py — Smart Telegram Notification Engine (MAHANBOT 2.0).

Delivers capacity radar alerts, provincial/city digests, tariff/capacity posts, and system warnings to Telegram
subscribers and channels with persistent queue delivery, rate limiting, and exponential backoff retry.
"""
from datetime import datetime
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import urllib.request

from database import DBHandler

logger = logging.getLogger("mahanbot.telegram_notifier")


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

    @staticmethod
    def get_token() -> str:
        """Fetch bot token from DB config or settings."""
        cfg = DBHandler.get_config()
        token = cfg.get("telegram_bot_token", "")
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
        return token.strip()

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

    @staticmethod
    def get_admin_chat_id() -> str:
        cfg = DBHandler.get_config()
        return str(cfg.get("telegram_admin_chat_id", "")).strip()

    @staticmethod
    def get_channel_id() -> str:
        cfg = DBHandler.get_config()
        return str(cfg.get("telegram_channel_id", "") or cfg.get("telegram_admin_chat_id", "")).strip()

    def send_raw_telegram(
        self,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """Send message directly via Telegram Bot API using urllib with optional proxy and inline markup."""
        token = self.get_token()
        if not token:
            return False, "توکن ربات تلگرام تنظیم نشده است."

        clean_cid = str(chat_id).strip()
        if not clean_cid:
            return False, "شناسه چت (chat_id) نامعتبر است."

        url = f"https://api.telegram.org/bot{token}/sendMessage"
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

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=req_data,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )

            # Check proxy
            cfg = DBHandler.get_config()
            proxy = cfg.get("telegram_proxy") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
            if proxy:
                proxy_handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                opener = urllib.request.build_opener(proxy_handler)
            else:
                opener = urllib.request.build_opener()

            with opener.open(req, timeout=12) as response:
                resp_bytes = response.read()
                resp_json = json.loads(resp_bytes.decode("utf-8"))
                if resp_json.get("ok"):
                    return True, "OK"
                else:
                    return False, resp_json.get("description", "Telegram API Error")
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")
                err_json = json.loads(err_body)
                desc = err_json.get("description", str(e))
                return False, f"HTTP {e.code}: {desc}"
            except Exception:
                return False, f"HTTP Error {e.code}: {e.reason}"
        except Exception as e:
            return False, str(e)

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
            f"🏦 <b>بانک:</b> {bank_name}\n"
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
            for item in bank_items:
                b_name = item.get("bank_name", "بانک")
                st = item.get("current_status", "UNKNOWN")
                emoji = status_emojis.get(st, "⚪")
                st_label = status_persian.get(st, st)
                lines.append(f"{emoji} <b>{b_name}</b> — {st_label}")

        lines.extend([
            f"\n🕒 <b>آخرین بروزرسانی:</b> {time_str}",
            f"📡 <b>منبع:</b> نشست مجاز سامانه تسهیلات قرض‌الحسنه بانک مرکزی",
            f"\n⚠️ <i>مشاهده نام بانک به معنی تضمین ثبت موفق نیست.</i>",
        ])
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
        target_cid = (chat_id or self.get_channel_id() or self.get_admin_chat_id() or "").strip()
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
