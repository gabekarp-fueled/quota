"""Webhook endpoints for Slack interactive payloads and Events API."""

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response

from src.claude.loop import run_agent_loop
from src.claude.tools import ToolRegistry
from src.config import settings
from src.tools.analytics_tools import register_analytics_tools
from src.tools.attio_tools import register_attio_tools
from src.tools.email_tools import register_email_tools
from src.tools.okr_tools import register_okr_tools
from src.tools.research_tools import register_research_tools
from src.tools.slack_reply_tools import register_slack_reply_tools

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Lock to prevent concurrent CRO conversations (one at a time)
_cro_lock = asyncio.Lock()


def _verify_slack_signature(
    signing_secret: str, timestamp: str, body: bytes, signature: str
) -> bool:
    """Verify Slack request signature (v0 scheme)."""
    if abs(time.time() - int(timestamp)) > 300:
        return False

    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    computed = "v0=" + hmac.new(
        signing_secret.encode(), sig_basestring.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


@router.post("/slack")
async def slack_interaction(request: Request):
    """Handle Slack interactive payloads (button clicks on approval cards).

    Acknowledges Slack immediately (within 3s deadline), then processes
    the action in a background task to avoid timeout warnings.
    """
    body = await request.body()
    signing_secret = getattr(request.app.state, "slack_signing_secret", "")

    if signing_secret:
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
        signature = request.headers.get("X-Slack-Signature", "")
        if not _verify_slack_signature(signing_secret, timestamp, body, signature):
            logger.warning("Slack webhook: invalid signature")
            return Response(status_code=401)

    from urllib.parse import parse_qs

    form_data = parse_qs(body.decode("utf-8"))
    payload_str = form_data.get("payload", [None])[0]
    if not payload_str:
        logger.warning("Slack webhook: no payload field")
        return Response(status_code=400)

    payload = json.loads(payload_str)
    actions = payload.get("actions", [])
    if not actions:
        return {"ok": True}

    asyncio.create_task(_process_slack_interaction(payload, request.app))
    return {"ok": True}


async def _process_slack_interaction(payload: dict, app) -> None:
    """Process a Slack interactive payload asynchronously after acknowledging."""
    actions = payload.get("actions", [])
    if not actions:
        return

    action = actions[0]
    action_id = action.get("action_id")
    action_value = json.loads(action.get("value", "{}"))

    note_id = action_value.get("note_id")
    gmail_draft_id = action_value.get("gmail_draft_id", "")
    record_id = action_value.get("record_id")
    account_name = action_value.get("account_name", "Unknown")
    contact_email = action_value.get("contact_email")
    contact_name = action_value.get("contact_name")
    touch_number = action_value.get("touch_number", 1)
    contact_record_id = action_value.get("contact_record_id", "")

    attio = app.state.attio
    slack = getattr(app.state, "slack", None)
    email_client = getattr(app.state, "email", None)

    channel = payload.get("channel", {}).get("id")
    message_ts = payload.get("message", {}).get("ts")
    user_name = payload.get("user", {}).get("username", "someone")

    if action_id == "approve_reply":
        logger.info("Reply approval: %s approved reply to %s", user_name, account_name)

        in_reply_to = action_value.get("in_reply_to") or None
        references = action_value.get("references") or None

        try:
            note_data = await attio.get_note(note_id)
            note_content = note_data.get("data", {}).get("content_plaintext", "") or note_data.get("data", {}).get("content", "")
            draft = json.loads(note_content)
        except Exception as e:
            logger.error("Failed to read reply draft note %s: %s", note_id, e)
            if slack and channel and message_ts:
                await slack.post_thread_reply(channel, message_ts, f":x: Failed to read draft — {e}")
            return {"ok": True}

        if not email_client:
            if slack and channel and message_ts:
                await slack.post_thread_reply(channel, message_ts, ":warning: Email client not configured. Send manually.")
            return {"ok": True}

        try:
            send_result = await email_client.send_reply(
                to=draft.get("to_email", contact_email),
                subject=draft.get("subject", ""),
                html_body=draft.get("html_body", ""),
                text_body=draft.get("text_body"),
                in_reply_to=draft.get("in_reply_to") or in_reply_to,
                references=draft.get("references") or references,
            )
            message_id = send_result.get("id", "unknown")
        except Exception as e:
            logger.error("Reply send failed after approval: %s", e)
            if slack and channel and message_ts:
                await slack.post_thread_reply(channel, message_ts, f":x: Send failed — {e}")
            return {"ok": True}

        try:
            await attio.create_note(
                parent_object="companies",
                parent_record_id=record_id,
                title=f"Reply Sent — {contact_name} (approved by {user_name})",
                content=(
                    f"Reply sent to {contact_name} <{contact_email}>\n"
                    f"Subject: {draft.get('subject', '')}\n"
                    f"Message ID: {message_id}\n"
                    f"Approved by: {user_name} via Slack\n"
                    f"---\n{draft.get('text_body', '')}"
                ),
            )
        except Exception as e:
            logger.error("Attio note failed after reply send: %s", e)

        if slack and channel and message_ts:
            await slack.update_message(
                channel=channel,
                ts=message_ts,
                text=f":white_check_mark: Reply sent — {account_name}",
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":white_check_mark: *Reply Sent*\n"
                            f"*{account_name}* → {contact_name} <{contact_email}>\n"
                            f"Approved by @{user_name}"
                        ),
                    },
                }],
            )

    elif action_id == "skip_reply":
        logger.info("Reply skipped: %s skipped reply to %s", user_name, account_name)
        if slack and channel and message_ts:
            await slack.update_message(
                channel=channel,
                ts=message_ts,
                text=f":fast_forward: Reply skipped — {account_name}",
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":fast_forward: *Reply Skipped* — {account_name} ({contact_name})\nSkipped by @{user_name}",
                    },
                }],
            )

    elif action_id == "approve_followup":
        logger.info("Follow-up approval: %s approved follow-up to %s", user_name, account_name)

        try:
            note_data = await attio.get_note(note_id)
            note_content = note_data.get("data", {}).get("content_plaintext", "") or note_data.get("data", {}).get("content", "")
            draft = json.loads(note_content)
        except Exception as e:
            logger.error("Failed to read follow-up draft note %s: %s", note_id, e)
            if slack and channel and message_ts:
                await slack.post_thread_reply(channel, message_ts, f":x: Failed to read draft — {e}")
            return {"ok": True}

        if not email_client:
            if slack and channel and message_ts:
                await slack.post_thread_reply(channel, message_ts, ":warning: Email client not configured.")
            return {"ok": True}

        try:
            send_result = await email_client.send_email(
                to=draft.get("to_email", contact_email),
                subject=draft.get("subject", ""),
                html_body=draft.get("html_body", ""),
                text_body=draft.get("text_body"),
            )
            message_id = send_result.get("id", "unknown")
        except Exception as e:
            logger.error("Follow-up send failed: %s", e)
            if slack and channel and message_ts:
                await slack.post_thread_reply(channel, message_ts, f":x: Send failed — {e}")
            return {"ok": True}

        try:
            await attio.create_note(
                parent_object="companies",
                parent_record_id=record_id,
                title=f"Post-call Follow-up Sent — {contact_name} (approved by {user_name})",
                content=(
                    f"Follow-up sent to {contact_name} <{contact_email}>\n"
                    f"Subject: {draft.get('subject', '')}\n"
                    f"Message ID: {message_id}\n"
                    f"Approved by: {user_name} via Slack\n"
                    f"---\n{draft.get('text_body', '')}"
                ),
            )
            await attio.update_record(
                "companies", record_id,
                {"values": {"outreach_status": [{"option": "Responded"}]}},
            )
        except Exception as e:
            logger.error("Attio update failed after follow-up send: %s", e)

        if slack and channel and message_ts:
            await slack.update_message(
                channel=channel,
                ts=message_ts,
                text=f":white_check_mark: Follow-up sent — {account_name}",
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":white_check_mark: *Follow-up Sent*\n"
                            f"*{account_name}* → {contact_name} <{contact_email}>\n"
                            f"Approved by @{user_name}"
                        ),
                    },
                }],
            )

    elif action_id == "skip_followup":
        logger.info("Follow-up skipped for %s by %s", account_name, user_name)
        if slack and channel and message_ts:
            await slack.update_message(
                channel=channel,
                ts=message_ts,
                text=f":fast_forward: Follow-up skipped — {account_name}",
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":fast_forward: *Follow-up Skipped* — {account_name}\nSkipped by @{user_name}",
                    },
                }],
            )

    elif action_id == "open_gmail_drafts":
        logger.info("Gmail Drafts opened by %s for %s", user_name, account_name)

    elif action_id == "mark_sent_outreach":
        logger.info("Mark as sent: %s marked %s Touch %d as sent", user_name, account_name, touch_number)

        touch_gaps = {1: 8, 2: 14}
        today = date.today()

        try:
            sent_note = (
                f"Touch {touch_number} email sent to {contact_name} <{contact_email}>\n"
                f"Sent via Gmail manually\n"
                f"Marked sent at: {datetime.now(timezone.utc).isoformat()}\n"
                f"Marked by: {user_name} via Slack"
            )
            await attio.create_note(
                parent_object="companies",
                parent_record_id=record_id,
                title=f"Touch {touch_number} Email Sent: {contact_name}",
                content=sent_note,
            )
        except Exception as e:
            logger.error("Attio note failed after mark-as-sent: %s", e)

        company_updates: dict = {
            "current_touch": [{"value": touch_number}],
            "last_touch_date": [{"value": today.isoformat()}],
        }
        if touch_number in touch_gaps:
            next_date = today + timedelta(days=touch_gaps[touch_number])
            company_updates["next_touch_date"] = [{"value": next_date.isoformat()}]
            company_updates["outreach_status"] = [{"option": "Sequence Active"}]
        else:
            company_updates["next_touch_date"] = [{"value": None}]
            company_updates["outreach_status"] = [{"option": "Nurture"}]

        try:
            await attio.update_record("companies", record_id, {"values": company_updates})
        except Exception as e:
            logger.error("Company update failed after mark-as-sent: %s", e)

        if contact_record_id:
            people_updates: dict = {
                "sequence_touch": [{"value": touch_number}],
                "last_touch_date": [{"value": today.isoformat()}],
                "sequence_status": [{"option": "Nurture" if touch_number >= 3 else "Active"}],
            }
            if touch_number in touch_gaps:
                next_date = today + timedelta(days=touch_gaps[touch_number])
                people_updates["next_touch_date"] = [{"value": next_date.isoformat()}]
            try:
                await attio.update_record("people", contact_record_id, {"values": people_updates})
            except Exception as e:
                logger.error("People sequence update failed after mark-as-sent: %s", e)
        else:
            logger.warning("No contact_record_id in action_value — People sequence not updated")

        if slack and channel and message_ts:
            await slack.update_message(
                channel=channel,
                ts=message_ts,
                text=f":white_check_mark: Sent — {account_name} → {contact_email}",
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":white_check_mark: *Sent*\n"
                            f"*{account_name}* → {contact_name} <{contact_email}>\n"
                            f"Touch {touch_number} • Marked sent by @{user_name}"
                        ),
                    },
                }],
            )

    elif action_id == "discard_outreach":
        logger.info("Discard: %s discarded %s Touch %d draft", user_name, account_name, touch_number)

        if email_client and gmail_draft_id:
            try:
                await email_client.delete_draft(gmail_draft_id)
                logger.info("Gmail draft deleted: %s", gmail_draft_id)
            except Exception as e:
                logger.error("Failed to delete Gmail draft %s: %s", gmail_draft_id, e)

        if slack and channel and message_ts:
            await slack.update_message(
                channel=channel,
                ts=message_ts,
                text=f":wastebasket: Discarded — {account_name} draft",
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":wastebasket: *Discarded*\n"
                            f"*{account_name}* → {contact_name} <{contact_email}>\n"
                            f"Touch {touch_number} • Draft deleted by @{user_name}"
                        ),
                    },
                }],
            )

    return {"ok": True}


