"""Runtime configuration loaded from environment variables or .env file."""

from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OMNI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_path: Path = Path("data/omni_monitor.db")
    host: str = "0.0.0.0"
    port: int = 8080
    api_key: str = ""
    scheduler_enabled: bool = True
    scheduler_tick_seconds: int = 10
    seed_defaults: bool = True
    fcm_credentials_path: str = ""
    default_fetch_timeout: float = 45.0
    failure_alert_threshold: int = 3
    history_retention_days: int = 30
