"""Parse raw resume text into structured YAML using the LLM and save to resume.yaml.

Strategy: Two-pass extraction to prevent truncation.
Pass 1 (personal + skills + summary) — small focused call.
Pass 2 (experience + education + certifications + projects) — detailed call.
Results are merged, defaults filled, and saved.
"""

from pathlib import Path
from typing import Any

import yaml

from core.logger import get_logger
from core.llm_router import call

log = get_logger(__name__)

TEMPLATE_PATH = Path("config/resume_template.yaml")
RESUME_PATH = Path("config/resume.yaml")

_SYSTEM_PASS1 = (
    "You are a resume parser. Extract ONLY the following fields from the resume text and return valid YAML. "
    "No markdown fences. No explanation. Extract word-for-word, do not summarise.\n"
    "Fields:\n"
    "- personal: name, current_title, email, phone, location, total_experience, linkedin, github\n"
    "- profile_summary: (string) the full objective/summary paragraph\n"
    "- skills:\n"
    "    primary: [list of core domain skills]\n"
    "    ai_ml: [list of AI/ML skills]\n"
    "    programming: [list of programming languages/frameworks]\n"
    "    databases_tools: [list of databases and tools]\n"
    "    domain: [list of domain knowledge areas]\n"
    "Return YAML only."
)

_SYSTEM_PASS2 = (
    "You are a resume parser. Extract ONLY the following fields from the resume text and return valid YAML. "
    "No markdown fences. No explanation. Extract every entry completely.\n"
    "Fields:\n"
    "- experience: list of objects with keys: title, company, location, duration, type, highlights\n"
    "  (highlights is a list of bullet point strings)\n"
    "- education: list of objects with keys: degree, institution, year, score\n"
    "- certifications: list of strings\n"
    "- projects: list of objects with keys: name, duration, highlights\n"
    "- languages: list of strings\n"
    "Return YAML only."
)


def _clean_yaml(raw: str) -> str:
    """Strip markdown fences from LLM output."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return s


def _call_and_parse(prompt: str, system: str, label: str) -> dict[str, Any]:
    """Call LLM, clean output, parse YAML. Returns empty dict on failure."""
    raw = call(prompt, system=system, task_type="resume_parsing", max_tokens=4000)
    if not raw:
        log.error("LLM returned empty for resume %s", label)
        return {}
    try:
        data = yaml.safe_load(_clean_yaml(raw))
        if isinstance(data, dict):
            return data
        log.error("Resume %s: LLM returned non-dict (%s)", label, type(data))
    except yaml.YAMLError as e:
        log.error("Resume %s YAML error: %s", label, e)
    return {}


def structure_resume(raw_text: str) -> dict[str, Any] | None:
    """Two-pass LLM extraction of resume data.

    Args:
        raw_text: Plain text extracted from the resume file.

    Returns:
        Merged resume dict, or None if both passes failed completely.
    """
    full_text = raw_text.strip()

    # Pass 1: personal/summary/skills — only the first 4000 chars (header + skills section)
    # Skills and personal info are always near the top of the resume
    pass1 = _call_and_parse(
        f"RESUME TEXT (first section only — personal info and skills):\n{full_text[:4000]}\n\n"
        f"Extract ONLY: personal info, profile summary, and the skills lists "
        f"(primary/ai_ml/programming/databases_tools/domain). "
        f"Do NOT include job descriptions or experience bullets as skills.",
        _SYSTEM_PASS1, "pass1-personal+skills"
    )

    # Brief pause between passes to avoid back-to-back rate limit on large inputs
    import time as _time
    _time.sleep(8)

    # Pass 2: experience, education, certifications, projects — full text needed
    pass2 = _call_and_parse(
        f"RESUME TEXT:\n{full_text[:12000]}\n\nExtract experience history, education, certifications, projects, and languages:",
        _SYSTEM_PASS2, "pass2-experience+education"
    )

    if not pass1 and not pass2:
        log.error("Both resume parsing passes failed — no data extracted")
        return None

    # Merge: pass1 base, pass2 adds work history
    merged: dict[str, Any] = {}
    merged.update(pass1)
    merged.update(pass2)

    # Ensure all top-level keys exist
    merged.setdefault("personal", {})
    merged.setdefault("profile_summary", "")
    merged.setdefault("skills", {})
    merged.setdefault("experience", [])
    merged.setdefault("education", [])
    merged.setdefault("certifications", [])
    merged.setdefault("projects", [])
    merged.setdefault("languages", [])

    # Ensure skills sub-keys exist
    if not isinstance(merged["skills"], dict):
        merged["skills"] = {}
    for sk in ("primary", "ai_ml", "programming", "databases_tools", "domain"):
        merged["skills"].setdefault(sk, [])

    log.info(
        "Resume structured OK | exp_entries=%d | skills=%s",
        len(merged.get("experience", [])),
        {k: len(v) for k, v in merged.get("skills", {}).items() if v},
    )
    return merged


def save_resume(data: dict[str, Any]) -> bool:
    """Write structured resume data to config/resume.yaml.

    Args:
        data: Structured resume dictionary.

    Returns:
        True if saved successfully.
    """
    try:
        RESUME_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RESUME_PATH, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True,
                      sort_keys=False, width=120)
        log.info("Resume saved to %s", RESUME_PATH)
        return True
    except Exception as e:
        log.error("Failed to save resume.yaml: %s", e)
        return False
