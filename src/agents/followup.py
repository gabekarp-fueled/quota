"""Follow-Up Agent — drafts post-call follow-up emails for accounts with booked meetings.

Not a Claude agentic loop — structured pipeline:
1. Find all "Meeting Booked" accounts with no follow-up note yet
2. For each, get the contact and draft a follow-up email via Claude
3. Save draft as Attio note + post Slack approval card
4. On approval (via webhook), email is sent and status moves to "Responded"
"""

import json
import logging
from typing import Any

import anthropic

from src.agents.scout import _parse_company

logger = logging.getLogger(__name__)

# Attio note titles that signal a follow-up has already been handled
FOLLOWUP_NOTE_MARKERS = [
    "post-call follow-up",
    "follow-up sent",
    "follow-up draft",
    "follow-up skipped",
]


class FollowUpAgent:
    """Scans for meetings that have occurred and drafts post-call follow-up emails."""

    name = "followup"

    def __init__(
        self,
        attio,
        claude_client: anthropic.AsyncAnthropic | None = None,
        slack_client=None,
        slack_channel: str = "",
        model: str = "claude-haiku-4-5-20251001",
    ):
        self.attio = attio
        self.claude_client = claude_client
        self.slack = slack_client
        self.slack_channel = slack_channel
        self.model = model

    async def run(self) -> dict[str, Any]:
        """Scan for accounts needing follow-ups and draft them."""
        logger.info("FollowUp agent: scanning for Meeting Booked accounts")

        # Query all "Meeting Booked" accounts
        try:
            result = await self.attio.query_records(
                object_slug="companies",
                filter_={"outreach_status": "Meeting Booked"},
                limit=50,
            )
            accounts = [_parse_company(r) for r in result.get("data", [])]
        except Exception as e:
            logger.error("Failed to query Meeting Booked accounts: %s", e)
            return {"action": "error", "error": str(e)}

        if not accounts:
            logger.info("FollowUp agent: no Meeting Booked accounts found")
            return {"action": "noop", "reason": "no meeting booked accounts"}

        logger.info("FollowUp agent: found %d Meeting Booked accounts", len(accounts))

        drafted = 0
        skipped = 0

        for account in accounts:
            try:
                did_draft = await self._process_account(account)
                if did_draft:
                    drafted += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error("FollowUp agent: failed on %s — %s", account.get("name"), e)
                skipped += 1

        logger.info("FollowUp agent: drafted=%d, skipped=%d", drafted, skipped)
        return {
            "action": "followup_scan",
            "accounts_checked": len(accounts),
            "drafted": drafted,
            "skipped": skipped,
        }

    async def _process_account(self, account: dict) -> bool:
        """Check if a follow-up is needed and draft one if so. Returns True if drafted."""
        company_id = account.get("id")
        company_name = account.get("name", "Unknown")

        # Check if we've already handled a follow-up for this account
        already_done = await self._has_followup_note(company_id)
        if already_done:
            logger.debug("FollowUp: %s already has a follow-up note — skipping", company_name)
            return False

        # Get the primary contact
        contact = await self._get_contact(company_id, company_name)
        if not contact or not contact.get("email"):
            logger.info("FollowUp: %s has no contact with email — skipping", company_name)
            return False

        # Draft the follow-up email
        draft_text = await self._draft_followup(account, contact)
        if not draft_text:
            logger.warning("FollowUp: drafting failed for %s — skipping", company_name)
            return False

        subject = f"Following up on our conversation — {company_name}"

        draft_envelope = {
            "to_email": contact["email"],
            "subject": subject,
            "html_body": draft_text.replace("\n", "<br>"),
            "text_body": draft_text,
            "draft_type": "followup",
        }

        # Save draft as Attio note
        note_id = None
        try:
            result = await self.attio.create_note(
                parent_object="companies",
                parent_record_id=company_id,
                title=f"Follow-up Draft — {contact.get('name', 'Contact')} (pending approval)",
                content=json.dumps(draft_envelope),
            )
            note_id = (
                result.get("data", {}).get("id", {}).get("note_id")
                or result.get("data", {}).get("id")
                or "unknown"
            )
        except Exception as e:
            logger.error("FollowUp: failed to save draft for %s: %s", company_name, e)
            return False

        # Post Slack notification for approval
        if self.slack and self.slack_channel and note_id:
            try:
                message = (
                    f":handshake: *Post-call Follow-up Ready* — {company_name}\n"
                    f"To: {contact.get('name', 'Contact')} <{contact['email']}>\n"
                    f"Subject: {subject}\n\n"
                    f"Preview:\n{draft_text[:400]}{'...' if len(draft_text) > 400 else ''}\n\n"
                    f"Note ID: {note_id}"
                )
                await self.slack.post_message(
                    channel=self.slack_channel,
                    text=message,
                )
                logger.info("FollowUp: draft notification posted for %s", company_name)
            except Exception as e:
                logger.error("FollowUp: Slack notification failed for %s: %s", company_name, e)

        return True

    async def _has_followup_note(self, company_id: str) -> bool:
        """Check if the account already has a follow-up note."""
        try:
            result = await self.attio.list_notes(
                parent_object="companies",
                parent_record_id=company_id,
            )
            notes = result.get("data", [])
            for note in notes:
                title = (note.get("title") or "").lower()
                for marker in FOLLOWUP_NOTE_MARKERS:
                    if marker in title:
                        return True
            return False
        except Exception as e:
            logger.error("Failed to check notes for %s: %s", company_id, e)
            return False  # Err on the side of drafting

    async def _get_contact(self, company_id: str, company_name: str) -> dict | None:
        """Get the primary contact for the company."""
        try:
            result = await self.attio.query_records(
                object_slug="people",
                filter_={"company": company_name},
                limit=5,
            )
            records = result.get("data", [])
            for r in records:
                values = r.get("values", {})
                emails = values.get("email_addresses", [])
                if emails:
                    email = emails[0].get("email_address") if isinstance(emails[0], dict) else None
                    if email:
                        name_parts = values.get("name", [{}])
                        name = name_parts[0].get("value") if name_parts else None
                        return {"name": name or email.split("@")[0], "email": email}
            return None
        except Exception as e:
            logger.error("Failed to get contact for %s: %s", company_name, e)
            return None

    async def _draft_followup(self, account: dict, contact: dict) -> str | None:
        """Use Claude to draft a post-call follow-up email."""
        if not self.claude_client:
            return None

        company_name = account.get("name", "the company")
        segment = account.get("segment", "")
        contact_name = contact.get("name", "there")

        try:
            response = await self.claude_client.messages.create(
                model=self.model,
                max_tokens=350,
                system=(
                    "You are a sales representative writing a post-call follow-up email. "
                    "Write a brief follow-up email (80-100 words). "
                    "Tone: warm, peer-to-peer, forward-looking. "
                    "Structure: thank them for their time → 1-line recap of what they found interesting → "
                    "propose a clear next step. "
                    "Sign off professionally. Return only the email body — no subject line."
                ),
                messages=[{
                    "role": "user",
                    "content": (
                        f"Contact: {contact_name}\n"
                        f"Company: {company_name}\n"
                        f"Segment: {segment}\n"
                        f"Context: We just had a Discovery call. They were interested enough to book it. "
                        f"Draft a warm follow-up that moves toward a next step."
                    ),
                }],
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error("Follow-up drafting failed for %s: %s", company_name, e)
            return None
