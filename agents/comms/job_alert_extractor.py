"""Extract structured job listings from job alert emails using LLM.

Strategy: ONE LLM call per email extracts ALL job listings (title, company,
location, work type, apply type, URL) from the raw email body/HTML.
No individual page fetches — fast, token-efficient, works with any portal.

Supports: LinkedIn, Naukri, Indeed, Instahyre, Foundit, and any other portal.
"""

import json
import re
from typing import Any

from core.db import get_conn, log_audit
from core.llm_router import call
from core.logger import get_logger

log = get_logger(__name__)

# ── URL extraction (regex, zero tokens) ──────────────────────────────────────
_JOB_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:"
    r"linkedin\.com/(?:comm/)?jobs/view/|"
    r"naukri\.com/job-listings-|"
    r"indeed\.com/(?:rc/clk|viewjob)|"
    r"instahyre\.com/jobs/|"
    r"foundit\.in/srp/|"
    r"glassdoor\.com/job-listing/|"
    r"wellfound\.com/jobs/"
    r")[^\s\"<>]{5,250}",
    re.IGNORECASE,
)
_ANY_URL_RE = re.compile(r"https?://[^\s\"<>']{25,300}", re.IGNORECASE)
_SKIP_URL_RE = re.compile(
    r"\.(png|jpg|gif|svg|css|woff|ico)|unsubscribe|manage.alert|"
    r"linkedin\.com/comm/email|tracking|click\.email|open\.mail",
    re.IGNORECASE,
)

_MAX_LEADS = 25

# ── LLM extraction system prompt ─────────────────────────────────────────────
_EXTRACT_SYSTEM = """You extract job listings from job alert emails (LinkedIn, Naukri, Indeed, etc.).
Return ONLY a valid JSON array. No markdown, no explanation, just the array.
Format: [{"title":"...","company":"...","location":"...","work_type":"hybrid/onsite/remote/not_specified","apply_type":"easy_apply/actively_recruiting/regular","url":"full URL or empty string"}]
Rules:
- Include ALL job listings found in the email, even if partial info
- work_type: hybrid/onsite/remote/not_specified
- apply_type: easy_apply (LinkedIn Easy Apply), actively_recruiting, regular
- url: the direct job URL if visible in text, otherwise empty string
- If no jobs found: return []"""


def _extract_urls_from_email(plain: str, html: str) -> list[str]:
    """Extract job-related URLs from plain text or HTML (no LLM)."""
    source = html if html else plain
    job_urls = _JOB_URL_RE.findall(source)
    if not job_urls:
        all_urls = _ANY_URL_RE.findall(source)
        job_urls = [u for u in all_urls if not _SKIP_URL_RE.search(u)]

    seen: set[str] = set()
    result: list[str] = []
    for u in job_urls:
        u = u.rstrip(".,);\"'")
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result[:_MAX_LEADS]


def _prepare_email_text(plain: str, html: str) -> str:
    """Prepare email text for LLM — use HTML structure when available."""
    if html:
        text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.S | re.I)
        # Extract href values (contain job URLs not visible in text)
        hrefs = re.findall(r'href=["\']([^"\']{10,300})["\']', text, re.I)
        job_hrefs = [h for h in hrefs if _JOB_URL_RE.search(h)]
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s{2,}", " ", text).strip()[:3500]
        if job_hrefs:
            text += "\n\nJob URLs in email links:\n" + "\n".join(job_hrefs[:20])
        return text
    return plain[:3500]


def _extract_jobs_llm(plain: str, html: str) -> list[dict[str, Any]]:
    """Send email content to LLM, get structured job list back in one call."""
    text = _prepare_email_text(plain, html)
    if not text.strip():
        return []

    response = call(
        f"Extract all job listings from this email:\n\n{text}",
        system=_EXTRACT_SYSTEM,
        task_type="fast_classification",
        max_tokens=1500,
    )
    if not response:
        return []

    json_match = re.search(r"\[.*\]", response, re.S)
    if not json_match:
        return []
    try:
        jobs = json.loads(json_match.group(0))
        return [j for j in jobs if isinstance(j, dict) and (j.get("title") or j.get("company"))]
    except (json.JSONDecodeError, Exception) as e:
        log.debug("LLM JSON parse failed: %s | raw: %s", e, response[:200])
        return []


