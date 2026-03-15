"""Pipeline analytics tools for the CRO agent.

Provides aggregate views of the outreach pipeline, engagement metrics,
and account management actions (reprioritize, flag for re-research).
"""

import logging
from datetime import date
from typing import Any

from src.claude.tools import ToolRegistry

logger = logging.getLogger(__name__)


def register_analytics_tools(registry: ToolRegistry, attio) -> None:
    """Register CRO analytics tools with the given registry."""

    # ── get_pipeline_summary ────────────────────────────────────────────

    async def get_pipeline_summary() -> Any:
        """Pull all accounts and compute pipeline summary stats."""
        from src.agents.scout import _parse_company
        result = await attio.query_records(
            object_slug="companies", filter_={}, limit=200
        )
        records = result.get("data", [])
        all_parsed = [_parse_company(r) for r in records]
        companies = [c for c in all_parsed if c.get("account_tier") in ("Tier 1", "Tier 2", "Tier 3")]

        status_counts: dict[str, int] = {}
        tier_counts: dict[str, int] = {}
        segment_counts: dict[str, int] = {}
        sequence_stats = {"touch_1": 0, "touch_2": 0, "touch_3": 0, "not_started": 0}
        stale_accounts = []

        today_str = date.today().isoformat()

        for c in companies:
            status = c.get("outreach_status") or "Unknown"
            status_counts[status] = status_counts.get(status, 0) + 1

            tier = c.get("account_tier") or "Untiered"
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

            seg = c.get("segment") or "Unknown"
            segment_counts[seg] = segment_counts.get(seg, 0) + 1

            touch = c.get("current_touch")
            if touch == 1:
                sequence_stats["touch_1"] += 1
            elif touch == 2:
                sequence_stats["touch_2"] += 1
            elif touch == 3:
                sequence_stats["touch_3"] += 1
            else:
                sequence_stats["not_started"] += 1

            if status == "Sequence Active":
                next_date = c.get("next_touch_date")
                if next_date and str(next_date)[:10] < today_str:
                    stale_accounts.append({
                        "name": c["name"],
                        "id": c["id"],
                        "tier": tier,
                        "current_touch": touch,
                        "next_touch_date": str(next_date)[:10],
                        "days_overdue": (date.today() - date.fromisoformat(str(next_date)[:10])).days,
                    })

        return {
            "total_accounts": len(companies),
            "by_status": status_counts,
            "by_tier": tier_counts,
            "by_segment": segment_counts,
            "sequence_distribution": sequence_stats,
            "stale_accounts": stale_accounts,
            "stale_count": len(stale_accounts),
        }

    registry.register(
        name="get_pipeline_summary",
        description=(
            "Get aggregate pipeline stats: account counts by status, tier, segment, "
            "sequence touch distribution, and stale accounts (overdue for next touch). "
            "Use this to understand overall pipeline health and identify problems."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=get_pipeline_summary,
    )

    # ── get_responded_accounts ──────────────────────────────────────────

    async def get_responded_accounts() -> Any:
        """Find accounts that have responded and may need follow-up."""
        from src.agents.scout import _parse_company
        result = await attio.query_records(
            object_slug="companies",
            filter_={"outreach_status": "Responded"},
            limit=50,
        )
        records = result.get("data", [])
        companies = [_parse_company(r) for r in records]
        return {
            "total": len(companies),
            "accounts": [
                {
                    "name": c["name"],
                    "id": c["id"],
                    "tier": c.get("account_tier"),
                    "segment": c.get("segment"),
                    "current_touch": c.get("current_touch"),
                }
                for c in companies
            ],
        }

    registry.register(
        name="get_responded_accounts",
        description=(
            "Get all accounts with outreach_status = 'Responded'. "
            "These accounts have replied and may need a meeting booking response "
            "or status update. Use this to identify hot accounts for follow-up."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=get_responded_accounts,
    )

    # ── get_meeting_ready_accounts ──────────────────────────────────────

    async def get_meeting_ready_accounts() -> Any:
        """Find accounts with Meeting Booked status that may need enablement."""
        from src.agents.scout import _parse_company
        result = await attio.query_records(
            object_slug="companies",
            filter_={"outreach_status": "Meeting Booked"},
            limit=50,
        )
        records = result.get("data", [])
        companies = [_parse_company(r) for r in records]
        return {
            "total": len(companies),
            "accounts": [
                {
                    "name": c["name"],
                    "id": c["id"],
                    "tier": c.get("account_tier"),
                    "segment": c.get("segment"),
                }
                for c in companies
            ],
        }

    registry.register(
        name="get_meeting_ready_accounts",
        description=(
            "Get all accounts with outreach_status = 'Meeting Booked'. "
            "These need Enablement call prep if not already done. "
            "Use this to ensure every meeting has a prepared brief."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=get_meeting_ready_accounts,
    )

    # ── reprioritize_account ────────────────────────────────────────────

    async def reprioritize_account(
        record_id: str,
        new_tier: str,
        rationale: str,
    ) -> Any:
        """Change an account's tier and log the rationale."""
        try:
            await attio.update_record(
                "companies",
                record_id,
                {"values": {
                    "account_tier": [{"option": new_tier}],
                    "tier_rationale": [{"value": rationale}],
                }},
            )
            await attio.create_note(
                parent_object="companies",
                parent_record_id=record_id,
                title=f"Tier Change → {new_tier}",
                content=f"CRO reprioritized to {new_tier}.\nRationale: {rationale}",
            )
            logger.info("Reprioritized %s → %s: %s", record_id, new_tier, rationale)
            return {"status": "updated", "new_tier": new_tier, "rationale": rationale}
        except Exception as e:
            logger.error("Failed to reprioritize %s: %s", record_id, e)
            return {"error": f"Failed to reprioritize: {e}"}

    registry.register(
        name="reprioritize_account",
        description=(
            "Change an account's tier (Tier 1/2/3) and log the rationale. "
            "Use when an account should move up (strong engagement, strategic fit) "
            "or down (no response, low potential). Creates a note documenting the change."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "Attio company record ID.",
                },
                "new_tier": {
                    "type": "string",
                    "description": "New tier: 'Tier 1', 'Tier 2', or 'Tier 3'.",
                    "enum": ["Tier 1", "Tier 2", "Tier 3"],
                },
                "rationale": {
                    "type": "string",
                    "description": "Why this account is being reprioritized (logged as note).",
                },
            },
            "required": ["record_id", "new_tier", "rationale"],
        },
        handler=reprioritize_account,
    )

    # ── trigger_re_research ─────────────────────────────────────────────

    async def trigger_re_research(
        record_id: str,
        reason: str,
    ) -> Any:
        """Flag an account for Scout to re-research."""
        try:
            await attio.create_task(
                content=f"Re-research needed: {reason}",
                linked_records=[
                    {"target_object": "companies", "target_record_id": record_id}
                ],
            )
            await attio.create_note(
                parent_object="companies",
                parent_record_id=record_id,
                title="Re-Research Requested (CRO)",
                content=f"CRO requested re-research.\nReason: {reason}",
            )
            logger.info("Triggered re-research for %s: %s", record_id, reason)
            return {"status": "flagged", "reason": reason}
        except Exception as e:
            logger.error("Failed to trigger re-research for %s: %s", record_id, e)
            return {"error": f"Failed to flag: {e}"}

    registry.register(
        name="trigger_re_research",
        description=(
            "Flag an account for Scout to re-research. Creates a task and note "
            "documenting why the account needs fresh intel. Use when account data "
            "is stale, triggers have changed, or personalization was too thin."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "Attio company record ID.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this account needs re-research.",
                },
            },
            "required": ["record_id", "reason"],
        },
        handler=trigger_re_research,
    )
