import logging

from src.agents.base import BaseAgent
from src.claude.loop import run_agent_loop

logger = logging.getLogger(__name__)


class CROAgent(BaseAgent):
    """Chief Revenue Officer — daily pipeline review and agent orchestration.

    Daily heartbeat:
    1. Pull pipeline summary → assess health, identify priorities
    2. Handle responded accounts → book meetings or triage
    3. Check meeting-booked accounts → dispatch Enablement for call prep
    4. Review tiers → promote/demote with rationale
    5. Dispatch sub-agents with specific focus for the day
    6. Write pipeline note to Attio
    """

    name = "cro"

    def __init__(self, *args, objectives_text: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.objectives_text = objectives_text

    async def run(self, focus: str | None = None) -> dict:
        logger.info("CRO agent heartbeat — daily pipeline review")

        if not self.claude_client:
            logger.warning("No Claude client — running in stub mode")
            return {"action": "noop", "message": "CRO agent stub — no Claude client."}

        okr_prefix = f"{self.objectives_text}\n\n---\n\n" if self.objectives_text else ""

        task_message = (
            f"{okr_prefix}"
            "Run your daily pipeline review and dispatch your agents. Follow this sequence:\n\n"
            "1. **Pipeline Health** — Call `get_pipeline_summary`. Note status counts, tier "
            "distribution, and any stale accounts.\n\n"
            "2. **Hot Leads First** — Call `get_responded_accounts`. For each account that "
            "has replied positively, send a meeting booking email and update status to "
            "'Meeting Booked'. For unclear replies, create an Attio task for human review. "
            "For negative/unsubscribe replies, update status to 'Disqualified' or 'Nurture'.\n\n"
            "3. **Meeting Prep** — Call `get_meeting_ready_accounts`. For each account without "
            "a call prep brief, dispatch Enablement with that account as the focus.\n\n"
            "4. **Tier Review** — Promote accounts with strong engagement, demote ones with no "
            "response after a full sequence. Use `reprioritize_account` with clear rationale.\n\n"
            "5. **Dispatch Agents** — Based on what you've seen in the pipeline, dispatch each "
            "sub-agent using `dispatch_agent(agent, focus)`. Be specific — tell each agent "
            "exactly what to prioritize and what to skip today. Only dispatch agents that have "
            "meaningful work.\n\n"
            "6. **Pipeline Note** — Save a brief pipeline note to Attio with: status counts, "
            "tier counts, top account to watch, what's working, what needs attention, and a "
            "summary of which agents you dispatched and why.\n\n"
            "Lead with numbers. Be direct. No filler."
        )

        agent_result = await run_agent_loop(
            client=self.claude_client,
            model=self.model,
            system_prompt=self.system_prompt,
            tools=self.tool_registry,
            user_message=task_message,
            max_turns=30,
        )

        logger.info(
            "CRO heartbeat complete: %d turns, %d input tokens, %d output tokens",
            agent_result.turns,
            agent_result.input_tokens,
            agent_result.output_tokens,
        )

        return {
            "action": "cro_review",
            "summary": agent_result.text[:1000],
            "tools_used": [tc.name for tc in agent_result.tool_calls],
            "turns": agent_result.turns,
            "input_tokens": agent_result.input_tokens,
            "output_tokens": agent_result.output_tokens,
        }
