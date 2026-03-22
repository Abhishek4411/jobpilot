"""Answer job application screening questions using qa_bank or LLM fallback."""

from pathlib import Path
from typing import Any

import yaml

from core.config_loader import load_config
from core.db import get_conn, log_audit
from core.llm_router import call
from core.logger import get_logger

log = get_logger(__name__)

SYSTEM_PROMPT_TEMPLATE = (
    "You are answering a job application question for {name}. "
    "Use ONLY the following resume data to answer. "
    "If the answer is not clearly in the resume, respond with exactly: "
    "'I would be happy to discuss this in detail during our conversation.' "
    "Never invent facts. Keep the answer under 50 words. Be direct and professional.\n\n"
    "RESUME DATA:\n{resume_text}"
)


def _load_resume_text() -> str:
    """Return a compact text representation of the current resume."""
    path = Path("config/resume.yaml")
    if not path.exists():
        return ""
    try:
        resume = yaml.safe_load(path.read_text(encoding="utf-8"))
        p = resume.get("personal", {})
        skills_data = resume.get("skills", {})
        skills = []
        if isinstance(skills_data, dict):
            for v in skills_data.values():
                if isinstance(v, list):
                    skills.extend(v)
        lines = [
            f"Name: {p.get('name', '')}",
            f"Title: {p.get('current_title', '')}",
            f"Experience: {p.get('total_experience', '')}",
            f"Location: {p.get('location', '')}",
            f"Skills: {', '.join(str(s) for s in skills[:30])}",
            f"Summary: {resume.get('profile_summary', '')[:300]}",
        ]
        return "\n".join(lines)
    except Exception as e:
        log.error("Failed to load resume for QA: %s", e)
        return ""


def answer_question(question_text: str) -> str:
    """Find the best answer for a screening question.

    Tries qa_bank patterns first. Falls back to LLM with resume context.
    Flags truly unknown questions for human review.

    Args:
        question_text: The screening question text.

    Returns:
        Answer string to submit in the application form.
    """
    cfg = load_config()
    qa_bank = cfg.get("qa_bank", {}).get("personal_answers", {})
    fallback = cfg.get("qa_bank", {}).get("fallback_answer", "")
    question_lower = question_text.lower()

    for _key, entry in qa_bank.items():
        patterns = entry.get("patterns", [])
        if any(p.lower() in question_lower for p in patterns):
            log.info("QA bank matched for: '%s...'", question_text[:40])
            return entry.get("answer", fallback)

    resume_name = cfg.get("resume", {}).get("personal", {}).get("name", "the candidate")
    resume_text = _load_resume_text()
    system = SYSTEM_PROMPT_TEMPLATE.format(name=resume_name, resume_text=resume_text)
    answer = call(question_text, system=system, task_type="question_answering", max_tokens=100)

    if not answer or "discuss this in detail" in answer.lower():
        _flag_for_review(question_text)
        return fallback

    log.info("LLM answered question: '%s...'", question_text[:40])
    return answer.strip()


def _flag_for_review(question_text: str) -> None:
    """Store an unanswerable question in user_inputs for human review."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO user_inputs (field_path, value, status) VALUES (?,?,?)",
        ("unanswered_question", question_text, "needs_review"),
    )
    conn.commit()
    log_audit("question_handler", "flagged_for_review", question_text[:100])
    log.warning("Question flagged for human review: %s", question_text[:80])
