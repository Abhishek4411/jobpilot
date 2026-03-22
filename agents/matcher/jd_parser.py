"""Parse job description text to extract structured requirements via LLM."""

from typing import Any

import yaml

from core.llm_router import call
from core.logger import get_logger

log = get_logger(__name__)

SYSTEM_PROMPT = (
    "You are a job description parser. Extract structured data from the JD. "
    "Return ONLY valid YAML with these exact keys. If a field is not found, use null or empty list. "
    "No markdown fences, no explanation."
)

YAML_TEMPLATE = """
required_skills: []
preferred_skills: []
experience_years: null
salary_range: ""
location: ""
remote_option: false
""".strip()


def parse_jd(jd_text: str) -> dict[str, Any]:
    """Extract structured requirements from a job description.

    Args:
        jd_text: Raw job description text (will be truncated to 2000 chars).

    Returns:
        Dict with keys: required_skills, preferred_skills, experience_years,
        salary_range, location, remote_option.
    """
    truncated = jd_text[:2000]
    prompt = (
        f"Parse this job description and return YAML with this structure:\n{YAML_TEMPLATE}\n\n"
        f"JOB DESCRIPTION:\n{truncated}"
    )

    raw = call(prompt, system=SYSTEM_PROMPT, task_type="jd_analysis", max_tokens=512)
    if not raw:
        return {"required_skills": [], "preferred_skills": [], "experience_years": None,
                "salary_range": "", "location": "", "remote_option": False}

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        data = yaml.safe_load(cleaned)
        if isinstance(data, dict):
            return data
    except yaml.YAMLError as e:
        log.warning("JD YAML parse error: %s", e)

    return {"required_skills": [], "preferred_skills": [], "experience_years": None,
            "salary_range": "", "location": "", "remote_option": False}
