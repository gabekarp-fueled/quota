"""Gmail API email client using OAuth2.

Implements create_draft, send_email, send_reply, and delete_draft
via the Gmail REST API with a refresh-token credential flow.
"""

import base64
import json
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


class EmailClient:
    def __init__(
        self,
        from_email: str,
        from_name: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> None:
        self.from_email = from_email
        self.from_name = from_name
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._access_token: Optional[str] = None
        self._http = httpx.AsyncClient(timeout=30)

    # ── Token management ──────────────────────────────────────────────────

    async def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        resp = await self._http.post(
            _TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        self._access_token = resp.json()["access_token"]
        return self._access_token

    async def _headers(self) -> dict:
        token = await self._get_access_token()
        return {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, url: str, **kwargs):
        headers = await self._headers()
        resp = await self._http.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401:
            # Token expired — refresh once and retry
            self._access_token = None
            headers = await self._headers()
            resp = await self._http.request(method, url, headers=headers, **kwargs)
        resp.raise_for_status()
        return resp

    # ── MIME helpers ──────────────────────────────────────────────────────

    def _build_mime(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        reply_to: Optional[str] = None,
        in_reply_to: Optional[str] = None,
        references: Optional[str] = None,
    ) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{self.from_name} <{self.from_email}>"
        msg["To"] = to
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references
        if text_body:
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        return msg

    @staticmethod
    def _encode_mime(msg: MIMEMultipart) -> str:
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return raw

    # ── Public API ────────────────────────────────────────────────────────

    async def create_draft(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> dict:
        """Create a Gmail draft and return {"draft_id": ...}."""
        msg = self._build_mime(to, subject, html_body, text_body, reply_to)
        body = {"message": {"raw": self._encode_mime(msg)}}
        resp = await self._request("POST", f"{_GMAIL_BASE}/drafts", json=body)
        data = resp.json()
        return {"draft_id": data.get("id", "unknown")}

    async def send_email(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> dict:
        """Send an email and return {"id": message_id}."""
        msg = self._build_mime(to, subject, html_body, text_body, reply_to)
        body = {"raw": self._encode_mime(msg)}
        resp = await self._request("POST", f"{_GMAIL_BASE}/messages/send", json=body)
        return resp.json()

    async def send_reply(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        in_reply_to: Optional[str] = None,
        references: Optional[str] = None,
    ) -> dict:
        """Send a reply email (with In-Reply-To / References headers)."""
        msg = self._build_mime(
            to, subject, html_body, text_body,
            in_reply_to=in_reply_to,
            references=references,
        )
        body = {"raw": self._encode_mime(msg)}
        resp = await self._request("POST", f"{_GMAIL_BASE}/messages/send", json=body)
        return resp.json()

    async def delete_draft(self, draft_id: str) -> None:
        """Delete a Gmail draft by its draft ID."""
        await self._request("DELETE", f"{_GMAIL_BASE}/drafts/{draft_id}")

    async def close(self) -> None:
        await self._http.aclose()
