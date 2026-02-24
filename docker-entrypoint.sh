#!/bin/sh
# Fix volume permissions (Railway mounts volumes as root)
DB_DIR=$(dirname "${DATABASE_PATH:-/app/data/schedulebot.db}")
mkdir -p "$DB_DIR" 2>/dev/null
chown -R schedulebot:schedulebot "$DB_DIR" 2>/dev/null || true
chown schedulebot:schedulebot /app/data 2>/dev/null || true

# Generate config.yaml from environment variables at runtime.
# Uses Python + yaml.safe_dump to prevent shell injection via env vars.

python3 -c "
import os, yaml

config = {
    'owner': {
        'name': os.environ.get('OWNER_NAME', 'Owner'),
        'email': os.environ.get('OWNER_EMAIL', ''),
        'owner_ids': {
            'telegram': os.environ.get('OWNER_TELEGRAM_ID', ''),
            'web': 'owner',
        },
    },
    'availability': {
        'timezone': os.environ.get('TIMEZONE', 'UTC'),
        'meeting_duration_minutes': int(os.environ.get('MEETING_DURATION', '30')),
        'buffer_minutes': int(os.environ.get('BUFFER_MINUTES', '15')),
        'min_notice_hours': int(os.environ.get('MIN_NOTICE_HOURS', '4')),
        'max_days_ahead': int(os.environ.get('MAX_DAYS_AHEAD', '14')),
    },
    'calendar': {
        'provider': 'google',
        'create_meet_link': True,
        'credentials_path': 'credentials.json',
        'token_path': 'token.json',
    },
    'llm': {
        'provider': os.environ.get('LLM_PROVIDER', 'anthropic'),
        'model': os.environ.get('LLM_MODEL', 'claude-haiku-4-20250414'),
    },
    'channels': {
        'telegram': {
            'enabled': True,
            'bot_token': os.environ.get('TELEGRAM_BOT_TOKEN', ''),
        },
        'web': {
            'enabled': os.environ.get('WEB_ENABLED', 'true').lower() in ('true', '1', 'yes'),
            'host': '0.0.0.0',
            'port': int(os.environ.get('PORT', '8080')),
            'api_key': os.environ.get('SCHEDULEBOT_API_KEY', ''),
            'allowed_origins': [o.strip() for o in os.environ.get('CORS_ORIGINS', '').split(',') if o.strip()],
        },
    },
    'notifications': {
        'channel': 'telegram',
        'owner_id': os.environ.get('OWNER_TELEGRAM_ID', ''),
    },
    'mcp': {
        'enabled': os.environ.get('MCP_ENABLED', 'true').lower() in ('true', '1', 'yes'),
        'transport': 'streamable-http',
        'path': '/mcp',
    },
    'agent_card': {
        'enabled': os.environ.get('AGENT_CARD_ENABLED', 'true').lower() in ('true', '1', 'yes'),
        'url': os.environ.get('PUBLIC_URL', '') or (
            'https://' + os.environ['RAILWAY_PUBLIC_DOMAIN']
            if os.environ.get('RAILWAY_PUBLIC_DOMAIN') else ''
        ),
        'description': os.environ.get('AGENT_DESCRIPTION', ''),
        'organization': os.environ.get('AGENT_ORGANIZATION', ''),
    },
}

with open('/app/config.yaml', 'w') as f:
    yaml.safe_dump(config, f, default_flow_style=False, allow_unicode=True)
"

# Ensure DATABASE_PATH points to the writable data dir
export DATABASE_PATH="${DATABASE_PATH:-/app/data/schedulebot.db}"

# Drop privileges to schedulebot user
exec gosu schedulebot "$@"
