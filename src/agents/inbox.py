"""Inbox Monitor Agent — detects email replies and calendar bookings.

Not a Claude agentic loop — this is a structured pipeline:
1. Poll Gmail IMAP for new unseen emails
2. Match sender to Attio contacts
3. Classify reply sentiment via Claude Haiku (single call, no tools)
4. Update Attio status + create notes
5. Notify via Slack

Also detects Google Calendar booking confirmations and auto-updates Attio.
"""

import json
import logging
import re
from datetime import date, timedelta
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

# Senders that indicate calendar booking notifications
CALENDAR_SENDERS = {
    "calendar-notification@google.com",
    "calendar@google.com",
    "noreply@google.com",
}

# Subject patterns indicating a calendar booking
CALENDAR_SUBJECT_PATTERNS = [
    r"invitation:",
    r"accepted.*invitation",
    r"new event:",
    r"confirmed:.*meeting",
    r"you have a new event",
]


class InboxMonitorAgent:
    """Monitors Gmail inbox for replies to outreach emails and calendar bookings.

    Unlike other agents, this does NOT use the Claude agentic loop.
    It's a structured pipeline that processes emails sequentially.
    """

    name = "inbox"

    def __init__(
        self,
        attio,
        claude_client: anthropic.AsyncAnthropic | None = None,
        inbox_client=None,
        email_client=None,
        slack_client=None,
        slack_channel: str = "",
        sentiment_model: str = "claude-haiku-4-5-20251001",
    ):
        self.attio = attio
        self.claude_client = claude_client
        self.inbox_client = inbox_client
        self.email_client = email_client
        self.slack = slack_client
        self.slack_channel = slack_channel
        self.sentiment_model = sentiment_model

    async def run(self, last_uid: int = 0) -> dict[str, Any]:
        """Run one inbox monitoring cycle."""
        if not self.inbox_client:
            logger.warning("No inbox client — skipping inbox monitor")
            return {"action": "noop", "reason": "no inbox client"}

        logger.info("Inbox monitor: checking for new emails (since UID %d)", last_uid)

        try:
            new_emails = await self.inbox_client.fetch_new_emails(since_uid=last_uid)
        except Exception as e:
            logger.error("Failed to fetch emails: %s", e)
            return {"action": "error", "error": str(e), "new_last_uid": last_uid}

        if not new_emails:
            logger.info("Inbox monitor: no new emails")
            return {"action": "noop", "emails_processed": 0, "new_last_uid": last_uid}

        logger.info("Inbox monitor: found %d new emails", len(new_emails))

        results = []
        new_last_uid = last_uid

        for email_msg in new_emails:
            try:
                result = await self._process_email(email_msg)
                results.append(result)
                if email_msg.uid > new_last_uid:
                    new_last_uid = email_msg.uid
            except Exception as e:
                logger.error(
                    "Failed to process email UID %d from %s: %s",
                    email_msg.uid, email_msg.from_email, e,
                )
                results.append({
                    "uid": email_msg.uid,
                    "from": email_msg.from_email,
                    "type": "error",
                    "error": str(e),
                })

        logger.info(
            "Inbox monitor complete: %d emails processed, new last_uid=%d",
            len(results), new_last_uid,
        )

        return {
            "action": "inbox_check",
            "emails_processed": len(results),
            "results": results,
            "new_last_uid": new_last_uid,
        }

    async def _process_email(self, email_msg) -> dict:
        if self._is_calendar_booking(email_msg):
            return await self._handle_calendar_booking(email_msg)
        return await self._handle_reply(email_msg)

    def _is_calendar_booking(self, email_msg) -> bool:
        if email_msg.from_email.lower() in CALENDAR_SENDERS:
            return True
        subject_lower = email_msg.subject.lower()
        for pattern in CALENDAR_SUBJECT_PATTERNS:
            if re.search(pattern, subject_lower):
                return True
        return False

    async def _handle_calendar_booking(self, email_msg) -> dict:
        logger.info("Calendar booking detected: %s", email_msg.subject)

        attendee_email = self._extract_attendee_email(email_msg.body)
        if not attendee_email:
            return {
                "uid": email_msg.uid,
                "type": "calendar_booking",
                "status": "skipped",
                "reason": "no attendee email found",
            }

        company_data = await self._find_company_by_contact_email(attendee_email)
        if not company_data:
            return {
                "uid": email_msg.uid,
                "type": "calendar_booking",
                "status": "unmatched",
                "attendee": attendee_email,
            }

        company_id = company_data["id"]
        company_name = company_data["name"]

        try:
            await self.attio.update_record(
                "companies", company_id,
                {"values": {"outreach_status": [{"option": "Meeting Booked"}]}},
            )
        except Exception as e:
            logger.error("Failed to update %s to Meeting Booked: %s", company_name, e)

        try:
            await self.attio.create_task(
                content=f"Call prep needed: {company_name} (meeting booked via calendar link)",
                linked_records=[
                    {"target_object": "companies", "target_record_id": company_id}
                ],
            )
        except Exception as e:
            logger.error("Failed to create enablement task for %s: %s", company_name, e)

        try:
            await self.attio.create_note(
                parent_object="companies",
                parent_record_id=company_id,
                title="Meeting Booked (Auto-Detected)",
                content=(
                    f"Meeting booked via calendar link.\n"
                    f"Attendee: {attendee_email}\n"
                    f"Subject: {email_msg.subject}\n"
                    f"Detected from calendar notification email."
                ),
            )
        except Exception as e:
            logger.error("Failed to create note for %s: %s", company_name, e)

        if self.slack and self.slack_channel:
            try:
                await self.slack.post_message(
                    channel=self.slack_channel,
                    text=(
                        f":calendar: *Meeting Booked* — {company_name}\n"
                        f"Attendee: {attendee_email}\n"
                        f"_Call prep task created for Enablement._"
                    ),
                )
            except Exception as e:
                logger.error("Failed to notify Slack about booking: %s", e)

        return {
            "uid": email_msg.uid,
            "type": "calendar_booking",
            "status": "processed",
            "company": company_name,
            "attendee": attendee_email,
        }

    def _extract_attendee_email(self, body: str) -> str | None:
        """Extract the first non-system email address from the body."""
        email_pattern = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
        found_emails = re.findall(email_pattern, body)

        our_email = getattr(self.email_client, "from_email", "") if self.email_client else ""
        excluded = {
            our_email.lower(),
            "calendar-notification@google.com",
            "noreply@google.com",
            "calendar@google.com",
        }

        for addr in found_emails:
            addr_lower = addr.lower()
            if addr_lower not in excluded and "google.com" not in addr_lower:
                return addr_lower

        return None

    async def _handle_reply(self, email_msg) -> dict:
        company_data = await self._find_company_by_contact_email(email_msg.from_email)
        if not company_data:
            return {
                "uid": email_msg.uid,
                "type": "reply",
                "status": "unmatched",
                "from": email_msg.from_email,
            }

        company_id = company_data["id"]
        company_name = company_data["name"]

        sentiment = await self._classify_sentiment(email_msg)
        status_update = self._determine_status_update(sentiment)

        if status_update:
            try:
                await self.attio.update_record(
                    "companies", company_id,
                    {"values": {"outreach_status": [{"option": status_update}]}},
                )
            except Exception as e:
                logger.error("Failed to update status for %s: %s", company_name, e)

        try:
            note_content = (
                f"Reply received from {email_msg.from_name} <{email_msg.from_email}>\n"
                f"Subject: {email_msg.subject}\n"
                f"Sentiment: {sentiment}\n"
                f"---\n"
                f"{email_msg.body[:1500]}"
            )
            await self.attio.create_note(
                parent_object="companies",
                parent_record_id=company_id,
                title=f"Reply from {email_msg.from_name} — {sentiment}",
                content=note_content,
            )
        except Exception as e:
            logger.error("Failed to create reply note for %s: %s", company_name, e)

        if sentiment in ("positive", "neutral") and self.slack and self.slack_channel:
            await self._draft_and_queue_reply(
                email_msg=email_msg,
                company_id=company_id,
                company_name=company_name,
            )
        elif sentiment == "redirect":
            await self._handle_redirect(email_msg, company_id, company_name)
        elif sentiment == "out_of_office":
            await self._pause_for_ooo(email_msg, company_id, company_name)
        elif self.slack and self.slack_channel:
            emoji = {
                "negative": ":no_entry:",
                "unsubscribe": ":no_bell:",
            }.get(sentiment, ":email:")

            try:
                await self.slack.post_message(
                    channel=self.slack_channel,
                    text=(
                        f"{emoji} *Reply Received* — {company_name}\n"
                        f"From: {email_msg.from_name} <{email_msg.from_email}>\n"
                        f"Subject: {email_msg.subject}\n"
                        f"Sentiment: *{sentiment}*\n"
                        f"Status → {status_update or 'unchanged'}"
                    ),
                )
            except Exception as e:
                logger.error("Failed to notify Slack about reply: %s", e)

        return {
            "uid": email_msg.uid,
            "type": "reply",
            "status": "processed",
            "company": company_name,
            "from": email_msg.from_email,
            "sentiment": sentiment,
            "status_update": status_update,
        }

    async def _handle_redirect(self, email_msg, company_id, company_name):
        referred = await self._extract_referred_contact(email_msg)
        referred_name = referred.get("name") or "unknown"
        referred_email = referred.get("email") or "unknown"

        try:
            task_content = (
                f"Redirect from {company_name} — add new contact to Attio and restart sequence:\n"
                f"Name: {referred_name}\n"
                f"Email: {referred_email}\n"
                f"Original contact: {email_msg.from_name} <{email_msg.from_email}>"
            )
            await self.attio.create_task(
                content=task_content,
                linked_records=[
                    {"target_object": "companies", "target_record_id": company_id}
                ],
            )
        except Exception as e:
            logger.error("Failed to create redirect task for %s: %s", company_name, e)

        if self.slack and self.slack_channel:
            try:
                await self.slack.post_message(
                    channel=self.slack_channel,
                    text=(
                        f":arrows_counterclockwise: *Redirect — {company_name}*\n"
                        f"_{email_msg.from_name}_ says to contact someone else:\n"
                        f"*{referred_name}* — `{referred_email}`\n"
                        f"_Add to Attio, link to {company_name}, restart sequence._"
                    ),
                )
            except Exception as e:
                logger.error("Failed to post redirect Slack notification: %s", e)

    async def _extract_referred_contact(self, email_msg) -> dict[str, str]:
        if not self.claude_client:
            return {"name": "unknown", "email": "unknown"}

        try:
            response = await self.claude_client.messages.create(
                model=self.sentiment_model,
                max_tokens=60,
                system=(
                    "Extract the name and email address of the person being referred to "
                    "in this redirect/wrong-person email reply. "
                    "Respond with ONLY a JSON object: {\"name\": \"...\", \"email\": \"...\"}. "
                    "If name or email is not found, use null for that field."
                ),
                messages=[{
                    "role": "user",
                    "content": f"Subject: {email_msg.subject}\n\n{email_msg.body[:800]}",
                }],
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
            data = json.loads(raw)
            return {
                "name": data.get("name") or "unknown",
                "email": data.get("email") or "unknown",
            }
        except Exception as e:
            logger.warning("Referred contact extraction failed: %s", e)
            return {"name": "unknown", "email": "unknown"}

    async def _pause_for_ooo(self, email_msg, company_id, company_name):
        resume_date = await self._extract_return_date(email_msg.body)
        if resume_date is None:
            resume_date = date.today() + timedelta(days=7)
            date_source = "estimated (+7 days)"
        else:
            date_source = "extracted from email"

        try:
            await self.attio.update_record(
                "companies", company_id,
                {"values": {"next_touch_date": [{"value": resume_date.isoformat()}]}},
            )
        except Exception as e:
            logger.error("Failed to set OOO resume date for %s: %s", company_name, e)

        try:
            await self.attio.create_note(
                parent_object="companies",
                parent_record_id=company_id,
                title=f"OOO — Sequence paused until {resume_date.isoformat()}",
                content=(
                    f"Out-of-office reply detected from {email_msg.from_name} "
                    f"<{email_msg.from_email}>.\n"
                    f"Subject: {email_msg.subject}\n"
                    f"Resume date: {resume_date.isoformat()} ({date_source})\n"
                    f"Sequence paused — next_touch_date set to resume date.\n\n"
                    f"---\n{email_msg.body[:500]}"
                ),
            )
        except Exception as e:
            logger.error("Failed to create OOO note for %s: %s", company_name, e)

        if self.slack and self.slack_channel:
            try:
                await self.slack.post_message(
                    channel=self.slack_channel,
                    text=(
                        f":palm_tree: *OOO Detected* — {company_name}\n"
                        f"From: {email_msg.from_name} <{email_msg.from_email}>\n"
                        f"Sequence paused. Resuming: *{resume_date.isoformat()}* ({date_source})"
                    ),
                )
            except Exception as e:
                logger.error("Failed to post OOO Slack notification for %s: %s", company_name, e)

    async def _extract_return_date(self, body: str) -> date | None:
        if not self.claude_client:
            return None

        try:
            response = await self.claude_client.messages.create(
                model=self.sentiment_model,
                max_tokens=20,
                system=(
                    "Extract the return date from this out-of-office email. "
                    f"Today is {date.today().isoformat()}. "
                    "Respond with ONLY the date in YYYY-MM-DD format (e.g. 2026-03-20). "
                    "If no specific return date is mentioned, respond with 'unknown'."
                ),
                messages=[{"role": "user", "content": body[:800]}],
            )
            raw = response.content[0].text.strip()
            if raw == "unknown" or not re.match(r"\d{4}-\d{2}-\d{2}", raw):
                return None
            return date.fromisoformat(raw[:10])
        except Exception as e:
            logger.warning("Return date extraction failed: %s", e)
            return None

    async def _draft_and_queue_reply(self, email_msg, company_id, company_name):
        if not self.claude_client:
            return

        draft_text = await self._draft_reply(email_msg, company_name)
        if not draft_text:
            return

        subject = email_msg.subject
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        draft_envelope = {
            "to_email": email_msg.from_email,
            "subject": subject,
            "html_body": draft_text.replace("\n", "<br>"),
            "text_body": draft_text,
            "in_reply_to": email_msg.message_id or "",
            "references": email_msg.message_id or "",
            "draft_type": "reply",
        }

        note_id = None
        try:
            result = await self.attio.create_note(
                parent_object="companies",
                parent_record_id=company_id,
                title=f"Reply Draft — {email_msg.from_name} (pending approval)",
                content=json.dumps(draft_envelope),
            )
            note_id = (
                result.get("data", {}).get("id", {}).get("note_id")
                or result.get("data", {}).get("id")
                or "unknown"
            )
        except Exception as e:
            logger.error("Failed to save reply draft for %s: %s", company_name, e)
            return

        if self.slack and self.slack_channel and note_id:
            try:
                await self.slack.post_message(
                    channel=self.slack_channel,
                    text=(
                        f":email: *Reply Draft Ready* — {company_name}\n"
                        f"From: {email_msg.from_name} <{email_msg.from_email}>\n"
                        f"Subject: {email_msg.subject}\n"
                        f"Draft saved to Attio (note_id: {note_id})\n"
                        f"_Review and approve via the approval webhook._"
                    ),
                )
            except Exception as e:
                logger.error("Failed to post reply approval notification for %s: %s", company_name, e)

    async def _draft_reply(self, email_msg, company_name: str) -> str | None:
        """Use Claude to draft a brief reply to an inbound email."""
        try:
            response = await self.claude_client.messages.create(
                model=self.sentiment_model,
                max_tokens=300,
                system=(
                    "You are a sales rep replying to an inbound email from a prospect. "
                    "Draft a short, warm reply (60-100 words). "
                    "Peer-to-peer tone, no marketing fluff. If they want to talk, suggest booking a time. "
                    "If they asked a specific question, answer it briefly and invite them to a call for detail. "
                    "Return only the email body — no subject line."
                ),
                messages=[{
                    "role": "user",
                    "content": (
                        f"Prospect company: {company_name}\n"
                        f"From: {email_msg.from_name} <{email_msg.from_email}>\n"
                        f"Subject: {email_msg.subject}\n\n"
                        f"Their message:\n{email_msg.body[:800]}"
                    ),
                }],
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error("Reply drafting failed for %s: %s", company_name, e)
            return None

    async def _classify_sentiment(self, email_msg) -> str:
        if not self.claude_client:
            return "neutral"

        try:
            response = await self.claude_client.messages.create(
                model=self.sentiment_model,
                max_tokens=20,
                system=(
                    "Classify the sentiment of this email reply to a sales outreach email. "
                    "Respond with exactly one word: positive, negative, neutral, "
                    "out_of_office, unsubscribe, or redirect. "
                    "positive = interested, wants to talk, asks questions. "
                    "negative = not interested, asks to stop, rude. "
                    "neutral = unclear intent, generic acknowledgment. "
                    "out_of_office = auto-reply, vacation, away. "
                    "unsubscribe = explicit opt-out or unsubscribe request. "
                    "redirect = wrong person, refers to someone else to contact instead."
                ),
                messages=[{
                    "role": "user",
                    "content": (
                        f"From: {email_msg.from_name} <{email_msg.from_email}>\n"
                        f"Subject: {email_msg.subject}\n\n"
                        f"{email_msg.body[:1000]}"
                    ),
                }],
            )
            sentiment = response.content[0].text.strip().lower()
            valid = {"positive", "negative", "neutral", "out_of_office", "unsubscribe", "redirect"}
            if sentiment not in valid:
                logger.warning("Unexpected sentiment: %s — defaulting to neutral", sentiment)
                return "neutral"
            return sentiment
        except Exception as e:
            logger.error("Sentiment classification failed: %s", e)
            return "neutral"

    def _determine_status_update(self, sentiment: str) -> str | None:
        mapping = {
            "positive": "Responded",
            "negative": "Nurture",
            "neutral": "Responded",
            "out_of_office": None,
            "unsubscribe": "Disqualified",
            "redirect": "Responded",
        }
        return mapping.get(sentiment)

    async def _find_company_by_contact_email(self, email_address: str) -> dict[str, Any] | None:
        try:
            result = await self.attio.query_records(
                object_slug="people",
                filter_={"email_addresses": email_address},
                limit=5,
            )
            records = result.get("data", [])
            if not records:
                return None

            person = records[0]
            values = person.get("values", {})

            company_refs = values.get("company", [])
            if company_refs and isinstance(company_refs, list) and len(company_refs) > 0:
                company_ref = company_refs[0]
                record_id = None
                if isinstance(company_ref, dict):
                    record_id = (
                        company_ref.get("record_id")
                        or company_ref.get("target_record_id")
                        or (company_ref.get("target", {}) or {}).get("record_id")
                    )

                if record_id:
                    from src.agents.scout import _parse_company
                    company_data = await self.attio.get_record("companies", record_id)
                    parsed = _parse_company(company_data.get("data", company_data))
                    return {"id": parsed["id"], "name": parsed["name"]}

            domain = email_address.split("@")[1] if "@" in email_address else None
            if domain:
                return await self._find_company_by_domain(domain)

            return None
        except Exception as e:
            logger.error("Failed to look up contact %s: %s", email_address, e)
            return None

    async def _find_company_by_domain(self, domain: str) -> dict[str, Any] | None:
        try:
            result = await self.attio.query_records(
                object_slug="companies",
                filter_={"domains": domain},
                limit=1,
            )
            records = result.get("data", [])
            if records:
                from src.agents.scout import _parse_company
                parsed = _parse_company(records[0])
                return {"id": parsed["id"], "name": parsed["name"]}
            return None
        except Exception as e:
            logger.error("Failed to look up company by domain %s: %s", domain, e)
            return None
