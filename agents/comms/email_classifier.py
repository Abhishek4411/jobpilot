"""Classify incoming emails into job-related categories.

Rule priority (zero LLM tokens):
  1. job_alert  — LinkedIn/Indeed/Naukri digests (checked BEFORE noreply, so
                  jobs-noreply@linkedin.com is caught here, not in irrelevant)
  2. application_confirmation — "You applied for N jobs"
  3. irrelevant — banking, shopping, unrelated noreply
  4. None       — needs LLM classification

classify_batch() sends multiple emails in one LLM call to save tokens.
"""

import re
from core.llm_router import call
from core.logger import get_logger

log = get_logger(__name__)

CATEGORIES = [
    "job_opportunity", "interview_request", "rejection",
    "follow_up", "status_update",
    "job_alert",                # LinkedIn/Indeed/Naukri digests — extract job links inside
    "application_confirmation", # "You applied for N jobs" — update applied stats
    "irrelevant",
]

SYSTEM_PROMPT = (
    "You classify emails for a job seeker in India. Reply with ONLY one category word.\n"
    "Categories: " + ", ".join(CATEGORIES) + "\n\n"
    "job_opportunity = recruiter/HR/talent-acquisition/staffing firm reached out about a specific role, "
    "OR email describes a job opening with title+company+location, even if sender is unknown. "
    "Cold-outreach from recruiters about ANY role = job_opportunity. "
    "When in doubt between job_opportunity and irrelevant, choose job_opportunity.\n"
    "interview_request = email explicitly invites to interview, schedules a call/meeting for hiring\n"
    "rejection = application was declined or not moving forward\n"
    "follow_up = checking on application/CV status\n"
    "status_update = application received/acknowledged by ATS/portal\n"
    "job_alert = LinkedIn/Indeed/Naukri automated digest listing multiple job links\n"
    "application_confirmation = system email confirming N jobs were applied to\n"
    "irrelevant = banking/OTP/shopping/social-media/unrelated to jobs\n\n"
    "IMPORTANT: Emails mentioning 'opportunity', 'role', 'position', 'CTC', 'notice period', "
    "'joining', 'hiring' are almost always job_opportunity, NOT irrelevant."
)

# ── Job alert senders/subjects — checked FIRST (before noreply catch-all) ─────
_JOB_ALERT_SENDER_RE = re.compile(
    r"jobs-noreply@linkedin\.com|jobalert|job-alert|job-alerts|"
    r"jobs@indeed\.com|jobmessenger|@instahyre\.com|"
    r"foundit\.in|foundit\.sg",
    re.IGNORECASE,
)
_JOB_ALERT_SUBJECT_RE = re.compile(
    r"jobs for you|job alert|new jobs matching|recommended jobs|"
    r"explore new jobs|be an early applicant|hot job|\d+ new jobs|"
    r"\d+ jobs matching|similar jobs",
    re.IGNORECASE,
)
# ── LinkedIn non-job prompts — override job_alert classification ───────────────
_PROFILE_PROMPT_RE = re.compile(
    r"is your (location|title|headline|profile) still|"
    r"have you moved (into|to)|"
    r"update your (location|profile|title|headline)|"
    r"still at |are you still working|did you (start|leave|move)|"
    r"congratulate|you have a new connection|people are looking at your|"
    r"views on your profile|who viewed your",
    re.IGNORECASE,
)

# ── Application confirmation — track manually applied jobs ────────────────────
_APP_CONFIRM_RE = re.compile(
    r"you applied|applied for\s+\d+|application submitted|"
    r"applied successfully|your application.*received|application.*confirmed",
    re.IGNORECASE,
)

# ── Recruiter cold-outreach — caught before irrelevant sender check ────────────
# Matches emails that describe a specific job role/opportunity regardless of sender
_RECRUITER_SUBJECT_RE = re.compile(
    r"\b(opportunity|opening|position|vacancy|role|hiring|recruitment|"
    r"job offer|we are hiring|shortlisted|interested in your profile|"
    r"your (cv|resume|profile)|ctc|notice period|lpa|joining)\b",
    re.IGNORECASE,
)
_RECRUITER_BODY_RE = re.compile(
    r"\b(recruiter|talent acquisition|hr team|staffing|placement|"
    r"we came across your profile|on behalf of|our client|"
    r"ctc|lpa|notice period|joining date|current (ctc|salary))\b",
    re.IGNORECASE,
)

# ── Truly irrelevant — after job alert check so noreply doesn't catch LinkedIn ─
_IRRELEVANT_SENDER_RE = re.compile(
    r"noreply|no-reply|donotreply|do-not-reply|notifications@|notification@|"
    r"automated@|mailer-daemon|postmaster@|"
    r"hdfc|icici|axis.*bank|sbi\.co\.in|sbicard|swiggy|zomato|amazon\.in|"
    r"amazonpay|myntra|flipkart|irctc|makemytrip|goibibo|eazydiner|"
    r"facebook\.com|instagram\.com|chess\.com|glassdoor\.com|"
    r"alerts@|newsletter|marketing@|promo@|digest@|"
    r"groww|zerodha|paytm|phonepe|nobroker|lenskart|mail\.uipath",
    re.IGNORECASE,
)
_IRRELEVANT_SUBJECT_RE = re.compile(
    r"track.*package|order.*confirmed|payment.*received|bank.*alert|"
    r"otp\b|transaction|statement|friend request|happy holi|smart banking|"
    r"rewards.*waiting|a splash of.*banking|undeliverable|delivery.*fail",
    re.IGNORECASE,
)

