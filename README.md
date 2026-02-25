# schedulebot

[![CI](https://github.com/anthroos/open-schedule-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/anthroos/open-schedule-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**Open-source AI scheduling agent. Make your calendar discoverable by AI agents.**

Deploy an always-on endpoint. Any AI agent discovers you via `agent.json`, checks availability via MCP, and books a meeting — no humans in the loop.

```
Guest: "Hi, I'd like to schedule a call"
Bot:   "Sure! What's your name, and where are you located?"
Guest: "Maria from Kyiv, maria@corp.com, about partnership"
Bot:   "Got it! Here are available slots in Kyiv time: ..."
Guest: "Wednesday at 13:00"
Bot:   "Confirmed! Wed Mar 04 at 13:00 Kyiv time. Calendar invite sent!"
```

## Features

- **MCP server** — AI agents book meetings via Model Context Protocol (Claude Code, Cursor, Open CLAW)
- **Agent discovery** — `/.well-known/agent.json` lets other agents find your endpoint automatically
- **Dual AI mode** — guests book meetings, owners manage schedule, both via conversation
- **Multi-LLM** — Anthropic Claude, OpenAI GPT, or local Ollama (auto-detected)
- **Multi-channel** — Telegram, Slack, Discord, Web API
- **Google Calendar** — freebusy check + event creation + Google Meet links
- **Booking management** — owner views upcoming meetings, cancels from chat (`/bookings`)
- **Dynamic timezone** — owner changes timezone at runtime (`/timezone` or via chat)
- **Guest timezone** — asks guest's city, shows slots in their local time, stores timezone with booking
- **Dual-TZ notifications** — owner sees both their time and guest's time in booking alerts
- **Reminders** — automatic reminders before meetings with self-service cancel links
- **Security** — rate limiting, prompt injection detection, input sanitization
- **Retry with backoff** — all external API calls protected against transient failures

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
  model: "claude-sonnet-4-20250514"

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

### Multi-Calendar

You can connect multiple Google Calendar accounts so the bot checks availability across all of them while creating bookings in one designated calendar.

**Roles:**
- `book` — the calendar where bookings are created (exactly one required)
- `watch` — read-only calendars used for availability checks; blocker events are created here when a booking is made

**Setup:**

1. For each Google account, create a separate OAuth credentials file in Google Cloud Console (same process as the [initial setup](docs/setup-google.md)). Name them distinctly, e.g. `credentials-work.json`, `credentials-personal.json`.

2. Authorize each account by running `schedulebot check` with the corresponding credentials. This creates a `token-*.json` for each.

3. Replace the `calendar:` section in `config.yaml` with a `calendars:` list:

```yaml
calendars:
  - name: "Work"
    calendar_id: "primary"
    credentials_path: "credentials-work.json"
    token_path: "token-work.json"
    role: "book"
    create_meet_link: true
  - name: "Personal"
    calendar_id: "primary"
    credentials_path: "credentials-personal.json"
    token_path: "token-personal.json"
    role: "watch"
    create_meet_link: false
```

4. Restart the bot.

**Notes:**
- The old single `calendar:` config still works — no changes needed if you use one calendar
- `calendar_id` is `"primary"` for the default calendar of the account, or a specific calendar ID (found in Google Calendar Settings → Integrate calendar)
- If a `watch` calendar is temporarily unreachable, the bot continues working with the remaining calendars
- If the `book` calendar is unreachable, booking fails (no silent double-bookings)

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

Owner: "Delete Monday 14:00-15:00"
Bot:   "Deleted rule: monday 14:00-15:00"

Owner: "Block Saturday"
Bot:   "Blocked: Saturday (all day)"

Owner: "What meetings do I have?"
Bot:   "You have 2 upcoming meetings:
        Mon Mar 02, 19:00-19:30 — Alex (alex@example.com) [11:00 London]
        Wed Mar 04, 19:00-19:30 — Maria (maria@corp.com) [13:00 Kyiv]"

Owner: "Cancel meeting with Alex"
Bot:   "Cancelled meeting with Alex. Calendar event removed."
```

**Quick commands:**

| Command | Description |
|---------|-------------|
| `/start` | Show bot intro, current rules, and upcoming meetings |
| `/schedule` | Show availability rules |
| `/bookings` | Show upcoming meetings |
| `/timezone` | Show or change timezone |
| `/clear` | Clear all availability rules |

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