# ── Slack Events API (Conversational CRO) ───────────────────────────────────


@router.post("/slack/events")
async def slack_events(request: Request):
    """Handle Slack Events API — app mentions trigger conversational CRO."""
    body = await request.body()

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return Response(status_code=400)

    if payload.get("type") == "url_verification":
        logger.info("Slack Events API: URL verification challenge received")
        return {"challenge": payload.get("challenge", "")}

    if request.headers.get("X-Slack-Retry-Num"):
        logger.info("Slack events: ignoring retry")
        return {"ok": True}

    signing_secret = getattr(request.app.state, "slack_signing_secret", "")
    if signing_secret:
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
        signature = request.headers.get("X-Slack-Signature", "")
        if not _verify_slack_signature(signing_secret, timestamp, body, signature):
            logger.warning("Slack events: invalid signature")
            return Response(status_code=401)

    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        event_type = event.get("type")

        if event.get("bot_id") or event.get("subtype") == "bot_message":
            logger.info("Slack events: ignoring bot message")
            return {"ok": True}

        if event_type == "app_mention":
            logger.info("Slack events: launching CRO handler for mention")
            asyncio.create_task(_handle_cro_mention(request.app, event))
            return {"ok": True}

        if event_type == "message" and event.get("channel_type") == "im":
            logger.info("Slack events: launching CRO handler for DM")
            asyncio.create_task(_handle_cro_mention(request.app, event))
            return {"ok": True}

    return {"ok": True}


