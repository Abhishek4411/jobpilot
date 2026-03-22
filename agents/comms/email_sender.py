"""Send approved email replies via Gmail SMTP with resume attachment.

Recipient validation:
- Blocks noreply/donotreply/jobalert addresses (would bounce or go to void)
- Decodes Naukri relay addresses (format: name<base64domain>@naukri.com)
  e.g. piyusha.singhYXNjZW5kaW9uLmNvbQ==@naukri.com -> piyusha.singh@ascendion.com
- Only sends to real, reachable recruiter addresses
"""

import base64
import os
import re
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from core.db import get_conn, log_audit
from core.logger import get_logger

log = get_logger(__name__)

_RESUME_DIR = Path("data/resumes")

# Addresses that will always bounce or go to a void inbox
_SKIP_RE = re.compile(
    r"noreply|no-reply|donotreply|do-not-reply|do_not_reply|"
    r"jobalert|job-alert|jobs-noreply|jobmessenger|"
    r"notifications@|mailer-daemon|postmaster@|"
    r"@instahyre\.com|@alerts\.|@email\.|@communications\.|"
    r"glassdoor\.com|foundit|talenttitan|swiggy|zomato|sbi@|"
    r"hdfc|amazonpay|amazon\.in|newsletter|digest@|marketing@",
    re.IGNORECASE,
)


def _decode_naukri_relay(local_part: str) -> str | None:
    """Decode a Naukri relay address to the real recruiter email.

    Naukri format: firstname.lastname<base64(domain)>@naukri.com
    Example: piyusha.singhYXNjZW5kaW9uLmNvbQ== -> piyusha.singh@ascendion.com

    Args:
        local_part: The part before '@naukri.com'.

    Returns:
        Real email address string, or None if decoding fails.
    """
    # Try each position as a potential base64 start
    for i in range(2, len(local_part)):
        b64_part = local_part[i:]
        padding = (4 - len(b64_part) % 4) % 4
        try:
            domain = base64.b64decode(b64_part + "=" * padding).decode("utf-8", errors="strict")
            if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*\.[a-zA-Z]{2,6}$", domain):
                name_part = local_part[:i].rstrip(".")
                real_email = f"{name_part}@{domain}"
                log.info("Decoded Naukri relay: %s -> %s", local_part[:30], real_email)
                return real_email
        except Exception:
            continue
    return None


def _extract_sendable_address(sender_header: str) -> str | None:
    """Extract a validated, sendable email from a raw sender header.

    Args:
        sender_header: Full From header, e.g. 'Name <email@domain.com>'

    Returns:
        Clean email address to send to, or None if it should be skipped.
    """
    # Extract email from "Name <email>" format
    m = re.search(r"<([^>]+)>", sender_header)
    email_addr = m.group(1).strip() if m else sender_header.strip()

    # Block known noreply/void patterns first
    if _SKIP_RE.search(email_addr):
        log.warning("Skipping send — blocked sender pattern: %s", email_addr[:60])
        return None

    # Handle Naukri relay addresses — try to decode to real email
    if "@naukri.com" in email_addr.lower():
        local = email_addr.split("@")[0]
        real = _decode_naukri_relay(local)
        if real:
            return real
        log.warning("Skipping send — cannot decode Naukri relay: %s", email_addr[:60])
        return None

    # Basic format validation
    if not re.match(r"^[^@]+@[^@]+\.[^@]{2,}$", email_addr):
        log.warning("Skipping send — invalid email format: %s", email_addr[:60])
        return None

    return email_addr


def _find_resume_pdf() -> Path | None:
    """Return the most recently modified PDF in data/resumes/, or None."""
    if not _RESUME_DIR.exists():
        return None
    pdfs = sorted(_RESUME_DIR.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    return pdfs[0] if pdfs else None


def _send_smtp(to_addr: str, subject: str, body: str) -> bool:
    """Send a single email via Gmail SMTP with resume PDF attached.

    Args:
        to_addr: Validated recipient email address.
        subject: Email subject.
        body: Plain text body.

    Returns:
        True if sent successfully.
    """
    gmail = os.environ.get("GMAIL_ADDRESS", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not gmail or not app_password:
        log.error("Gmail SMTP credentials not set")
        return False
    try:
        msg = MIMEMultipart()
        msg["Subject"] = f"Re: {subject}" if not subject.startswith("Re:") else subject
        msg["From"] = gmail
        msg["To"] = to_addr
        msg.attach(MIMEText(body, "plain"))

        resume_path = _find_resume_pdf()
        if resume_path:
            with open(resume_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=resume_path.name)
            part["Content-Disposition"] = f'attachment; filename="{resume_path.name}"'
            msg.attach(part)
            log.info("Resume attached: %s", resume_path.name)
        else:
            log.warning("No resume PDF in %s — sending without attachment", _RESUME_DIR)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail, app_password)
            server.sendmail(gmail, [to_addr], msg.as_string())
        return True
    except Exception as e:
        log.error("SMTP send failed to %s: %s", to_addr, e)
        return False


def send_approved_replies() -> int:
    """Send all approved-but-not-yet-sent email replies.

    Validates each recipient address before sending.
    Naukri relay addresses are decoded to real emails.
    Noreply/void addresses are skipped and marked sent=2 (skipped).

    Returns:
        Number of emails actually sent.
    """
    conn = get_conn()
    pending = conn.execute(
        "SELECT * FROM emails WHERE reply_approved=1 AND reply_sent=0 AND reply_draft IS NOT NULL"
    ).fetchall()

    sent_count = 0
    for row in pending:
        email_row = dict(row)
        raw_sender = email_row["sender"]
        subject = email_row["subject"]
        body = email_row["reply_draft"]
        email_id = email_row["id"]

        to_addr = _extract_sendable_address(raw_sender)
        if not to_addr:
            # Mark as skipped (reply_sent=2) so it doesn't keep appearing
            conn.execute("UPDATE emails SET reply_sent=2 WHERE id=?", (email_id,))
            conn.commit()
            log.warning("Skipped send for '%s' — no valid recipient", subject[:50])
            log_audit("comms", "email_skipped",
                      f"reason=invalid_recipient, sender={raw_sender[:60]}, subject={subject[:40]}")
            continue

        success = _send_smtp(to_addr, subject, body)
        if success:
            conn.execute("UPDATE emails SET reply_sent=1 WHERE id=?", (email_id,))
            conn.commit()
            log_audit("comms", "email_sent", f"to={to_addr}, subject={subject[:50]}")
            sent_count += 1
            log.info("Reply sent to %s", to_addr)
        else:
            log.error("Failed to send reply to %s", to_addr)

    if sent_count:
        log.info("Sent %d approved email replies", sent_count)
    return sent_count
