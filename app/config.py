from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class GroupConfig(BaseModel):
    id: str
    name: str
    enabled: bool = True
    notes: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    whapi_token: str = ""
    whapi_base_url: str = "https://gate.whapi.cloud"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    # Approximate list pricing, USD per million tokens. Update to match the current
    # Anthropic pricing page for the configured model -- these are not fetched live.
    anthropic_price_input_per_mtok: float = 3.0
    anthropic_price_output_per_mtok: float = 15.0

    database_url: str = "sqlite:///./digest.db"

    self_number: str = ""
    # How the user is referred to / addressed in group chats, so pass-1 summarization
    # can flag messages that mention or concern them.
    user_display_name: str = ""

    webhook_base_url: str = ""

    timezone: str = "Asia/Dubai"
    daily_digest_hour: int = 8
    enable_scheduler: bool = True

    retention_days: int = 30

    groups_config_path: str = "groups.yaml"

    @property
    def self_chat_id(self) -> str:
        if not self.self_number:
            return ""
        return f"{self.self_number}@s.whatsapp.net"

    def load_groups(self) -> list[GroupConfig]:
        path = Path(self.groups_config_path)
        if not path.exists():
            return []
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return [GroupConfig(**g) for g in data.get("groups", [])]


@lru_cache
def get_settings() -> Settings:
    return Settings()
