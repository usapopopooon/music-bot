"""Environment variable loading and validation."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Bot configuration loaded from environment variables.

    Field names are lowercase; env vars are matched case-insensitively, so
    `DISCORD_TOKENS` populates `discord_tokens`. See SPEC.md §8.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Required ---
    discord_tokens: str
    lavalink_host: str
    lavalink_password: str
    database_url: str

    # --- Optional ---
    lavalink_port: int = 2333
    lavalink_secure: bool = False

    db_pool_size: int = Field(default=4, ge=1, le=32)
    max_bot_instances: int = Field(default=4, ge=1, le=16)
    max_players_per_bot: int = Field(default=50, ge=1)
    max_queue_size: int = Field(default=500, ge=1)

    memory_limit_mb: int | None = Field(default=None, ge=64)
    memory_soft_limit_percent: int = Field(default=90, ge=10, le=99)

    dev_guild_id: int | None = None
    app_env: Literal["production", "development"] = "production"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("discord_tokens")
    @classmethod
    def _validate_tokens(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("DISCORD_TOKENS must not be empty")
        return v

    @property
    def tokens(self) -> list[str]:
        """Parsed, deduplicated, validated token list. See SPEC §7.7.1."""
        raw = [t.strip() for t in self.discord_tokens.split(",")]
        tokens = [t for t in raw if t]
        if not tokens:
            raise ValueError("DISCORD_TOKENS produced no non-empty tokens")
        if len(tokens) != len(set(tokens)):
            raise ValueError("DISCORD_TOKENS must not contain duplicates")
        if len(tokens) > self.max_bot_instances:
            raise ValueError(
                f"DISCORD_TOKENS has {len(tokens)} entries; "
                f"MAX_BOT_INSTANCES is {self.max_bot_instances}"
            )
        return tokens


_settings: Settings | None = None


def get_settings() -> Settings:
    """Lazy-load and cache the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