# ── Interview invites — caught separately so they get the right category ───────
_INTERVIEW_SUBJECT_RE = re.compile(
    r"\b(interview|phone screen|video call|introductory call|hiring.*call|"
    r"screening call|technical.*round|assessment.*round)\b",
    re.IGNORECASE,
)
_INTERVIEW_BODY_RE = re.compile(
    r"\b(schedule.*interview|interview.*schedule|join.*interview|"
    r"interview.*link|meet.*interview|zoom.*meeting|teams.*meeting|"
    r"google.*meet|your.*interview.*is|we.*like.*to.*interview)\b",
    re.IGNORECASE,
)


_NON_HUMAN_SENDER_RE = re.compile(
    r"noreply|no-reply|donotreply|notifications@|automated@|mailer-daemon|postmaster@|"
    r"bounce|daemon|delivery@|alerts@|newsletter|digest@|info@alerts\.",
    re.IGNORECASE,
)


def _rule_based_filter(sender: str, subject: str, body: str = "") -> str | None:
    """Return category from rules alone, or None if LLM classification needed."""
    # Profile update prompts from LinkedIn — not job alerts, not actionable
    if _PROFILE_PROMPT_RE.search(subject):
        return "irrelevant"
    if _JOB_ALERT_SENDER_RE.search(sender) or _JOB_ALERT_SUBJECT_RE.search(subject):
        return "job_alert"
    if _APP_CONFIRM_RE.search(subject):
        return "application_confirmation"
    # Skip all remaining recruiter/interview checks for automated senders
    if _NON_HUMAN_SENDER_RE.search(sender):
        pass  # fall through to irrelevant check below
    else:
        # Interview invites from real humans — strongest signal, check first
        if _INTERVIEW_SUBJECT_RE.search(subject) or _INTERVIEW_BODY_RE.search(body):
            return "interview_request"
        # Recruiter cold-outreach: subject or body mentions role/CTC/opportunity
        if _RECRUITER_SUBJECT_RE.search(subject) or _RECRUITER_BODY_RE.search(body):
            return "job_opportunity"
    if _IRRELEVANT_SENDER_RE.search(sender) or _IRRELEVANT_SUBJECT_RE.search(subject):
        return "irrelevant"
    return None


def classify_email(subject: str, body_preview: str, sender: str = "") -> str:
    """Classify a single email. Prefer classify_batch() for multiple emails."""
    rule = _rule_based_filter(sender, subject, body_preview[:300])
    if rule:
        log.info("Email pre-filtered: %s | sender=%s | subject=%s",
                 rule, sender[:40], subject[:40])
        return rule
    prompt = f"From: {sender[:80]}\nSubject: {subject}\nBody: {body_preview[:400]}\n\nCategory:"
    result = call(prompt, system=SYSTEM_PROMPT, task_type="fast_classification", max_tokens=20)
    result = result.strip().lower().replace(".", "").split()[0] if result.strip() else ""
    for cat in CATEGORIES:
        if cat in result:
            log.info("Email classified: %s | Subject: %s", cat, subject[:50])
            return cat
    log.info("Email unclear ('%s'), defaulting irrelevant", result[:30])
    return "irrelevant"


def classify_batch(emails: list[dict]) -> list[str]:
    """Classify multiple emails with one LLM call (rule-based first, LLM for rest).

    Args:
        emails: List of email dicts with keys: subject, sender, body_preview.

    Returns:
        Category strings in same order as input.
    """
    results = ["irrelevant"] * len(emails)
    to_classify: list[tuple[int, dict]] = []

    for i, em in enumerate(emails):
        rule = _rule_based_filter(
            em.get("sender", ""), em.get("subject", ""), em.get("body_preview", "")[:300]
        )
        if rule:
            results[i] = rule
        else:
            to_classify.append((i, em))

    if not to_classify:
        return results

    lines = [
        f"[{seq}] From:{em.get('sender','')[:60]} "
        f"Subject:{em.get('subject','')[:70]} Body:{em.get('body_preview','')[:250]}"
        for seq, (_, em) in enumerate(to_classify, start=1)
    ]
    response = call(
        "\n".join(lines) + f"\n\nClassify each [1]-[{len(to_classify)}]: reply 1:category 2:category ...",
        system=SYSTEM_PROMPT, task_type="fast_classification",
        max_tokens=len(to_classify) * 20,
    )
    if response:
        for num_str, cat in re.findall(r"(\d+):([a-z_]+)", response.lower()):
            seq = int(num_str) - 1
            if 0 <= seq < len(to_classify) and cat in CATEGORIES:
                results[to_classify[seq][0]] = cat
    return results
