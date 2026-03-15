import json
import logging

from src.agents.base import BaseAgent
from src.claude.loop import run_agent_loop

logger = logging.getLogger(__name__)

VALID_TIERS = {"Tier 1", "Tier 2", "Tier 3"}


def _parse_company(record: dict) -> dict:
    """Extract common fields from an Attio company record."""
    values = record.get("values", {})

    def _get(slug, default=None):
        attr = values.get(slug, [])
        if not attr:
            return default
        first = attr[0]
        if "option" in first:
            return first["option"].get("title")
        if "status" in first:
            return first["status"].get("title")
        if "email_address" in first:
            return first["email_address"]
        if "domain" in first:
            return first["domain"]
        return first.get("value", default)

    return {
        "id": record.get("id", {}).get("record_id", ""),
        "name": _get("name", ""),
        "account_tier": _get("account_tier"),
        "outreach_status": _get("outreach_status"),
        "segment": _get("segment"),
        "current_touch": _get("current_touch"),
        "last_touch_date": _get("last_touch_date"),
        "next_touch_date": _get("next_touch_date"),
    }


class ScoutAgent(BaseAgent):
    """Research accounts, enrich data, identify contacts.

    Reads accounts from Attio, runs Claude to analyze each one,
    generates research briefs, and saves them back to Attio as notes.
    """

    name = "scout"

    async def run(self, focus: str | None = None) -> dict:
        logger.info("Scout agent heartbeat — querying tiered accounts")

        result = await self.attio.query_records(
            object_slug="companies",
            filter_={},
            limit=500,
        )

        records = result.get("data", [])
        all_companies = [_parse_company(r) for r in records]

        companies = [
            c for c in all_companies
            if c.get("account_tier") in VALID_TIERS
        ][:self.batch_size]

        logger.info(
            "Scout found %d tiered accounts (of %d total) to research",
            len(companies), len(all_companies),
        )

        if not self.claude_client:
            logger.warning("No Claude client — running in basic mode")
            return {
                "action": "list_accounts",
                "total_found": len(companies),
                "accounts": [
                    {
                        "name": c["name"],
                        "outreach_status": c["outreach_status"],
                    }
                    for c in companies
                ],
            }

        results = []
        total_input_tokens = 0
        total_output_tokens = 0

        for company in companies:
            focus_prefix = f"## CRO Directive\n{focus}\n\n" if focus else ""
            task_message = (
                f"{focus_prefix}"
                f"Analyze this account and generate a research brief. "
                f"Save the brief as a note on the account using attio_create_note.\n\n"
                f"Account data:\n{json.dumps(company, indent=2, default=str)}"
            )

            agent_result = await run_agent_loop(
                client=self.claude_client,
                model=self.model,
                system_prompt=self.system_prompt,
                tools=self.tool_registry,
                user_message=task_message,
                max_turns=10,
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
                "Scout processed %s in %d turns (%d tools)",
                company["name"],
                agent_result.turns,
                len(agent_result.tool_calls),
            )

        logger.info(
            "Scout heartbeat complete: %d accounts, %d input tokens, %d output tokens",
            len(results),
            total_input_tokens,
            total_output_tokens,
        )

        return {
            "action": "scout_research",
            "accounts_processed": len(results),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "results": results,
        }

    async def run_for_company(self, company: dict) -> str:
        """Run Scout research on a single company dict and return a summary string."""
        task_message = (
            f"Analyze this account and generate a research brief. "
            f"Save the brief as a note on the account using attio_create_note.\n\n"
            f"Account data:\n{json.dumps(company, indent=2, default=str)}"
        )

        agent_result = await run_agent_loop(
            client=self.claude_client,
            model=self.model,
            system_prompt=self.system_prompt,
            tools=self.tool_registry,
            user_message=task_message,
            max_turns=10,
        )

        logger.info(
            "Scout (on-demand) processed %s in %d turns",
            company.get("name"),
            agent_result.turns,
        )
        return agent_result.text or "Research complete — brief saved to Attio."
