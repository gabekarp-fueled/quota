"""Slack API client for posting messages and managing approval cards.

Uses the Bot Token (xoxb-...) to call the Slack Web API via httpx.
"""

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_SLACK_API = "https://slack.com/api"


class SlackClient:
    def __init__(self, bot_token: str) -> None:
        self._token = bot_token
        self._http = httpx.AsyncClient(
            timeout=15,
            headers={"Authorization": f"Bearer {bot_token}"},
        )

    # ── Internal helpers ──────────────────────────────────────────────────

    async def _call(self, method: str, **payload) -> dict:
        resp = await self._http.post(f"{_SLACK_API}/{method}", json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            error = data.get("error", "unknown_error")
            logger.error("Slack API error (%s): %s", method, error)
            raise RuntimeError(f"Slack {method} failed: {error}")
        return data

    # ── Public API ────────────────────────────────────────────────────────

    async def post_message(
        self,
        channel: str,
        text: str,
        blocks: Optional[list] = None,
        thread_ts: Optional[str] = None,
    ) -> dict:
        """Post a message to a channel. Returns the Slack API response."""
        payload: dict[str, Any] = {"channel": channel, "text": text}
        if blocks:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return await self._call("chat.postMessage", **payload)

    async def post_thread_reply(
        self,
        channel: str,
        thread_ts: str,
        text: str,
        blocks: Optional[list] = None,
    ) -> dict:
        """Post a reply inside an existing thread."""
        payload: dict[str, Any] = {
            "channel": channel,
            "thread_ts": thread_ts,
            "text": text,
        }
        if blocks:
            payload["blocks"] = blocks
        return await self._call("chat.postMessage", **payload)

    async def update_message(
        self,
        channel: str,
        ts: str,
        text: str,
        blocks: Optional[list] = None,
    ) -> dict:
        """Update an existing message (used to resolve approval cards)."""
        payload: dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
        if blocks:
            payload["blocks"] = blocks
        return await self._call("chat.update", **payload)

    async def close(self) -> None:
        await self._http.aclose()
