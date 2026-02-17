"""Configuration loading from YAML + environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class OwnerConfig:
    name: str = "Owner"
    email: str = ""


@dataclass
class AvailabilityConfig:
    timezone: str = "UTC"
    working_hours: dict[str, list[str]] = field(default_factory=dict)
    meeting_duration_minutes: int = 30
    buffer_minutes: int = 15
    min_notice_hours: int = 4
    max_days_ahead: int = 14


@dataclass
class CalendarConfig:
    provider: str = "google"
    create_meet_link: bool = True
    credentials_path: str = "credentials.json"
    token_path: str = "token.json"


@dataclass
class LLMConfig:
    provider: str = "anthropic"
    model: str = "claude-haiku-4-20250414"
    base_url: str | None = None


@dataclass
class ChannelConfig:
    enabled: bool = False
    extra: dict = field(default_factory=dict)

    def get(self, key: str, default=None):
        return self.extra.get(key, default)


@dataclass
class NotificationsConfig:
    channel: str = "telegram"
    owner_id: str = ""


@dataclass
class Config:
    owner: OwnerConfig = field(default_factory=OwnerConfig)
    availability: AvailabilityConfig = field(default_factory=AvailabilityConfig)
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    channels: dict[str, ChannelConfig] = field(default_factory=dict)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    dry_run: bool = False


def _resolve_env_vars(value: str) -> str:
    """Replace ${VAR} with environment variable values."""
    def replacer(match):
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))
    return re.sub(r"\$\{(\w+)\}", replacer, value)


def _resolve_dict(d: dict) -> dict:
    """Recursively resolve env vars in a dict."""
    resolved = {}
    for k, v in d.items():
        if isinstance(v, str):
            resolved[k] = _resolve_env_vars(v)
        elif isinstance(v, dict):
            resolved[k] = _resolve_dict(v)
        elif isinstance(v, list):
            resolved[k] = [_resolve_env_vars(i) if isinstance(i, str) else i for i in v]
        else:
            resolved[k] = v
    return resolved


def load_config(config_path: str | Path, env_path: str | Path | None = None) -> Config:
    """Load config from YAML file with env var resolution."""
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()

    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    raw = _resolve_dict(raw)

    owner_data = raw.get("owner", {})
    owner = OwnerConfig(
        name=owner_data.get("name", "Owner"),
        email=owner_data.get("email", ""),
    )

    avail_data = raw.get("availability", {})
    availability = AvailabilityConfig(
        timezone=avail_data.get("timezone", "UTC"),
        working_hours=avail_data.get("working_hours", {}),
        meeting_duration_minutes=avail_data.get("meeting_duration_minutes", 30),
        buffer_minutes=avail_data.get("buffer_minutes", 15),
        min_notice_hours=avail_data.get("min_notice_hours", 4),
        max_days_ahead=avail_data.get("max_days_ahead", 14),
    )

    cal_data = raw.get("calendar", {})
    calendar = CalendarConfig(
        provider=cal_data.get("provider", "google"),
        create_meet_link=cal_data.get("create_meet_link", True),
        credentials_path=cal_data.get("credentials_path", "credentials.json"),
        token_path=cal_data.get("token_path", "token.json"),
    )

    llm_data = raw.get("llm", {})
    llm = LLMConfig(
        provider=llm_data.get("provider", "anthropic"),
        model=llm_data.get("model", "claude-haiku-4-20250414"),
        base_url=llm_data.get("base_url"),
    )

    channels = {}
    for name, ch_data in raw.get("channels", {}).items():
        if isinstance(ch_data, dict):
            enabled = ch_data.pop("enabled", False)
            channels[name] = ChannelConfig(enabled=enabled, extra=ch_data)

    notif_data = raw.get("notifications", {})
    notifications = NotificationsConfig(
        channel=notif_data.get("channel", "telegram"),
        owner_id=notif_data.get("owner_id", ""),
    )

    return Config(
        owner=owner,
        availability=availability,
        calendar=calendar,
        llm=llm,
        channels=channels,
        notifications=notifications,
    )