def process_job_alert(email_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract all job listings from a job alert email.

    Uses LLM for structured extraction (one call per email).
    Falls back to URL-only stubs if LLM extraction fails.

    Args:
        email_data: Email dict with keys: subject, sender, body_preview, html_body.

    Returns:
        List of job dicts: {title, company, location, work_type, apply_type, url, source_subject}
    """
    subject = email_data.get("subject", "")
    plain = email_data.get("body_preview", "")
    html = email_data.get("html_body", "")

    if not plain and not html:
        log.info("Job alert '%s': no body content", subject[:50])
        return []

    # Step 1: Extract URLs via regex (free, instant)
    urls = _extract_urls_from_email(plain, html)

    # Step 2: LLM structured extraction (one API call per email)
    jobs = _extract_jobs_llm(plain, html)

    if jobs:
        # Enrich: assign extracted URLs to jobs that have no URL
        orphan_urls = [u for u in urls
                       if not any(u[:60] in j.get("url", "") for j in jobs)]
        for i, job in enumerate(jobs):
            if not job.get("url") and i < len(orphan_urls):
                job["url"] = orphan_urls[i]
        log.info("Job alert '%s': LLM extracted %d jobs", subject[:50], len(jobs))
    elif urls:
        # Only create stubs for definite job portal URLs (not tracking/redirect links)
        job_urls = [u for u in urls if _JOB_URL_RE.search(u)]
        if not job_urls:
            log.info("Job alert '%s': LLM found no jobs, no valid job portal URLs", subject[:50])
            return []
        jobs = [{"title": "", "company": "", "location": "", "url": u,
                 "work_type": "", "apply_type": ""} for u in job_urls]
        log.info("Job alert '%s': LLM failed, %d portal URL stubs", subject[:50], len(jobs))
    else:
        log.info("Job alert '%s': no jobs found", subject[:50])
        return []

    for job in jobs:
        job["source_subject"] = subject

    return jobs[:_MAX_LEADS]


def _avoid_companies() -> set[str]:
    """Return lowercased company names to suppress from leads (best-effort)."""
    try:
        from agents.memory.job_context import load_strategy
        return {str(c).lower() for c in load_strategy().get("avoid_companies", [])}
    except Exception:
        return set()


def store_leads_as_jobs(leads: list[dict[str, Any]]) -> int:
    """Insert extracted job leads into jobs table (dedup by URL).

    Skips leads from companies in the avoid_companies blacklist.

    Returns:
        Number of new jobs inserted.
    """
    if not leads:
        return 0
    conn = get_conn()
    inserted = 0
    blacklist = _avoid_companies()
    for lead in leads:
        if blacklist and lead.get("company", "").lower() in blacklist:
            log.info("Skipping blacklisted company: %s", lead.get("company"))
            continue
        url = lead.get("url", "").strip()
        title = lead.get("title", "").strip()
        if not url and not title:
            continue
        dedup_url = url or f"alert-no-url-{abs(hash(title + lead.get('company', '')))}"
        notes = (
            f"work_type={lead.get('work_type','')}, "
            f"apply_type={lead.get('apply_type','')}, "
            f"from_email={lead.get('source_subject','')[:60]}"
        )
        try:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (title,company,location,url,source,status,notes) "
                "VALUES (?,?,?,?,?,?,?)",
                (title[:200], lead.get("company", "")[:200],
                 lead.get("location", "")[:200],
                 dedup_url[:500], "email_alert", "discovered", notes),
            )
            inserted += conn.execute("SELECT changes()").fetchone()[0]
        except Exception as e:
            log.warning("Failed to insert lead '%s': %s", title[:40], e)
    conn.commit()
    if inserted:
        log_audit("comms", "job_leads_stored", f"count={inserted}")
    return inserted
