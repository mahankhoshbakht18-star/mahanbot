"""Wire contract shared by the authority and manager proxy; contains no signing or admin secrets."""
from pydantic import BaseModel, Field, StrictBool


class Issue(BaseModel):
    customer: str = Field(min_length=1, max_length=120)
    days: int = Field(default=30, ge=1, le=3650)
    max_devices: int = Field(default=1, ge=1, le=50)


class DeviceIdentity(BaseModel):
    installation_id: str = Field(min_length=16, max_length=128, pattern=r'^[a-zA-Z0-9-]+$')
    machine_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class Activate(DeviceIdentity):
    license_key: str = Field(min_length=10, max_length=160)


class Renew(BaseModel):
    days: int = Field(ge=1, le=3650)


class ChangeState(BaseModel):
    active: StrictBool
