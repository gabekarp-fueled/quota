import json
import logging

from src.agents.base import BaseAgent
from src.agents.scout import _parse_company
from src.claude.loop import run_agent_loop

logger = logging.getLogger(__name__)


class ChannelsAgent(BaseAgent):
    """Track channel partner activity, coordinate co-selling.

    Queries accounts by channel partner, runs Claude to generate portfolio
    summaries, flags coordination needs.

    Configure your channel partners in your channels.md prompt file.
    The agent queries Attio for accounts associated with each partner.
    """

    name = "channels"

    # Override channel_partners in your prompt or subclass.
    # These are read from the Attio `channel_partner` field on company records.
    channel_partners: list[str] = []

    async def run(self, focus: str | None = None) -> dict:
        logger.info("Channels agent heartbeat — querying partner accounts")

        if not self.claude_client:
            logger.warning("No Claude client — running in stub mode")
            return {"action": "noop", "message": "Channels agent stub — no Claude client."}

        # Query accounts for each partner
        all_partner_data = {}
        partners = self.channel_partners or []

        if not partners:
            # No partners configured — run a generic channel report
            all_result = await self.attio.query_records(
                object_slug="companies",
                filter_={},
                limit=self.batch_size,
            )
            all_companies = [_parse_company(r) for r in all_result.get("data", [])]
            all_partner_data["all"] = all_companies
        else:
            for partner in partners:
                result = await self.attio.query_records(
                    object_slug="companies",
                    filter_={"channel_partner": partner},
                    limit=self.batch_size,
                )
                records = result.get("data", [])
                companies = [_parse_company(r) for r in records]
                all_partner_data[partner] = companies
                logger.info("Channels found %d accounts for partner %s", len(companies), partner)

        partner_summary = json.dumps(
            {
                partner: [
                    {
                        "name": c["name"],
                        "tier": c.get("account_tier"),
                        "outreach_status": c.get("outreach_status"),
                    }
                    for c in companies
                ]
                for partner, companies in all_partner_data.items()
            },
            indent=2,
            default=str,
        )

        focus_prefix = f"## CRO Directive\n{focus}\n\n" if focus else ""
        task_message = (
            f"{focus_prefix}"
            f"Analyze the channel partner portfolios below and generate a summary report. "
            f"For each partner, summarize: total accounts, breakdown by tier and outreach status, "
            f"any coordination issues, and any opportunities or stale accounts.\n\n"
            f"Partner portfolio data:\n{partner_summary}"
        )

        agent_result = await run_agent_loop(
            client=self.claude_client,
            model=self.model,
            system_prompt=self.system_prompt,
            tools=self.tool_registry,
            user_message=task_message,
            max_turns=8,
        )

        total_accounts = sum(len(c) for c in all_partner_data.values())

        logger.info(
            "Channels heartbeat complete: %d partner accounts, %d input tokens, %d output tokens",
            total_accounts,
            agent_result.input_tokens,
            agent_result.output_tokens,
        )

        return {
            "action": "channels_report",
            "total_partner_accounts": total_accounts,
            "partners_analyzed": list(all_partner_data.keys()),
            "total_input_tokens": agent_result.input_tokens,
            "total_output_tokens": agent_result.output_tokens,
            "summary": agent_result.text[:1000],
            "tools_used": [tc.name for tc in agent_result.tool_calls],
        }
