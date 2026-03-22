"""Extract text from plain text resume files."""

from pathlib import Path

from core.logger import get_logger

log = get_logger(__name__)


def extract_text(path: str | Path) -> str:
    """Read a plain text file and return its contents.

    Args:
        path: Absolute or relative path to the text file.

    Returns:
        File contents as a string, or empty string on failure.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace").strip()
        log.info("TXT extracted: %d chars from %s", len(text), path)
        return text
    except Exception as e:
        log.error("TXT extraction failed for %s: %s", path, e)
        return ""
