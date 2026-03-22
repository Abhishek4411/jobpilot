"""Poll Telegram for user commands and act on them in a background thread.

Supported commands:
  /status              - Today's discovery/apply/approval stats
  /jobs                - Top 5 recently matched jobs
  /approve <email_id>  - Approve a queued reply draft
  /skip <email_id>     - Skip (reject) a queued reply draft
"""

import os
import threading
import time

import requests

from core.logger import get_logger

log = get_logger(__name__)
_API = "https://api.telegram.org/bot{token}/{method}"
_last_update_id: int = 0
_running: bool = False


def _token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _chat_id() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "")


def _reply(text: str) -> None:
    from agents.comms.telegram_notifier import send_message
    send_message(text)


def _cmd_status() -> None:
    from core.db import get_conn
    c = get_conn()
    today = c.execute("SELECT COUNT(*) FROM jobs WHERE date(discovered_at)=date('now')").fetchone()[0]
    matched = c.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='matched' AND date(discovered_at)=date('now')"
    ).fetchone()[0]
    applied = c.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='applied' AND date(applied_at)=date('now')"
    ).fetchone()[0]
    pending = c.execute(
        "SELECT COUNT(*) FROM emails WHERE reply_approved IS NULL AND reply_draft IS NOT NULL "
        "AND category IN ('interview_request','job_opportunity','follow_up')"
    ).fetchone()[0]
    _reply(
        f"<b>Status</b>\nDiscovered today: {today}\nMatched: {matched}\n"
        f"Applied today: {applied}\nPending email approvals: {pending}"
    )


def _cmd_jobs() -> None:
    from core.db import get_conn
    rows = get_conn().execute(
        "SELECT title, company, match_score FROM jobs WHERE status='matched' "
        "ORDER BY discovered_at DESC LIMIT 5"
    ).fetchall()
    if not rows:
        _reply("No matched jobs yet today.")
        return
    lines = ["<b>Top 5 Matched Jobs</b>"]
    for r in rows:
        pct = round((r["match_score"] or 0) * 100)
        lines.append(f"{pct}% - {r['title']} at {r['company']}")
    _reply("\n".join(lines))


def _cmd_approve(arg: str) -> None:
    try:
        eid = int(arg.strip())
    except ValueError:
        _reply("Usage: /approve <numeric email id>")
        return
    from core.db import get_conn
    get_conn().execute("UPDATE emails SET reply_approved=1 WHERE id=?", (eid,))
    get_conn().commit()
    _reply(f"Reply for email #{eid} approved.")


def _cmd_skip(arg: str) -> None:
    try:
        eid = int(arg.strip())
    except ValueError:
        _reply("Usage: /skip <numeric email id>")
        return
    from core.db import get_conn
    get_conn().execute("UPDATE emails SET reply_approved=0 WHERE id=?", (eid,))
    get_conn().commit()
    _reply(f"Reply for email #{eid} skipped.")


def _poll_once() -> None:
    global _last_update_id
    token = _token()
    if not token:
        return
    try:
        url = _API.format(token=token, method="getUpdates")
        resp = requests.get(url, params={"offset": _last_update_id + 1, "timeout": 8}, timeout=12)
        if not resp.ok:
            return
        for upd in resp.json().get("result", []):
            _last_update_id = upd["update_id"]
            msg = upd.get("message", {})
            text = (msg.get("text") or "").strip()
            if str(msg.get("chat", {}).get("id", "")) != _chat_id():
                continue
            if text == "/status":
                _cmd_status()
            elif text == "/jobs":
                _cmd_jobs()
            elif text.startswith("/approve "):
                _cmd_approve(text[9:])
            elif text.startswith("/skip "):
                _cmd_skip(text[6:])
    except Exception as e:
        log.debug("Telegram poll error: %s", e)


def _loop() -> None:
    log.info("Telegram listener started")
    while _running:
        _poll_once()
        time.sleep(10)
    log.info("Telegram listener stopped")


def start_listener() -> None:
    """Start background polling thread. No-op if token not configured."""
    global _running
    if not _token():
        log.info("TELEGRAM_BOT_TOKEN not set — listener disabled")
        return
    _running = True
    threading.Thread(target=_loop, daemon=True, name="telegram-listener").start()


def stop_listener() -> None:
    global _running
    _running = False
