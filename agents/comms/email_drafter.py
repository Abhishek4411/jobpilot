"""Draft professional email replies using the LLM with resume context.

Category handling:
- job_alert          -> extract job links (no draft reply)
- application_confirmation -> log applied count (no draft reply)
- sent_mail          -> track manually sent applications (no draft reply)
- bounce_detected    -> log bad email address warning (no draft reply)
- interview_request / job_opportunity / follow_up -> draft LLM reply for approval
"""

import re
from pathlib import Path
from typing import Any

import yaml

from core.db import get_conn, log_audit
from core.llm_router import call, get_resume_summary
from core.logger import get_logger

log = get_logger(__name__)
_DRAFT_CATEGORIES = {"interview_request", "job_opportunity", "follow_up"}
_PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s-]?)?\(?\d{3,5}\)?[\s.-]?\d{3,5}[\s.-]?\d{3,5}")

SYSTEM_PROMPT = (
    "You are drafting a professional reply on behalf of {name}.\n\n"
    "STRICT RULES:\n"
    "- Never use em dashes. Use commas or periods instead.\n"
    "- Never use: delve, tapestry, leverage, synergy, spearhead, cutting-edge\n"
    "- Never start with: I hope this email finds you well\n"
    "- Keep under 80 words. Be warm, professional, and concise.\n"
    "- All facts must come from the resume data below.\n"
    "- Do not invent information.\n\nRESUME DATA:\n{resume_summary}"
)


def _get_resume_summary() -> tuple[str, str]:
    path = Path("config/resume.yaml")
    if not path.exists():
        return ("the candidate", "")
    try:
        resume = yaml.safe_load(path.read_text(encoding="utf-8"))
        p = resume.get("personal", {})
        skills_data = resume.get("skills", {})
        skills = []
        if isinstance(skills_data, dict):
            for v in skills_data.values():
                if isinstance(v, list):
                    skills.extend(v)
        summary = (
            f"Name: {p.get('name', '')}\nTitle: {p.get('current_title', '')}\n"
            f"Experience: {p.get('total_experience', '')}\nLocation: {p.get('location', '')}\n"
            f"Email: {p.get('email', '')}\nPhone: {p.get('phone', '')}\n"
            f"Skills: {', '.join(str(s) for s in skills[:25])}\n"
            f"Summary: {resume.get('profile_summary', '')[:250]}"
        )
        return (p.get("name", "the candidate"), summary)
    except Exception:
        return ("the candidate", "")


def _memory_context() -> str:
    """Return recruiter/company memory + strategy email context for LLM injection."""
    try:
        from agents.memory.job_context import get_context, load_strategy
        parts = [
            load_strategy().get("email_context", ""),
            get_context("recruiters", max_chars=300),
            get_context("companies", max_chars=200),
        ]
        return "\n".join(p for p in parts if p)
    except Exception:
        return ""


def _save_recruiter_contact(email_data: dict[str, Any], category: str) -> None:
    """Extract and save recruiter name, email, phone to memory on first contact."""
    try:
        from agents.memory.job_context import save_entry
        sender = email_data.get("sender", "")
        subject = email_data.get("subject", "")
        body = email_data.get("body_preview", "")
        if not sender:
            return
        # Parse "Name <email>" or plain email
        name_match = re.match(r"^([^<]+)<([^>]+)>", sender.strip())
        if name_match:
            recruiter_name = name_match.group(1).strip().strip('"')
            recruiter_email = name_match.group(2).strip()
        else:
            recruiter_name = ""
            recruiter_email = sender.strip()
        # Extract phone from body (first match only)
        phone_match = _PHONE_RE.search(body or "")
        phone = phone_match.group(0).strip() if phone_match else ""
        contact_line = (
            f"Email: {recruiter_email}"
            + (f" | Name: {recruiter_name}" if recruiter_name else "")
            + (f" | Phone: {phone}" if phone else "")
            + f" | Type: {category} | Subject: {subject[:60]}"
        )
        save_entry("recruiters", recruiter_name or recruiter_email, contact_line, permanent=True)
        log.info("Recruiter contact saved: %s", recruiter_email[:50])
    except Exception as e:
        log.debug("Recruiter contact save error: %s", e)


def draft_reply(email_data: dict[str, Any]) -> str | None:
    cached = get_resume_summary()
    if cached:
        name = cached.split("Name:")[1].split(" Title:")[0].strip() if "Name:" in cached else "the candidate"
        resume_summary = cached
    else:
        name, resume_summary = _get_resume_summary()
    mem = _memory_context()
    resume_with_mem = f"{resume_summary}\n\nCONTEXT:\n{mem}" if mem else resume_summary
    system = SYSTEM_PROMPT.format(name=name, resume_summary=resume_with_mem)
    prompt = (
        f"Email received:\nFrom: {email_data.get('sender', '')}\n"
        f"Subject: {email_data.get('subject', '')}\n"
        f"Body: {email_data.get('body_preview', '')[:800]}\n\nWrite a professional reply:"
    )
    draft = call(prompt, system=system, task_type="quality_drafting", max_tokens=200)
    if not draft:
        log.warning("LLM returned empty draft for: %s", email_data.get("subject", "")[:50])
        return None
    log.info("Draft created for: %s", email_data.get("subject", "")[:50])
    return draft.strip()


