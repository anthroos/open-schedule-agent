# schedulebot

Open-source AI scheduling agent with pluggable channel adapters.

Your own Calendly, but conversational. Works with Telegram, Web API, Slack, Discord — or build your own adapter.

## How it works

1. Someone messages your bot (Telegram, web widget, etc.)
2. AI assistant asks their name, shows available time slots
3. Guest picks a slot, confirms
4. Google Calendar event + Meet link created automatically
5. You get a notification

## Quick start

```bash
pip install schedulebot[telegram]
schedulebot init
# Edit config.yaml and .env with your details
schedulebot check
schedulebot run
```

## Install options

```bash
pip install schedulebot                    # core only
pip install schedulebot[telegram]          # + Telegram
pip install schedulebot[web]               # + FastAPI web endpoint
pip install schedulebot[telegram,web]      # multiple channels
pip install schedulebot[all]               # everything
```

## Configuration

`config.yaml`:

```yaml
owner:
  name: "Your Name"

availability:
  timezone: "Europe/Kyiv"
  working_hours:
    monday: ["09:00-17:00"]
    tuesday: ["09:00-13:30"]
    wednesday: ["09:00-17:00"]
    thursday: ["09:00-17:00"]
    friday: ["09:00-13:30"]
  meeting_duration_minutes: 30
  buffer_minutes: 15

calendar:
  provider: "google"
  create_meet_link: true

llm:
  provider: "anthropic"     # anthropic | openai | ollama
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

## Architecture

```
Channel (TG/Slack/Web/Discord)
  → ChannelAdapter (abstract interface)
    → SchedulingEngine (channel-agnostic core)
      → AvailabilityEngine (YAML rules - calendar busy times)
      → LLM (Anthropic/OpenAI/Ollama)
      → Google Calendar (freebusy + event creation)
```

The core engine doesn't know what Telegram or Slack is. It receives `IncomingMessage`, returns `OutgoingMessage`. Channel adapters handle the translation.

## Building your own adapter

```python
from schedulebot.channels.base import ChannelAdapter
from schedulebot.models import IncomingMessage, OutgoingMessage

class MyAdapter(ChannelAdapter):
    @property
    def name(self) -> str:
        return "my_channel"

    async def start(self) -> None:
        # Start listening for messages
        ...

    async def stop(self) -> None:
        # Graceful shutdown
        ...

    async def send_message(self, sender_id: str, message: OutgoingMessage) -> None:
        # Send a message to a user
        ...
```

## Web API

When the web channel is enabled, you get a REST API:

```bash
# Send a message
curl -X POST http://localhost:8080/api/message \
  -H "Content-Type: application/json" \
  -d '{"sender_id": "user-123", "sender_name": "John", "text": "I want to book a meeting"}'

# Health check
curl http://localhost:8080/api/health
```

## Docker

```bash
docker compose up -d
```

## License

MIT
