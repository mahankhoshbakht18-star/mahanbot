from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings, get_settings
from .db import Database
from .models import Device, DeviceToken, OtpMessage, OtpSession, PairingCode, SessionStatus
from .schemas import DeviceOtp, PairingClaim, PairingCreate, SessionAction, SessionCreate, SessionExtend
from .security import decrypt_otp, encrypt_otp, issue_token, matches, national_id_hash, secret_hash

bearer = HTTPBearer(auto_error=False)


def now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """SQLite drops timezone metadata; production PostgreSQL preserves it."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    database = Database(cfg.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if cfg.environment == "test":
            await database.create_for_tests()
        yield
        await database.engine.dispose()

    app = FastAPI(title="Mahan OTP Relay", version="2.0", lifespan=lifespan)
    app.state.settings = cfg
    app.state.database = database
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=cfg.trusted_hosts + ["testserver"])

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        if int(request.headers.get("content-length", "0") or 0) > cfg.max_body_bytes:
            return JSONResponse({"detail": "request too large"}, status_code=413)
        if cfg.force_https and request.url.scheme != "https" and request.client and request.client.host not in {"127.0.0.1", "::1"}:
            return JSONResponse({"detail": "https required"}, status_code=400)
        response: Response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store", "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
        })
        return response

    async def db_session():
        async for session in database.session():
            yield session

    def require_static(expected: str):
        async def dependency(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
            if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
                raise HTTPException(status_code=401, detail="invalid authentication")
        return dependency

    require_bot = require_static(cfg.bot_token.get_secret_value())
    require_admin = require_static(cfg.admin_token.get_secret_value())

    async def require_device(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        session: AsyncSession = Depends(db_session),
    ) -> Device:
        if credentials is None:
            raise HTTPException(status_code=401, detail="invalid device authentication")
        digest = secret_hash(credentials.credentials, cfg.token_pepper.get_secret_value())
        row = (await session.execute(select(DeviceToken).where(DeviceToken.token_hash == digest))).scalar_one_or_none()
        if row is None or row.revoked_at is not None or as_utc(row.expires_at) <= now():
            raise HTTPException(status_code=401, detail="invalid device authentication")
        device = await session.get(Device, row.device_id)
        if device is None or device.revoked_at is not None or device.status != "ACTIVE":
            raise HTTPException(status_code=403, detail="device revoked")
        row.last_used_at = now()
        device.last_seen_at = now()
        await session.commit()
        return device

    @app.get("/status")
    async def health():
        return {"ok": True, "service": "mahan-otp-relay", "version": "2.0"}

    @app.get("/api/v2/version")
    async def version():
        return {"api": "v2", "compat_v1": cfg.enable_otp_v1}

    @app.post("/api/v2/pairing/create", dependencies=[Depends(require_bot)])
    async def create_pairing(payload: PairingCreate, session: AsyncSession = Depends(db_session)):
        code = "".join(secrets.choice("0123456789") for _ in range(payload.digits))
        session.add(PairingCode(
            code_hash=secret_hash(code, cfg.token_pepper.get_secret_value()),
            created_by=payload.created_by, expires_at=now() + timedelta(minutes=5),
        ))
        await session.commit()
        return {"ok": True, "code": code, "expires_in_seconds": 300, "qr_payload": f"mahanbot-otp://pair/{code}"}

    @app.post("/api/v2/pairing/claim")
    async def claim_pairing(payload: PairingClaim, session: AsyncSession = Depends(db_session)):
        digest = secret_hash(payload.code, cfg.token_pepper.get_secret_value())
        pairing = (await session.execute(select(PairingCode).where(PairingCode.code_hash == digest).with_for_update())).scalar_one_or_none()
        if pairing is None:
            raise HTTPException(status_code=404, detail="pairing code not found")
        pairing.attempt_count += 1
        if pairing.claimed_at or as_utc(pairing.expires_at) <= now() or pairing.attempt_count > pairing.max_attempts:
            await session.commit()
            raise HTTPException(status_code=410, detail="pairing code expired")
        if not payload.consent:
            await session.commit()
            raise HTTPException(status_code=422, detail="consent required")
        raw_token = issue_token()
        device = Device(public_device_id="dev_" + secrets.token_hex(12), display_name=payload.display_name,
                        app_version=payload.app_version, app_flavor=payload.app_flavor,
                        consent_version=payload.consent_version)
        session.add(device)
        await session.flush()
        session.add(DeviceToken(device_id=device.id,
                                token_hash=secret_hash(raw_token, cfg.token_pepper.get_secret_value()),
                                expires_at=now() + timedelta(days=90)))
        pairing.claimed_at = now()
        await session.commit()
        return {"ok": True, "device_id": device.public_device_id, "device_token": raw_token}

    @app.post("/api/v2/bot/sessions", dependencies=[Depends(require_bot)])
    async def create_session(payload: SessionCreate, session: AsyncSession = Depends(db_session)):
        item = OtpSession(job_id=payload.job_id,
                          national_id_hash=national_id_hash(payload.national_id, cfg.token_pepper.get_secret_value()),
                          purpose=payload.purpose, waiting_since=now(),
                          expires_at=now() + timedelta(seconds=payload.expires_in_seconds),
                          device_id=payload.device_id)
        session.add(item)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(status_code=409, detail="job already has a session")
        return {"ok": True, "session_id": item.id, "status": item.status,
                "waiting_since": item.waiting_since, "expires_at": item.expires_at}

    @app.get("/api/v2/bot/sessions/{session_id}", dependencies=[Depends(require_bot)])
    async def get_session(session_id: str, session: AsyncSession = Depends(db_session)):
        item = await session.get(OtpSession, session_id)
        if item is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {"ok": True, "session_id": item.id, "job_id": item.job_id,
                "status": item.status, "waiting_since": item.waiting_since, "expires_at": item.expires_at}

    @app.get("/api/v2/device/sessions")
    async def device_sessions(device: Device = Depends(require_device), session: AsyncSession = Depends(db_session)):
        items = (await session.execute(select(OtpSession).where(
            OtpSession.device_id.in_([None, device.id]), OtpSession.status == SessionStatus.WAITING.value,
            OtpSession.expires_at > now()))).scalars().all()
        return {"ok": True, "sessions": [{"session_id": x.id, "purpose": x.purpose,
                                             "expires_at": x.expires_at} for x in items]}

    @app.post("/api/v2/device/otp")
    @app.post("/api/v2/device/manual-otp")
    async def accept_otp(payload: DeviceOtp, idempotency_key: str = Header(alias="Idempotency-Key"),
                         device: Device = Depends(require_device), session: AsyncSession = Depends(db_session)):
        if idempotency_key != payload.message_id:
            raise HTTPException(status_code=422, detail="idempotency mismatch")
        item = (await session.execute(select(OtpSession).where(OtpSession.id == payload.session_id).with_for_update())).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="session not found")
        if item.status != SessionStatus.WAITING.value or as_utc(item.expires_at) <= now():
            raise HTTPException(status_code=410, detail="session expired")
        if item.device_id not in (None, device.id):
            raise HTTPException(status_code=403, detail="session device mismatch")
        if national_id_hash(payload.national_id, cfg.token_pepper.get_secret_value()) != item.national_id_hash:
            raise HTTPException(status_code=422, detail="national id mismatch")
        if payload.received_at < as_utc(item.waiting_since) - timedelta(seconds=cfg.clock_skew_seconds) or payload.received_at > now() + timedelta(seconds=cfg.clock_skew_seconds):
            raise HTTPException(status_code=422, detail="invalid received timestamp")
        message = OtpMessage(session_id=item.id, message_id=payload.message_id,
                             otp_ciphertext=encrypt_otp(payload.otp, cfg.encryption_key.get_secret_value()),
                             received_at=payload.received_at, expires_at=item.expires_at,
                             sim_slot=payload.sim_slot)
        session.add(message)
        item.device_id = device.id
        item.status = SessionStatus.OTP_RECEIVED.value
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return JSONResponse({"ok": True, "duplicate": True}, status_code=409)
        return {"ok": True, "accepted": True}

    @app.get("/api/v2/bot/sessions/{session_id}/wait", dependencies=[Depends(require_bot)])
    async def wait_otp(session_id: str, job_id: str, timeout_seconds: int = 120,
                       session: AsyncSession = Depends(db_session)):
        deadline = asyncio.get_running_loop().time() + min(max(timeout_seconds, 1), 130)
        while True:
            item = (await session.execute(select(OtpSession).where(OtpSession.id == session_id).with_for_update())).scalar_one_or_none()
            if item is None or item.job_id != job_id:
                raise HTTPException(status_code=404, detail="session not found")
            if as_utc(item.expires_at) <= now() or item.status in {SessionStatus.EXPIRED.value, SessionStatus.CANCELLED.value}:
                raise HTTPException(status_code=410, detail="session expired")
            message = (await session.execute(select(OtpMessage).where(
                OtpMessage.session_id == session_id, OtpMessage.consumed_at.is_(None),
                OtpMessage.expires_at > now()).order_by(OtpMessage.accepted_at).with_for_update())).scalars().first()
            if message and (message.lease_expires_at is None or as_utc(message.lease_expires_at) <= now() or message.lease_job_id == job_id):
                message.status = "DELIVERED"; message.delivered_at = now()
                message.lease_job_id = job_id; message.lease_expires_at = now() + timedelta(seconds=cfg.delivery_lease_seconds)
                item.status = SessionStatus.DELIVERED.value
                otp = decrypt_otp(message.otp_ciphertext or "", cfg.encryption_key.get_secret_value())
                await session.commit()
                return {"ok": True, "otp": otp, "delivery_id": message.id, "lease_expires_at": message.lease_expires_at}
            await session.rollback()
            if asyncio.get_running_loop().time() >= deadline:
                return JSONResponse({"ok": False, "status": "WAITING"}, status_code=202)
            await asyncio.sleep(1.5)

    @app.post("/api/v2/bot/sessions/{session_id}/consume", dependencies=[Depends(require_bot)])
    async def consume(session_id: str, action: SessionAction, session: AsyncSession = Depends(db_session)):
        item = (await session.execute(select(OtpSession).where(OtpSession.id == session_id).with_for_update())).scalar_one_or_none()
        if item is None or item.job_id != action.job_id:
            raise HTTPException(status_code=404, detail="session not found")
        message = (await session.execute(select(OtpMessage).where(OtpMessage.session_id == session_id,
            OtpMessage.lease_job_id == action.job_id, OtpMessage.consumed_at.is_(None)).with_for_update())).scalars().first()
        if message is None:
            raise HTTPException(status_code=409, detail="no active delivery")
        message.consumed_at = now(); message.otp_ciphertext = None; message.status = "CONSUMED"
        item.consumed_at = now(); item.status = SessionStatus.CONSUMED.value
        await session.commit()
        return {"ok": True}

    @app.post("/api/v2/bot/sessions/{session_id}/cancel", dependencies=[Depends(require_bot)])
    async def cancel(session_id: str, action: SessionAction, session: AsyncSession = Depends(db_session)):
        item = await session.get(OtpSession, session_id)
        if item is None or item.job_id != action.job_id:
            raise HTTPException(status_code=404, detail="session not found")
        item.status = SessionStatus.CANCELLED.value; item.cancelled_at = now()
        await session.commit()
        return {"ok": True}

    @app.post("/api/v2/bot/sessions/{session_id}/extend", dependencies=[Depends(require_bot)])
    async def extend(session_id: str, action: SessionExtend, session: AsyncSession = Depends(db_session)):
        item = await session.get(OtpSession, session_id)
        if item is None or item.job_id != action.job_id:
            raise HTTPException(status_code=404, detail="session not found")
        item.expires_at += timedelta(seconds=action.seconds)
        await session.commit()
        return {"ok": True, "expires_at": item.expires_at}

    @app.post("/api/v2/device/heartbeat")
    async def heartbeat(device: Device = Depends(require_device)):
        return {"ok": True, "device_id": device.public_device_id, "server_time": now()}

    @app.get("/api/v2/device/profile")
    async def profile(device: Device = Depends(require_device)):
        return {"ok": True, "device_id": device.public_device_id, "status": device.status,
                "app_version": device.app_version, "flavor": device.app_flavor,
                "consent_version": device.consent_version}

    return app


app = create_app()
