import json
import logging
from datetime import date

from src.agents.base import BaseAgent
from src.agents.scout import _parse_company
from src.claude.loop import run_agent_loop

logger = logging.getLogger(__name__)

_VALID_TIERS = {"Tier 1", "Tier 2", "Tier 3"}


class OutreachAgent(BaseAgent):
    """Draft and manage multi-touch sequences, track responses.

    Heartbeat logic:
    1. Query "Not Started" accounts → draft & send/save Touch 1
    2. Query "Sequence Active" accounts due for next touch → draft Touch 2 or 3
    """

    name = "outreach"

    async def run(self, focus: str | None = None) -> dict:
        logger.info("Outreach agent heartbeat — querying accounts")

        new_result = await self.attio.query_records(
            object_slug="companies",
            filter_={"outreach_status": "Not Started"},
            limit=self.batch_size,
        )
        all_new = [_parse_company(r) for r in new_result.get("data", [])]
        new_accounts = [a for a in all_new if a.get("account_tier") in _VALID_TIERS]
        logger.info(
            "Outreach found %d new accounts (%d untiered skipped)",
            len(new_accounts), len(all_new) - len(new_accounts),
        )

        active_result = await self.attio.query_records(
            object_slug="companies",
            filter_={"outreach_status": "Sequence Active"},
            limit=self.batch_size * 2,
        )
        all_active = [_parse_company(r) for r in active_result.get("data", [])]
        active_accounts = [a for a in all_active if a.get("account_tier") in _VALID_TIERS]

        today_str = date.today().isoformat()
        due_accounts = [
            a for a in active_accounts
            if a.get("next_touch_date") and str(a["next_touch_date"])[:10] <= today_str
        ]
        logger.info(
            "Outreach found %d active accounts, %d due for next touch",
            len(active_accounts), len(due_accounts),
        )

        all_work = []

        for company in new_accounts:
            all_work.append((company, 1))

        for company in due_accounts:
            current = company.get("current_touch") or 0
            next_touch = current + 1
            if next_touch <= 3:
                all_work.append((company, next_touch))
            else:
                logger.info(
                    "Cleanup: %s at touch %d still Sequence Active — moving to Nurture",
                    company["name"], current,
                )
                try:
                    await self.attio.update_record(
                        "companies", company["id"],
                        {"outreach_status": "Nurture", "next_touch_date": None},
                    )
                except Exception as e:
                    logger.warning("Failed to move %s to Nurture: %s", company["name"], e)

        if not all_work:
            return {
                "action": "outreach_heartbeat",
                "accounts_processed": 0,
                "message": "No accounts need outreach right now.",
            }

        if not self.claude_client:
            logger.warning("No Claude client — running in stub mode")
            return {"action": "noop", "message": "Outreach agent stub — no Claude client."}

        results = []
        total_input_tokens = 0
        total_output_tokens = 0

        for company, touch_number in all_work:
            tier = company.get("account_tier", "Tier 3")

            if touch_number in (1, 3):
                contact = await self._find_contact_for_company(company)
                if not contact:
                    logger.info(
                        "Outreach skip — no contact found for %s (Touch %d). Creating Scout task.",
                        company["name"], touch_number,
                    )
                    try:
                        await self.attio.create_task(
                            content=(
                                f"Scout needed: no contact found for {company['name']} — "
                                f"find a real contact (name + email) and add to Attio before next outreach run."
                            ),
                            linked_records=[
                                {"target_object": "companies", "target_record_id": company["id"]}
                            ],
                        )
                    except Exception as e:
                        logger.warning("Failed to create Scout task for %s: %s", company["name"], e)
                    results.append({
                        "account": company["name"],
                        "tier": tier,
                        "touch": touch_number,
                        "summary": "Skipped — no contact in Attio. Scout task created.",
                        "tools_used": [],
                        "turns": 0,
                    })
                    continue

            task_message = self._build_task_message(company, touch_number, tier, focus)

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
                "tier": tier,
                "touch": touch_number,
                "summary": agent_result.text[:500],
                "tools_used": [tc.name for tc in agent_result.tool_calls],
                "turns": agent_result.turns,
            })

            logger.info(
                "Outreach: %s (%s) Touch %d in %d turns",
                company["name"], tier, touch_number, agent_result.turns,
            )

        logger.info(
            "Outreach heartbeat complete: %d accounts, %d input tokens, %d output tokens",
            len(results), total_input_tokens, total_output_tokens,
        )

        return {
            "action": "outreach_heartbeat",
            "accounts_processed": len(results),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "results": results,
        }

    def _build_task_message(self, company: dict, touch_number: int, tier: str, focus: str | None = None) -> str:
        name = company["name"]

        if touch_number == 1:
            touch_instruction = self._touch_1_instruction(name, tier)
        elif touch_number == 2:
            touch_instruction = self._touch_2_instruction(name, tier)
        elif touch_number == 3:
            touch_instruction = self._touch_3_instruction(name, tier)
        else:
            touch_instruction = f"Touch {touch_number} is beyond the sequence. Skip this account."

        focus_prefix = f"## CRO Directive\n{focus}\n\n" if focus else ""
        return (
            f"{focus_prefix}"
            f"Process Touch {touch_number} for this account.\n\n"
            f"{touch_instruction}\n\n"
            f"Account data:\n{json.dumps(company, indent=2, default=str)}"
        )

    def _touch_1_instruction(self, name: str, tier: str) -> str:
        if tier == "Tier 1":
            return (
                "This is a TIER 1 account — Touch 1 (cold email).\n"
                "1. Use `attio_get_contacts` to find a real contact with an email address.\n"
                "2. Draft a personalized Touch 1 email following the prompt guidelines.\n"
                "3. Use `email_save_draft` to store the draft — note the gmail_draft_id returned.\n"
                "4. If `slack_notify_approval` is available, call it with the draft_id so the "
                "rep gets an Approve/Reject card in Slack.\n"
                "5. Create an Attio task 'Approve outreach: {name} — Touch 1' as backup.\n"
                "6. Call `sequence_advance` with touch_completed=1 to set the sequence state.\n"
                "Do NOT use `email_send` — Tier 1 requires approval."
            ).format(name=name)
        else:
            return (
                "This is a {tier} account — Touch 1 (cold email).\n"
                "1. Use `attio_get_contacts` to find a real contact with an email address.\n"
                "2. Draft a personalized Touch 1 email following the prompt guidelines.\n"
                "3. Use `email_send` to send it immediately.\n"
                "4. Call `sequence_advance` with touch_completed=1 to set the sequence state.\n"
                "If email tools are unavailable, save as Attio note and update status manually."
            ).format(tier=tier)

    def _touch_2_instruction(self, name: str, tier: str) -> str:
        return (
            "This is Touch 2 (LinkedIn message) for {name}.\n"
            "1. Use `attio_get_contacts` to find the contact's LinkedIn URL.\n"
            "2. Draft a short LinkedIn connection request / message:\n"
            "   - Reference Touch 1 (they got an email 8+ days ago)\n"
            "   - Keep it under 300 characters for a connection note\n"
            "   - Lighter tone than email — be conversational\n"
            "   - Reference something specific (their role, a company initiative, etc.)\n"
            "3. Save the LinkedIn draft as an Attio note titled 'Touch 2 LinkedIn: {name}'\n"
            "4. Create an Attio task: 'Send LinkedIn message: {name} — Touch 2'\n"
            "5. Call `sequence_advance` with touch_completed=2 to advance the sequence.\n"
            "LinkedIn messages are always manual — just draft and save, never auto-send."
        ).format(name=name)

    def _touch_3_instruction(self, name: str, tier: str) -> str:
        if tier == "Tier 1":
            return (
                "This is a TIER 1 account — Touch 3 (value-add email).\n"
                "1. Use `attio_get_contacts` to get the contact info.\n"
                "2. Draft a Touch 3 value-add email:\n"
                "   - Different angle from Touch 1 — share industry data or insight\n"
                "   - Reference that you reached out before (don't be pushy)\n"
                "   - Include a data point, case study reference, or market trend\n"
                "   - Final CTA with a scheduling link\n"
                "3. Use `email_save_draft` — note the gmail_draft_id.\n"
                "4. If `slack_notify_approval` is available, call it for approval.\n"
                "5. Create backup Attio task 'Approve outreach: {name} — Touch 3'.\n"
                "6. Call `sequence_advance` with touch_completed=3.\n"
                "Do NOT use `email_send` — Tier 1 requires approval."
            ).format(name=name)
        else:
            return (
                "This is a {tier} account — Touch 3 (value-add email).\n"
                "1. Use `attio_get_contacts` to get the contact info.\n"
                "2. Draft a Touch 3 value-add email:\n"
                "   - Different angle from Touch 1 — share industry data or insight\n"
                "   - Reference that you reached out before (don't be pushy)\n"
                "   - Include a data point, case study reference, or market trend\n"
                "   - Final CTA with a scheduling link\n"
                "3. Use `email_send` to send it immediately.\n"
                "4. Call `sequence_advance` with touch_completed=3.\n"
                "This is the final touch — after this the sequence completes."
            ).format(tier=tier)

    async def _find_contact_for_company(self, company: dict) -> dict | None:
        """Return the first contact with a real email for this company, or None."""
        company_id = company["id"]
        company_name = company["name"]
        try:
            result = await self.attio.query_records(
                object_slug="people",
                filter_={"org_id": company_id},
                limit=10,
            )
            for person in result.get("data", []):
                email = person.get("email")
                if email and "@" in email:
                    name = person.get("name") or email.split("@")[0]
                    return {"email": email, "name": name}
        except Exception as e:
            logger.warning("Pre-flight contact check failed for %s: %s", company_name, e)
        return None
