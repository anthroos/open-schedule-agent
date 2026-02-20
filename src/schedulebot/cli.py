"""CLI entry point for schedulebot."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import signal
import sys
from pathlib import Path

from . import __version__


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize schedulebot configuration in the current directory."""
    config_dest = Path("config.yaml")
    env_dest = Path(".env")

    # Find example files from package
    pkg_dir = Path(__file__).parent.parent.parent  # src/schedulebot -> project root
    config_src = pkg_dir / "config.example.yaml"
    env_src = pkg_dir / ".env.example"

    if config_dest.exists() and not args.force:
        print(f"config.yaml already exists. Use --force to overwrite.")
    else:
        if config_src.exists():
            shutil.copy(config_src, config_dest)
        else:
            # Fallback: write a minimal config
            config_dest.write_text(
                "owner:\n  name: \"Your Name\"\n\navailability:\n  timezone: \"UTC\"\n  "
                "working_hours:\n    monday: [\"09:00-17:00\"]\n\ncalendar:\n  provider: "
                "\"google\"\n\nllm:\n  provider: \"anthropic\"\n\nchannels:\n  telegram:\n    "
                "enabled: true\n    bot_token: \"${TELEGRAM_BOT_TOKEN}\"\n"
            )
        print(f"Created {config_dest}")

    if env_dest.exists() and not args.force:
        print(f".env already exists. Use --force to overwrite.")
    else:
        if env_src.exists():
            shutil.copy(env_src, env_dest)
        else:
            env_dest.write_text("ANTHROPIC_API_KEY=sk-ant-...\nTELEGRAM_BOT_TOKEN=...\n")
        print(f"Created {env_dest}")

    print("\nNext steps:")
    print("  1. Edit config.yaml with your details")
    print("  2. Edit .env with your API keys")
    print("  3. Set up Google Calendar: schedulebot check")
    print("  4. Run: schedulebot run")


def cmd_check(args: argparse.Namespace) -> None:
    """Check all connections (calendar, LLM, channels)."""
    from .config import load_config

    print(f"schedulebot v{__version__} — connection check\n")

    try:
        config = load_config(args.config)
        print(f"[OK] Config loaded from {args.config}")
    except Exception as e:
        print(f"[FAIL] Config: {e}")
        sys.exit(1)

    # Check calendar
    try:
        from .calendar.google_auth import get_google_credentials
        creds = get_google_credentials(
            config.calendar.credentials_path,
            config.calendar.token_path,
        )
        print(f"[OK] Google Calendar authenticated")
    except Exception as e:
        print(f"[FAIL] Google Calendar: {e}")

    # Check LLM
    try:
        llm = _build_llm(config)
        print(f"[OK] LLM provider: {config.llm.provider} ({config.llm.model})")
    except Exception as e:
        print(f"[FAIL] LLM: {e}")

    # Check channels
    for name, ch_config in config.channels.items():
        if ch_config.enabled:
            token = ch_config.get("bot_token", "")
            if token and not token.startswith("$"):
                print(f"[OK] Channel {name}: token configured")
            else:
                print(f"[WARN] Channel {name}: enabled but token not set")
        else:
            print(f"[--] Channel {name}: disabled")


def cmd_slots(args: argparse.Namespace) -> None:
    """Display available slots for debugging."""
    from .config import load_config
    from .calendar.google_calendar import GoogleCalendarProvider
    from .core.availability import AvailabilityEngine

    from .database import Database

    config = load_config(args.config)
    calendar = GoogleCalendarProvider(config.calendar, config.availability.timezone)
    db = Database()
    db.connect()
    availability = AvailabilityEngine(config.availability, calendar, db)

    async def show():
        slots = await availability.get_available_slots()
        if not slots:
            print("No available slots found.")
            return
        print(f"Available slots (next {config.availability.max_days_ahead} days):\n")
        for i, slot in enumerate(slots, 1):
            print(f"  {i}. {slot}")
        print(f"\nTotal: {len(slots)} slots")

    asyncio.run(show())


def cmd_mcp(args: argparse.Namespace) -> None:
    """Run the MCP server (stdio transport for local testing / Claude Desktop)."""
    from .config import load_config
    from .calendar.google_calendar import GoogleCalendarProvider
    from .core.availability import AvailabilityEngine
    from .database import Database
    from .mcp_server import create_mcp_server

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_config(args.config)
    db = Database()
    db.connect()
    calendar = GoogleCalendarProvider(config.calendar, config.availability.timezone)
    availability = AvailabilityEngine(config.availability, calendar, db)

    mcp = create_mcp_server(config, availability, calendar, db)
    mcp.run(transport=args.transport)


def cmd_run(args: argparse.Namespace) -> None:
    """Run the scheduling bot."""
    from .config import load_config

    config = load_config(args.config)
    if args.dry_run:
        config.dry_run = True

    if config.dry_run:
        print("Running in DRY RUN mode (no calendar events will be created)\n")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    asyncio.run(_run_bot(config))


