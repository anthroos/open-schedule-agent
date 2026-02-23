#!/bin/sh
# Generate config.yaml from environment variables at runtime.
# This avoids baking secrets into the Docker image.

cat > /app/config.yaml <<YAML
owner:
  name: "${OWNER_NAME:-Owner}"
  email: "${OWNER_EMAIL:-}"
  owner_ids:
    telegram: "${OWNER_TELEGRAM_ID:-}"
    web: "owner"

availability:
  timezone: "${TIMEZONE:-UTC}"
  meeting_duration_minutes: ${MEETING_DURATION:-30}
  buffer_minutes: ${BUFFER_MINUTES:-15}
  min_notice_hours: ${MIN_NOTICE_HOURS:-4}
  max_days_ahead: ${MAX_DAYS_AHEAD:-14}

calendar:
  provider: "google"
  create_meet_link: true
  credentials_path: "credentials.json"
  token_path: "token.json"

llm:
  provider: "${LLM_PROVIDER:-anthropic}"
  model: "${LLM_MODEL:-claude-haiku-4-20250414}"

channels:
  telegram:
    enabled: true
    bot_token: "${TELEGRAM_BOT_TOKEN}"
  web:
    enabled: ${WEB_ENABLED:-true}
    host: "0.0.0.0"
    port: ${PORT:-8080}
    api_key: "${SCHEDULEBOT_API_KEY:-}"
    allowed_origins: [${CORS_ORIGINS:-}]

notifications:
  channel: "telegram"
  owner_id: "${OWNER_TELEGRAM_ID:-}"

mcp:
  enabled: ${MCP_ENABLED:-true}
  transport: "streamable-http"
  path: "/mcp"

agent_card:
  enabled: ${AGENT_CARD_ENABLED:-true}
  url: "${PUBLIC_URL:-}"
  description: "${AGENT_DESCRIPTION:-}"
  organization: "${AGENT_ORGANIZATION:-}"
YAML

exec "$@"