def _handle_job_alert(email_data: dict[str, Any]) -> int:
    try:
        from agents.comms.job_alert_extractor import process_job_alert, store_leads_as_jobs
        job_leads = process_job_alert(email_data)
        stored = store_leads_as_jobs(job_leads) if job_leads else 0
        log.info("Job alert '%s': extracted %d, stored %d new",
                 email_data.get("subject", "")[:50], len(job_leads or []), stored)
        return stored
    except Exception as e:
        log.warning("Job alert extraction failed: %s", e)
        return 0


def _handle_app_confirmation(email_data: dict[str, Any]) -> None:
    subject = email_data.get("subject", "")
    body = email_data.get("body_preview", "")
    m = re.search(r"applied.*?(\d+)\s+job|(\d+)\s+(?:application|job).*applied",
                  subject + " " + body, re.IGNORECASE)
    count = int(m.group(1) or m.group(2)) if m else 1
    log_audit("comms", "application_confirmed", f"count={count}, subject={subject[:60]}")
    log.info("Application confirmed: %d job(s) | %s", count, subject[:50])


def _handle_sent_mail(email_data: dict[str, Any]) -> str:
    """Extract job context (role, company) from a sent email using LLM."""
    subject = email_data.get("subject", "")
    body = email_data.get("body_preview", "")
    to_addr = email_data.get("to", "")
    prompt = (
        f"To: {to_addr}\nSubject: {subject}\nBody: {body[:500]}\n\n"
        "Extract from this sent job-related email:\n"
        "role: [job title or unknown]\ncompany: [company name or unknown]\n"
        "type: [application/interest/follow_up/other]\n"
        "Reply with only those 3 lines."
    )
    result = call(prompt, system="Extract job context from sent email. Be concise.",
                  task_type="fast_classification", max_tokens=60)
    context = result.strip() if result else ""
    log.info("Sent mail context: %s | to=%s", context.replace("\n", " | ")[:80], to_addr[:40])
    return context


def process_and_store(email_data: dict[str, Any]) -> None:
    from agents.comms.email_classifier import classify_email

    subject = email_data.get("subject", "")
    sender = email_data.get("sender", "")
    body = email_data.get("body_preview", "")

    # Use pre-computed category (from classify_batch) to skip redundant LLM call
    category = email_data.get("_category") or classify_email(subject, body, sender=sender)

    draft = None
    leads = 0

    if category == "job_alert":
        leads = _handle_job_alert(email_data)
    elif category == "application_confirmation":
        _handle_app_confirmation(email_data)
    elif category == "sent_mail":
        # Extract job context so agent understands what was sent; store in DB for tracking
        draft = _handle_sent_mail(email_data)
    elif category == "bounce_detected":
        log_audit("comms", "email_bounced", f"subject={subject[:60]}")
        log.warning("Bounced email detected: %s", subject[:60])
        # Fall through to INSERT so bounce is stored in DB (prevents re-reading every cycle)
    elif category in _DRAFT_CATEGORIES:
        draft = draft_reply({**email_data, "category": category})
        # Save recruiter contact info (name, email, phone) to memory
        _save_recruiter_contact(email_data, category)
        # Auto-extract and store interview details if this is an interview invite
        if category == "interview_request":
            try:
                from agents.comms.interview_extractor import (
                    extract_interview_details, store_interview
                )
                details = extract_interview_details(email_data)
                if details:
                    # email_id not yet known — store() called after INSERT below
                    email_data["_interview_details"] = details
            except Exception as e:
                log.debug("Interview extraction error: %s", e)

    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO emails "
        "(message_id,subject,sender,body_preview,category,reply_draft,leads_found,"
        "received_at,processed_at) VALUES (?,?,?,?,?,?,?,?,datetime('now'))",
        (email_data.get("message_id", ""), subject, sender,
         body[:2000], category, draft, leads, email_data.get("received_at")),
    )
    conn.commit()
    log_audit("comms", "email_processed", f"category={category}, subject={subject[:50]}")

    # Store interview details now that we have the email_id
    if email_data.get("_interview_details"):
        try:
            from agents.comms.interview_extractor import store_interview
            row = conn.execute(
                "SELECT id FROM emails WHERE message_id=?",
                (email_data.get("message_id", ""),)
            ).fetchone()
            email_id = row["id"] if row else None
            store_interview(email_data["_interview_details"], email_id=email_id)
        except Exception as e:
            log.debug("Interview store error: %s", e)
