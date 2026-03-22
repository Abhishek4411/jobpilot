"""Watch data/resumes/ for new or modified files and trigger the CV parse pipeline."""

import threading
import time
from pathlib import Path
from typing import Callable

from core.logger import get_logger

log = get_logger(__name__)

WATCH_DIR = Path("data/resumes")
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}
# Files to always ignore even if they have a supported extension
SKIP_FILENAMES = {"readme.txt", "readme.md", "readme.pdf", ".gitkeep", "sample.txt"}


def _parse_pipeline(file_path: Path) -> None:
    """Run the full CV parse pipeline for a single file.

    Args:
        file_path: Path to the resume file that was created or modified.
    """
    from agents.cv_manager import diff_detector, validator
    from agents.cv_manager.structurer import structure_resume, save_resume
    import yaml

    ext = file_path.suffix.lower()
    log.info("CV Watcher triggered for: %s", file_path.name)

    if not diff_detector.has_changed(file_path):
        log.info("File unchanged, skipping parse")
        return

    if ext == ".pdf":
        from agents.cv_manager.parser_pdf import extract_text
    elif ext == ".docx":
        from agents.cv_manager.parser_docx import extract_text
    elif ext == ".txt":
        from agents.cv_manager.parser_txt import extract_text
    else:
        log.warning("Unsupported file type: %s", ext)
        return

    raw_text = extract_text(file_path)
    if not raw_text:
        log.error("No text extracted from %s", file_path.name)
        return

    data = structure_resume(raw_text)
    if not data:
        log.error("Resume structuring failed for %s", file_path.name)
        return

    missing = validator.validate(data)
    save_resume(data)

    yaml_str = yaml.dump(data, default_flow_style=False, allow_unicode=True)
    diff_detector.store_version(file_path, yaml_str, missing, source="auto_parse")

    from core.db import log_audit
    log_audit("cv_manager", "resume_parsed", f"file={file_path.name}, missing={missing}")
    log.info("CV parse complete for %s. Missing fields: %s", file_path.name, missing)


class _ResumeHandler:
    """Minimal file event handler for the resumes directory."""

    def dispatch(self, event_type: str, path: str) -> None:
        """Handle a file system event.

        Args:
            event_type: 'created' or 'modified'.
            path: Path to the affected file.
        """
        p = Path(path)
        if p.suffix.lower() in SUPPORTED_EXTENSIONS and p.name.lower() not in SKIP_FILENAMES:
            threading.Thread(target=_parse_pipeline, args=(p,), daemon=True).start()


def start_watcher() -> None:
    """Start watching data/resumes/ using polling (watchdog not required).

    Runs in a daemon thread that polls every 10 seconds.
    """
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    seen: dict[str, float] = {}

    def _poll() -> None:
        handler = _ResumeHandler()
        while True:
            try:
                for f in WATCH_DIR.iterdir():
                    if f.suffix.lower() not in SUPPORTED_EXTENSIONS:
                        continue
                    if f.name.lower() in SKIP_FILENAMES:
                        continue
                    mtime = f.stat().st_mtime
                    if f.name not in seen:
                        seen[f.name] = mtime
                        handler.dispatch("created", str(f))
                    elif seen[f.name] != mtime:
                        seen[f.name] = mtime
                        handler.dispatch("modified", str(f))
            except Exception as e:
                log.error("Watcher poll error: %s", e)
            time.sleep(10)

    t = threading.Thread(target=_poll, daemon=True, name="cv-watcher")
    t.start()
    log.info("CV Watcher started on %s", WATCH_DIR)
