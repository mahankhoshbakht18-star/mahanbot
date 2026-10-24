# core/otp_queue.py
"""
core/otp_queue.py — Independent OTP Request Queue & Matching Engine for MAHANBOT

Features:
- Unique OTP request ID per authentication attempt
- Correlation with applicant, phone number, and browser session
- Prevention of duplicate/cross-applicant code delivery
- Configurable poll intervals and TTL timeouts
- Full audit log of delivery and verification status
"""
import logging
import re
import secrets
import time
from typing import Optional, Dict, Any, List, Tuple

from database import DBHandler
from event_logger import EVENT_BROADCASTER, build_event

logger = logging.getLogger("mahanbot.otp_queue")


def generate_otp_request_id(nid: str) -> str:
    """Generate a cryptographically random, unique OTP request ID."""
    clean_nid = str(nid).strip()
    return f"otp_{clean_nid[-4:]}_{int(time.time())}_{secrets.token_hex(4)}"


def normalize_phone(phone: Optional[str]) -> str:
    """Normalize Iranian phone number to standard 11-digit 09xxxxxxxxx format."""
    if not phone:
        return ""
    p = str(phone).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")).strip()
    p = re.sub(r"[^0-9]", "", p)
    if p.startswith("0098"):
        p = "0" + p[4:]
    elif p.startswith("98"):
        p = "0" + p[2:]
    elif p.startswith("+98"):
        p = "0" + p[3:]
    return p


