# schedulebot

[![CI](https://github.com/anthroos/open-schedule-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/anthroos/open-schedule-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**Open-source AI scheduling agent. Your own Calendly, but conversational.**

Both sides — the guest **and** the owner — talk to AI. No dashboards, no forms.

```
Guest: "Hi, I'd like to schedule a call"
Bot:   "Sure! What's your name, email, and topic?"
Guest: "Maria, maria@corp.com, about partnership"
Bot:   "Got it! Here are available slots: ..."
Guest: "Slot 3"
Bot:   "Confirmed! Meeting with Ivan on Thu 14:00. Google Meet link: ..."
```

## Features

- **Dual AI mode** — guests book meetings, owners manage schedule, both via conversation
- **Multi-LLM** — Anthropic Claude, OpenAI GPT, or local Ollama (auto-detected)
- **Multi-channel** — Telegram, Slack, Discord, Web API (or build your own adapter)
- **Google Calendar** — freebusy check + event creation + Google Meet links
- **Owner notifications** — get Telegram alerts when someone books
- **MCP server** — integrate with any AI agent via Model Context Protocol
- **Input validation** — rate limiting, message length, prompt injection detection
- **Retry with backoff** — all external API calls protected against transient failures
- **Guest timezone** — asks guest their city, shows slots in their local time
- **Dry-run mode** — test the full flow without creating real events

## Quick start

```bash
git clone https://github.com/anthroos/open-schedule-agent.git
cd open-schedule-agent
pip install -e ".[telegram]"
schedulebot init
# Edit config.yaml and .env with your details
schedulebot check    # sets up Google Calendar auth
schedulebot run
```

### Prerequisites

- Python 3.10+
- Google account with Google Calendar ([setup guide](docs/setup-google.md))
- Telegram bot token from [@BotFather](https://t.me/BotFather)
- Anthropic or OpenAI API key

### Install options

```bash
pip install -e "."                      # core only
pip install -e ".[telegram]"            # + Telegram
pip install -e ".[web]"                 # + FastAPI web endpoint
pip install -e ".[telegram,web]"        # multiple channels
pip install -e ".[all]"                 # everything
```

## How it works

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ Telegram │  │  Slack   │  │ Discord  │  │ Web API  │
│ Adapter  │  │ Adapter  │  │ Adapter  │  │ Adapter  │
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
       │              │              │              │
       └──────┬───────┘──────┬───────┘──────────────┘
                    │
          ┌─────────▼──────────┐
          │  SchedulingEngine  │
          │  (owner / guest)   │
          └──┬─────┬────────┬──┘
             │     │        │
    ┌────────▼─┐ ┌─▼──────┐ ┌▼────────────┐
    │   LLM    │ │ Avail. │ │  Google Cal  │
    │ Provider │ │ Engine │ │  (freebusy)  │
    └──────────┘ └────────┘ └─────────────┘
```

The core engine is channel-agnostic. It receives `IncomingMessage`, returns `OutgoingMessage`. Channel adapters handle the translation.

## Configuration

`config.yaml`:

```yaml
owner:
  name: "Your Name"

availability:
  timezone: "Europe/Kyiv"
  meeting_duration_minutes: 30
  buffer_minutes: 15

calendar:
  provider: "google"
  create_meet_link: true

llm:
  provider: "anthropic"     # anthropic | openai | ollama (auto-detected)
  model: "claude-haiku-4-20250414"

channels:
  telegram:
    enabled: true
    bot_token: "${TELEGRAM_BOT_TOKEN}"
  web:
    enabled: false
    port: 8080
```

## CLI commands

| Command | Description |
|---------|-------------|
| `schedulebot init` | Create config.yaml and .env in current directory |
| `schedulebot check` | Verify calendar, LLM, and channel connections |
| `schedulebot slots` | Show available time slots (for debugging) |
| `schedulebot run` | Start the bot |
| `schedulebot run --dry-run` | Run without creating real calendar events |
| `schedulebot mcp` | Run as MCP server (stdio or streamable-http) |

## LLM Providers

| Provider | Function Calling | Auto-detected | Notes |
|----------|:---:|:---:|-------|
| Anthropic (Claude) | Yes | Yes | Recommended. Set `ANTHROPIC_API_KEY`. |
| OpenAI (GPT) | Yes | Yes | Set `OPENAI_API_KEY`. |
| Ollama (local) | No | No | Text-based fallback. No tool use. |

**Auto-detection:** Just set your API key in `.env`. If `provider: "anthropic"` but only `OPENAI_API_KEY` is set, schedulebot switches to OpenAI automatically (and adjusts the model). No config changes needed.

## Owner mode

Owners manage their schedule by chatting:

```
Owner: "Add Monday 10-18"
Bot:   "Added availability: Monday 10:00-18:00"

Owner: "Block Saturday"
Bot:   "Blocked: Saturday (all day)"

Owner: "/schedule"
Bot:   "Monday: 10:00-18:00, Tuesday: 09:00-13:30, ..."
```

## Web API

When the web channel is enabled:

```bash
# Send a message
curl -X POST http://localhost:8080/api/message \
  -H "Content-Type: application/json" \
  -d '{"sender_id": "user-123", "sender_name": "John", "text": "I want to book a meeting"}'

# Health check
curl http://localhost:8080/api/health
```

## Building your own adapter

```python
from schedulebot.channels.base import ChannelAdapter

class MyAdapter(ChannelAdapter):
    @property
    def name(self) -> str:
        return "my_channel"

    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    async def send_message(self, sender_id, message) -> None:
        ...
```

## Deploy

### Docker

```bash
docker compose up -d
```

### Railway

One-click cloud deploy with auto-HTTPS. See the [Railway deployment guide](docs/deploy-railway.md).

## Development

```bash
git clone https://github.com/anthroos/open-schedule-agent.git
cd open-schedule-agent
pip install -e ".[all,dev]"
pytest tests/ -v
```

## License

MIT