async def _handle_cro_mention(app, event: dict) -> None:
    """Background handler for @CRO mentions — runs the CRO agent loop."""
    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts") or event.get("ts", "")
    raw_text = event.get("text", "")

    user_message = re.sub(r"<@[A-Z0-9]+>\s*", "", raw_text).strip()

    slack = getattr(app.state, "slack", None)
    if not slack:
        logger.error("CRO mention: no Slack client configured")
        return

    if not user_message:
        await slack.post_thread_reply(
            channel, thread_ts, "What would you like me to look into?"
        )
        return

    if _cro_lock.locked():
        await slack.post_thread_reply(
            channel, thread_ts,
            ":hourglass_flowing_sand: I'm working on another request. I'll get to this next."
        )

    async with _cro_lock:
        try:
            await slack.post_thread_reply(channel, thread_ts, ":brain: Working on it...")

            attio = app.state.attio
            email_client = getattr(app.state, "email", None)
            claude_client = app.state.claude

            apollo = getattr(app.state, "apollo", None)
            fullenrich = getattr(app.state, "fullenrich", None)
            scout_prompt = app.state.prompts.get("scout", "")

            registry = ToolRegistry()
            register_attio_tools(registry, attio)
            register_analytics_tools(registry, attio)
            if email_client:
                register_email_tools(
                    registry, email_client, attio, settings.email_daily_send_limit
                )
            register_research_tools(
                registry, attio, claude_client,
                scout_prompt=scout_prompt,
                scout_model=settings.scout_model,
                scout_batch_size=settings.scout_batch_size,
                apollo=apollo,
                fullenrich=fullenrich,
            )
            register_okr_tools(registry)
            register_slack_reply_tools(registry, slack, channel, thread_ts)

            cro_prompt = app.state.prompts.get("cro", "")
            cro_conversational = app.state.prompts.get("cro_conversational", "")
            system_prompt = cro_prompt
            if cro_conversational:
                system_prompt += "\n\n---\n\n" + cro_conversational
            if not claude_client:
                await slack.post_thread_reply(
                    channel, thread_ts, ":warning: Claude client not configured."
                )
                return

            result = await run_agent_loop(
                client=claude_client,
                model=settings.cro_model,
                system_prompt=system_prompt,
                tools=registry,
                user_message=user_message,
                max_turns=settings.cro_conversational_max_turns,
            )

            logger.info(
                "CRO conversation: %d turns, %d input tokens, %d output tokens",
                result.turns,
                result.input_tokens,
                result.output_tokens,
            )

            try:
                from src.db.models import Run
                from src.db.session import get_session_factory
                factory = get_session_factory()
                if factory:
                    async with factory() as session:
                        session.add(Run(
                            agent_name="cro",
                            focus=f"slack: {user_message[:200]}",
                            status="ok",
                            turns=result.turns,
                            input_tokens=result.input_tokens,
                            output_tokens=result.output_tokens,
                            summary=(result.text or "")[:2000],
                            tools_used=[],
                            completed_at=datetime.now(timezone.utc),
                        ))
                        await session.commit()
            except Exception:
                logger.exception("CRO mention: failed to log run to DB")

            response_text = result.text or "Done — I completed the actions but have nothing to add."

            if len(response_text) > 3900:
                chunks = [
                    response_text[i: i + 3900]
                    for i in range(0, len(response_text), 3900)
                ]
                for chunk in chunks:
                    await slack.post_thread_reply(channel, thread_ts, chunk)
            else:
                await slack.post_thread_reply(channel, thread_ts, response_text)

        except Exception as e:
            logger.error("CRO mention handler failed: %s", e, exc_info=True)
            try:
                await slack.post_thread_reply(
                    channel, thread_ts,
                    f":x: Something went wrong: {e}"
                )
            except Exception:
                logger.error("Failed to post error message to Slack")
