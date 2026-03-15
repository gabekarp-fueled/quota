"""OKR tools for the CRO agent.

Provides read and write access to the OKR database so CRO can:
- List active objectives and their key results (with IDs for updates)
- Update key result current_value based on pipeline observations

CRO should call list_key_results first to get kr_ids, then
update_key_result with measured values. Never estimate — only update
what can be directly counted from pipeline data.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.claude.tools import ToolRegistry
from src.db.models import KeyResult, Objective
from src.db.session import get_session_factory

logger = logging.getLogger(__name__)


def register_okr_tools(registry: ToolRegistry) -> None:
    """Register OKR read/write tools for use by CRO."""

    # ── list_key_results ─────────────────────────────────────────────────────

    async def list_key_results() -> list[dict]:
        """List all active KRs with IDs, current values, and targets."""
        factory = get_session_factory()
        if not factory:
            return [{"error": "No database configured — OKR tools unavailable"}]

        async with factory() as session:
            result = await session.execute(
                select(Objective)
                .where(Objective.active == True)  # noqa: E712
                .options(selectinload(Objective.key_results))
                .order_by(Objective.created_at)
            )
            objectives = result.scalars().all()

        output = []
        for obj in objectives:
            for kr in obj.key_results:
                pct = None
                if kr.target_value and kr.target_value > 0:
                    pct = round((kr.current_value or 0) / kr.target_value * 100, 1)
                output.append({
                    "kr_id": str(kr.id),
                    "objective": obj.title,
                    "kr_title": kr.title,
                    "metric": kr.metric,
                    "current_value": kr.current_value or 0,
                    "target_value": kr.target_value,
                    "progress_pct": pct,
                })

        return output

    registry.register(
        name="list_key_results",
        description=(
            "List all active OKR key results with their IDs, current values, and targets. "
            "Call this before update_key_result to get the kr_id for each KR you want to update."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=list_key_results,
    )

    # ── update_key_result ─────────────────────────────────────────────────────

    async def update_key_result(
        kr_id: str,
        current_value: float,
        note: str = "",
    ) -> dict:
        """Update the current_value of a specific key result."""
        factory = get_session_factory()
        if not factory:
            return {"error": "No database configured — cannot update KR"}

        async with factory() as session:
            result = await session.execute(
                select(KeyResult).where(KeyResult.id == kr_id)
            )
            kr = result.scalar_one_or_none()
            if not kr:
                return {"error": f"Key result '{kr_id}' not found"}

            old_value = kr.current_value or 0
            kr.current_value = current_value
            await session.commit()

        pct = None
        if kr.target_value and kr.target_value > 0:
            pct = round(current_value / kr.target_value * 100, 1)

        logger.info(
            "KR updated — '%s': %.1f → %.1f (%.1f%% of target)",
            kr.title,
            old_value,
            current_value,
            pct or 0,
        )

        return {
            "status": "updated",
            "kr_title": kr.title,
            "old_value": old_value,
            "new_value": current_value,
            "target_value": kr.target_value,
            "progress_pct": pct,
            "note": note,
        }

    registry.register(
        name="update_key_result",
        description=(
            "Update the current value of an OKR key result. "
            "Call list_key_results first to get kr_ids. "
            "Only update values you can directly measure from pipeline data — "
            "never project or estimate. "
            "Include a note explaining how you calculated the value."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "kr_id": {
                    "type": "string",
                    "description": "UUID of the key result to update (from list_key_results).",
                },
                "current_value": {
                    "type": "number",
                    "description": "The current measured value for this KR.",
                },
                "note": {
                    "type": "string",
                    "description": (
                        "How you calculated this value — be specific. "
                        "E.g. 'Counted 8 accounts with status Sequence Active or beyond'."
                    ),
                },
            },
            "required": ["kr_id", "current_value"],
        },
        handler=update_key_result,
    )
