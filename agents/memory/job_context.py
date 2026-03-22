"""Rolling markdown memory for job search context.

Files in data/memory/:
  companies.md  - target and avoid companies (permanent by default)
  recruiters.md - past recruiter interactions (7-day TTL unless [keep])
  decisions.md  - reasons jobs were skipped or approved (7-day TTL)
  notes.md      - freeform notes (7-day TTL)

Format per entry:  <!-- DATE: 2026-03-19 --> [keep?]
  content
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from core.logger import get_logger

log = get_logger(__name__)

_MEMORY_DIR = Path("data/memory")
_STRATEGY_PATH = Path("config/user_strategy.yaml")
_FILES = ("companies", "recruiters", "decisions", "notes")
_DATE_RE = re.compile(r"<!--\s*DATE:\s*(\d{4}-\d{2}-\d{2})\s*-->")


def _path(category: str) -> Path:
    return _MEMORY_DIR / f"{category}.md"


def _ensure_dirs() -> None:
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def get_context(category: str, max_chars: int = 600) -> str:
    """Return recent memory entries as a short string for LLM injection."""
    p = _path(category)
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8").strip()
        return text[-max_chars:] if len(text) > max_chars else text
    except Exception as e:
        log.debug("Memory read error (%s): %s", category, e)
        return ""


def save_entry(category: str, key: str, text: str, permanent: bool = False) -> None:
    """Append a dated entry to a memory file.

    Args:
        category: One of 'companies', 'recruiters', 'decisions', 'notes'.
        key: Short label (e.g. company name or email subject).
        text: Content to store.
        permanent: If True, marks entry with [keep] to survive pruning.
    """
    _ensure_dirs()
    p = _path(category)
    keep_tag = " [keep]" if permanent else ""
    entry = f"\n<!-- DATE: {date.today().isoformat()} -->{keep_tag}\n**{key}**: {text}\n"
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        log.warning("Memory write error (%s): %s", category, e)


def prune_old(days: int = 7) -> None:
    """Remove entries older than `days` from non-permanent categories."""
    _ensure_dirs()
    cutoff = date.today() - timedelta(days=days)
    for cat in ("recruiters", "decisions", "notes"):
        p = _path(cat)
        if not p.exists():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
            kept: list[str] = []
            skip_block = False
            for line in lines:
                m = _DATE_RE.search(line)
                if m:
                    entry_date = date.fromisoformat(m.group(1))
                    is_old = entry_date < cutoff
                    has_keep = "[keep]" in line
                    skip_block = is_old and not has_keep
                if not skip_block:
                    kept.append(line)
            p.write_text("".join(kept), encoding="utf-8")
        except Exception as e:
            log.warning("Memory prune error (%s): %s", cat, e)


def load_strategy() -> dict[str, Any]:
    """Return parsed user_strategy.yaml as a dict."""
    if not _STRATEGY_PATH.exists():
        return {}
    try:
        return yaml.safe_load(_STRATEGY_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:
        log.warning("Strategy load error: %s", e)
        return {}
