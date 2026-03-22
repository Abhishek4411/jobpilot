"""Extract interview details from interview_request emails and store in DB.

Auto-detects: date/time, interview type, meeting link, job role/company.
Generates LLM prep topics for each interview.
Sends Telegram alert immediately when a new interview is scheduled.
"""

import json
import re
from typing import Any

from core.db import get_conn, log_audit
from core.llm_router import call
from core.logger import get_logger

log = get_logger(__name__)

_EXTRACT_SYSTEM = """Extract interview details from this email. Return ONLY valid JSON (no markdown).
Format:
{"company":"","role":"","location":"","interview_type":"telephonic/video/face_to_face/unknown",
"date":"YYYY-MM-DD or empty","time":"HH:MM or empty","timezone":"IST/UTC/etc or empty",
"meeting_link":"full URL or empty","meeting_id":"or empty","meeting_password":"or empty",
"jd_snippet":"brief role description from email, max 200 chars"}
Rules: interview_type must be one of: telephonic, video, face_to_face, unknown"""

_PREP_SYSTEM = """You prepare job candidates for interviews. List 6-8 specific topics to prepare.
Format: numbered list, one topic per line. Be specific to the role and company. No em dashes."""


def extract_interview_details(email_data: dict[str, Any]) -> dict[str, Any] | None:
    """Use LLM to extract structured interview details from an email."""
    subject = email_data.get("subject", "")
    body = email_data.get("body_preview", "")
    sender = email_data.get("sender", "")

    prompt = (
        f"From: {sender}\nSubject: {subject}\n"
        f"Body: {body[:1500]}\n\nExtract interview details:"
    )
    response = call(prompt, system=_EXTRACT_SYSTEM, task_type="fast_classification", max_tokens=300)
    if not response:
        return None

    json_match = re.search(r"\{.*\}", response, re.S)
    if not json_match:
        return None
    try:
        details = json.loads(json_match.group(0))
        return details if isinstance(details, dict) else None
    except (json.JSONDecodeError, Exception) as e:
        log.debug("Interview JSON parse failed: %s | raw=%s", e, response[:100])
        return None


def generate_prep_topics(role: str, company: str, jd_snippet: str) -> str:
    """Generate interview preparation topics using quality LLM."""
    prompt = (
        f"Role: {role or 'the position'}\n"
        f"Company: {company or 'the company'}\n"
        f"Job context: {jd_snippet or 'not available'}\n\n"
        "List specific topics to prepare for this interview:"
    )
    result = call(prompt, system=_PREP_SYSTEM, task_type="quality_drafting", max_tokens=300)
    return result.strip() if result else ""


def store_interview(details: dict[str, Any], email_id: int | None = None,
                    job_id: int | None = None) -> int | None:
    """Insert interview record into DB. Returns new interview ID."""
    # Build scheduled_at datetime string
    date_str = details.get("date", "")
    time_str = details.get("time", "")
    if date_str and time_str:
        scheduled_at = f"{date_str} {time_str}"
    elif date_str:
        scheduled_at = date_str
    else:
        scheduled_at = None

    # Generate prep topics (quality LLM call)
    topics = generate_prep_topics(
        details.get("role", ""), details.get("company", ""), details.get("jd_snippet", "")
    )

    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO interviews "
            "(email_id,job_id,company,role,location,interview_type,scheduled_at,"
            "meeting_link,meeting_id,meeting_password,jd_snippet,topics_to_prepare) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (email_id, job_id,
             details.get("company", "")[:200], details.get("role", "")[:200],
             details.get("location", "")[:200],
             details.get("interview_type", "unknown"),
             scheduled_at,
             details.get("meeting_link", "")[:500],
             details.get("meeting_id", "")[:100],
             details.get("meeting_password", "")[:100],
             details.get("jd_snippet", "")[:500],
             topics[:1000] if topics else ""),
        )
        conn.commit()
        interview_id = cur.lastrowid
        log.info("Interview stored: id=%d, %s at %s", interview_id,
                 details.get("company", "?"), scheduled_at)
        log_audit("comms", "interview_scheduled",
                  f"company={details.get('company','?')}, date={scheduled_at}")

        # Auto-create job + application record so stats and applied count reflect reality
        if not job_id:
            job_id = _ensure_job_record(conn, details)
        if job_id:
            conn.execute(
                "INSERT OR IGNORE INTO applications (job_id, applied_via, status) VALUES (?,?,?)",
                (job_id, "recruiter_email", "interview"),
            )
            conn.commit()
            # Update the interview row with the job_id we just found/created
            conn.execute("UPDATE interviews SET job_id=? WHERE id=?", (job_id, interview_id))
            conn.commit()

        _send_telegram_alert(interview_id, details, scheduled_at, topics)
        return interview_id
    except Exception as e:
        log.warning("Failed to store interview: %s", e)
        return None


def _ensure_job_record(conn, details: dict) -> int | None:
    """Find or create a job record for an interview that came via email."""
    company = (details.get("company") or "").strip()
    role = (details.get("role") or "").strip()
    if not company and not role:
        return None
    # Try to find an existing job record for this company/role
    existing = conn.execute(
        "SELECT id FROM jobs WHERE company LIKE ? AND title LIKE ? LIMIT 1",
        (f"%{company}%", f"%{role}%"),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE jobs SET status='interview_scheduled' WHERE id=?", (existing["id"],)
        )
        conn.commit()
        return existing["id"]
    # Create a new job record (source=recruiter_email, status=interview_scheduled)
    cur = conn.execute(
        "INSERT INTO jobs (title, company, source, status, match_score) VALUES (?,?,?,?,?)",
        (role or "Unknown Role", company or "Unknown Company",
         "recruiter_email", "interview_scheduled", 1.0),
    )
    conn.commit()
    log.info("Auto-created job record for interview: %s at %s", role, company)
    return cur.lastrowid


def _send_telegram_alert(interview_id: int, details: dict, scheduled_at: str | None,
                         topics: str) -> None:
    """Push immediate Telegram notification for a new interview."""
    try:
        from agents.comms.telegram_notifier import send_message
        company = details.get("company", "Unknown")
        role = details.get("role", "Role")
        itype = details.get("interview_type", "interview").replace("_", " ").title()
        link = details.get("meeting_link", "")
        text = (
            f"Interview Scheduled\n"
            f"Company: {company}\nRole: {role}\nType: {itype}\n"
            f"When: {scheduled_at or 'date not specified'}\n"
            + (f"Link: {link}\n" if link else "")
            + f"\nView details: http://localhost:5000/calendar"
        )
        send_message(text)
    except Exception as e:
        log.debug("Interview Telegram alert failed: %s", e)
