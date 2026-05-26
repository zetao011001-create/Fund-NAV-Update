"""IMAP email client for Aliyun Qiye mailbox.

Two modes:
- poll(): one-shot fetch of new mail (used by the daemon every N minutes)
- idle(): long-lived IDLE connection (best-effort; falls back to poll on errors)
"""

from __future__ import annotations

import email
import email.header
import email.utils
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path
from typing import Iterator, Optional

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError

from .config import (
    ATTACHMENT_DIR,
    EMAIL_ADDRESS,
    EMAIL_LOOKBACK_DAYS,
    IMAP_PORT,
    IMAP_SERVER,
    NAV_SUBJECT_KEYWORDS,
    get_email_password,
)
from .logger import log


@dataclass
class Attachment:
    filename: str
    content: bytes
    saved_path: Optional[Path] = None


@dataclass
class FetchedEmail:
    uid: str
    subject: str
    sender: str
    received_at: Optional[datetime]
    body_text: str
    body_html: str
    attachments: list[Attachment] = field(default_factory=list)


class EmailClient:
    """IMAP client wrapper. Use as a context manager."""

    def __init__(
        self,
        host: str = IMAP_SERVER,
        port: int = IMAP_PORT,
        username: str = EMAIL_ADDRESS,
        password: Optional[str] = None,
        attachment_dir: Path = ATTACHMENT_DIR,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password if password is not None else get_email_password()
        self.attachment_dir = attachment_dir
        self.client: Optional[IMAPClient] = None

    # ---------- lifecycle ----------
    def __enter__(self) -> "EmailClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        log.info(f"Connecting to IMAP {self.host}:{self.port} as {self.username}")
        try:
            self.client = IMAPClient(self.host, port=self.port, ssl=True, timeout=30)
            self.client.login(self.username, self.password)
            self.client.select_folder("INBOX")
            log.info("IMAP login successful")
        except (IMAPClientError, socket.error) as e:
            log.error(f"IMAP connection failed: {e}")
            raise

    def close(self) -> None:
        if self.client:
            try:
                self.client.logout()
            except Exception as e:
                log.warning(f"IMAP logout error (ignored): {e}")
            finally:
                self.client = None

    # ---------- searching ----------
    def search_nav_emails(
        self,
        since_days: int = EMAIL_LOOKBACK_DAYS,
        only_unseen: bool = False,
    ) -> list[int]:
        """Return UID list for candidate NAV emails. Subject filter happens later."""
        if not self.client:
            raise RuntimeError("IMAP not connected")

        criteria: list = []
        if only_unseen:
            criteria.append("UNSEEN")
        if since_days > 0:
            since = (datetime.now() - timedelta(days=since_days)).date()
            criteria.extend(["SINCE", since])
        if not criteria:
            criteria = ["ALL"]

        try:
            uids = self.client.search(criteria)
            log.info(f"IMAP search criteria={criteria} → {len(uids)} hits")
            return list(uids)
        except IMAPClientError as e:
            log.error(f"IMAP search failed: {e}")
            return []

    # ---------- fetching ----------
    def fetch(self, uids: list[int], save_attachments: bool = True) -> Iterator[FetchedEmail]:
        if not self.client or not uids:
            return
        for uid in uids:
            try:
                data = self.client.fetch([uid], ["RFC822", "INTERNALDATE"])
                if uid not in data:
                    continue
                raw = data[uid].get(b"RFC822")
                received_at = data[uid].get(b"INTERNALDATE")
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)
                yield self._parse_message(uid, msg, received_at, save_attachments)
            except Exception as e:
                log.exception(f"Failed to fetch UID={uid}: {e}")

    def mark_as_read(self, uid: int) -> None:
        if not self.client:
            return
        try:
            self.client.add_flags([uid], [b"\\Seen"])
        except IMAPClientError as e:
            log.warning(f"mark_as_read failed for UID={uid}: {e}")

    # ---------- IDLE (best-effort) ----------
    def idle_loop(self, on_new_uids, idle_timeout: int = 25 * 60) -> None:
        """Long-lived IDLE; calls on_new_uids(uids) when EXISTS responses arrive.

        Aliyun Qiye may close idle connections; caller should wrap in retry.
        """
        if not self.client:
            raise RuntimeError("IMAP not connected")
        try:
            self.client.idle()
            log.info("IMAP IDLE entered")
            deadline = time.time() + idle_timeout
            while time.time() < deadline:
                resp = self.client.idle_check(timeout=60)
                if resp:
                    log.debug(f"IDLE response: {resp}")
                    self.client.idle_done()
                    uids = self.search_nav_emails(since_days=1, only_unseen=True)
                    on_new_uids(uids)
                    self.client.idle()
                    deadline = time.time() + idle_timeout
        finally:
            try:
                self.client.idle_done()
            except Exception:
                pass

    # ---------- internals ----------
    def _parse_message(
        self,
        uid: int,
        msg: Message,
        received_at: Optional[datetime],
        save_attachments: bool,
    ) -> FetchedEmail:
        subject = _decode_header(msg.get("Subject", ""))
        sender = _decode_header(msg.get("From", ""))

        body_text = ""
        body_html = ""
        attachments: list[Attachment] = []

        for part in msg.walk():
            if part.is_multipart():
                continue
            content_disposition = str(part.get("Content-Disposition") or "").lower()
            content_type = (part.get_content_type() or "").lower()
            filename = part.get_filename()
            if filename:
                filename = _decode_header(filename)

            if "attachment" in content_disposition or filename:
                payload = part.get_payload(decode=True)
                if not payload or not filename:
                    continue
                att = Attachment(filename=filename, content=payload)
                if save_attachments:
                    safe = _safe_filename(f"{uid}_{filename}")
                    target = self.attachment_dir / safe
                    try:
                        target.write_bytes(payload)
                        att.saved_path = target
                    except OSError as e:
                        log.warning(f"Cannot save attachment {target}: {e}")
                attachments.append(att)
                continue

            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                text = payload.decode("utf-8", errors="replace")

            if content_type == "text/plain":
                body_text += text
            elif content_type == "text/html":
                body_html += text

        return FetchedEmail(
            uid=str(uid),
            subject=subject,
            sender=sender,
            received_at=received_at if isinstance(received_at, datetime) else None,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
        )


def is_nav_email(subject: str, body_text: str = "") -> bool:
    haystack = f"{subject or ''}\n{body_text or ''}"
    return any(kw in haystack for kw in NAV_SUBJECT_KEYWORDS)


def _decode_header(value: str) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    out: list[str] = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(enc or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out).strip()


_FILENAME_BAD = re.compile(r"[^\w.\-_一-鿿]+")


def _safe_filename(name: str) -> str:
    name = _FILENAME_BAD.sub("_", name)
    return name[:200] or "attachment"
