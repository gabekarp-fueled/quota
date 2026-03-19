"""Slack notification tools for Claude agents.

Registers slack_notify_approval, which posts an interactive approval
card to Slack so a rep can approve or discard a drafted outreach email.
"""

import json
import logging
from typing import Any

from src.claude.tools import ToolRegistry

logger = logging.getLogger(__name__)


def register_slack_tools(
    registry: ToolRegistry,
    slack,
    channel: str,
) -> None:
    """Register Slack notification tools with the given registry."""

    async def slack_notify_approval(
        gmail_draft_id: str,
        record_id: str,
        account_name: str,
        contact_email: str,
        contact_name: str,
        touch_number: int = 1,
        contact_record_id: str = "",
        preview_text: str = "",
    ) -> Any:
        """Post an interactive Slack approval card for a drafted outreach email."""
        action_value = json.dumps({
            "gmail_draft_id": gmail_draft_id,
            "record_id": record_id,
            "account_name": account_name,
            "contact_email": contact_email,
            "contact_name": contact_name,
            "touch_number": touch_number,
            "contact_record_id": contact_record_id,
        })

        preview = preview_text[:300] if preview_text else "(open Gmail Drafts to preview)"

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":envelope: *Outreach Draft Ready — {account_name}*\n"
                        f"*To:* {contact_name} <{contact_email}>\n"
                        f"*Touch:* {touch_number}"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"_{preview}_",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": ":white_check_mark: Mark as Sent"},
                        "style": "primary",
                        "action_id": "mark_sent_outreach",
                        "value": action_value,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": ":email: Open Gmail Drafts"},
                        "action_id": "open_gmail_drafts",
                        "value": action_value,
                        "url": "https://mail.google.com/mail/u/0/#drafts",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": ":wastebasket: Discard"},
                        "style": "danger",
                        "action_id": "discard_outreach",
                        "value": action_value,
                    },
                ],
            },
        ]

        try:
            result = await slack.post_message(
                channel=channel,
                text=f"Outreach draft ready: {account_name} → {contact_name}",
                blocks=blocks,
            )
            logger.info(
                "Slack approval card posted for %s → %s (draft %s)",
                account_name, contact_email, gmail_draft_id,
            )
            return {"status": "notified", "ts": result.get("ts")}
        except Exception as e:
            logger.error("Failed to post Slack approval card: %s", e)
            return {"error": f"Failed to post Slack notification: {e}"}

    registry.register(
        name="slack_notify_approval",
        description=(
            "Post an interactive Slack approval card for a drafted outreach email. "
            "Call this AFTER email_save_draft to notify the rep. "
            "The card has 'Mark as Sent', 'Open Gmail Drafts', and 'Discard' buttons. "
            "Provide the gmail_draft_id returned by email_save_draft, plus the "
            "company and contact details for the card header."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "gmail_draft_id": {
                    "type": "string",
                    "description": "The draft ID returned by email_save_draft.",
                },
                "record_id": {
                    "type": "string",
                    "description": "Attio company record ID.",
                },
                "account_name": {
                    "type": "string",
                    "description": "Company name shown in the Slack card header.",
                },
                "contact_email": {
                    "type": "string",
                    "description": "Recipient email address.",
                },
                "contact_name": {
                    "type": "string",
                    "description": "Recipient full name.",
                },
                "touch_number": {
                    "type": "integer",
                    "description": "Which touch in the sequence (1, 2, or 3).",
                    "default": 1,
                },
                "contact_record_id": {
                    "type": "string",
                    "description": "Attio People record ID for the contact.",
                },
                "preview_text": {
                    "type": "string",
                    "description": "Short preview of the email body (first 200–300 chars).",
                },
            },
            "required": [
                "gmail_draft_id", "record_id", "account_name",
                "contact_email", "contact_name",
            ],
        },
        handler=slack_notify_approval,
    )
