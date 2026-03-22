"""Detect whether a resume file has changed since last parse using SHA256 hash."""

import hashlib
from pathlib import Path

from core.db import get_conn, get_resume_version
from core.logger import get_logger

log = get_logger(__name__)


def file_hash(path: str | Path) -> str:
    """Compute SHA256 hash of a file's contents.

    Args:
        path: Path to the file to hash.

    Returns:
        Lowercase hex SHA256 digest.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def has_changed(path: str | Path) -> bool:
    """Return True if the file differs from the last stored version.

    Args:
        path: Path to the resume file.

    Returns:
        True if file is new or hash differs from stored version.
    """
    try:
        current_hash = file_hash(path)
        last_version = get_resume_version()
        if last_version is None:
            log.info("No previous version found, treating as changed")
            return True
        changed = last_version["file_hash"] != current_hash
        if changed:
            log.info("Resume file changed (hash mismatch)")
        else:
            log.debug("Resume file unchanged")
        return changed
    except Exception as e:
        log.error("hash check failed: %s", e)
        return True


def store_version(path: str | Path, parsed_yaml: str, missing_fields: list[str], source: str) -> None:
    """Save a new resume version record to the database.

    Args:
        path: Path to the uploaded resume file.
        parsed_yaml: YAML string of the parsed resume.
        missing_fields: List of field paths that were empty.
        source: How the version was created ('upload', 'editor', 'auto_parse').
    """
    import json
    conn = get_conn()
    conn.execute(
        "INSERT INTO resume_versions (file_name,file_hash,parsed_yaml,missing_fields,source) VALUES (?,?,?,?,?)",
        (Path(path).name, file_hash(path), parsed_yaml, json.dumps(missing_fields), source),
    )
    conn.commit()
    log.info("Stored resume version for %s", Path(path).name)
