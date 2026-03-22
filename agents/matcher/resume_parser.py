"""Generate and cache a sentence embedding for the current resume."""

from pathlib import Path
from typing import Any

import numpy as np

from core.config_loader import load_config
from core.logger import get_logger

log = get_logger(__name__)

EMBEDDING_PATH = Path("data/resume_embedding.npy")
HASH_PATH = Path("data/resume_hash.txt")


def _flatten_resume(resume: dict[str, Any]) -> str:
    """Convert resume dict to a single text block for embedding.

    Args:
        resume: Parsed resume dictionary.

    Returns:
        Single string containing all resume content.
    """
    parts: list[str] = []

    personal = resume.get("personal", {})
    parts.append(personal.get("current_title", ""))
    parts.append(resume.get("profile_summary", ""))

    skills = resume.get("skills", {})
    if isinstance(skills, dict):
        for skill_list in skills.values():
            if isinstance(skill_list, list):
                parts.extend(skill_list)
    elif isinstance(skills, list):
        parts.extend(skills)

    for exp in resume.get("experience", []):
        parts.append(exp.get("title", ""))
        parts.append(exp.get("company", ""))
        parts.extend(exp.get("highlights", []))

    for proj in resume.get("projects", []):
        parts.append(proj.get("name", ""))
        parts.extend(proj.get("highlights", []))

    parts.extend(resume.get("certifications", []))
    return " ".join(str(p) for p in parts if p)


def get_resume_embedding(model=None) -> np.ndarray:
    """Return the cached resume embedding, regenerating if resume has changed.

    Returns:
        1D numpy array of the resume embedding.
    """
    from agents.cv_manager.diff_detector import file_hash

    resume_path = Path("config/resume.yaml")
    current_hash = file_hash(resume_path) if resume_path.exists() else ""
    stored_hash = HASH_PATH.read_text().strip() if HASH_PATH.exists() else ""

    if EMBEDDING_PATH.exists() and current_hash == stored_hash:
        log.debug("Using cached resume embedding")
        return np.load(EMBEDDING_PATH)

    log.info("Generating new resume embedding")
    try:
        from sentence_transformers import SentenceTransformer
        import yaml

        resume = yaml.safe_load(resume_path.read_text(encoding="utf-8"))
        text = _flatten_resume(resume)
        if model is None:
            model = SentenceTransformer("all-MiniLM-L6-v2")
        embedding = model.encode(text)

        EMBEDDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        np.save(EMBEDDING_PATH, embedding)
        HASH_PATH.write_text(current_hash)
        log.info("Resume embedding saved (%d dims)", len(embedding))
        return embedding
    except Exception as e:
        log.error("Failed to generate resume embedding: %s", e)
        return np.zeros(384)
