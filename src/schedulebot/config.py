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
    owner_ids: dict[str, str] = field(default_factory=dict)  # channel -> sender_id


@dataclass
class AvailabilityConfig:
    timezone: str = "UTC"
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
class BookingLinksConfig:
    links: dict[str, str] = field(default_factory=dict)  # channel -> URL


@dataclass
class ServiceConfig:
    name: str = ""
    slug: str = ""
    duration_minutes: int = 30
    price: float = 0
    currency: str = "USD"
    description: str = ""


@dataclass
class MCPConfig:
    enabled: bool = False
    transport: str = "streamable-http"  # stdio | streamable-http
    path: str = "/mcp"


@dataclass
class AgentCardConfig:
    """Public agent identity for /.well-known/agent.json discovery."""
    enabled: bool = False
    url: str = ""  # Public base URL, e.g. "https://schedule.example.com"
    description: str = ""
    organization: str = ""


@dataclass
class Config:
    owner: OwnerConfig = field(default_factory=OwnerConfig)
    availability: AvailabilityConfig = field(default_factory=AvailabilityConfig)
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    channels: dict[str, ChannelConfig] = field(default_factory=dict)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    booking_links: BookingLinksConfig = field(default_factory=BookingLinksConfig)
    services: list[ServiceConfig] = field(default_factory=list)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    agent_card: AgentCardConfig = field(default_factory=AgentCardConfig)
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
    config_path = Path(config_path).resolve()
    config_dir = config_path.parent

    if env_path:
        load_dotenv(env_path)
    else:
        # Look for .env next to config file first, then CWD
        env_beside_config = config_dir / ".env"
        if env_beside_config.exists():
            load_dotenv(env_beside_config)
        else:
            load_dotenv()

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    raw = _resolve_dict(raw)

    owner_data = raw.get("owner", {})
    owner = OwnerConfig(
        name=owner_data.get("name", "Owner"),
        email=owner_data.get("email", ""),
        owner_ids=owner_data.get("owner_ids", {}),
    )

    avail_data = raw.get("availability", {})
    availability = AvailabilityConfig(
        timezone=avail_data.get("timezone", "UTC"),
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

    booking_links = BookingLinksConfig(
        links=raw.get("booking_links", {}),
    )

    services = []
    for svc_data in raw.get("services", []):
        if isinstance(svc_data, dict):
            services.append(ServiceConfig(
                name=svc_data.get("name", ""),
                slug=svc_data.get("slug", ""),
                duration_minutes=svc_data.get("duration_minutes", 30),
                price=svc_data.get("price", 0),
                currency=svc_data.get("currency", "USD"),
                description=svc_data.get("description", ""),
            ))

    mcp_data = raw.get("mcp", {})
    mcp = MCPConfig(
        enabled=mcp_data.get("enabled", False),
        transport=mcp_data.get("transport", "streamable-http"),
        path=mcp_data.get("path", "/mcp"),
    )

    agent_data = raw.get("agent_card", {})
    agent_card = AgentCardConfig(
        enabled=agent_data.get("enabled", False),
        url=agent_data.get("url", ""),
        description=agent_data.get("description", ""),
        organization=agent_data.get("organization", ""),
    )

    dry_run = os.environ.get("DRY_RUN", "").lower() in ("true", "1", "yes")

    return Config(
        owner=owner,
        availability=availability,
        calendar=calendar,
        llm=llm,
        channels=channels,
        notifications=notifications,
        booking_links=booking_links,
        services=services,
        mcp=mcp,
        agent_card=agent_card,
        dry_run=dry_run,
    )
