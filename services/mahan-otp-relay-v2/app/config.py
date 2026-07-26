from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OTP_", extra="ignore")

    environment: str = "production"
    database_url: str = ""
    bot_token: SecretStr = SecretStr("")
    admin_token: SecretStr = SecretStr("")
    token_pepper: SecretStr = SecretStr("")
    encryption_key: SecretStr = SecretStr("")
    trusted_hosts: list[str] = Field(default_factory=lambda: ["otp.mahanvip.ir"])
    force_https: bool = True
    max_body_bytes: int = 16_384
    delivery_lease_seconds: int = 30
    clock_skew_seconds: int = 90
    enable_otp_v1: bool = False

    @model_validator(mode="after")
    def fail_closed(self) -> "Settings":
        missing = [
            name
            for name, value in {
                "OTP_DATABASE_URL": self.database_url,
                "OTP_BOT_TOKEN": self.bot_token.get_secret_value(),
                "OTP_ADMIN_TOKEN": self.admin_token.get_secret_value(),
                "OTP_TOKEN_PEPPER": self.token_pepper.get_secret_value(),
                "OTP_ENCRYPTION_KEY": self.encryption_key.get_secret_value(),
            }.items()
            if not value
        ]
        if missing:
            raise ValueError("missing required security configuration: " + ", ".join(missing))
        if self.environment == "production" and not self.database_url.startswith("postgresql+"):
            raise ValueError("production requires PostgreSQL")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
