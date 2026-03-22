"""Send push notifications to Telegram via Bot API.

Uses raw requests — no python-telegram-bot dependency needed.
Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment.
"""

import os

import requests

from core.logger import get_logger

log = get_logger(__name__)
_API = "https://api.telegram.org/bot{token}/{method}"


def _token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _chat_id() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "")


def send_message(text: str) -> bool:
    """Send a plain HTML-formatted message. Returns True on success."""
    token, chat_id = _token(), _chat_id()
    if not token or not chat_id:
        log.debug("Telegram not configured, skipping")
        return False
    try:
        url = _API.format(token=token, method="sendMessage")
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
        if not resp.ok:
            log.warning("Telegram send failed: %s", resp.text[:120])
        return resp.ok
    except Exception as e:
        log.warning("Telegram error: %s", e)
        return False


def send_job_alert(job: dict) -> bool:
    """Send a formatted job card with match score and dashboard link."""
    score_pct = round((job.get("match_score") or 0) * 100)
    text = (
        f"<b>New Job Match ({score_pct}%)</b>\n"
        f"<b>{job.get('title', 'Unknown Role')}</b> at {job.get('company', '?')}\n"
        f"Location: {job.get('location', 'Not specified')}\n"
        f"Source: {job.get('source', '?')}\n"
        f"<a href=\"http://localhost:5000/jobs\">View on Dashboard</a>"
    )
    return send_message(text)


def send_recruiter_alert(email_row: dict, draft_preview: str = "") -> bool:
    """Notify about a recruiter email that has a draft ready for approval."""
    preview = draft_preview[:200] if draft_preview else "(no draft)"
    cat = email_row.get("category", "").replace("_", " ").title()
    text = (
        f"<b>Recruiter Email: {cat}</b>\n"
        f"From: {email_row.get('sender', '?')}\n"
        f"Subject: {email_row.get('subject', '?')[:80]}\n"
        f"Draft: {preview}\n"
        f"<a href=\"http://localhost:5000/approvals\">Approve on Dashboard</a>\n"
        f"Or reply: /approve {email_row.get('id', '?')}"
    )
    return send_message(text)


def send_daily_brief(stats: dict) -> bool:
    """Push today's job search summary at the daily briefing time."""
    text = (
        f"<b>JobPilot Daily Brief</b>\n"
        f"Discovered: {stats.get('discovered', 0)}\n"
        f"Matched: {stats.get('matched', 0)}\n"
        f"Applied: {stats.get('applied', 0)}\n"
        f"Pending approvals: {stats.get('pending_approvals', 0)}\n"
        f"New emails: {stats.get('new_emails', 0)}"
    )
    return send_message(text)


def send_error_alert(agent: str, error: str) -> bool:
    """Push a crash or error notification."""
    text = f"<b>JobPilot Error</b>\nAgent: {agent}\n{str(error)[:300]}"
    return send_message(text)
