"""Validate that all required resume fields are present and non-empty."""

from typing import Any

from core.logger import get_logger

log = get_logger(__name__)

REQUIRED_FIELDS: list[tuple[str, ...]] = [
    ("personal", "name"),
    ("personal", "email"),
    ("personal", "phone"),
    ("personal", "total_experience"),
]


def validate(resume: dict[str, Any]) -> list[str]:
    """Check that all required fields exist and are non-empty.

    Also verifies at least one skill and one experience entry are present.

    Args:
        resume: Parsed resume dictionary (from resume.yaml).

    Returns:
        List of missing field paths (e.g. ['personal.email', 'skills']).
        Empty list means the resume is valid.
    """
    missing: list[str] = []

    for field_path in REQUIRED_FIELDS:
        value = resume
        for key in field_path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if not value or (isinstance(value, str) and not value.strip()):
            missing.append(".".join(field_path))

    skills = resume.get("skills", {})
    has_skill = False
    if isinstance(skills, dict):
        has_skill = any(
            isinstance(v, list) and len(v) > 0
            for v in skills.values()
        )
    elif isinstance(skills, list) and len(skills) > 0:
        has_skill = True
    if not has_skill:
        missing.append("skills (at least one skill required)")

    experience = resume.get("experience", [])
    if not isinstance(experience, list) or len(experience) == 0:
        missing.append("experience (at least one entry required)")

    if missing:
        log.warning("Resume validation failed. Missing: %s", missing)
    else:
        log.info("Resume validation passed")

    return missing
