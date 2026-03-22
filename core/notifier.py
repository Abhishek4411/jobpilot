"""Desktop, email, and Telegram notifications for important job search events."""

import os
import smtplib
from email.mime.text import MIMEText

from core.logger import get_logger

log = get_logger(__name__)


def _desktop(title: str, message: str) -> None:
    """Send a desktop notification using plyer (best-effort)."""
    try:
        from plyer import notification
        notification.notify(title=title, message=message, timeout=8)
    except Exception as e:
        log.debug("Desktop notification failed: %s", e)


def _email(title: str, message: str) -> None:
    """Send an email notification via Gmail SMTP (best-effort)."""
    gmail = os.environ.get("GMAIL_ADDRESS", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    to_addr = os.environ.get("NOTIFICATION_EMAIL", gmail)
    if not gmail or not password:
        log.debug("Email notification skipped: no credentials configured")
        return
    try:
        msg = MIMEText(message)
        msg["Subject"] = f"[JobPilot] {title}"
        msg["From"] = gmail
        msg["To"] = to_addr
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail, password)
            server.sendmail(gmail, [to_addr], msg.as_string())
        log.info("Email notification sent: %s", title)
    except Exception as e:
        log.error("Email notification failed: %s", e)


def _telegram(title: str, message: str) -> None:
    """Send a Telegram notification (best-effort)."""
    try:
        from agents.comms.telegram_notifier import send_message
        send_message(f"<b>{title}</b>\n{message}")
    except Exception as e:
        log.debug("Telegram notification failed: %s", e)


def notify(title: str, message: str, channel: str = "desktop") -> None:
    """Send a notification via the specified channel.

    Args:
        title: Short notification title.
        message: Body text of the notification.
        channel: 'desktop', 'email', 'telegram', or 'both'. Defaults to 'desktop'.
    """
    log.info("NOTIFY [%s]: %s", channel, title)
    if channel in ("desktop", "both"):
        _desktop(title, message)
    if channel in ("email", "both"):
        _email(title, message)
    if channel == "telegram":
        _telegram(title, message)
