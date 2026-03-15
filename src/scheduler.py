"""Lightweight async scheduler for periodic agent heartbeats.

Runs as background asyncio tasks inside the FastAPI lifespan — no external
cron or scheduler service needed. Starts automatically with the app and
cancels cleanly on shutdown.

Schedule:
  7:00 AM  — CRO (reviews pipeline, dispatches Scout/Outreach/Enablement/Channels)
  8:00 AM  — Digest (Slack pipeline summary)
  10:00 AM — FollowUp (post-meeting follow-ups)
  Every 15m — Inbox Monitor (reply processing)

Scout, Outreach, Enablement, and Channels are dispatched by CRO with
specific focus instructions. They can also be triggered manually via
their heartbeat endpoints.
"""

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone

logger = logging.getLogger(__name__)

# US Pacific (UTC-7 PDT / UTC-8 PST) — adjust if needed
PACIFIC_OFFSET = timedelta(hours=-7)
PACIFIC_TZ = timezone(PACIFIC_OFFSET)

# Module-level status dict — updated by _run_* helpers, read by /admin
_scheduler_status: dict = {}


def get_scheduler_status() -> dict:
    """Return a snapshot of all scheduler task run statuses."""
    return dict(_scheduler_status)


async def _run_periodic(
    name: str,
    coro_factory,
    interval_seconds: int,
    initial_delay: int = 30,
):
    """Run a coroutine factory on a fixed interval."""
    await asyncio.sleep(initial_delay)
    logger.info("Scheduler: %s starting (every %ds)", name, interval_seconds)

    while True:
        try:
            logger.info("Scheduler: %s — running", name)
            _scheduler_status[name] = {"status": "running", "started_at": datetime.now(PACIFIC_TZ).isoformat()}
            await coro_factory()
            _scheduler_status[name] = {"status": "ok", "last_run_at": datetime.now(PACIFIC_TZ).isoformat()}
            logger.info("Scheduler: %s — done", name)
        except asyncio.CancelledError:
            logger.info("Scheduler: %s — cancelled", name)
            return
        except Exception:
            _scheduler_status[name] = {"status": "error", "last_run_at": datetime.now(PACIFIC_TZ).isoformat()}
            logger.exception("Scheduler: %s — failed (will retry next cycle)", name)

        await asyncio.sleep(interval_seconds)


async def _run_daily_at(
    name: str,
    coro_factory,
    target_time: time,
    tz: timezone = PACIFIC_TZ,
):
    """Run a coroutine factory once per day at a specific local time."""
    logger.info("Scheduler: %s will run daily at %s", name, target_time.isoformat())

    while True:
        now = datetime.now(tz)
        target = datetime.combine(now.date(), target_time, tzinfo=tz)

        if now >= target:
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        logger.info(
            "Scheduler: %s — next run in %.0f minutes (%s)",
            name,
            wait_seconds / 60,
            target.strftime("%Y-%m-%d %H:%M %Z"),
        )

        try:
            await asyncio.sleep(wait_seconds)
        except asyncio.CancelledError:
            logger.info("Scheduler: %s — cancelled", name)
            return

        try:
            logger.info("Scheduler: %s — running", name)
            _scheduler_status[name] = {"status": "running", "started_at": datetime.now(tz).isoformat()}
            await coro_factory()
            _scheduler_status[name] = {"status": "ok", "last_run_at": datetime.now(tz).isoformat()}
            logger.info("Scheduler: %s — done", name)
        except asyncio.CancelledError:
            logger.info("Scheduler: %s — cancelled", name)
            return
        except Exception:
            _scheduler_status[name] = {"status": "error", "last_run_at": datetime.now(tz).isoformat()}
            logger.exception("Scheduler: %s — failed (will retry tomorrow)", name)

        await asyncio.sleep(60)


async def _load_agent_cfg_from_db(app, agent_name: str) -> dict | None:
    """Load agent config from DB if available."""
    from src.db.session import get_session_factory
    from src.db.models import Agent
    from sqlalchemy import select

    factory = get_session_factory()
    if not factory:
        return None
    try:
        async with factory() as session:
            result = await session.execute(select(Agent).where(Agent.name == agent_name))
            agent = result.scalar_one_or_none()
            if agent:
                return {
                    "system_prompt": agent.system_prompt,
                    "model": agent.model,
                    "batch_size": agent.batch_size,
                    "enabled": agent.enabled,
                }
    except Exception:
        logger.exception("Failed to load agent config from DB for '%s'", agent_name)
    return None


