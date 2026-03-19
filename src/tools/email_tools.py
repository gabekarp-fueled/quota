"""Email delivery and sequence management tools for Claude agents.

Registers email_save_draft, email_send, and sequence_advance tools that
Outreach uses to manage multi-touch outreach sequences.
"""

import logging
import re as _re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.claude.tools import ToolRegistry

logger = logging.getLogger(__name__)

# ── Email signature ──────────────────────────────────────────────────────────
# Configure your signature below. These are appended to every outbound email.
# Leave as empty strings to send without a signature.

_SIGNATURE_HTML = (
    "<br><br>"
    '<hr style="border:none;border-top:1px solid #e0e0e0;margin:16px 0;">'
    '<p style="margin:0;font-family:Arial,sans-serif;font-size:13px;color:#222;line-height:1.6;">'
    # Replace the placeholders below with your name, phone, website, and scheduling link.
    "<!-- YOUR NAME --><br>"
    "<!-- YOUR PHONE --><br>"
    '<!-- <a href="https://yourwebsite.com">yourwebsite.com</a> --><br><br>'
    '<!-- <a href="https://cal.com/your-link">Schedule a Meeting</a> -->'
    "</p>"
)

_SIGNATURE_TEXT = (
    "\n\n--\n"
    "<!-- YOUR NAME -->\n"
    "<!-- YOUR PHONE -->\n"
    "<!-- YOUR WEBSITE -->\n"
    "<!-- SCHEDULING LINK -->"
)

# Track sends across a single heartbeat (reset per registration)
_sends_today = 0
_sends_limit = 90

# Role-based local-part prefixes that indicate a fabricated/guessed email.
# Blocks obviously constructed addresses before sending.
_ROLE_PATTERNS = _re.compile(
    r"^(vp|svp|evp|ceo|cfo|coo|cto|cso|cpo|dir|director|head|chief|"
    r"info|contact|hello|sales|admin|support)[\._@]",
    _re.IGNORECASE,
)


def _is_fabricated_email(email: str) -> bool:
    """Return True if the email looks like a constructed role-based address."""
    local = email.split("@")[0] if "@" in email else email
    return bool(_ROLE_PATTERNS.match(local))


