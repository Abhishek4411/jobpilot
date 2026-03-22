"""Filter out job listings already stored in the database."""

import hashlib
from typing import Any

from core.db import get_conn
from core.logger import get_logger

log = get_logger(__name__)


def _url_hash(url: str) -> str:
    """Compute a short hash of a URL for quick comparison.

    Args:
        url: Job listing URL.

    Returns:
        First 16 chars of SHA256 hex digest.
    """
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def deduplicate(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only jobs whose URLs are not already in the database.

    Args:
        jobs: Raw list of job dicts from scrapers.

    Returns:
        Filtered list containing only new, unseen jobs.
    """
    if not jobs:
        return []

    conn = get_conn()
    existing_urls = {
        row[0] for row in conn.execute("SELECT url FROM jobs").fetchall()
    }

    new_jobs = [j for j in jobs if j.get("url") and j["url"] not in existing_urls]

    dupes = len(jobs) - len(new_jobs)
    log.info("Deduplication: %d total, %d new, %d duplicates", len(jobs), len(new_jobs), dupes)
    return new_jobs