async def _load_objectives_text_from_db() -> str:
    """Load active OKRs from DB and format for CRO injection."""
    from src.db.session import get_session_factory
    from src.db.models import Objective
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    factory = get_session_factory()
    if not factory:
        return ""
    try:
        async with factory() as session:
            result = await session.execute(
                select(Objective)
                .where(Objective.active == True)
                .options(selectinload(Objective.key_results))
            )
            objectives = result.scalars().all()
            if not objectives:
                return ""
            lines = ["## Current OKRs\n"]
            for obj in objectives:
                lines.append(f"**Objective:** {obj.title}")
                if obj.description:
                    lines.append(obj.description)
                if obj.target_date:
                    lines.append(f"Target: {obj.target_date}")
                for kr in obj.key_results:
                    progress = (
                        f"{kr.current_value}/{kr.target_value}"
                        if kr.target_value is not None
                        else str(kr.current_value)
                    )
                    lines.append(f"  - KR: {kr.title} ({kr.metric}: {progress})")
                lines.append("")
            return "\n".join(lines)
    except Exception:
        logger.exception("Failed to load OKRs from DB")
        return ""


async def _log_run_to_db(result: dict, agent_name: str, focus: str = "") -> None:
    """Save a completed run record to the DB from scheduler context."""
    from datetime import datetime, timezone
    from src.db.session import get_session_factory
    from src.db.models import Run

    factory = get_session_factory()
    if not factory:
        return
    try:
        async with factory() as session:
            run = Run(
                agent_name=agent_name,
                focus=focus,
                status=result.get("status", "ok"),
                turns=result.get("turns"),
                input_tokens=result.get("input_tokens"),
                output_tokens=result.get("output_tokens"),
                summary=(result.get("summary") or result.get("action") or "")[:2000],
                tools_used=result.get("tools_used") or [],
                completed_at=datetime.now(timezone.utc),
            )
            session.add(run)
            await session.commit()
    except Exception:
        logger.exception("Failed to log run to DB for '%s'", agent_name)


