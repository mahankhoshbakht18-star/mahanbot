from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PairingCreate(StrictModel):
    created_by: str = Field(min_length=1, max_length=80)
    digits: int = Field(default=6, ge=6, le=8)


class PairingClaim(StrictModel):
    code: str = Field(pattern=r"^\d{6}(?:\d{2})?$")
    display_name: str = Field(default="MahanBot OTP Companion", max_length=80)
    app_version: str = Field(default="1.0.0", max_length=20)
    app_flavor: str = Field(pattern=r"^(consent|private)$")
    consent: bool
    consent_version: str = Field(max_length=16)


class SessionCreate(StrictModel):
    job_id: str = Field(min_length=1, max_length=80)
    national_id: str = Field(pattern=r"^\d{10}$")
    purpose: str = Field(pattern=r"^(register|select)$")
    expires_in_seconds: int = Field(default=180, ge=30, le=600)
    device_id: str | None = None


class DeviceOtp(StrictModel):
    session_id: str
    message_id: str = Field(min_length=8, max_length=128)
    national_id: str = Field(pattern=r"^\d{10}$")
    otp: str = Field(pattern=r"^\d{4,8}$")
    received_at: datetime
    sim_slot: int | None = Field(default=None, ge=0, le=1)
    source: str = Field(pattern=r"^(android-consent|android-private|manual)$")
    consent_version: str = Field(min_length=1, max_length=16)

    @field_validator("received_at")
    @classmethod
    def requires_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("received_at must include timezone")
        return value


class SessionAction(StrictModel):
    job_id: str = Field(min_length=1, max_length=80)


class SessionExtend(SessionAction):
    seconds: int = Field(ge=30, le=300)
