"""Gmail IMAP inbox client for monitoring inbound email.

Uses the App Password credential (not OAuth2) with asyncio-compatible
IMAP via aioimaplib.
"""

import asyncio
import email
import logging
import re
from dataclasses import dataclass, field
from email.header import decode_header
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class EmailMessage:
    uid: int
    from_email: str
    from_name: str
    subject: str
    body: str
    message_id: str = ""
    in_reply_to: str = ""


def _decode_header_value(value: str) -> str:
    """Decode an RFC 2047 encoded header value to a plain string."""
    parts = decode_header(value or "")
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _extract_address(header: str) -> tuple[str, str]:
    """Return (name, email) from a From/To header string."""
    match = re.match(r'"?([^"<]*)"?\s*<?([^>]+)>?', header.strip())
    if match:
        name = match.group(1).strip()
        addr = match.group(2).strip()
        return name, addr
    return "", header.strip()


def _get_body(msg: email.message.Message) -> str:
    """Extract plain-text body from a MIME message, falling back to HTML."""
    plain = None
    html = None
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                continue
            if ct == "text/plain" and plain is None:
                plain = part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
            elif ct == "text/html" and html is None:
                html = part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                html = text
            else:
                plain = text

    return plain or html or ""


class InboxClient:
    def __init__(
        self,
        email_address: str,
        password: str,
        host: str = "imap.gmail.com",
        port: int = 993,
    ) -> None:
        self.email_address = email_address
        self.password = password
        self.host = host
        self.port = port

    async def fetch_new_emails(self, since_uid: int = 0) -> List[EmailMessage]:
        """Fetch all emails with UID greater than since_uid from INBOX."""
        try:
            import aioimaplib
        except ImportError:
            logger.error("aioimaplib not installed — inbox monitoring unavailable")
            return []

        messages: List[EmailMessage] = []

        try:
            imap = aioimaplib.IMAP4_SSL(host=self.host, port=self.port)
            await imap.wait_hello_from_server()
            await imap.login(self.email_address, self.password)
            await imap.select("INBOX")

            search_criterion = f"UID {since_uid + 1}:*" if since_uid > 0 else "ALL"
            _, data = await imap.uid("search", search_criterion)

            uid_list_raw = data[0]
            if isinstance(uid_list_raw, bytes):
                uid_list_raw = uid_list_raw.decode()
            uid_list = [int(u) for u in uid_list_raw.split() if u.strip()]

            for uid in uid_list:
                if uid <= since_uid:
                    continue
                try:
                    _, fetch_data = await imap.uid("fetch", str(uid), "(RFC822)")
                    raw = None
                    for item in fetch_data:
                        if isinstance(item, bytes) and item.startswith(b"From ") is False:
                            # Try to detect the raw message bytes
                            if len(item) > 200:
                                raw = item
                                break
                    if raw is None:
                        for item in fetch_data:
                            if isinstance(item, (bytes, bytearray)) and len(item) > 100:
                                raw = item
                                break

                    if not raw:
                        continue

                    parsed = email.message_from_bytes(raw)
                    from_header = parsed.get("From", "")
                    from_name, from_email = _extract_address(_decode_header_value(from_header))
                    subject = _decode_header_value(parsed.get("Subject", "(no subject)"))
                    body = _get_body(parsed)
                    message_id = parsed.get("Message-ID", "").strip()
                    in_reply_to = parsed.get("In-Reply-To", "").strip()

                    messages.append(EmailMessage(
                        uid=uid,
                        from_email=from_email,
                        from_name=from_name,
                        subject=subject,
                        body=body,
                        message_id=message_id,
                        in_reply_to=in_reply_to,
                    ))
                except Exception as e:
                    logger.error("Failed to fetch/parse email UID %d: %s", uid, e)

            await imap.logout()

        except Exception as e:
            logger.error("IMAP connection error: %s", e)

        return messages