async def _run_bot(config) -> None:
    """Set up and run all enabled channels."""
    from .calendar.google_calendar import GoogleCalendarProvider
    from .core.engine import SchedulingEngine
    from .database import Database

    import os
    db_path = os.environ.get("DATABASE_PATH", "schedulebot.db")
    db = Database(db_path)
    db.connect()

    calendar = GoogleCalendarProvider(config.calendar, config.availability.timezone)
    llm = _build_llm(config)
    engine = SchedulingEngine(config, calendar, llm, db)

    # Create MCP server if enabled
    mcp_app = None
    if config.mcp.enabled:
        try:
            from .mcp_server import create_mcp_server
            mcp_server = create_mcp_server(config, engine.availability, calendar, db)
            if config.mcp.transport == "streamable-http":
                mcp_app = mcp_server.streamable_http_app()
                print(f"MCP server enabled at {config.mcp.path}")
        except ImportError:
            print("[WARN] MCP dependencies not installed. Run: pip install schedulebot[mcp]")

    adapters = []
    for name, ch_config in config.channels.items():
        if not ch_config.enabled:
            continue
        adapter = _build_channel(
            name, ch_config.extra, engine.handle_message, db,
            mcp_app=mcp_app, mcp_path=config.mcp.path,
            owner_name=config.owner.name,
        )
        if adapter:
            adapters.append(adapter)

    # Wire owner notifications
    notif_channel = config.notifications.channel
    notif_owner_id = config.notifications.owner_id
    if notif_channel and notif_owner_id:
        notif_adapter = next((a for a in adapters if a.name == notif_channel), None)
        if notif_adapter:
            from .notifications import Notifier
            engine.notifier = Notifier(notif_adapter, notif_owner_id)
            print(f"Owner notifications enabled via {notif_channel} -> {notif_owner_id}")

    if not adapters:
        print("No channels enabled. Enable at least one channel in config.yaml.")
        sys.exit(1)

    print(f"Starting {len(adapters)} channel(s): {', '.join(a.name for a in adapters)}")

    # Handle graceful shutdown
    stop_event = asyncio.Event()

    def signal_handler():
        print("\nShutting down...")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    # Start all adapters
    tasks = [asyncio.create_task(a.start()) for a in adapters]

    await stop_event.wait()

    # Stop all adapters
    for adapter in adapters:
        await adapter.stop()

    db.close()


def _build_llm(config):
    """Build LLM provider from config, with auto-detection of API keys."""
    import os

    provider = config.llm.provider.lower()
    model = config.llm.model

    has_anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_openai_key = bool(os.environ.get("OPENAI_API_KEY"))

    # Auto-detect: if configured provider's key is missing, try the other
    if provider == "anthropic" and not has_anthropic_key and has_openai_key:
        print("[auto-detect] ANTHROPIC_API_KEY not found, switching to OpenAI")
        provider = "openai"
    elif provider == "openai" and not has_openai_key and has_anthropic_key:
        print("[auto-detect] OPENAI_API_KEY not found, switching to Anthropic")
        provider = "anthropic"

    # Fix model mismatch after provider switch
    if provider == "openai" and model.startswith("claude"):
        model = "gpt-4o-mini"
        print(f"[auto-detect] Model adjusted to {model}")
    elif provider == "anthropic" and model.startswith("gpt"):
        model = "claude-haiku-4-20250414"
        print(f"[auto-detect] Model adjusted to {model}")

    if provider == "anthropic":
        from .llm.anthropic import AnthropicProvider
        return AnthropicProvider(model=model)
    elif provider == "openai":
        from .llm.openai import OpenAIProvider
        return OpenAIProvider(model=model)
    elif provider == "ollama":
        from .llm.ollama import OllamaProvider
        return OllamaProvider(model=model, base_url=config.llm.base_url or "http://localhost:11434")
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


def _build_channel(name, config_extra, on_message, db=None, mcp_app=None, mcp_path="/mcp", owner_name="Owner"):
    """Build channel adapter by name."""
    if name == "telegram":
        from .channels.telegram import TelegramAdapter
        return TelegramAdapter(config_extra, on_message)
    elif name == "web":
        from .channels.web import WebAdapter
        return WebAdapter(config_extra, on_message, db=db, mcp_app=mcp_app, mcp_path=mcp_path, owner_name=owner_name)
    elif name == "slack":
        print(f"[WARN] Slack adapter not yet implemented (v0.2)")
        return None
    elif name == "discord":
        print(f"[WARN] Discord adapter not yet implemented (v0.2)")
        return None
    else:
        print(f"[WARN] Unknown channel: {name}")
        return None


def main():
    parser = argparse.ArgumentParser(
        prog="schedulebot",
        description="Open-source AI scheduling agent",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize configuration")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing files")

    # check
    check_parser = subparsers.add_parser("check", help="Check all connections")
    check_parser.add_argument("-c", "--config", default="config.yaml", help="Config file path")

    # slots
    slots_parser = subparsers.add_parser("slots", help="Show available time slots")
    slots_parser.add_argument("-c", "--config", default="config.yaml", help="Config file path")
    slots_parser.add_argument("--days", type=int, default=None, help="Override max_days_ahead")

    # run
    run_parser = subparsers.add_parser("run", help="Run the bot")
    run_parser.add_argument("-c", "--config", default="config.yaml", help="Config file path")
    run_parser.add_argument("--dry-run", action="store_true", help="Don't create calendar events")
    run_parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    # mcp
    mcp_parser = subparsers.add_parser("mcp", help="Run the MCP server")
    mcp_parser.add_argument("-c", "--config", default="config.yaml", help="Config file path")
    mcp_parser.add_argument("-t", "--transport", default="stdio", choices=["stdio", "streamable-http"], help="MCP transport")
    mcp_parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "init": cmd_init,
        "check": cmd_check,
        "slots": cmd_slots,
        "run": cmd_run,
        "mcp": cmd_mcp,
    }
    commands[args.command](args)
