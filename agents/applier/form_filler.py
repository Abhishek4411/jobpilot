"""Map form field labels to resume data for generic job application forms."""

from pathlib import Path
from typing import Any

import yaml

from agents.applier.question_handler import answer_question
from core.config_loader import load_config
from core.logger import get_logger

log = get_logger(__name__)

FIELD_MAP: dict[str, list[str]] = {
    "name": ["full name", "your name", "applicant name", "name"],
    "email": ["email", "e-mail", "email address"],
    "phone": ["phone", "mobile", "contact number", "telephone"],
    "location": ["location", "city", "current location", "where are you based"],
    "experience": ["years of experience", "total experience", "work experience"],
    "current_company": ["current company", "current employer", "present company"],
    "current_title": ["current role", "job title", "designation", "current designation"],
    "linkedin": ["linkedin", "linkedin url", "linkedin profile"],
}


def _get_resume() -> dict[str, Any]:
    """Load the current resume YAML."""
    path = Path("config/resume.yaml")
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def fill_field(label: str) -> str:
    """Return the appropriate resume value for a given form field label.

    Tries direct label matching first, then falls back to the QA handler.

    Args:
        label: The form field label text (e.g. 'Email Address').

    Returns:
        Value string to enter in the form field.
    """
    resume = _get_resume()
    personal = resume.get("personal", {})
    label_lower = label.lower().strip()

    for field_key, patterns in FIELD_MAP.items():
        if any(p in label_lower for p in patterns):
            mapping = {
                "name": personal.get("name", ""),
                "email": personal.get("email", ""),
                "phone": personal.get("phone", ""),
                "location": personal.get("location", ""),
                "experience": personal.get("total_experience", ""),
                "current_company": resume.get("experience", [{}])[0].get("company", "") if resume.get("experience") else "",
                "current_title": personal.get("current_title", ""),
                "linkedin": personal.get("linkedin", ""),
            }
            value = mapping.get(field_key, "")
            if value:
                log.debug("Field '%s' mapped to '%s'", label, value)
                return value

    log.info("No direct mapping for field '%s', using QA handler", label)
    return answer_question(label)
