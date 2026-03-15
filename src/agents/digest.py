"""Daily Activity Digest Agent — posts pipeline summary to Slack.

Pure data aggregation — no Claude needed. Queries Attio for all
companies, computes stats, formats a plain-text summary, and posts
to Slack.
"""

import logging
from datetime import date
from typing import Any

from src.agents.scout import _parse_company

logger = logging.getLogger(__name__)


class DigestAgent:
    """Posts a daily pipeline activity digest to Slack.

    Unlike other agents, this does NOT extend BaseAgent or use
    the Claude agentic loop. It is a simple data pipeline.
    """

    name = "digest"

    def __init__(
        self,
        attio,
        slack_client=None,
        slack_channel: str = "",
    ):
        self.attio = attio
        self.slack = slack_client
        self.slack_channel = slack_channel

    async def run(self) -> dict[str, Any]:
        """Compute pipeline stats and post digest to Slack."""
        if not self.slack or not self.slack_channel:
            logger.warning("Digest: no Slack client or channel — skipping")
            return {"action": "noop", "reason": "no slack configured"}

        logger.info("Digest agent: computing pipeline stats")

        # Fetch all companies
        try:
            result = await self.attio.query_records(
                object_slug="companies", filter_={}, limit=200
            )
            records = result.get("data", [])
            companies = [_parse_company(r) for r in records]
        except Exception as e:
            logger.error("Digest: failed to query companies: %s", e)
            return {"action": "error", "error": str(e)}

        # Compute stats
        stats = self._compute_stats(companies)

        # Build and post digest
        try:
            message = self._build_message(stats)
            await self.slack.post_message(
                channel=self.slack_channel,
                text=message,
            )
            logger.info("Digest posted to Slack: %d accounts", stats["total_accounts"])
        except Exception as e:
            logger.error("Digest: failed to post to Slack: %s", e)
            return {"action": "error", "error": str(e)}

        return {
            "action": "digest_posted",
            "total_accounts": stats["total_accounts"],
            "by_status": stats["by_status"],
            "by_tier": stats["by_tier"],
            "stale_count": stats["stale_count"],
        }

    def _compute_stats(self, companies: list[dict]) -> dict[str, Any]:
        """Compute aggregate pipeline statistics from parsed company records."""
        today_str = date.today().isoformat()

        status_counts: dict[str, int] = {}
        tier_counts: dict[str, int] = {}
        stale_count = 0
        today_touches = 0
        responded_awaiting = 0

        for c in companies:
            # Status breakdown
            status = c.get("outreach_status") or "Unknown"
            status_counts[status] = status_counts.get(status, 0) + 1

            # Tier breakdown
            tier = c.get("account_tier") or "Untiered"
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

            # Today's touches
            last_touch = c.get("last_touch_date")
            if last_touch and str(last_touch)[:10] == today_str:
                today_touches += 1

            # Stale detection
            if status == "Sequence Active":
                next_date = c.get("next_touch_date")
                if next_date and str(next_date)[:10] < today_str:
                    stale_count += 1

            # Responded accounts awaiting follow-up
            if status == "Responded":
                responded_awaiting += 1

        return {
            "total_accounts": len(companies),
            "by_status": status_counts,
            "by_tier": tier_counts,
            "stale_count": stale_count,
            "today_touches": today_touches,
            "responded_awaiting": responded_awaiting,
        }

    def _build_message(self, stats: dict) -> str:
        """Format pipeline stats as a Slack message."""
        lines = [
            f"*Daily Pipeline Digest*",
            f"Total tracked accounts: {stats['total_accounts']}",
            "",
            "*By Status:*",
        ]
        for status, count in sorted(stats["by_status"].items()):
            lines.append(f"  • {status}: {count}")

        lines.append("")
        lines.append("*By Tier:*")
        for tier, count in sorted(stats["by_tier"].items()):
            lines.append(f"  • {tier}: {count}")

        if stats["stale_count"]:
            lines.append(f"\n:warning: {stats['stale_count']} accounts overdue for next touch")
        if stats["responded_awaiting"]:
            lines.append(f":mailbox_with_mail: {stats['responded_awaiting']} accounts responded and awaiting follow-up")
        if stats["today_touches"]:
            lines.append(f":email: {stats['today_touches']} touches sent today")

        return "\n".join(lines)
