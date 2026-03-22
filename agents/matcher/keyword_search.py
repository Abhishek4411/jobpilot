"""Compute keyword overlap score between resume skills and a job description."""

from typing import Any

import yaml
from pathlib import Path

from core.logger import get_logger

log = get_logger(__name__)

_skills_cache: list[str] | None = None  # cached to avoid YAML re-read on every job


def _get_resume_skills() -> list[str]:
    """Load all skills from resume.yaml as a flat list.

    Returns:
        List of all skill strings from the resume.
    """
    global _skills_cache
    if _skills_cache is not None:
        return _skills_cache
    path = Path("config/resume.yaml")
    if not path.exists():
        return []
    try:
        resume = yaml.safe_load(path.read_text(encoding="utf-8"))
        skills_data = resume.get("skills", {})
        skills: list[str] = []
        if isinstance(skills_data, dict):
            for v in skills_data.values():
                if isinstance(v, list):
                    skills.extend(str(s) for s in v)
        elif isinstance(skills_data, list):
            skills = [str(s) for s in skills_data]
        _skills_cache = skills
        return _skills_cache
    except Exception as e:
        log.error("Failed to load resume skills: %s", e)
        return []


def keyword_overlap(jd_text: str) -> float:
    """Compute the fraction of resume skills found in the job description.

    Args:
        jd_text: Job description text.

    Returns:
        Score between 0.0 and 1.0.
    """
    skills = _get_resume_skills()
    if not skills or not jd_text:
        return 0.0

    jd_lower = jd_text.lower()
    matches = sum(1 for s in skills if s.lower() in jd_lower)
    score = matches / len(skills)
    log.debug("Keyword overlap: %d/%d = %.2f", matches, len(skills), score)
    return round(min(score, 1.0), 4)
