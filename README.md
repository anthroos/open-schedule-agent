# schedulebot

[![CI](https://github.com/anthroos/open-schedule-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/anthroos/open-schedule-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**Open-source AI scheduling agent. Make your calendar discoverable by AI agents.**

Deploy an always-on endpoint. Any AI agent discovers you via `agent.json`, checks availability via MCP, and books a meeting — no humans in the loop.

```
Guest: "Hi, I'd like to schedule a call"
Bot:   "Sure! What's your name, email, and topic?"
Guest: "Maria, maria@corp.com, about partnership"
Bot:   "Got it! Here are available slots: ..."
Guest: "Slot 3"
Bot:   "Confirmed! Meeting with Ivan on Thu 14:00. Google Meet link: ..."
```

## Features

- **MCP server** — AI agents book meetings via Model Context Protocol (Claude Code, Cursor, Open CLAW)
- **Agent discovery** — `/.well-known/agent.json` lets other agents find your endpoint automatically
- **Dual AI mode** — guests book meetings, owners manage schedule, both via conversation
- **Multi-LLM** — Anthropic Claude, OpenAI GPT, or local Ollama (auto-detected)
- **Multi-channel** — Telegram, Slack, Discord, Web API
- **Google Calendar** — freebusy check + event creation + Google Meet links
- **Reminders** — automatic reminders before meetings with cancel links
- **Security** — rate limiting, prompt injection detection, input sanitization
- **Retry with backoff** — all external API calls protected against transient failures
- **Guest timezone** — shows slots in the guest's local time

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
pip install -e ".[mcp]"                 # + MCP server
pip install -e ".[telegram,web,mcp]"    # multiple channels
pip install -e ".[all]"                 # everything
```

## How it works

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ Telegram │  │  Slack   │  │ Discord  │  │ Web API  │  │   MCP    │
│ Adapter  │  │ Adapter  │  │ Adapter  │  │ Adapter  │  │  Server  │
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
       │              │              │              │              │
       └──────┬───────┘──────┬───────┘──────┬──────┘──────────────┘
                    │               │
          ┌─────────▼───────────────▼─┐
          │     SchedulingEngine      │
          │     (owner / guest)       │
          └──┬─────┬───────────────┬──┘
             │     │               │
    ┌────────▼─┐ ┌─▼──────┐ ┌─────▼───────┐
    │   LLM    │ │ Avail. │ │  Google Cal  │
    │ Provider │ │ Engine │ │  (freebusy)  │
    └──────────┘ └────────┘ └─────────────┘
```

The core engine is channel-agnostic. It receives `IncomingMessage`, returns `OutgoingMessage`. Channel adapters handle the translation.

## MCP Server

The MCP server lets AI agents interact with your calendar programmatically. Tools:

| Tool | Description |
|------|-------------|
| `get_services` | List available meeting types |
| `get_available_slots` | Check available times for a given date |
| `get_pricing` | Get pricing information |
| `book_consultation` | Book a meeting with name, email, date, time |
| `cancel_booking` | Cancel a booking by ID |

### Use in Claude Code

Add to your MCP config (`.claude/settings.json` or project settings):

```json
{
  "mcpServers": {
    "schedule-ivan": {
      "url": "https://schedule.yourdomain.com/mcp"
    }
  }
}
```

Then just ask naturally:

```
> Book a call with Ivan about AI consulting for next Wednesday afternoon
```

### Use locally (stdio)

```bash
schedulebot mcp --transport stdio
```

### Use remotely (HTTP)

When deployed with web channel enabled, MCP is mounted at `/mcp`:

```yaml
# config.yaml
mcp:
  enabled: true
  transport: "streamable-http"
  path: "/mcp"
```

Your MCP endpoint: `https://yourdomain.com/mcp`

## Agent Discovery

When deployed, your service serves two discovery endpoints:

| Endpoint | Purpose |
|----------|---------|
| `/.well-known/agent.json` | Agent identity card — lets AI agents find your scheduling endpoint |
| `/.well-known/mcp.json` | MCP-specific discovery — lists available MCP servers |

### Example `agent.json` response

```json
{
  "schema_version": "0.1",
  "name": "Ivan Pasichnyk",
  "description": "AI/ML engineer, available for consulting",
  "capabilities": {
    "scheduling": {
      "protocol": "mcp",
      "url": "https://schedule.yourdomain.com/mcp",
      "transport": "streamable-http",
      "tools": ["get_available_slots", "book_consultation", "cancel_booking"]
    }
  }
}
```

### How other agents find you

1. Agent fetches `https://yourdomain.com/.well-known/agent.json`
2. Finds MCP endpoint URL and available tools
3. Calls `get_available_slots` to check your calendar
4. Calls `book_consultation` to create a meeting
5. Calendar event + Google Meet link created automatically

Enable discovery in `config.yaml`:

```yaml
agent_card:
  enabled: true
  url: "https://schedule.yourdomain.com"
  description: "AI/ML engineer, available for consulting"
  organization: "Your Company"
```

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

mcp:
  enabled: false             # true to enable MCP server
  transport: "streamable-http"
  path: "/mcp"

agent_card:
  enabled: false
  url: ""                    # your public URL
```

See [config.example.yaml](config.example.yaml) for all options.

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

**Auto-detection:** Just set your API key in `.env`. If `provider: "anthropic"` but only `OPENAI_API_KEY` is set, schedulebot switches to OpenAI automatically (and adjusts the model).

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

# Manage schedule (requires API key)
curl -X POST http://localhost:8080/api/schedule/rules \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"day": "monday", "start": "10:00", "end": "18:00"}'

# Health check
curl http://localhost:8080/api/health
```

## Deploy

### Docker

```bash
# Copy and edit environment variables
cp .env.example .env
# Edit .env with your API keys and settings

# Place Google Calendar credentials
# credentials.json and token.json in project root
# (run `schedulebot check` locally first to authorize)

# Start
docker compose up -d

# Check logs
docker compose logs -f
```

Endpoints available after deploy:
- `/api/health` — health check
- `/api/message` — booking API
- `/mcp` — MCP server (if enabled)
- `/.well-known/agent.json` — agent discovery (if enabled)
- `/.well-known/mcp.json` — MCP discovery (if enabled)

**Docker environment variables:**

All configuration is set via environment variables in `.env`. The `docker-entrypoint.sh` generates `config.yaml` at runtime from these variables. See [.env.example](.env.example) for the full list.

Key variables for production:
- `PUBLIC_URL` — your public HTTPS URL (required for cancel links and agent discovery)
- `MCP_ENABLED=true` — enables MCP server (default in Docker)
- `AGENT_CARD_ENABLED=true` — enables agent.json discovery (default in Docker)

### Railway

One-click cloud deploy with auto-HTTPS. See the [Railway deployment guide](docs/deploy-railway.md).

### Building your own adapter

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

## Development

```bash
git clone https://github.com/anthroos/open-schedule-agent.git
cd open-schedule-agent
pip install -e ".[all,dev]"
pytest tests/ -v
```

## License

MIT
