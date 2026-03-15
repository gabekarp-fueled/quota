# Quota

An open-source AI sales agent framework built on [Claude](https://anthropic.com/claude). Quota runs a team of autonomous agents that research prospects, send outreach sequences, monitor your inbox, and report on pipeline — all coordinated by a CRO agent that reads your OKRs and decides what to focus on each day.

---

## Overview

Quota ships eight agents out of the box:

| Agent | Trigger | What it does |
|-------|---------|--------------|
| **CRO** | Daily 7am | Reviews OKRs, dispatches other agents, posts a daily plan |
| **Scout** | On demand | Researches a company and finds contacts via Apollo |
| **Outreach** | On demand | Sends 3-touch email sequences via Gmail |
| **Enablement** | On demand | Generates call prep briefs from CRM data |
| **Channels** | On demand | Produces channel partner opportunity reports |
| **Inbox** | Every 15min | Reads Gmail, classifies replies, updates Attio |
| **Digest** | Daily 8am | Summarizes pipeline activity, posts to Slack |
| **Follow-Up** | Daily 10am | Drafts post-call follow-up emails, queues for approval |

All agents share a common architecture: they receive a system prompt (editable via the dashboard), a set of tools, and run in an agentic loop powered by Claude's tool use API.

---

## Architecture

```
quota/
├── src/
│   ├── agents/          # Agent implementations
│   │   ├── base.py      # BaseAgent + run_agent_loop
│   │   ├── cro.py
│   │   ├── scout.py
│   │   ├── outreach.py
│   │   ├── enablement.py
│   │   ├── channels.py
│   │   ├── inbox.py
│   │   ├── digest.py
│   │   └── followup.py
│   ├── tools/           # Tool registries
│   │   ├── attio_tools.py
│   │   ├── email_tools.py
│   │   ├── research_tools.py
│   │   ├── analytics_tools.py
│   │   ├── dispatch_tools.py
│   │   ├── okr_tools.py
│   │   └── slack_reply_tools.py
│   ├── routers/         # FastAPI routers
│   │   ├── api.py       # Management REST API
│   │   ├── heartbeats.py
│   │   ├── webhooks.py
│   │   └── health.py
│   ├── claude/
│   │   ├── loop.py      # Agentic loop + ToolRegistry
│   │   ├── tools.py
│   │   └── prompts.py   # Prompt file loader
│   ├── db/
│   │   ├── models.py    # SQLAlchemy models
│   │   └── session.py
│   ├── config.py        # Pydantic settings
│   ├── main.py          # FastAPI app + lifespan
│   └── scheduler.py     # Asyncio background scheduler
├── prompts/             # Markdown system prompts (editable via UI)
│   ├── shared.md        # Prepended to every agent's prompt
│   ├── cro.md
│   ├── scout.md
│   ├── outreach.md
│   └── ...
├── ui/                  # React dashboard (Vite + Tailwind)
├── Dockerfile
├── pyproject.toml
├── .env.example
└── oauth_setup.py
```

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/your-org/quota.git
cd quota
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — at minimum set DATABASE_URL, ANTHROPIC_API_KEY, DASHBOARD_PASSWORD
```

### 3. Set up Gmail OAuth

```bash
pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
python oauth_setup.py
```

See [SETUP.md](SETUP.md) for full Gmail, Slack, Apollo, and Attio configuration.

### 4. Customize your prompts

Edit the files in `prompts/` — replace all `[YOUR X]` placeholders with your company context, product description, and scheduling link.

### 5. Run the server

```bash
uvicorn src.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) and sign in with your `DASHBOARD_PASSWORD`.

---

## Dashboard

The web dashboard provides:

- **Dashboard** — Agent status, recent runs, pipeline summary
- **Agents** — Configure models, batch sizes, and edit prompt files live
- **OKRs** — Manage objectives and key results injected into CRO
- **Runs** — Full run history with tool usage and summaries

---

## Agents in Detail

### CRO

The CRO agent runs every morning and acts as an orchestrator. It:
1. Reads your current OKRs from the database
2. Queries Attio for pipeline state
3. Decides which sub-agents to dispatch using the `dispatch_agent` tool
4. Posts a daily plan to Slack

You control the CRO's priorities by editing your OKRs in the dashboard.

### Scout

Given a company name, Scout:
1. Searches Attio for existing data
2. Searches Apollo for contacts and company info
3. Creates/updates the Attio company record
4. Returns a structured Research Brief

### Outreach

Outreach executes email sequences:
- **Touch 1** — Initial cold outreach
- **Touch 2** — +8 days, if no reply
- **Touch 3** — +14 days, if no reply
- **Nurture** — Ongoing low-frequency contact

It uses Gmail OAuth2 to send, and records sends back to Attio.

### Inbox

Polls Gmail every 15 minutes, classifies replies (positive/negative/meeting booked/other), and updates Attio outreach status accordingly.

### Digest

Aggregates the day's pipeline activity and posts a formatted summary to Slack.

### Follow-Up

After calls logged in Attio, generates personalized follow-up emails and queues them as Gmail drafts (or sends via Slack approval flow if Slack is configured).

---

## Integrations

| Integration | Required | Purpose |
|-------------|----------|---------|
| Anthropic Claude | Yes | Powers all agents |
| PostgreSQL | Yes | Stores agents, runs, OKRs |
| Gmail (OAuth2) | Recommended | Send/receive email |
| Attio CRM | Recommended | Prospect and pipeline data |
| Apollo.io | Optional | Contact sourcing |
| FullEnrich | Optional | Email verification |
| Slack | Optional | Notifications and approvals |

All integrations except Claude and PostgreSQL gracefully degrade — the server starts and agents run even if they're not configured.

---

## Deploying to Railway

1. Create a new Railway project
2. Add a PostgreSQL plugin
3. Connect your GitHub repo
4. Set environment variables (copy from `.env.example`)
5. Change `DATABASE_URL` scheme to `postgresql+asyncpg://`
6. Deploy — Railway uses the Dockerfile automatically

---

## Customization

### Adding an agent

1. Create `src/agents/your_agent.py` extending `BaseAgent`
2. Add a prompt at `prompts/your_agent.md`
3. Add a heartbeat endpoint in `src/routers/heartbeats.py`
4. Seed it in `src/main.py` → `_AGENT_DEFAULTS`
5. Optionally add it to `_VALID_AGENTS` in `src/tools/dispatch_tools.py`

### Swapping the CRM

The CRM integration is contained in `src/tools/attio_tools.py`. Replace it with tools for HubSpot, Salesforce, or any other CRM — the agent architecture is CRM-agnostic.

### Changing models

Each agent has a configurable model in the database, editable via the dashboard. You can run different agents on different Claude models (e.g. Haiku for inbox/digest, Sonnet for scout/outreach).

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT — see [LICENSE](LICENSE).
