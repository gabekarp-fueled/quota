"""Slack reply tools for conversational CRO mode.

Registers a slack_post_reply tool that lets the CRO agent post
messages back to a Slack thread during an interactive conversation.
"""

import logging
from typing import Any

from src.claude.tools import ToolRegistry

logger = logging.getLogger(__name__)


def register_slack_reply_tools(
    registry: ToolRegistry,
    slack,
    channel: str,
    thread_ts: str,
) -> None:
    """Register Slack reply tools scoped to a specific thread.

    Args:
        registry: Tool registry to add tools to.
        slack: Slack client for posting messages.
        channel: Channel ID where the conversation is happening.
        thread_ts: Thread timestamp to reply in.
    """

    async def slack_post_reply(message: str) -> Any:
        """Post a message to the current Slack thread."""
        try:
            result = await slack.post_thread_reply(
                channel=channel,
                thread_ts=thread_ts,
                text=message,
            )
            return {"status": "posted", "ts": result.get("ts")}
        except Exception as e:
            logger.error("Failed to post Slack reply: %s", e)
            return {"error": f"Failed to post reply: {e}"}

    registry.register(
        name="slack_post_reply",
        description=(
            "Post a message to the current Slack conversation thread. "
            "Use this for progress updates, multi-part responses, or when you need "
            "to share intermediate results before your final answer."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message text to post. Supports Slack formatting: *bold*, _italic_, `code`, bullet lists.",
                },
            },
            "required": ["message"],
        },
        handler=slack_post_reply,
    )
