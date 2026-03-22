"""Centralized logging with rotating file handler, console output, and PII sanitization."""

import logging
import os
import re
from logging.handlers import RotatingFileHandler

_loggers: dict[str, logging.Logger] = {}

# Patterns to redact from log messages before writing to file
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d\s\-]{7,14}\d)\b")
_SECRET_RE = re.compile(
    r"(?i)(password|token|api_key|secret|app_pass)\s*[=:]\s*\S+",
    re.IGNORECASE,
)


class _SanitizingFilter(logging.Filter):
    """Redact emails, phone numbers, and secret values from file log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        msg = _EMAIL_RE.sub("[email]", msg)
        msg = _PHONE_RE.sub("[phone]", msg)
        msg = _SECRET_RE.sub(r"\1=[secret]", msg)
        # Rebuild the record message (args already merged by getMessage)
        record.msg = msg
        record.args = None
        return True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, creating it on first call.

    Args:
        name: Module name, typically __name__ of the calling module.

    Returns:
        Configured Logger instance with file and console handlers.
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    if logger.handlers:
        _loggers[name] = logger
        return logger

    logger.setLevel(logging.INFO)

    log_dir = "data/logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "jobpilot.log")

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5_242_880, backupCount=3, encoding="utf-8"
    )
    # File captures INFO+ so log file matches what the terminal shows
    file_handler.setLevel(logging.INFO)
    file_handler.addFilter(_SanitizingFilter())

    console_handler = logging.StreamHandler()
    # Console stays at INFO so the terminal shows live activity
    console_handler.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    file_handler.setFormatter(fmt)
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    _loggers[name] = logger
    return logger