def start_scheduler(app) -> list[asyncio.Task]:
    """Create and return background scheduler tasks.

    Called during FastAPI lifespan startup. Returns task handles so
    they can be cancelled on shutdown.
    """
    from src.agents.cro import CROAgent
    from src.agents.digest import DigestAgent
    from src.agents.followup import FollowUpAgent
    from src.agents.inbox import InboxMonitorAgent
    from src.claude.tools import ToolRegistry
    from src.config import settings
    from src.tools.analytics_tools import register_analytics_tools
    from src.tools.attio_tools import register_attio_tools
    from src.tools.dispatch_tools import register_dispatch_tools
    from src.tools.email_tools import register_email_tools
    from src.tools.research_tools import register_research_tools

    tasks: list[asyncio.Task] = []

    # ── Inbox Monitor (every 15 minutes) ─────────────────────────────────
    inbox = getattr(app.state, "inbox", None)
    if inbox and getattr(app.state, "claude", None):

        async def _inbox_heartbeat():
            agent = InboxMonitorAgent(
                attio=app.state.attio,
                claude_client=app.state.claude,
                inbox_client=app.state.inbox,
                email_client=getattr(app.state, "email", None),
                slack_client=getattr(app.state, "slack", None),
                slack_channel=getattr(app.state, "slack_approval_channel", ""),
                sentiment_model=settings.inbox_sentiment_model,
            )
            result = await agent.run(last_uid=app.state.inbox_last_uid)
            if result.get("new_last_uid"):
                app.state.inbox_last_uid = result["new_last_uid"]
            logger.info(
                "Inbox: processed %d emails, new_last_uid=%s",
                result.get("emails_processed", 0),
                result.get("new_last_uid"),
            )

        tasks.append(
            asyncio.create_task(
                _run_periodic("inbox-monitor", _inbox_heartbeat, interval_seconds=900),
                name="scheduler:inbox-monitor",
            )
        )
    else:
        logger.warning("Scheduler: inbox monitor disabled (missing IMAP or Claude client)")

    # ── Daily Digest (8:00 AM Pacific) ───────────────────────────────────
    slack = getattr(app.state, "slack", None)
    if slack:

        async def _digest_heartbeat():
            if datetime.now(PACIFIC_TZ).weekday() >= 5:
                logger.info("Scheduler: daily-digest — skipping (weekend)")
                return
            agent = DigestAgent(
                attio=app.state.attio,
                slack_client=app.state.slack,
                slack_channel=app.state.slack_approval_channel,
            )
            result = await agent.run()
            logger.info(
                "Digest: %d accounts, posted=%s",
                result.get("total_accounts", 0),
                result.get("action"),
            )

        tasks.append(
            asyncio.create_task(
                _run_daily_at("daily-digest", _digest_heartbeat, target_time=time(8, 0)),
                name="scheduler:daily-digest",
            )
        )
    else:
        logger.warning("Scheduler: daily digest disabled (no Slack client)")

    # ── Follow-Up Check (10:00 AM Pacific) ───────────────────────────────
    claude_client = getattr(app.state, "claude", None)
    if claude_client:

        async def _followup_heartbeat():
            if datetime.now(PACIFIC_TZ).weekday() >= 5:
                logger.info("Scheduler: followup-check — skipping (weekend)")
                return
            agent = FollowUpAgent(
                attio=app.state.attio,
                claude_client=app.state.claude,
                slack_client=getattr(app.state, "slack", None),
                slack_channel=getattr(app.state, "slack_approval_channel", ""),
                model=settings.inbox_sentiment_model,
            )
            result = await agent.run()
            logger.info(
                "FollowUp: checked=%d, drafted=%d, skipped=%d",
                result.get("accounts_checked", 0),
                result.get("drafted", 0),
                result.get("skipped", 0),
            )

        tasks.append(
            asyncio.create_task(
                _run_daily_at("followup-check", _followup_heartbeat, target_time=time(10, 0)),
                name="scheduler:followup-check",
            )
        )
    else:
        logger.warning("Scheduler: followup check disabled (no Claude client)")

    # ── CRO (daily 7:00 AM Pacific) ───────────────────────────────────────
    if claude_client:

        async def _cro_heartbeat():
            if datetime.now(PACIFIC_TZ).weekday() >= 5:
                logger.info("Scheduler: CRO — skipping (weekend)")
                return
            cfg = await _load_agent_cfg_from_db(app, "cro")
            system_prompt = cfg["system_prompt"] if cfg else app.state.prompts.get("cro", "")
            model = cfg["model"] if cfg else settings.cro_model
            batch_size = cfg["batch_size"] if cfg else settings.cro_batch_size
            objectives_text = await _load_objectives_text_from_db()

            registry = ToolRegistry()
            register_attio_tools(registry, app.state.attio)
            register_analytics_tools(registry, app.state.attio)
            email_client = getattr(app.state, "email", None)
            apollo = getattr(app.state, "apollo", None)
            fullenrich = getattr(app.state, "fullenrich", None)
            if email_client:
                register_email_tools(
                    registry, email_client, app.state.attio, settings.email_daily_send_limit
                )
            register_research_tools(
                registry,
                app.state.attio,
                app.state.claude,
                scout_prompt=app.state.prompts.get("scout", ""),
                scout_model=settings.scout_model,
                scout_batch_size=settings.scout_batch_size,
                apollo=apollo,
                fullenrich=fullenrich,
            )
            register_dispatch_tools(registry, app)
            agent = CROAgent(
                attio=app.state.attio,
                claude_client=app.state.claude,
                system_prompt=system_prompt,
                tool_registry=registry,
                model=model,
                batch_size=batch_size,
                objectives_text=objectives_text,
            )
            result = await agent.run()
            await _log_run_to_db(result, "cro")
            logger.info("CRO: %s", result.get("summary", "")[:200])

        tasks.append(
            asyncio.create_task(
                _run_daily_at("cro", _cro_heartbeat, target_time=time(7, 0)),
                name="scheduler:cro",
            )
        )
    else:
        logger.warning("Scheduler: CRO disabled (no Claude client)")

    logger.info("Scheduler: started %d background tasks", len(tasks))
    return tasks


async def stop_scheduler(tasks: list[asyncio.Task]):
    """Cancel all scheduler tasks and wait for them to finish."""
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Scheduler: all tasks stopped")
