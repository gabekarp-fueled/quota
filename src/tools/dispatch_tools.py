"""Dispatch tools for CRO — run sub-agents with specific focus instructions.

CRO calls dispatch_agent(agent, focus) after its pipeline review to direct
each sub-agent's work for the day. The agent runs in-process and returns
a summary of what it did.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from src.claude.tools import ToolRegistry
from src.config import settings

logger = logging.getLogger(__name__)

_VALID_AGENTS = {"scout", "outreach", "enablement", "channels"}


async def _log_dispatched_run(agent_name: str, result: dict, focus: str = "") -> None:
    """Log a CRO-dispatched sub-agent run to DB so it appears in the dashboard."""
    from src.db.models import Run
    from src.db.session import get_session_factory

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
                input_tokens=result.get("input_tokens") or result.get("total_input_tokens"),
                output_tokens=result.get("output_tokens") or result.get("total_output_tokens"),
                summary=(result.get("summary") or result.get("action") or "")[:2000],
                tools_used=result.get("tools_used") or [],
                completed_at=datetime.now(timezone.utc),
            )
            session.add(run)
            await session.commit()
            logger.info("Logged dispatched run for '%s' to DB", agent_name)
    except Exception:
        logger.exception("Failed to log dispatched run for '%s'", agent_name)


def register_dispatch_tools(registry: ToolRegistry, app: Any) -> None:
    """Register the dispatch_agent tool for use by CRO."""

    async def dispatch_agent(agent: str, focus: str) -> str:
        """Dispatch a sub-agent with specific focus for this run.

        Args:
            agent: Which agent to run — 'scout', 'outreach', 'enablement', or 'channels'.
            focus: Specific instructions for this run: what to prioritize, which accounts
                   to target, what to skip, context from the pipeline review.

        Returns:
            Summary of what the agent did.
        """
        if agent not in _VALID_AGENTS:
            return f"Unknown agent '{agent}'. Valid: {', '.join(sorted(_VALID_AGENTS))}"

        logger.info("CRO dispatching %s — focus: %.120s", agent, focus)

        try:
            result = await _run_agent(agent, focus, app)
            await _log_dispatched_run(agent, result, focus=focus)
            summary = str(result)[:600]
            logger.info("CRO dispatch complete — %s: %s", agent, summary[:100])
            return f"Agent '{agent}' completed.\n{summary}"
        except Exception as e:
            logger.exception("Dispatch failed for agent '%s'", agent)
            return f"Agent '{agent}' failed: {e}"

    registry.register(
        name="dispatch_agent",
        description=(
            "Dispatch a sub-agent with specific focus instructions for this run. "
            "Call this after your pipeline review to direct each agent's work for the day. "
            "The agent runs immediately and returns a summary of what it did. "
            "Only dispatch if there is meaningful work — skip agents with empty queues."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "enum": ["scout", "outreach", "enablement", "channels"],
                    "description": "Which agent to dispatch.",
                },
                "focus": {
                    "type": "string",
                    "description": (
                        "Specific instructions for this run: what to prioritize, which accounts "
                        "to target, what to skip. Be direct — the agent will read this before "
                        "deciding what to work on."
                    ),
                },
            },
            "required": ["agent", "focus"],
        },
        handler=dispatch_agent,
    )


async def _run_agent(agent: str, focus: str, app: Any) -> dict:
    """Build and run a sub-agent with the given focus string."""
    from src.agents.channels import ChannelsAgent
    from src.agents.enablement import EnablementAgent
    from src.agents.outreach import OutreachAgent
    from src.agents.scout import ScoutAgent
    from src.claude.tools import ToolRegistry
    from src.tools.attio_tools import register_attio_tools
    from src.tools.email_tools import register_email_tools

    attio = app.state.attio
    claude_client = app.state.claude
    email_client = getattr(app.state, "email", None)
    slack = getattr(app.state, "slack", None)
    slack_channel = getattr(app.state, "slack_approval_channel", "")
    apollo = getattr(app.state, "apollo", None)
    prompts = app.state.prompts

    if agent == "scout":
        registry = ToolRegistry()
        register_attio_tools(registry, attio)
        fullenrich = getattr(app.state, "fullenrich", None)
        if apollo:
            try:
                from src.tools.apollo_tools import register_apollo_tools
                register_apollo_tools(registry, apollo, attio, settings.apollo_credits_per_heartbeat,
                                      fullenrich=fullenrich)
            except ImportError:
                pass
        return await ScoutAgent(
            attio=attio,
            claude_client=claude_client,
            system_prompt=prompts.get("scout", ""),
            tool_registry=registry,
            model=settings.scout_model,
            batch_size=settings.scout_batch_size,
        ).run(focus=focus)

    if agent == "outreach":
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
        return await OutreachAgent(
            attio=attio,
            claude_client=claude_client,
            system_prompt=prompts.get("outreach", ""),
            tool_registry=registry,
            model=settings.outreach_model,
            batch_size=settings.outreach_batch_size,
        ).run(focus=focus)

    if agent == "enablement":
        registry = ToolRegistry()
        register_attio_tools(registry, attio)
        try:
            from src.tools.content_tools import register_content_tools
            register_content_tools(registry)
        except ImportError:
            pass
        return await EnablementAgent(
            attio=attio,
            claude_client=claude_client,
            system_prompt=prompts.get("enablement", ""),
            tool_registry=registry,
            model=settings.enablement_model,
            batch_size=settings.enablement_batch_size,
        ).run(focus=focus)

    if agent == "channels":
        registry = ToolRegistry()
        register_attio_tools(registry, attio)
        return await ChannelsAgent(
            attio=attio,
            claude_client=claude_client,
            system_prompt=prompts.get("channels", ""),
            tool_registry=registry,
            model=settings.channels_model,
            batch_size=settings.channels_batch_size,
        ).run(focus=focus)

    raise ValueError(f"Unknown agent: {agent}")
