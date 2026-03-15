import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.agents.channels import ChannelsAgent
from src.agents.cro import CROAgent
from src.agents.digest import DigestAgent
from src.agents.enablement import EnablementAgent
from src.agents.followup import FollowUpAgent
from src.agents.inbox import InboxMonitorAgent
from src.agents.outreach import OutreachAgent
from src.agents.scout import ScoutAgent
from src.claude.tools import ToolRegistry
from src.config import settings
from src.db.models import Agent, Objective, Run
from src.db.session import get_db_optional
from src.tools.analytics_tools import register_analytics_tools
from src.tools.attio_tools import register_attio_tools
from src.tools.dispatch_tools import register_dispatch_tools
from src.tools.email_tools import register_email_tools
from src.tools.okr_tools import register_okr_tools
from src.tools.research_tools import register_research_tools

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["heartbeats"])


class HeartbeatRequest(BaseModel):
    focus: str | None = None


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _get_agent_cfg(db: AsyncSession | None, name: str, app_state) -> dict:
    """Load agent config from DB; fall back to settings defaults."""
    if db:
        result = await db.execute(select(Agent).where(Agent.name == name))
        agent = result.scalar_one_or_none()
        if agent:
            return {
                "system_prompt": agent.system_prompt,
                "model": agent.model,
                "batch_size": agent.batch_size,
                "enabled": agent.enabled,
            }
    return {
        "system_prompt": app_state.prompts.get(name, ""),
        "model": getattr(settings, f"{name}_model", settings.cro_model),
        "batch_size": getattr(settings, f"{name}_batch_size", 5),
        "enabled": True,
    }


