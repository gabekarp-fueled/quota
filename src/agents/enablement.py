import json
import logging

from src.agents.base import BaseAgent
from src.agents.scout import _parse_company
from src.claude.loop import run_agent_loop

logger = logging.getLogger(__name__)


class EnablementAgent(BaseAgent):
    """Generate personalized content, prep for calls, create briefs.

    Reads accounts with meetings booked, runs Claude to generate call prep
    briefs using discovery frameworks, saves to Attio.
    """

    name = "enablement"

    async def run(self, focus: str | None = None) -> dict:
        logger.info("Enablement agent heartbeat — querying accounts for call prep")

        result = await self.attio.query_records(
            object_slug="companies",
            filter_={"outreach_status": "Meeting Booked"},
            limit=self.batch_size,
        )

        records = result.get("data", [])
        companies = [_parse_company(r) for r in records]
        logger.info("Enablement found %d accounts needing call prep", len(companies))

        if not companies:
            return {
                "action": "call_prep",
                "accounts_processed": 0,
                "message": "No accounts with outreach_status='Meeting Booked' found.",
            }

        if not self.claude_client:
            logger.warning("No Claude client — running in stub mode")
            return {"action": "noop", "message": "Enablement agent stub — no Claude client."}

        results = []
        total_input_tokens = 0
        total_output_tokens = 0

        for company in companies:
            focus_prefix = f"## CRO Directive\n{focus}\n\n" if focus else ""
            task_message = (
                f"{focus_prefix}"
                f"Generate a call prep brief for this account. "
                f"Use get_discovery_questions to get the question framework. "
                f"Use get_objection_responses to prepare for likely objections. "
                f"Save the brief as a note titled 'Call Prep: {company['name']}' "
                f"and create a task 'Call prep ready: {company['name']}'.\n\n"
                f"Account data:\n{json.dumps(company, indent=2, default=str)}"
            )

            agent_result = await run_agent_loop(
                client=self.claude_client,
                model=self.model,
                system_prompt=self.system_prompt,
                tools=self.tool_registry,
                user_message=task_message,
                max_turns=15,
            )

            total_input_tokens += agent_result.input_tokens
            total_output_tokens += agent_result.output_tokens

            results.append({
                "account": company["name"],
                "summary": agent_result.text[:500],
                "tools_used": [tc.name for tc in agent_result.tool_calls],
                "turns": agent_result.turns,
            })

            logger.info(
                "Enablement prepped %s in %d turns (%d tools)",
                company["name"],
                agent_result.turns,
                len(agent_result.tool_calls),
            )

        logger.info(
            "Enablement heartbeat complete: %d briefs, %d input tokens, %d output tokens",
            len(results),
            total_input_tokens,
            total_output_tokens,
        )

        return {
            "action": "call_prep",
            "accounts_processed": len(results),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "results": results,
        }