class OTPQueueManager:
    @staticmethod
    def create_request(
        applicant_id: str,
        phone_reference: Optional[str] = None,
        session_id: Optional[str] = None,
        ttl_seconds: int = 120,
        step: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new independent OTP request for an applicant entering Step 2."""
        nid = str(applicant_id).strip()
        now = time.time()
        cfg = DBHandler.get_config() or {}
        configured_ttl = int(cfg.get("otp_wait_timeout_seconds", cfg.get("otp_validity_seconds", ttl_seconds)))
        expires_at = now + configured_ttl

        # Lookup phone if not provided
        if not phone_reference:
            app = DBHandler.get_applicant(nid)
            if app:
                d = dict(app).get("data")
                if isinstance(d, str):
                    import json
                    try:
                        d = json.loads(d)
                    except Exception:
                        d = {}
                phone_reference = (d or {}).get("mobile") or (d or {}).get("phone") or ""

        phone = normalize_phone(phone_reference)
        req_id = generate_otp_request_id(nid)

        try:
            with DBHandler._connect() as conn:
                # Invalidate any prior pending requests for this applicant
                conn.execute(
                    """UPDATE otp_requests
                       SET delivery_status = 'EXPIRED', updated_at = ?
                       WHERE applicant_id = ? AND delivery_status IN ('CREATED', 'WAITING_DELIVERY')""",
                    (now, nid),
                )
                conn.execute(
                    """INSERT INTO otp_requests
                       (otp_request_id, applicant_id, session_id, phone_reference,
                        request_started_at, expires_at, delivery_status, attempt_count,
                        verification_status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'WAITING_DELIVERY', 0, 'pending', ?, ?)""",
                    (req_id, nid, session_id or "", phone, now, expires_at, now, now),
                )
                conn.commit()
        except Exception as e:
            logger.error("Failed to insert otp_request for %s: %s", nid, e)

        # Update runtime reference
        DBHandler.update_runtime(nid, otp_request_id=req_id, otp_state="WAITING_SMS")

        # Emit live event
        EVENT_BROADCASTER.emit_event(
            build_event(
                "otp_request_created",
                nid=nid,
                otp_request_id=req_id,
                phone=phone,
                expires_at=expires_at,
                ttl=configured_ttl,
            )
        )
        logger.info("Created OTP request %s for %s (phone: %s, TTL: %ds)", req_id, nid, phone, configured_ttl)
        return {
            "otp_request_id": req_id,
            "applicant_id": nid,
            "session_id": session_id,
            "phone_reference": phone,
            "request_started_at": now,
            "expires_at": expires_at,
            "delivery_status": "WAITING_DELIVERY",
        }

    @staticmethod
    def get_active_request(applicant_id: str) -> Optional[Dict[str, Any]]:
        """Get the current unexpired active OTP request for an applicant."""
        nid = str(applicant_id).strip()
        now = time.time()
        try:
            with DBHandler._connect() as conn:
                row = conn.execute(
                    """SELECT otp_request_id, applicant_id, session_id, phone_reference,
                              request_started_at, sms_received_at, expires_at, sms_source,
                              otp_code, delivery_status, attempt_count, verification_status
                       FROM otp_requests
                       WHERE applicant_id = ? AND delivery_status IN ('WAITING_DELIVERY', 'RECEIVED')
                         AND expires_at > ?
                       ORDER BY id DESC LIMIT 1""",
                    (nid, now),
                ).fetchone()
                if row:
                    cols = ["otp_request_id", "applicant_id", "session_id", "phone_reference",
                            "request_started_at", "sms_received_at", "expires_at", "sms_source",
                            "otp_code", "delivery_status", "attempt_count", "verification_status"]
                    return dict(zip(cols, row))
        except Exception as e:
            logger.warning("Error fetching active otp_request for %s: %s", nid, e)
        return None

    @staticmethod
    def deliver_otp(
        code: str,
        source: str = "android",
        phone: Optional[str] = None,
        explicit_nid: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Deliver incoming OTP code to the appropriate waiting request.
        Guarantees:
        1. No cross-applicant misdelivery.
        2. No reuse of consumed code.
        3. Expiry verification.
        """
        clean_code = str(code).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")).strip()
        if not re.fullmatch(r"[0-9]{4,8}", clean_code):
            return False, {"error": "invalid_code_format", "message": "کد پیامک نامعتبر است."}

        norm_phone = normalize_phone(phone)
        norm_nid = str(explicit_nid).strip() if explicit_nid else None
        now = time.time()

        # Step 1: Match by explicit request_id
        target_req = None
        if request_id:
            with DBHandler._connect() as conn:
                row = conn.execute(
                    """SELECT id, otp_request_id, applicant_id, phone_reference, expires_at, delivery_status
                       FROM otp_requests WHERE otp_request_id = ?""",
                    (request_id,),
                ).fetchone()
                if row:
                    target_req = {
                        "id": row[0], "otp_request_id": row[1], "applicant_id": row[2],
                        "phone_reference": row[3], "expires_at": row[4], "delivery_status": row[5]
                    }

        # Step 2: Match by explicit NID
        if not target_req and norm_nid:
            active = OTPQueueManager.get_active_request(norm_nid)
            if active:
                target_req = active

        # Step 3: Match by Phone reference
        if not target_req and norm_phone:
            with DBHandler._connect() as conn:
                rows = conn.execute(
                    """SELECT id, otp_request_id, applicant_id, phone_reference, expires_at, delivery_status
                       FROM otp_requests
                       WHERE phone_reference = ? AND delivery_status = 'WAITING_DELIVERY' AND expires_at > ?
                       ORDER BY id DESC""",
                    (norm_phone, now),
                ).fetchall()
                if len(rows) == 1:
                    row = rows[0]
                    target_req = {
                        "id": row[0], "otp_request_id": row[1], "applicant_id": row[2],
                        "phone_reference": row[3], "expires_at": row[4], "delivery_status": row[5]
                    }
                elif len(rows) > 1:
                    # Ambiguity among multiple waiting applicants sharing same phone
                    logger.warning("Multiple active requests match phone %s; requires manual confirmation", norm_phone)
                    return False, {
                        "error": "ambiguous_phone_match",
                        "candidates": [r[2] for r in rows],
                        "message": "چندین متقاضی همزمان با این شماره تلفن منتظر پیامک هستند؛ ورود دستی الزامی است.",
                    }

        # Step 4: Fallback to single active waiter (strictly ONLY if exactly 1 waiter exists)
        if not target_req and not norm_nid and not norm_phone:
            with DBHandler._connect() as conn:
                rows = conn.execute(
                    """SELECT id, otp_request_id, applicant_id, phone_reference, expires_at, delivery_status
                       FROM otp_requests
                       WHERE delivery_status = 'WAITING_DELIVERY' AND expires_at > ?
                       ORDER BY id DESC""",
                    (now,),
                ).fetchall()
                if len(rows) == 1:
                    row = rows[0]
                    target_req = {
                        "id": row[0], "otp_request_id": row[1], "applicant_id": row[2],
                        "phone_reference": row[3], "expires_at": row[4], "delivery_status": row[5]
                    }
                elif len(rows) > 1:
                    # Multiple applicants waiting; do NOT randomly guess!
                    logger.warning("Incoming SMS without NID or Phone cannot be disambiguated among %d waiters", len(rows))
                    return False, {
                        "error": "multiple_waiters_ambiguity",
                        "count": len(rows),
                        "message": "بیش از یک متقاضی منتظر پیامک هستند و پیامک ورودی فاقد کد ملی یا شماره است.",
                    }

        if not target_req:
            return False, {"error": "no_matching_request", "message": "هیچ درخواست فعالی برای این پیامک یافت نشد."}

        nid = target_req["applicant_id"]
        req_id = target_req["otp_request_id"]

        # Check expiry
        if now > target_req["expires_at"]:
            try:
                with DBHandler._connect() as conn:
                    conn.execute("UPDATE otp_requests SET delivery_status = 'EXPIRED', updated_at = ? WHERE id = ?", (now, target_req["id"]))
                    conn.commit()
            except Exception:
                pass
            return False, {"error": "request_expired", "message": "مهلت زمانی این درخواست پیامک به پایان رسیده است."}

        # Update request as RECEIVED
        try:
            with DBHandler._connect() as conn:
                conn.execute(
                    """UPDATE otp_requests
                       SET otp_code = ?, sms_source = ?, sms_received_at = ?,
                           delivery_status = 'RECEIVED', updated_at = ?
                       WHERE otp_request_id = ?""",
                    (clean_code, source, now, now, req_id),
                )
                conn.commit()
        except Exception as e:
            logger.error("Failed to update otp_request %s: %s", req_id, e)
            return False, {"error": "db_update_failed"}

        # Sync with legacy DBHandler & runtime for existing bots
        DBHandler.set_otp(nid, clean_code, ts=now, status="received", request_id=req_id)
        DBHandler.update_runtime(nid, otp_state="SMS_RECEIVED", last_otp_code=clean_code)

        # Notify waiting threads
        from routes.otp import _signal_otp_event, _emit_applicants_snapshot
        import core.state as _state
        import asyncio
        if _state.MAIN_LOOP:
            _state.MAIN_LOOP.call_soon_threadsafe(lambda: asyncio.create_task(_signal_otp_event(nid)))

        # Emit live event
        EVENT_BROADCASTER.emit_event(
            build_event(
                "otp_delivered",
                nid=nid,
                otp_request_id=req_id,
                source=source,
                code_masked=f"{clean_code[:2]}**{clean_code[-2:]}" if len(clean_code) >= 4 else "***",
                ts=now,
            )
        )
        _emit_applicants_snapshot()
        logger.info("OTP code delivered to applicant %s (request %s, source: %s)", nid, req_id, source)
        return True, {
            "status": "ok",
            "applicant_id": nid,
            "otp_request_id": req_id,
            "code": clean_code,
            "source": source,
        }

    @staticmethod
    def consume_otp(request_id_or_nid: str) -> Optional[str]:
        """
        Atomically fetch and consume a delivered OTP code.
        Guarantees single-use: marks the request as CONSUMED / VERIFIED.
        """
        key = str(request_id_or_nid).strip()
        now = time.time()
        try:
            with DBHandler._connect() as conn:
                row = conn.execute(
                    """SELECT otp_request_id, applicant_id, otp_code, expires_at
                       FROM otp_requests
                       WHERE (otp_request_id = ? OR applicant_id = ?)
                         AND delivery_status IN ('RECEIVED', 'DELIVERED')
                         AND verification_status != 'consumed'
                         AND expires_at > ?
                       ORDER BY id DESC LIMIT 1""",
                    (key, key, now),
                ).fetchone()
                if not row or not row[2]:
                    return None
                req_id, nid, code, _ = row
                conn.execute(
                    """UPDATE otp_requests
                       SET verification_status = 'consumed', delivery_status = 'CONSUMED', updated_at = ?
                       WHERE otp_request_id = ?""",
                    (now, req_id),
                )
                conn.commit()
                return code
        except Exception as e:
            logger.error("Failed to consume OTP for %s: %s", key, e)
            return None

    @staticmethod
    def mark_verified(applicant_id: str, request_id: Optional[str] = None) -> None:
        """Mark the OTP request as successfully consumed and verified."""
        nid = str(applicant_id).strip()
        now = time.time()
        try:
            with DBHandler._connect() as conn:
                if request_id:
                    conn.execute(
                        """UPDATE otp_requests
                           SET delivery_status = 'VERIFIED', verification_status = 'verified', updated_at = ?
                           WHERE otp_request_id = ?""",
                        (now, request_id),
                    )
                else:
                    conn.execute(
                        """UPDATE otp_requests
                           SET delivery_status = 'VERIFIED', verification_status = 'verified', updated_at = ?
                           WHERE applicant_id = ? AND delivery_status = 'RECEIVED'""",
                        (now, nid),
                    )
                conn.commit()
        except Exception as e:
            logger.warning("Error marking OTP verified for %s: %s", nid, e)

    @staticmethod
    def mark_expired(applicant_id: str, request_id: Optional[str] = None, reason: str = "expired") -> None:
        """Mark the OTP request as expired (e.g. CBI rejected code as expired)."""
        nid = str(applicant_id).strip()
        now = time.time()
        try:
            with DBHandler._connect() as conn:
                if request_id:
                    conn.execute(
                        """UPDATE otp_requests
                           SET delivery_status = 'EXPIRED', verification_status = ?, updated_at = ?
                           WHERE otp_request_id = ?""",
                        (reason, now, request_id),
                    )
                else:
                    conn.execute(
                        """UPDATE otp_requests
                           SET delivery_status = 'EXPIRED', verification_status = ?, updated_at = ?
                           WHERE applicant_id = ? AND delivery_status IN ('WAITING_DELIVERY', 'RECEIVED')""",
                        (reason, now, nid),
                    )
                conn.commit()
        except Exception as e:
            logger.warning("Error marking OTP expired for %s: %s", nid, e)

    @staticmethod
    def list_requests(limit: int = 50) -> List[Dict[str, Any]]:
        """List recent OTP requests with full status."""
        cols = ["id", "otp_request_id", "applicant_id", "session_id", "phone_reference",
                "request_started_at", "sms_received_at", "expires_at", "sms_source",
                "otp_code", "delivery_status", "attempt_count", "verification_status",
                "created_at", "updated_at"]
        try:
            with DBHandler._connect() as conn:
                rows = conn.execute(
                    f"SELECT {','.join(cols)} FROM otp_requests ORDER BY id DESC LIMIT ?",
                    (max(1, min(limit, 200)),),
                ).fetchall()
                result = []
                for r in rows:
                    item = dict(zip(cols, r))
                    code = item.get("otp_code")
                    if code:
                        item["masked_code"] = f"{code[:2]}***{code[-1]}" if len(code) >= 3 else "***"
                    else:
                        item["masked_code"] = "-"
                    item.pop("otp_code", None)  # Mask secret from general payload
                    result.append(item)
                return result
        except Exception as e:
            logger.warning("Error listing otp_requests: %s", e)
            return []
