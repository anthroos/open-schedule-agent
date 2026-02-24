# Managed Platform Plan

## Status: Planning (Feb 2026)

## Model

Open-source agents (MIT, self-hosted) + managed platform (paid, hosted by us).

```
Open source (GitHub)              Managed (welabeldata.com)
────────────────────              ──────────────────────────
Self-host, full control     →     Configure & deploy in clicks
Free forever                →     $5-15/mo per agent
Community support           →     Priority support, SLA
Manual updates              →     Auto-updates, monitoring
```

## Why This Works

1. Open source removes entry barrier — people try free, see it works
2. Managed removes DevOps pain — "just give me a URL"
3. Each new agent increases platform ARPU
4. Cross-agent composition is the moat (scheduling + CRM share context)

## Agent Portfolio (planned)

| Agent | Status | Description |
|-------|--------|-------------|
| Scheduling | v0.1.0 shipped | Calendar MCP endpoint, multi-channel, agent.json discovery |
| CRM/Outreach | Planned | Contact management, email/telegram/whatsapp outreach |
| Support | Planned | FAQ bot with human escalation |
| Invoice | Idea | Billing and payment tracking |

## Managed Platform — MVP

### What the customer gets:
- Persistent public URL with HTTPS
- MCP endpoint at /mcp
- Agent discovery at /.well-known/agent.json
- Dashboard: bookings, analytics, uptime
- Cancel links, reminders — all configured via UI

### Onboarding flow:
1. Sign up
2. Connect Google Calendar (OAuth)
3. Set timezone + working hours
4. Get URL → done

### Architecture (MVP):
- One Railway instance per customer (simple, isolates data)
- Automated deploy via Railway API or template
- Config generated from onboarding form
- Billing: Stripe, per-agent monthly

### Architecture (scale):
- Multi-tenant: one service, many configs
- Shared infra, lower cost per customer
- Migrate when >50 customers

## Revenue

- Free: open source, self-host
- Starter ($5/mo): 1 agent, 100 bookings/mo, subdomain
- Pro ($15/mo): custom domain, analytics, priority support
- Team ($49/mo): multiple agents, cross-agent workflows, SSO

## Go-to-market

1. Open source builds awareness (GitHub, MCP community, AI bloggers)
2. Landing page converts to managed (welabeldata.com/schedule-agent/)
3. Channels: Awesome MCP lists, AI/MCP YouTubers, Claude Code community, Open CLAW community
4. Content: "How to make your calendar AI-accessible in 5 min"

## Competitive Landscape

| | Cal.com | Calendly | Us |
|---|---|---|---|
| Interface | Forms | Forms | Conversational AI |
| AI agent support | No | No | Native MCP |
| Self-host | Yes | No | Yes |
| Agent discovery | No | No | agent.json |
| Price (managed) | $12/mo | $10/mo | $5/mo |

## Next Steps

1. Ensure open-source agent is solid (tests, docs, deploy)
2. Get early users via GitHub + MCP community
3. Validate demand for managed version
4. Build onboarding flow MVP
5. Launch managed version
