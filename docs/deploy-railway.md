# Deploy to Railway

One-click cloud deploy. Railway builds from the Dockerfile and manages HTTPS, domains, and restarts.

## Prerequisites

Before deploying, complete the local setup first:

1. [Google Calendar setup](setup-google.md) -- you need `credentials.json` and `token.json`
2. Telegram bot token from [@BotFather](https://t.me/BotFather)
3. Your Telegram user ID from [@userinfobot](https://t.me/userinfobot)
4. Anthropic or OpenAI API key

## 1. Create Railway Project

1. Go to [railway.app](https://railway.app/) and sign in
2. Click **New Project** -> **Deploy from GitHub repo**
3. Select `anthroos/schedulebot` (or your fork)
4. Railway will auto-detect `railway.toml` and use the Dockerfile

## 2. Add a Volume

The bot stores its SQLite database and Google token on disk. Without a volume, data is lost on every deploy.

1. In your project, click **+ New** -> **Volume**
2. Mount path: `/app/data`
3. Name: `schedulebot-volume`

## 3. Set Environment Variables

Go to your service -> **Variables** tab and add:

### Required

| Variable | Example | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | `123456:ABC-DEF...` | From BotFather |
| `OWNER_TELEGRAM_ID` | `123456789` | Your Telegram user ID |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Or use `OPENAI_API_KEY` instead |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `OWNER_NAME` | `Owner` | Your name (shown to guests) |
| `OWNER_EMAIL` | _(empty)_ | Contact email |
| `TIMEZONE` | `UTC` | Your timezone, e.g. `Asia/Makassar` |
| `MEETING_DURATION` | `30` | Meeting length in minutes |
| `BUFFER_MINUTES` | `15` | Buffer between meetings |
| `MIN_NOTICE_HOURS` | `4` | Minimum advance booking time |
| `MAX_DAYS_AHEAD` | `14` | How far ahead guests can book |
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `openai`, or `ollama` |
| `LLM_MODEL` | `claude-haiku-4-20250414` | Model name |
| `WEB_ENABLED` | `true` | Enable web API + health check |
| `PORT` | `8080` | Web server port (Railway sets this) |
| `SCHEDULEBOT_API_KEY` | _(empty)_ | API key for web endpoint auth |
| `CORS_ORIGINS` | _(empty)_ | Allowed origins, e.g. `"https://mysite.com"` |
| `MCP_ENABLED` | `true` | Enable MCP server for agent-to-agent |
| `AGENT_CARD_ENABLED` | `true` | Serve `/.well-known/agent.json` |
| `PUBLIC_URL` | _(empty)_ | Your Railway URL, e.g. `https://mybot.up.railway.app` |
| `AGENT_DESCRIPTION` | _(empty)_ | Description for agent discovery |
| `AGENT_ORGANIZATION` | _(empty)_ | Your org name |

### Google Calendar (for containerized deploy)

Since you can't open a browser on Railway, authorize locally first, then pass tokens as env vars:

```bash
# On your local machine (after running schedulebot check):
base64 < credentials.json    # copy output
base64 < token.json           # copy output
```

Set in Railway:

| Variable | Description |
|----------|-------------|
| `GOOGLE_CREDENTIALS_JSON` | Base64-encoded `credentials.json` |
| `GOOGLE_TOKEN_JSON` | Base64-encoded `token.json` |

## 4. Networking

Railway assigns a public domain automatically:

- Your URL: `https://<service>-<id>.up.railway.app`
- Port 8080 is exposed by default
- HTTPS is handled by Railway (no cert config needed)

To use a custom domain:
1. Go to **Settings** -> **Networking** -> **Public Networking**
2. Click **+ Custom Domain**
3. Add CNAME record in your DNS pointing to Railway

## 5. Verify

After deploy completes:

```bash
# Health check
curl https://your-service.up.railway.app/api/health

# Agent discovery
curl https://your-service.up.railway.app/.well-known/agent.json

# Send a test message (if web enabled)
curl -X POST https://your-service.up.railway.app/api/message \
  -H "Content-Type: application/json" \
  -d '{"sender_id": "test", "sender_name": "Test", "text": "hi"}'
```

Also test via Telegram -- message your bot and check it responds.

## How It Works

The `docker-entrypoint.sh` script generates `config.yaml` from environment variables at container startup. This means:

- No secrets are baked into the Docker image
- You change config by updating Railway env vars (triggers redeploy)
- The `railway.toml` configures the health check endpoint and restart policy

## Troubleshooting

### Health check failing
- Make sure `WEB_ENABLED=true` (default)
- Check logs in Railway for startup errors
- The health check hits `/api/health` on the configured port

### Google Calendar not working
- Verify `GOOGLE_CREDENTIALS_JSON` and `GOOGLE_TOKEN_JSON` are set
- These must be base64-encoded (no line breaks in the value)
- If token expired, re-run `schedulebot check` locally, re-encode `token.json`

### Bot not responding on Telegram
- Check `TELEGRAM_BOT_TOKEN` is correct
- Check logs for "Building Telegram app" message
- Make sure the bot is not running elsewhere (only one instance can use a bot token)

### Volume data lost
- Ensure the volume mount path matches where the app writes (`/app/data`)
- Check Railway volume is attached to the service
