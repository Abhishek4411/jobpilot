"""Score job listings against the resume using embeddings and keyword overlap.

Critical fix (Session 9): The old code loaded SentenceTransformer inside a per-job
function causing 2000+ model instantiations per scout cycle. This spiked memory,
caused silent failures (cosine fell back to 0.0), and nothing ever matched.
Model is now loaded ONCE per batch in score_and_store().
"""

from typing import Any

import numpy as np

from agents.matcher.keyword_search import keyword_overlap
from agents.matcher.resume_parser import get_resume_embedding
from core.config_loader import load_config
from core.db import insert_job, update_job_status, log_audit
from core.logger import get_logger

log = get_logger(__name__)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _load_deal_breakers() -> list[str]:
    """Return deal-breaker phrases from user_strategy.yaml (best-effort)."""
    try:
        from agents.memory.job_context import load_strategy
        return [str(d).lower() for d in load_strategy().get("deal_breakers", [])]
    except Exception:
        return []


def score_and_store(jobs: list[dict[str, Any]]) -> int:
    """Score a list of jobs, store them in DB, and mark matches above threshold.

    Loads SentenceTransformer ONCE per batch, not once per job.
    Jobs matching deal-breaker phrases are auto-suppressed (scored, not matched).

    Args:
        jobs: List of job dicts from the scout agent.

    Returns:
        Number of jobs that met the matching threshold.
    """
    cfg = load_config()
    threshold = cfg.get("settings", {}).get("matching", {}).get("threshold", 0.45)
    deal_breakers = _load_deal_breakers()

    model = None
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        log.info("SentenceTransformer loaded for scoring %d jobs", len(jobs))
    except Exception as e:
        log.warning("SentenceTransformer unavailable, using keyword-only scoring: %s", e)

    resume_embedding = get_resume_embedding(model=model)
    resume_is_zeros = not np.any(resume_embedding)
    if resume_is_zeros:
        log.warning("Resume embedding is zero vector — cosine scores will be 0. "
                    "Check sentence-transformers install and config/resume.yaml")

    matched_count = 0
    scores_sample: list[float] = []

    for job in jobs:
        job_id = insert_job(job)
        if not job_id:
            continue

        jd_text = job.get("description", "")
        if not jd_text:
            update_job_status(job_id, "scored", 0.0)
            continue

        cosine = 0.0
        if model and not resume_is_zeros:
            try:
                jd_embedding = model.encode(jd_text[:2000])
                cosine = _cosine_similarity(resume_embedding, jd_embedding)
            except Exception as e:
                log.warning("JD embedding failed for job_id=%d: %s", job_id, e)

        kw_score = keyword_overlap(jd_text)
        final = round(0.6 * cosine + 0.4 * kw_score, 4)
        scores_sample.append(final)

        jd_lower = jd_text.lower()
        deal_hit = next((d for d in deal_breakers if d in jd_lower), None)
        if deal_hit:
            log.info("Deal-breaker '%s' suppressed: %s @ %s", deal_hit, job.get("title"), job.get("company"))
            status = "scored"
        else:
            status = "matched" if final >= threshold else "scored"
        update_job_status(job_id, status, final)

        if status == "matched":
            matched_count += 1
            log.info("MATCH (%.2f): %s @ %s", final, job.get("title"), job.get("company"))

    if scores_sample:
        avg = round(sum(scores_sample) / len(scores_sample), 4)
        top = round(max(scores_sample), 4)
        log_audit("matcher", "scoring_complete",
                  f"jobs={len(jobs)}, matched={matched_count}, avg={avg}, top={top}, threshold={threshold}")
        log.info("Score summary: avg=%.4f top=%.4f threshold=%.2f matched=%d/%d",
                 avg, top, threshold, matched_count, len(scores_sample))
    else:
        log_audit("matcher", "scoring_complete", f"jobs={len(jobs)}, matched=0")

    return matched_count
