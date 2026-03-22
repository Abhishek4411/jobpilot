"""Read emails from Gmail via IMAP.

fetch_unread_emails()     — regular 1-min cycle: UNSEEN inbox (last 7 days)
fetch_all_recent_emails() — startup catchup: ALL inbox emails (seen+unseen, 14 days)
fetch_sent_emails()       — sent folder + bounce detection (mailer-daemon)

Each email dict includes both body_preview (plain text) and html_body (raw HTML)
so job_alert_extractor can parse LinkedIn/Naukri links from the original HTML.
"""

import email
import email.header
import imaplib
import os
import re
import socket
from datetime import datetime, timedelta
from typing import Any

from core.db import get_conn, log_audit
from core.logger import get_logger

log = get_logger(__name__)
_MAX_BODY = 8000
_MAX_HTML = 60000  # larger limit: LinkedIn alert HTML contains job card structure


_IMAP_TIMEOUT = 20  # seconds — prevents email job from hanging when Gmail is unreachable


def _connect() -> imaplib.IMAP4_SSL | None:
    """Create an authenticated Gmail IMAP connection with a 20-second timeout."""
    gmail = os.environ.get("GMAIL_ADDRESS", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not gmail or not app_password:
        log.warning("Gmail credentials not set, skipping email read")
        return None
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(_IMAP_TIMEOUT)
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(gmail, app_password)
        return imap
    except Exception as e:
        log.error("Gmail IMAP connection failed: %s", e)
        return None
    finally:
        socket.setdefaulttimeout(old_timeout)


def _decode_subject(raw_subject: str) -> str:
    """Decode MIME-encoded subject to plain text."""
    try:
        parts = email.header.decode_header(raw_subject)
        decoded = []
        for data, charset in parts:
            if isinstance(data, bytes):
                decoded.append(data.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(str(data))
        return "".join(decoded)
    except Exception:
        return raw_subject or ""


def _extract_body(msg: email.message.Message) -> tuple[str, str]:
    """Return (plain_text, raw_html) from an email message.

    Returns both so job_alert_extractor can use the HTML for link/structure parsing.
    """
    plain = ""
    html = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                if ct == "text/plain" and not plain:
                    plain = text[:_MAX_BODY]
                elif ct == "text/html" and not html:
                    html = text[:_MAX_HTML]
            except Exception:
                pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                plain = payload.decode(
                    msg.get_content_charset() or "utf-8", errors="replace")[:_MAX_BODY]
        except Exception:
            pass

    if not plain and html:
        stripped = re.sub(r"<[^>]+>", " ", html)
        plain = re.sub(r"\s{2,}", " ", stripped).strip()[:_MAX_BODY]
    return plain.strip(), html


def _get_existing_ids() -> set[str]:
    conn = get_conn()
    return {r[0] for r in conn.execute("SELECT message_id FROM emails").fetchall()}


def _fetch_folder(imap: imaplib.IMAP4_SSL, folder: str, criteria: str,
                  limit: int, existing_ids: set, mark_seen: bool) -> list[dict]:
    """Low-level fetch: search a folder and return new email dicts."""
    emails: list[dict[str, Any]] = []
    try:
        imap.select(folder, readonly=not mark_seen)
        _, msg_nums = imap.search(None, criteria)
        ids = msg_nums[0].split()
    except Exception as e:
        log.debug("Folder '%s' search failed: %s", folder, e)
        return emails

    if ids:
        log.info("Found %d emails in '%s'", len(ids), folder)

    for num in ids[-limit:]:
        try:
            _, data = imap.fetch(num, "(RFC822)")
            raw = data[0][1]
            msg = email.message_from_bytes(raw)
        except Exception as e:
            log.warning("Failed to fetch email %s: %s", num, e)
            continue

        message_id = msg.get("Message-ID", "").strip()
        if not message_id:
            message_id = f"no-id-{msg.get('Date','')}-{msg.get('Subject','')[:20]}"
        if message_id in existing_ids:
            continue

        plain, html = _extract_body(msg)
        emails.append({
            "message_id": message_id,
            "subject": _decode_subject(msg.get("Subject", "")),
            "sender": msg.get("From", ""),
            "to": msg.get("To", ""),
            "body_preview": plain,
            "html_body": html,
            "received_at": msg.get("Date", ""),
        })

        if mark_seen:
            try:
                imap.store(num, "+FLAGS", "\\Seen")
            except Exception:
                pass

    return emails


def fetch_unread_emails() -> list[dict[str, Any]]:
    """Fetch UNSEEN inbox emails from last 14 days. Called every 5 minutes."""
    imap = _connect()
    if not imap:
        return []
    since = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
    existing_ids = _get_existing_ids()
    try:
        emails = _fetch_folder(imap, "INBOX", f"(UNSEEN SINCE {since})",
                               limit=50, existing_ids=existing_ids, mark_seen=True)
        if emails:
            log.info("Fetched %d new unread emails", len(emails))
            log_audit("comms", "emails_fetched", f"count={len(emails)}")
        return emails
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def fetch_all_recent_emails() -> list[dict[str, Any]]:
    """Fetch ALL inbox emails from last 14 days including already-read ones.

    Called once on startup to catch job alerts that were read but never processed.
    Deduplicates against DB so already-processed emails are skipped.
    """
    imap = _connect()
    if not imap:
        return []
    since = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
    existing_ids = _get_existing_ids()
    try:
        emails = _fetch_folder(imap, "INBOX", f"(SINCE {since})",
                               limit=300, existing_ids=existing_ids, mark_seen=False)
        if emails:
            log.info("Catchup: %d unprocessed emails found in last 7 days", len(emails))
            log_audit("comms", "emails_catchup", f"count={len(emails)}")
        return emails
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def fetch_spam_emails() -> list[dict[str, Any]]:
    """Check Gmail Spam folder for misclassified recruiter emails.

    Only fetches emails with job-related subjects to avoid noise.
    Marks them as UNSEEN so Gmail keeps them in spam (read-only scan).
    """
    imap = _connect()
    if not imap:
        return []
    since = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
    existing_ids = _get_existing_ids()
    # Subject patterns that look like recruiter/job emails
    job_subjects = ["job", "position", "opportunity", "role", "hiring", "recruiter",
                    "interview", "opening", "vacancy", "application"]
    results: list[dict[str, Any]] = []
    try:
        for folder in ("[Gmail]/Spam", "Spam", "Junk"):
            for keyword in job_subjects[:5]:  # limit IMAP searches
                emails = _fetch_folder(
                    imap, folder, f'(SINCE {since} SUBJECT "{keyword}")',
                    limit=5, existing_ids=existing_ids, mark_seen=False,
                )
                results.extend(emails)
                if results:
                    break  # found the right folder name
            if results:
                break
        if results:
            log.info("Spam scan: %d potential recruiter emails found", len(results))
            log_audit("comms", "spam_scanned", f"count={len(results)}")
    except Exception as e:
        log.debug("Spam scan error: %s", e)
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return results


def fetch_sent_emails() -> list[dict[str, Any]]:
    """Read Sent Mail folder + detect mailer-daemon bounces in inbox.

    Sent emails help track manually applied jobs.
    Bounce detection flags wrong/invalid email addresses the agent may have used.
    """
    imap = _connect()
    if not imap:
        return []
    since = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
    existing_ids = _get_existing_ids()
    results: list[dict[str, Any]] = []
    try:
        # Try common Gmail sent folder names
        for folder in ("[Gmail]/Sent Mail", "Sent", "INBOX.Sent"):
            sent = _fetch_folder(imap, folder, f"(SINCE {since})",
                                 limit=30, existing_ids=existing_ids, mark_seen=False)
            if sent:
                for em in sent:
                    em["_category"] = "sent_mail"
                results.extend(sent)
                break

        # Bounce notifications arrive in INBOX from mailer-daemon
        bounces = _fetch_folder(
            imap, "INBOX", f"(FROM \"mailer-daemon\" SINCE {since})",
            limit=10, existing_ids=existing_ids, mark_seen=False
        )
        for em in bounces:
            em["_category"] = "bounce_detected"
        results.extend(bounces)

        if results:
            log.info("Sent/bounce check: %d emails", len(results))
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return results