async def _get_objectives_text(db: AsyncSession | None) -> str:
    """Load active OKRs from DB and format as CRO prompt prefix."""
    if not db:
        return ""
    result = await db.execute(
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


async def _log_run(db: AsyncSession | None, result: dict, agent_name: str, focus: str = "") -> None:
    """Save a completed run record to the DB."""
    if not db:
        return
    run = Run(
        agent_name=agent_name,
        focus=focus or "",
        status=result.get("status", "ok"),
        turns=result.get("turns"),
        input_tokens=result.get("input_tokens"),
        output_tokens=result.get("output_tokens"),
        summary=(result.get("summary") or result.get("action") or "")[:2000],
        tools_used=result.get("tools_used") or [],
        completed_at=datetime.now(timezone.utc),
    )
    db.add(run)
    await db.commit()


# ── Tool registry builders ────────────────────────────────────────────────────

def _build_scout_tools(attio, apollo=None, fullenrich=None) -> ToolRegistry:
    registry = ToolRegistry()
    register_attio_tools(registry, attio)
    if apollo:
        try:
            from src.tools.apollo_tools import register_apollo_tools
            register_apollo_tools(registry, apollo, attio, settings.apollo_credits_per_heartbeat,
                                  fullenrich=fullenrich)
        except ImportError:
            pass
    return registry


def _build_outreach_tools(attio, email_client=None, slack=None, slack_channel=None) -> ToolRegistry:
    registry = ToolRegistry()
    register_attio_tools(registry, attio)
    if email_client:
        register_email_tools(registry, email_client, attio, settings.email_daily_send_limit)
    if slack and slack_channel:
        try:
            from src.tools.slack_tools import register_slack_tools
            register_slack_tools(registry, slack, slack_channel)
        except ImportError:
            pass
    return registry


def _build_enablement_tools(attio) -> ToolRegistry:
    registry = ToolRegistry()
    register_attio_tools(registry, attio)
    try:
        from src.tools.content_tools import register_content_tools
        register_content_tools(registry)
    except ImportError:
        pass
    return registry


def _build_cro_tools(request: Request) -> ToolRegistry:
    attio = request.app.state.attio
    email_client = getattr(request.app.state, "email", None)
    claude_client = request.app.state.claude
    apollo = getattr(request.app.state, "apollo", None)
    fullenrich = getattr(request.app.state, "fullenrich", None)
    scout_prompt = request.app.state.prompts.get("scout", "")

    registry = ToolRegistry()
    register_attio_tools(registry, attio)
    register_analytics_tools(registry, attio)
    if email_client:
        register_email_tools(registry, email_client, attio, settings.email_daily_send_limit)
    register_research_tools(
        registry, attio, claude_client,
        scout_prompt=scout_prompt,
        scout_model=settings.scout_model,
        scout_batch_size=settings.scout_batch_size,
        apollo=apollo,
        fullenrich=fullenrich,
    )
    register_okr_tools(registry)
    register_dispatch_tools(registry, request.app)
    return registry


# ── Agent heartbeat endpoints ─────────────────────────────────────────────────

@router.post("/scout/heartbeat")
async def scout_heartbeat(
    request: Request,
    body: HeartbeatRequest = HeartbeatRequest(),
    db: AsyncSession | None = Depends(get_db_optional),
):
    """Trigger Scout manually. Normally dispatched by CRO."""
    attio = request.app.state.attio
    apollo = getattr(request.app.state, "apollo", None)
    fullenrich = getattr(request.app.state, "fullenrich", None)
    cfg = await _get_agent_cfg(db, "scout", request.app.state)
    agent = ScoutAgent(
        attio=attio,
        claude_client=request.app.state.claude,
        system_prompt=cfg["system_prompt"],
        tool_registry=_build_scout_tools(attio, apollo, fullenrich=fullenrich),
        model=cfg["model"],
        batch_size=cfg["batch_size"],
    )
    result = await agent.run(focus=body.focus)
    await _log_run(db, result, "scout", focus=body.focus or "")
    return {"agent": "scout", "status": "completed", "result": result}


@router.post("/outreach/heartbeat")
async def outreach_heartbeat(
    request: Request,
    body: HeartbeatRequest = HeartbeatRequest(),
    db: AsyncSession | None = Depends(get_db_optional),
):
    """Trigger Outreach manually. Normally dispatched by CRO."""
    attio = request.app.state.attio
    email_client = getattr(request.app.state, "email", None)
    slack = getattr(request.app.state, "slack", None)
    slack_channel = getattr(request.app.state, "slack_approval_channel", "")
    cfg = await _get_agent_cfg(db, "outreach", request.app.state)
    agent = OutreachAgent(
        attio=attio,
        claude_client=request.app.state.claude,
        system_prompt=cfg["system_prompt"],
        tool_registry=_build_outreach_tools(attio, email_client, slack, slack_channel),
        model=cfg["model"],
        batch_size=cfg["batch_size"],
    )
    result = await agent.run(focus=body.focus)
    await _log_run(db, result, "outreach", focus=body.focus or "")
    return {"agent": "outreach", "status": "completed", "result": result}


@router.post("/enablement/heartbeat")
async def enablement_heartbeat(
    request: Request,
    body: HeartbeatRequest = HeartbeatRequest(),
    db: AsyncSession | None = Depends(get_db_optional),
):
    """Trigger Enablement manually. Normally dispatched by CRO."""
    attio = request.app.state.attio
    cfg = await _get_agent_cfg(db, "enablement", request.app.state)
    agent = EnablementAgent(
        attio=attio,
        claude_client=request.app.state.claude,
        system_prompt=cfg["system_prompt"],
        tool_registry=_build_enablement_tools(attio),
        model=cfg["model"],
        batch_size=cfg["batch_size"],
    )
    result = await agent.run(focus=body.focus)
    await _log_run(db, result, "enablement", focus=body.focus or "")
    return {"agent": "enablement", "status": "completed", "result": result}


@router.post("/channels/heartbeat")
async def channels_heartbeat(
    request: Request,
    body: HeartbeatRequest = HeartbeatRequest(),
    db: AsyncSession | None = Depends(get_db_optional),
):
    """Trigger Channels manually. Normally dispatched by CRO."""
    attio = request.app.state.attio
    cfg = await _get_agent_cfg(db, "channels", request.app.state)
    registry = ToolRegistry()
    register_attio_tools(registry, attio)
    agent = ChannelsAgent(
        attio=attio,
        claude_client=request.app.state.claude,
        system_prompt=cfg["system_prompt"],
        tool_registry=registry,
        model=cfg["model"],
        batch_size=cfg["batch_size"],
    )
    result = await agent.run(focus=body.focus)
    await _log_run(db, result, "channels", focus=body.focus or "")
    return {"agent": "channels", "status": "completed", "result": result}


@router.post("/cro/heartbeat")
async def cro_heartbeat(
    request: Request,
    db: AsyncSession | None = Depends(get_db_optional),
):
    """Daily pipeline review. CRO reads the pipeline, handles hot leads, and dispatches agents."""
    attio = request.app.state.attio
    cfg = await _get_agent_cfg(db, "cro", request.app.state)
    objectives_text = await _get_objectives_text(db)
    agent = CROAgent(
        attio=attio,
        claude_client=request.app.state.claude,
        system_prompt=cfg["system_prompt"],
        tool_registry=_build_cro_tools(request),
        model=cfg["model"],
        batch_size=cfg["batch_size"],
        objectives_text=objectives_text,
    )
    result = await agent.run()
    await _log_run(db, result, "cro")
    return {"agent": "cro", "status": "completed", "result": result}


@router.post("/inbox/heartbeat")
async def inbox_heartbeat(
    request: Request,
    db: AsyncSession | None = Depends(get_db_optional),
):
    """Runs every 15 minutes. Checks Gmail for replies and processes them."""
    attio = request.app.state.attio
    inbox = getattr(request.app.state, "inbox", None)
    slack = getattr(request.app.state, "slack", None)
    slack_channel = getattr(request.app.state, "slack_approval_channel", "")

    if not inbox:
        return {"agent": "inbox", "status": "skipped", "reason": "no inbox client"}

    last_uid = getattr(request.app.state, "inbox_last_uid", 0)
    email_client = getattr(request.app.state, "email", None)

    agent = InboxMonitorAgent(
        attio=attio,
        claude_client=request.app.state.claude,
        inbox_client=inbox,
        email_client=email_client,
        slack_client=slack,
        slack_channel=slack_channel,
        sentiment_model=settings.inbox_sentiment_model,
    )
    result = await agent.run(last_uid=last_uid)

    new_last_uid = result.get("new_last_uid", last_uid)
    request.app.state.inbox_last_uid = new_last_uid

    await _log_run(db, result, "inbox")
    return {"agent": "inbox", "status": "completed", "result": result}


@router.post("/digest/heartbeat")
async def digest_heartbeat(
    request: Request,
    db: AsyncSession | None = Depends(get_db_optional),
):
    """Runs daily at 8am. Posts pipeline digest to Slack."""
    attio = request.app.state.attio
    slack = getattr(request.app.state, "slack", None)
    slack_channel = getattr(request.app.state, "slack_approval_channel", "")

    if not slack:
        return {"agent": "digest", "status": "skipped", "reason": "no slack client"}

    agent = DigestAgent(
        attio=attio,
        slack_client=slack,
        slack_channel=slack_channel,
    )
    result = await agent.run()
    await _log_run(db, result, "digest")
    return {"agent": "digest", "status": "completed", "result": result}


@router.post("/followup/heartbeat")
async def followup_heartbeat(
    request: Request,
    db: AsyncSession | None = Depends(get_db_optional),
):
    """Runs daily at 10am. Scans Meeting Booked accounts and drafts follow-ups."""
    attio = request.app.state.attio
    slack = getattr(request.app.state, "slack", None)
    slack_channel = getattr(request.app.state, "slack_approval_channel", "")

    agent = FollowUpAgent(
        attio=attio,
        claude_client=request.app.state.claude,
        slack_client=slack,
        slack_channel=slack_channel,
        model=settings.inbox_sentiment_model,
    )
    result = await agent.run()
    await _log_run(db, result, "followup")
    return {"agent": "followup", "status": "completed", "result": result}
