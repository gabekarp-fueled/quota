import logging
import os
from contextlib import asynccontextmanager

import anthropic
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.routers import health, heartbeats, webhooks
from src.routers.api import router as api_router
from src.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def _init_db(app: FastAPI):
    """Initialize DB, create tables, and seed agent records."""
    if not settings.database_url:
        logger.warning("DATABASE_URL not set — DB features disabled (run history, OKR management)")
        app.state.db_available = False
        return

    try:
        from src.db.models import Agent, Base
        from src.db.session import get_session_factory, init_db
        from sqlalchemy import select, text

        engine, _ = init_db(settings.database_url)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for stmt in [
                "ALTER TABLE agents ADD COLUMN IF NOT EXISTS display_name VARCHAR",
                "ALTER TABLE agents ADD COLUMN IF NOT EXISTS system_prompt TEXT DEFAULT ''",
                "ALTER TABLE agents ADD COLUMN IF NOT EXISTS model VARCHAR DEFAULT 'claude-sonnet-4-6'",
                "ALTER TABLE agents ADD COLUMN IF NOT EXISTS batch_size INTEGER DEFAULT 10",
                "ALTER TABLE agents ADD COLUMN IF NOT EXISTS enabled BOOLEAN DEFAULT TRUE",
                "ALTER TABLE agents ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now()",
                "ALTER TABLE agents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
            ]:
                await conn.execute(text(stmt))
            type_rows = await conn.execute(text(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_name IN ('objectives', 'key_results', 'runs') AND column_name = 'id'"
            ))
            col_types = {(r.table_name, r.column_name): r.data_type for r in type_rows}
            logger.info("DB column types probe: %s", col_types)

        async with engine.begin() as mconn:
            obj_id_type = col_types.get(("objectives", "id"))
            if obj_id_type == "integer":
                logger.info("Migrating objectives INTEGER PK → UUID")
                await mconn.execute(text("ALTER TABLE key_results DROP CONSTRAINT IF EXISTS key_results_objective_id_fkey"))
                await mconn.execute(text("ALTER TABLE objectives DROP CONSTRAINT IF EXISTS objectives_pkey"))
                await mconn.execute(text("ALTER TABLE objectives ALTER COLUMN id DROP DEFAULT"))
                await mconn.execute(text("ALTER TABLE objectives ALTER COLUMN id TYPE UUID USING gen_random_uuid()"))
                await mconn.execute(text("ALTER TABLE objectives ALTER COLUMN id SET DEFAULT gen_random_uuid()"))
                await mconn.execute(text("ALTER TABLE objectives ADD PRIMARY KEY (id)"))

            kr_id_type = col_types.get(("key_results", "id"))
            if kr_id_type == "integer":
                logger.info("Migrating key_results INTEGER PKs → UUID")
                await mconn.execute(text("ALTER TABLE key_results DROP CONSTRAINT IF EXISTS key_results_pkey"))
                await mconn.execute(text("ALTER TABLE key_results DROP CONSTRAINT IF EXISTS key_results_objective_id_fkey"))
                await mconn.execute(text("TRUNCATE TABLE key_results"))
                await mconn.execute(text("ALTER TABLE key_results ALTER COLUMN id DROP DEFAULT"))
                await mconn.execute(text("ALTER TABLE key_results ALTER COLUMN id TYPE UUID USING gen_random_uuid()"))
                await mconn.execute(text("ALTER TABLE key_results ALTER COLUMN id SET DEFAULT gen_random_uuid()"))
                await mconn.execute(text("ALTER TABLE key_results ALTER COLUMN objective_id DROP DEFAULT"))
                await mconn.execute(text("ALTER TABLE key_results ALTER COLUMN objective_id TYPE UUID USING NULL"))
                await mconn.execute(text("ALTER TABLE key_results ADD PRIMARY KEY (id)"))
                await mconn.execute(text(
                    "ALTER TABLE key_results ADD CONSTRAINT key_results_objective_id_fkey "
                    "FOREIGN KEY (objective_id) REFERENCES objectives(id) ON DELETE CASCADE"
                ))

            runs_id_type = col_types.get(("runs", "id"))
            if runs_id_type == "integer":
                logger.info("Migrating runs INTEGER PK → UUID")
                await mconn.execute(text("ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_pkey"))
                await mconn.execute(text("ALTER TABLE runs ALTER COLUMN id DROP DEFAULT"))
                await mconn.execute(text("ALTER TABLE runs ALTER COLUMN id TYPE UUID USING gen_random_uuid()"))
                await mconn.execute(text("ALTER TABLE runs ALTER COLUMN id SET DEFAULT gen_random_uuid()"))
                await mconn.execute(text("ALTER TABLE runs ADD PRIMARY KEY (id)"))

            await mconn.execute(text("DROP INDEX IF EXISTS agents_name_unique"))
            await mconn.execute(text("UPDATE agents SET name = LOWER(name) WHERE name != LOWER(name)"))

            await mconn.execute(text("""
                DO $$
                DECLARE r RECORD;
                BEGIN
                    FOR r IN (
                        WITH RECURSIVE chain AS (
                            SELECT 'agents'::text COLLATE "C" AS t
                            UNION
                            SELECT tc.table_name::text
                            FROM information_schema.table_constraints tc
                            JOIN information_schema.referential_constraints rc
                              ON tc.constraint_name = rc.constraint_name
                            JOIN information_schema.key_column_usage ccu
                              ON ccu.constraint_name = rc.unique_constraint_name
                            JOIN chain ON ccu.table_name = chain.t
                            WHERE tc.constraint_type = 'FOREIGN KEY'
                        )
                        SELECT DISTINCT ON (tc.constraint_name)
                            tc.constraint_name,
                            tc.table_name    AS child_table,
                            kcu.column_name  AS child_col,
                            ccu.table_name   AS parent_table,
                            ccu.column_name  AS parent_col
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                          ON tc.constraint_name = kcu.constraint_name
                        JOIN information_schema.referential_constraints rc
                          ON tc.constraint_name = rc.constraint_name
                        JOIN information_schema.key_column_usage ccu
                          ON ccu.constraint_name = rc.unique_constraint_name
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                          AND ccu.table_name IN (SELECT t FROM chain)
                          AND rc.delete_rule != 'CASCADE'
                    ) LOOP
                        EXECUTE format(
                            'ALTER TABLE %I DROP CONSTRAINT %I',
                            r.child_table, r.constraint_name
                        );
                        EXECUTE format(
                            'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I(%I) ON DELETE CASCADE',
                            r.child_table, r.constraint_name, r.child_col, r.parent_table, r.parent_col
                        );
                    END LOOP;
                END $$;
            """))

            await mconn.execute(text("""
                DELETE FROM agents WHERE id IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY name ORDER BY
                                CASE WHEN model = 'claude-sonnet-4-6' THEN 1 ELSE 0 END ASC,
                                created_at ASC NULLS LAST
                        ) AS rn FROM agents
                    ) t WHERE rn > 1
                )
            """))
            await mconn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS agents_name_unique ON agents(name)"
            ))

            _required_agent_cols = {"id", "name"}
            nn_rows = await mconn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='agents' AND is_nullable='NO'"
            ))
            for nn_row in nn_rows:
                col = nn_row[0]
                if col not in _required_agent_cols:
                    await mconn.execute(text(f'ALTER TABLE agents ALTER COLUMN "{col}" DROP NOT NULL'))

            for agent_name, display in [
                ("scout", "Scout"), ("outreach", "Outreach"), ("enablement", "Enablement"),
                ("channels", "Channels"), ("cro", "CRO"), ("inbox", "Inbox"),
                ("digest", "Digest"), ("followup", "Follow-Up"),
            ]:
                await mconn.execute(text(
                    "UPDATE agents SET display_name = :d WHERE name = :n AND (display_name IS NULL OR display_name = '')"
                ), {"d": display, "n": agent_name})

        logger.info("DB tables created/verified")
        app.state.db_available = True

        try:
            _AGENT_DEFAULTS = [
                {"name": "scout",      "display_name": "Scout",      "model": settings.scout_model,      "batch_size": settings.scout_batch_size},
                {"name": "outreach",   "display_name": "Outreach",   "model": settings.outreach_model,   "batch_size": settings.outreach_batch_size},
                {"name": "enablement", "display_name": "Enablement", "model": settings.enablement_model, "batch_size": settings.enablement_batch_size},
                {"name": "channels",   "display_name": "Channels",   "model": settings.channels_model,   "batch_size": settings.channels_batch_size},
                {"name": "cro",        "display_name": "CRO",        "model": settings.cro_model,        "batch_size": settings.cro_batch_size},
            ]
            factory = get_session_factory()
            async with factory() as session:
                for defaults in _AGENT_DEFAULTS:
                    result = await session.execute(select(Agent).where(Agent.name == defaults["name"]))
                    existing = result.scalar_one_or_none()
                    if not existing:
                        session.add(Agent(
                            **defaults,
                            system_prompt=app.state.prompts.get(defaults["name"], ""),
                        ))
                        logger.info("Seeded agent '%s'", defaults["name"])
                    elif not existing.display_name:
                        existing.display_name = defaults["display_name"]
                await session.commit()
        except Exception:
            logger.exception("Agent seeding failed — existing agents unaffected")

    except Exception:
        logger.exception("DB initialization failed — DB features disabled (app will still start)")
        app.state.db_available = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Pipedrive CRM client ──────────────────────────────────────────────
    try:
        from src.pipedrive.client import PipedriveClient
        app.state.attio = PipedriveClient(api_token=settings.pipedrive_api_token)
        if settings.pipedrive_api_token:
            logger.info("Pipedrive client initialized")
        else:
            logger.warning("PIPEDRIVE_API_TOKEN not set — all CRM operations will fail")
    except ImportError:
        logger.error("PipedriveClient not found — check src/pipedrive/client.py")
        app.state.attio = None

    # ── Claude API client ─────────────────────────────────────────────────
    if settings.anthropic_api_key:
        app.state.claude = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        logger.info("Claude API client initialized")
    else:
        app.state.claude = None
        logger.warning("ANTHROPIC_API_KEY not set — agents will run in basic/stub mode")

    # ── Apollo.io client (contact sourcing for Scout) ─────────────────────
    if settings.apollo_api_key:
        try:
            from src.apollo.client import ApolloClient
            app.state.apollo = ApolloClient(api_key=settings.apollo_api_key)
            logger.info("Apollo.io client initialized")
        except ImportError:
            app.state.apollo = None
            logger.warning("Apollo client not found — Scout will run without contact sourcing")
    else:
        app.state.apollo = None
        logger.info("APOLLO_API_KEY not set — Scout will run without contact sourcing")

    # ── FullEnrich client (email enrichment fallback for Scout) ───────────
    if settings.fullenrich_api_key:
        try:
            from src.fullenrich.client import FullEnrichClient
            app.state.fullenrich = FullEnrichClient(api_key=settings.fullenrich_api_key)
            logger.info("FullEnrich client initialized")
        except ImportError:
            app.state.fullenrich = None
    else:
        app.state.fullenrich = None

    # ── Gmail API client (email delivery via OAuth2) ──────────────────────
    if (settings.gmail_refresh_token and settings.google_client_id
            and settings.google_client_secret and settings.gmail_from_email):
        try:
            from src.email.client import EmailClient
            app.state.email = EmailClient(
                from_email=settings.gmail_from_email,
                from_name=settings.gmail_from_name,
                client_id=settings.google_client_id,
                client_secret=settings.google_client_secret,
                refresh_token=settings.gmail_refresh_token,
            )
            logger.info("Gmail API email client initialized (%s)", settings.gmail_from_email)
        except ImportError:
            app.state.email = None
            logger.warning("EmailClient not found — Outreach will draft only, no sending")
    else:
        app.state.email = None
        logger.warning("Gmail credentials not set — Outreach will draft only, no sending")

    # ── Gmail IMAP client (inbox monitoring) ─────────────────────────────
    if settings.gmail_app_password and settings.gmail_from_email:
        try:
            from src.email.inbox import InboxClient
            app.state.inbox = InboxClient(
                email_address=settings.gmail_from_email,
                password=settings.gmail_app_password,
                host=settings.gmail_imap_host,
                port=settings.gmail_imap_port,
            )
            app.state.inbox_last_uid = 0
            logger.info("Gmail IMAP inbox client initialized (%s)", settings.gmail_from_email)
        except ImportError:
            app.state.inbox = None
            app.state.inbox_last_uid = 0
    else:
        app.state.inbox = None
        app.state.inbox_last_uid = 0
        logger.warning("Gmail credentials not set — inbox monitoring disabled")

    # ── Slack client (approval flow + conversational CRO) ────────────────
    if settings.slack_bot_token:
        try:
            from src.slack.client import SlackClient
            app.state.slack = SlackClient(bot_token=settings.slack_bot_token)
            app.state.slack_signing_secret = settings.slack_signing_secret
            app.state.slack_approval_channel = settings.slack_approval_channel_id
            logger.info("Slack client initialized (approval channel: %s)", settings.slack_approval_channel_id)
        except ImportError:
            app.state.slack = None
            app.state.slack_signing_secret = ""
            app.state.slack_approval_channel = ""
    else:
        app.state.slack = None
        app.state.slack_signing_secret = ""
        app.state.slack_approval_channel = ""
        logger.warning("SLACK_BOT_TOKEN not set — Tier 1 approvals will be CRM-only (no Slack)")

    # ── System prompts (loaded once from markdown files) ──────────────────
    try:
        from src.claude.prompts import load_all_prompts
        app.state.prompts = load_all_prompts()
    except ImportError:
        app.state.prompts = {}

    # ── Database ──────────────────────────────────────────────────────────
    await _init_db(app)

    # ── Background scheduler ──────────────────────────────────────────────
    scheduler_tasks = start_scheduler(app)

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    await stop_scheduler(scheduler_tasks)
    if app.state.attio:
        await app.state.attio.close()
    if getattr(app.state, "apollo", None):
        await app.state.apollo.close()
    if getattr(app.state, "fullenrich", None):
        await app.state.fullenrich.close()
    if getattr(app.state, "email", None):
        await app.state.email.close()
    if getattr(app.state, "inbox", None):
        await app.state.inbox.close()
    if getattr(app.state, "slack", None):
        await app.state.slack.close()


app = FastAPI(
    title="Quota Agent Service",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(heartbeats.router)
app.include_router(webhooks.router)
app.include_router(api_router)

# Serve the React dashboard — mounted last so API routes take priority
_static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="ui")
    logger.info("Serving React dashboard from %s", os.path.abspath(_static_dir))
else:
    logger.info("No static/ dir — React dashboard not available (run: cd ui && npm run build)")