def register_email_tools(
    registry: ToolRegistry,
    email_client,
    attio,
    daily_send_limit: int = 100,
) -> None:
    """Register email delivery tools with the given registry."""
    global _sends_today, _sends_limit
    _sends_today = 0
    _sends_limit = daily_send_limit

    # ── email_save_draft ───────────────────────────────────────────────

    async def email_save_draft(
        record_id: str,
        contact_email: str,
        contact_name: str,
        subject: str,
        html_body: str,
        text_body: str = "",
        touch_number: int = 1,
        contact_record_id: str = "",
    ) -> Any:
        """Create a Gmail draft for later review and sending."""
        # Append signature
        html_body = html_body + _SIGNATURE_HTML
        text_body = (text_body + _SIGNATURE_TEXT) if text_body else _SIGNATURE_TEXT

        # Guard: reject obviously constructed role-based emails
        if _is_fabricated_email(contact_email):
            logger.error(
                "email_save_draft BLOCKED — fabricated email: %s (account %s)",
                contact_email, record_id,
            )
            return {
                "error": (
                    f"Blocked: '{contact_email}' looks like a constructed role-based address. "
                    "Use attio_get_contacts to find a verified email. Do not fabricate addresses."
                ),
            }

        title = f"Touch {touch_number} — {contact_name} <{contact_email}>"

        try:
            draft_result = await email_client.create_draft(
                to=contact_email,
                subject=subject,
                html_body=html_body,
                text_body=text_body or None,
                reply_to=email_client.from_email,
            )
            draft_id = draft_result.get("draft_id", "unknown")
            logger.info("Gmail draft created: %s → draft_id=%s", title, draft_id)
            return {
                "status": "draft_saved",
                "gmail_draft_id": draft_id,
                "title": title,
                "to": contact_email,
                "contact_record_id": contact_record_id,
            }
        except Exception as e:
            logger.error("Failed to create Gmail draft for %s: %s", contact_email, e)
            return {"error": f"Failed to create draft: {e}"}

    registry.register(
        name="email_save_draft",
        description=(
            "Create a Gmail draft for a Tier 1 outreach email. "
            "The draft appears in your Gmail Drafts folder — open it, edit if needed, "
            "and send it manually. Returns gmail_draft_id for the Slack notification. "
            "Always provide contact_record_id (the People record ID) so sequence state "
            "can be tracked when the email is marked as sent via Slack."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "Attio company record ID.",
                },
                "contact_email": {
                    "type": "string",
                    "description": "Recipient email address.",
                },
                "contact_name": {
                    "type": "string",
                    "description": "Recipient full name for personalization.",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line.",
                },
                "html_body": {
                    "type": "string",
                    "description": (
                        "Email body as HTML. Use <p> tags for paragraphs, "
                        "<a href='...'> for links. Keep it clean and simple."
                    ),
                },
                "text_body": {
                    "type": "string",
                    "description": "Plain text version of the email body (optional fallback).",
                },
                "touch_number": {
                    "type": "integer",
                    "description": "Which touch in the sequence (1, 2, or 3). Default 1.",
                    "default": 1,
                },
                "contact_record_id": {
                    "type": "string",
                    "description": (
                        "Attio People record ID for the specific contact. "
                        "Used by the Slack 'Mark as Sent' handler to advance per-contact sequence state."
                    ),
                },
            },
            "required": ["record_id", "contact_email", "contact_name", "subject", "html_body"],
        },
        handler=email_save_draft,
    )

    # ── email_send ─────────────────────────────────────────────────────

    async def email_send(
        record_id: str,
        contact_email: str,
        contact_name: str,
        subject: str,
        html_body: str,
        text_body: str = "",
        touch_number: int = 1,
        contact_record_id: str = "",
    ) -> Any:
        """Send an email via Gmail and log to Attio."""
        global _sends_today

        # Append signature before sending
        html_body = html_body + _SIGNATURE_HTML
        text_body = (text_body + _SIGNATURE_TEXT) if text_body else _SIGNATURE_TEXT

        # Guard: reject obviously constructed role-based emails
        if _is_fabricated_email(contact_email):
            logger.error(
                "email_send BLOCKED — fabricated email detected: %s (account %s)",
                contact_email, record_id,
            )
            return {
                "error": (
                    f"Blocked: '{contact_email}' looks like a constructed role-based address, not a real contact. "
                    "Use attio_get_contacts to find a verified email. Do not fabricate addresses."
                ),
            }

        # Check daily send budget
        if _sends_today >= _sends_limit:
            return {
                "error": (
                    f"Daily email send limit reached ({_sends_today}/{_sends_limit}). "
                    "Remaining accounts will not get emails this run."
                ),
                "sends_today": _sends_today,
            }

        # Step 1: Send via Gmail
        try:
            send_result = await email_client.send_email(
                to=contact_email,
                subject=subject,
                html_body=html_body,
                text_body=text_body or None,
                reply_to=email_client.from_email,
            )
            _sends_today += 1
            message_id = send_result.get("id", "unknown")
        except Exception as e:
            logger.error("Email send failed for %s: %s", contact_email, e)
            return {"error": f"Email send failed: {e}", "sends_today": _sends_today}

        # Step 2: Log sent email as Attio note
        sent_note = (
            f"Touch {touch_number} email sent to {contact_name} <{contact_email}>\n"
            f"Subject: {subject}\n"
            f"Message ID: {message_id}\n"
            f"Sent at: {datetime.now(timezone.utc).isoformat()}\n"
            f"---\n{text_body or html_body}"
        )
        try:
            await attio.create_note(
                parent_object="companies",
                parent_record_id=record_id,
                title=f"Touch {touch_number} Email Sent: {contact_name}",
                content=sent_note,
            )
        except Exception as e:
            logger.error("Attio note failed after send for %s: %s", record_id, e)
            # Non-fatal — email was already sent

        # Step 3: Update outreach status to "Sequence Active"
        try:
            await attio.update_record(
                "companies",
                record_id,
                {"values": {"outreach_status": [{"option": "Sequence Active"}]}},
            )
        except Exception as e:
            logger.error("Attio status update failed for %s: %s", record_id, e)
            # Non-fatal — email was already sent, status can be fixed manually

        logger.info(
            "Email sent: Touch %d to %s <%s> (message_id=%s, sends_today=%d/%d)",
            touch_number, contact_name, contact_email, message_id,
            _sends_today, _sends_limit,
        )

        return {
            "status": "sent",
            "message_id": message_id,
            "to": contact_email,
            "subject": subject,
            "touch_number": touch_number,
            "sends_today": _sends_today,
            "contact_record_id": contact_record_id,
        }

    registry.register(
        name="email_send",
        description=(
            "Send an email via Gmail and update the account's outreach status in Attio. "
            "Use this for Tier 2 and Tier 3 accounts that are auto-approved for sending. "
            "NEVER use this for Tier 1 accounts — use email_save_draft instead. "
            "The tool sends the email, logs it as an Attio note, and sets outreach_status "
            "to 'Sequence Active'. Each send counts against the daily limit. "
            "After calling this, ALWAYS call sequence_advance to update the touch state."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "Attio company record ID.",
                },
                "contact_email": {
                    "type": "string",
                    "description": "Recipient email address.",
                },
                "contact_name": {
                    "type": "string",
                    "description": "Recipient full name.",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line.",
                },
                "html_body": {
                    "type": "string",
                    "description": (
                        "Email body as HTML. Use <p> tags for paragraphs, "
                        "<a href='...'> for links."
                    ),
                },
                "text_body": {
                    "type": "string",
                    "description": "Plain text version of the email (optional fallback).",
                },
                "touch_number": {
                    "type": "integer",
                    "description": "Which touch in the sequence (1, 2, or 3). Default 1.",
                    "default": 1,
                },
                "contact_record_id": {
                    "type": "string",
                    "description": (
                        "Attio People record ID for the specific contact. "
                        "Pass this so sequence_advance can update per-contact sequence state."
                    ),
                },
            },
            "required": ["record_id", "contact_email", "contact_name", "subject", "html_body"],
        },
        handler=email_send,
    )

    # ── sequence_advance ────────────────────────────────────────────────

    # Sequence cadence: Touch 1 → +8 days → Touch 2 → +14 days → Touch 3 → done
    # Adjust TOUCH_GAPS to match your preferred cadence.
    TOUCH_GAPS = {1: 8, 2: 14}

    async def sequence_advance(
        contact_record_id: str,
        touch_completed: int,
        company_record_id: str = "",
    ) -> Any:
        """Advance the outreach sequence for a specific contact after a touch is sent or saved."""
        today = date.today()

        if touch_completed in TOUCH_GAPS:
            next_date = today + timedelta(days=TOUCH_GAPS[touch_completed])
            new_sequence_status = "Active"
            sequence_complete = False
        else:
            next_date = None
            new_sequence_status = "Nurture"
            sequence_complete = True

        # ── Log the completed touch as a done Pipedrive Activity ──────────
        try:
            await attio.create_task(
                content=f"Touch {touch_completed} sent",
                deadline=today.isoformat(),
                linked_records=[{"object": "persons", "id": contact_record_id}],
                done=True,
                activity_type="email",
            )
        except Exception as e:
            logger.error("Failed to log completed touch activity for %s: %s", contact_record_id, e)
            return {"error": f"Failed to log touch activity: {e}"}

        # ── Schedule the next touch as a pending Activity ─────────────────
        if next_date:
            try:
                await attio.create_task(
                    content=f"Touch {touch_completed + 1} due",
                    deadline=next_date.isoformat(),
                    linked_records=[{"object": "persons", "id": contact_record_id}],
                    done=False,
                    activity_type="email",
                )
            except Exception as e:
                logger.error("Failed to schedule next touch activity for %s: %s", contact_record_id, e)

        # ── Update sequence_status on the contact (still a custom field) ──
        try:
            await attio.update_record("people", contact_record_id, {"sequence_status": new_sequence_status})
            logger.info(
                "Contact sequence advanced: contact=%s touch=%d next=%s complete=%s",
                contact_record_id, touch_completed,
                next_date.isoformat() if next_date else "N/A",
                sequence_complete,
            )
        except Exception as e:
            logger.error("Failed to update sequence_status for %s: %s", contact_record_id, e)

        # ── Company aggregate status update (optional) ────────────────────
        if company_record_id and touch_completed == 1:
            try:
                await attio.update_record(
                    "companies", company_record_id, {"outreach_status": "Sequence Active"}
                )
            except Exception as e:
                logger.error("Company status update failed for %s: %s", company_record_id, e)

        return {
            "status": "advanced",
            "contact_record_id": contact_record_id,
            "sequence_touch": touch_completed,
            "last_touch_date": today.isoformat(),
            "next_touch_date": next_date.isoformat() if next_date else None,
            "sequence_complete": sequence_complete,
        }

    registry.register(
        name="sequence_advance",
        description=(
            "Advance the outreach sequence for a specific contact after completing a touch. "
            "Call this AFTER email_send or email_save_draft. "
            "Logs the completed touch as a done Pipedrive Activity, schedules the next touch "
            "as a pending Activity with a due date, and updates the contact's sequence_status. "
            "Optionally updates the Company's outreach_status to 'Sequence Active' on Touch 1. "
            "Cadence: Touch 1 → +8 days → Touch 2 → +14 days → Touch 3 → Nurture."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "contact_record_id": {
                    "type": "string",
                    "description": "CRM People record ID for the specific contact being advanced.",
                },
                "touch_completed": {
                    "type": "integer",
                    "description": "The touch number just completed (1, 2, or 3).",
                },
                "company_record_id": {
                    "type": "string",
                    "description": (
                        "Optional CRM company record ID. If provided, updates the Company's "
                        "outreach_status to 'Sequence Active' when Touch 1 is completed."
                    ),
                },
            },
            "required": ["contact_record_id", "touch_completed"],
        },
        handler=sequence_advance,
    )
