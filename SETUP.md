# Quota Setup Guide

This guide covers configuring Attio CRM field slugs and the other integration-specific setup steps that go beyond the basic `.env` configuration.

## Attio CRM Field Slugs

Quota reads and writes Attio company records using field slugs. The default slugs assumed in `src/tools/attio_tools.py` are:

| Field | Default Slug | Description |
|-------|-------------|-------------|
| Account tier | `account_tier` | Prospect priority (e.g. Tier 1, Tier 2) |
| Segment | `segment` | Industry or market segment |
| Outreach status | `outreach_status` | Pipeline stage (see values below) |
| Channel partner | `channel_partner` | Referring partner name |

### Outreach Status Values

The pipeline summary in the dashboard groups companies by `outreach_status`. The default values used in `src/routers/api.py` are:

- `responded` — Prospect replied to outreach
- `meeting_booked` — Meeting scheduled
- `need_attention` — Requires manual follow-up

To use different values, update the `pipeline_summary` function in `src/routers/api.py` and the `outreach.md` prompt.

### Finding Your Field Slugs

1. Go to app.attio.com → your workspace
2. Navigate to Settings → Objects → Companies → Attributes
3. Each attribute has a "Slug" field — use this value in the code

### Customizing Slugs

If your Attio workspace uses different slugs, update `_SELECT_SLUGS` in `src/tools/attio_tools.py`:

```python
_SELECT_SLUGS = {
    "your_tier_slug",
    "your_segment_slug",
    "your_status_slug",
}
```

Also update the tool descriptions in `attio_query_companies` and `attio_update_company` to reflect your actual field names so the agents use them correctly.

## Gmail OAuth2 Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or select an existing one)
3. Enable the **Gmail API**: APIs & Services → Library → search "Gmail API" → Enable
4. Create credentials: APIs & Services → Credentials → Create Credentials → OAuth client ID
   - Application type: **Desktop app**
   - Name: "Quota"
5. Download the credentials JSON file and save it as `credentials.json` in the project root
6. Run the setup script:
   ```bash
   pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
   python oauth_setup.py
   ```
7. A browser window will open — authorize the app with your Gmail account
8. The script creates `gmail_token.json` — this is your OAuth token
9. Set `GMAIL_TOKEN_PATH=gmail_token.json` in your `.env`
10. For deployment, copy `gmail_token.json` to your server and set the path accordingly

## Slack Bot Setup

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → Create New App → From scratch
2. Add the following **Bot Token Scopes** (OAuth & Permissions):
   - `chat:write` — Post messages
   - `channels:history` — Read channel messages
   - `im:history` — Read DM messages
   - `im:write` — Send DMs
3. Install the app to your workspace
4. Copy the **Bot User OAuth Token** (xoxb-...) → set as `SLACK_BOT_TOKEN`
5. Add the bot to the channel where you want digests posted
6. Copy the channel ID (right-click channel → Copy link → the ID is the last path segment) → set as `SLACK_CHANNEL_ID`

## Apollo.io Setup

1. Log in to [apollo.io](https://app.apollo.io)
2. Go to Settings → API → Create API Key
3. Set as `APOLLO_API_KEY` in your `.env`

The Scout agent uses Apollo to search for contacts at target companies and enrich prospect data.

## Database Setup

Quota requires a PostgreSQL database. Tables are created automatically on startup.

### Local Development

```bash
# Using Docker
docker run -d \
  --name quota-db \
  -e POSTGRES_DB=quota \
  -e POSTGRES_USER=quota \
  -e POSTGRES_PASSWORD=quota \
  -p 5432:5432 \
  postgres:16

# Set in .env:
DATABASE_URL=postgresql+asyncpg://quota:quota@localhost:5432/quota
```

### Railway Deployment

1. Add a PostgreSQL plugin to your Railway project
2. Copy the `DATABASE_URL` from Railway → Variables → DATABASE_URL
3. Change the scheme from `postgresql://` to `postgresql+asyncpg://`

## Prompt Customization

All agent prompts are stored as Markdown files in `prompts/`. They are seeded into the database on first run and can be edited live via the dashboard UI (Agents → select agent → Prompt Files tab).

Key placeholders to replace in the prompts:

- `[YOUR COMPANY NAME]` — Your company name
- `[YOUR PRODUCT/SERVICE]` — What you sell
- `[YOUR TARGET MARKET]` — Who you sell to
- `[YOUR VALUE PROPOSITION]` — Why customers choose you
- `[YOUR SCHEDULING LINK]` — Your Calendly or equivalent link
- `[YOUR NAME]` — Your name for email signatures

The `shared.md` file is prepended to every agent's prompt, making it the right place for company-wide context.

## Email Signature

Update the placeholder signature in `src/tools/email_tools.py`:

```python
_SIGNATURE_HTML = """
<br><br>
<div style="font-family: Arial, sans-serif; font-size: 13px; color: #333;">
  <strong>Your Name</strong><br>
  Your Title · Your Company<br>
  <a href="tel:+15551234567">+1 555 123 4567</a> ·
  <a href="https://yourcompany.com">yourcompany.com</a><br>
  <a href="https://cal.com/yourname">Schedule a call →</a>
</div>
"""
```
