"""Task scheduler: coordinates all agents with APScheduler and a FIFO browser queue."""

import queue
import random
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.config_loader import load_config
from core.db import log_audit
from core.logger import get_logger

log = get_logger(__name__)

browser_queue: queue.Queue = queue.Queue(maxsize=1)
_scheduler: BackgroundScheduler | None = None
_catchup_done = threading.Event()  # prevents double-run of startup email catchup


def _refresh_audio_background() -> None:
    """Regenerate audio briefing in a daemon thread (non-blocking)."""
    from core.config_loader import load_config
    from agents.comms.audio_briefer import generate_briefing
    name = load_config().get("resume", {}).get("personal", {}).get("name", "there").split()[0]
    generate_briefing(name)


def _notify_new_jobs(new_jobs: list) -> None:
    """Push Telegram alerts for high-scoring newly discovered jobs."""
    try:
        from core.config_loader import load_config
        from agents.comms.telegram_notifier import send_job_alert
        threshold = load_config().get("settings", {}).get("notify_threshold", 0.70)
        for job in new_jobs:
            score = job.get("match_score") or 0
            if score >= threshold:
                send_job_alert(job)
    except Exception as e:
        log.debug("Job Telegram notify error: %s", e)


def _scout_job() -> None:
    """Run scout + matcher cycle: discover jobs then score them.
    Always regenerates audio briefing at the end so it reflects fresh data.
    """
    from agents.scout.scraper import scrape_jobs
    from agents.scout.naukri_scraper import scrape_naukri
    from agents.scout.deduplicator import deduplicate
    from agents.matcher.scorer import score_and_store

    log.info("Scout cycle starting")
    log_audit("orchestrator", "scout_start", "")
    all_jobs = scrape_jobs() + scrape_naukri()
    new_jobs = deduplicate(all_jobs)
    if new_jobs:
        matched = score_and_store(new_jobs)
        log_audit("orchestrator", "scout_complete", f"new={len(new_jobs)}, matched={matched}")
        _notify_new_jobs(new_jobs)
    else:
        log.info("Scout: no new jobs found")

    # Regenerate audio after every scout so briefing always reflects latest data
    threading.Thread(target=_refresh_audio_background, daemon=True).start()


def _notify_recruiter_emails(emails: list, categories: list) -> None:
    """Push Telegram alerts for interview requests and job opportunities."""
    try:
        from agents.comms.telegram_notifier import send_recruiter_alert
        from core.db import get_conn
        alert_cats = {"interview_request", "job_opportunity"}
        for em, cat in zip(emails, categories):
            if cat not in alert_cats:
                continue
            # Look up the draft we just stored
            row = get_conn().execute(
                "SELECT id, sender, subject, category, reply_draft FROM emails "
                "WHERE message_id=? LIMIT 1",
                (em.get("message_id", ""),),
            ).fetchone()
            if row:
                send_recruiter_alert(dict(row), row["reply_draft"] or "")
    except Exception as e:
        log.debug("Recruiter Telegram notify error: %s", e)


def _email_job() -> None:
    """Read, batch-classify, and process new emails (inbox + sent + spam + bounces)."""
    from agents.comms.email_reader import fetch_unread_emails, fetch_sent_emails, fetch_spam_emails
    from agents.comms.email_classifier import classify_batch
    from agents.comms.email_drafter import process_and_store
    from agents.comms.email_sender import send_approved_replies

    # Regular inbox scan (UNSEEN, last 7 days)
    emails = fetch_unread_emails()
    if emails:
        # ONE batch LLM call classifies all emails (saves tokens vs per-email calls)
        categories = classify_batch(emails)
        for em, cat in zip(emails, categories):
            em["_category"] = cat
            process_and_store(em)
        _notify_recruiter_emails(emails, categories)

    # Spam scan: catch recruiter emails misclassified by Gmail
    for em in fetch_spam_emails():
        try:
            process_and_store(em)
        except Exception as e:
            log.error("process_and_store(spam) error: %s | subject=%s", e, em.get("subject","")[:50])

    # Sent mail scan + bounce detection: track outbound applications
    for em in fetch_sent_emails():
        try:
            process_and_store(em)
        except Exception as e:
            log.error("process_and_store(sent/bounce) error: %s | subject=%s", e, em.get("subject","")[:50])

    send_approved_replies()


def _email_catchup() -> None:
    """Startup catchup: scan ALL emails from last 7 days (inbox + sent + spam) once."""
    if _catchup_done.is_set():
        log.debug("Email catchup already completed, skipping duplicate run")
        return
    _catchup_done.set()
    from agents.comms.email_reader import (
        fetch_all_recent_emails, fetch_sent_emails, fetch_spam_emails
    )
    from agents.comms.email_classifier import classify_batch
    from agents.comms.email_drafter import process_and_store

    # Inbox (all seen + unseen from last 7 days)
    emails = fetch_all_recent_emails()
    if emails:
        log.info("Email catchup: classifying %d inbox emails", len(emails))
        categories = classify_batch(emails)
        for em, cat in zip(emails, categories):
            em["_category"] = cat
            process_and_store(em)
        log_audit("orchestrator", "email_catchup_complete", f"processed={len(emails)}")

    # Sent mail catchup: track outbound interest emails user sent manually
    for em in fetch_sent_emails():
        process_and_store(em)

    # Spam catchup: pick up recruiter emails that landed in spam
    for em in fetch_spam_emails():
        process_and_store(em)


def _cv_update_job() -> None:
    """Apply one subtle Naukri CV update (runs in browser queue)."""
    try:
        browser_queue.put_nowait("cv_update")
    except queue.Full:
        log.info("Browser busy, skipping CV update this cycle")
        return

    try:
        from agents.optimizer.naukri_cv_updater import run_cv_update
        run_cv_update()
    finally:
        browser_queue.get_nowait()


def _hourly_cleanup() -> None:
    """Trim log file and flush stale in-memory state."""
    from core.db import cleanup_log_file
    cleanup_log_file()


def _daily_purge() -> None:
    """Purge DB records, prune memory, and wipe LLM caches at midnight."""
    from core.db import cleanup_old_data
    cleanup_old_data(days=14)
    log_audit("orchestrator", "daily_purge", "Deleted records older than 14 days")
    try:
        from agents.memory.job_context import prune_old
        prune_old(days=7)
    except Exception as e:
        log.debug("Memory prune error: %s", e)
    try:
        from core.llm_router import clear_prompt_cache, invalidate_resume_cache
        clear_prompt_cache()
        invalidate_resume_cache()
    except Exception as e:
        log.debug("Cache clear error: %s", e)


def _interview_reminder() -> None:
    """Send Telegram reminder for interviews scheduled in the next 24 hours."""
    try:
        from core.db import get_conn
        from agents.comms.telegram_notifier import send_message
        conn = get_conn()
        upcoming = conn.execute(
            "SELECT company, role, interview_type, scheduled_at, meeting_link "
            "FROM interviews WHERE status='scheduled' "
            "AND scheduled_at BETWEEN datetime('now') AND datetime('now', '+24 hours') "
            "ORDER BY scheduled_at"
        ).fetchall()
        if not upcoming:
            return
        lines = ["Upcoming Interviews (next 24h):"]
        for row in upcoming:
            when = row["scheduled_at"] or "TBD"
            itype = (row["interview_type"] or "interview").replace("_", " ")
            lines.append(
                f"  {row['company']} - {row['role']} ({itype}) at {when}"
                + (f"\n  Link: {row['meeting_link']}" if row["meeting_link"] else "")
            )
        lines.append("\nView calendar: http://localhost:5000/calendar")
        send_message("\n".join(lines))
        log.info("Interview reminder sent for %d upcoming", len(upcoming))
    except Exception as e:
        log.debug("Interview reminder error: %s", e)


def _audio_job() -> None:
    """Generate the daily audio briefing and push a Telegram summary."""
    from core.config_loader import load_config
    name = load_config().get("resume", {}).get("personal", {}).get("name", "there").split()[0]
    from agents.comms.audio_briefer import generate_briefing
    path = generate_briefing(name)
    if path:
        log_audit("orchestrator", "audio_briefing_generated", path)
    # Push text summary to Telegram alongside audio
    try:
        from core.db import get_conn
        from agents.comms.telegram_notifier import send_daily_brief
        c = get_conn()
        stats = {
            "discovered": c.execute("SELECT COUNT(*) FROM jobs WHERE date(discovered_at)=date('now')").fetchone()[0],
            "matched": c.execute("SELECT COUNT(*) FROM jobs WHERE status='matched' AND date(discovered_at)=date('now')").fetchone()[0],
            "applied": c.execute("SELECT COUNT(*) FROM jobs WHERE status='applied' AND date(applied_at)=date('now')").fetchone()[0],
            "pending_approvals": c.execute("SELECT COUNT(*) FROM emails WHERE reply_approved IS NULL AND reply_draft IS NOT NULL").fetchone()[0],
            "new_emails": c.execute("SELECT COUNT(*) FROM emails WHERE date(received_at)=date('now')").fetchone()[0],
        }
        send_daily_brief(stats)
    except Exception as e:
        log.debug("Daily brief Telegram error: %s", e)


def _apply_approved_jobs() -> None:
    """Apply to all jobs that have been approved on the dashboard.

    Routing:
    - linkedin source → LinkedIn Easy Apply agent
    - naukri source   → Naukri Playwright apply agent
    - indeed / other  → open in browser for manual apply (these portals block automation)
    """
    from core.db import get_conn, update_job_status, log_audit as _audit
    import webbrowser, threading
    conn = get_conn()
    approved = conn.execute(
        "SELECT id, url, source, title, company FROM jobs WHERE status='approved' LIMIT 5"
    ).fetchall()

    for job in approved:
        source = (job["source"] or "").lower()
        url = job["url"] or ""

        # Indeed and other portals: automated apply is blocked — open in browser
        if "indeed" in source or "indeed" in url:
            update_job_status(job["id"], "apply_failed")
            _audit("applier", "indeed_manual_needed",
                   f"job_id={job['id']} url={url[:80]}")
            log.warning("Indeed job opened for manual apply: %s at %s", job["title"], job["company"])
            def _open(u=url):
                try:
                    webbrowser.open(u)
                except Exception:
                    pass
            threading.Thread(target=_open, daemon=True).start()
            continue

        try:
            browser_queue.put_nowait("apply")
        except queue.Full:
            log.info("Browser busy, deferring application for job_id=%d", job["id"])
            continue
        try:
            if "linkedin" in source or "linkedin" in url:
                from agents.applier.linkedin_apply import apply_linkedin
                apply_linkedin(job["id"], url)
            else:
                from agents.applier.naukri_apply import apply_naukri
                apply_naukri(job["id"], url)
        finally:
            try:
                browser_queue.get_nowait()
            except queue.Empty:
                pass


def start(cfg: dict | None = None) -> None:
    """Initialize and start the APScheduler with all jobs.

    Args:
        cfg: Optional config dict. Loads from disk if not provided.
    """
    global _scheduler
    if cfg is None:
        cfg = load_config()

    sched_cfg = cfg.get("settings", {}).get("scheduling", {})
    scout_interval = sched_cfg.get("scout_interval_minutes", 30)
    email_interval = sched_cfg.get("email_interval_minutes", 5)
    cv_min = sched_cfg.get("cv_update_min_minutes", 15)
    cv_max = sched_cfg.get("cv_update_max_minutes", 30)
    audio_hour = sched_cfg.get("audio_briefing_hour", 20)
    audio_min = sched_cfg.get("audio_briefing_minute", 0)

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_scout_job, IntervalTrigger(minutes=scout_interval), id="scout", max_instances=1)
    _scheduler.add_job(_email_job, IntervalTrigger(minutes=email_interval), id="email", max_instances=1)
    _scheduler.add_job(_apply_approved_jobs, IntervalTrigger(minutes=2), id="apply", max_instances=1)
    _scheduler.add_job(
        _cv_update_job,
        IntervalTrigger(minutes=random.randint(cv_min, cv_max)),
        id="cv_update", max_instances=1,
    )
    _scheduler.add_job(
        _audio_job, CronTrigger(hour=audio_hour, minute=audio_min), id="audio", max_instances=1
    )
    _scheduler.add_job(_hourly_cleanup, IntervalTrigger(hours=1), id="log_cleanup", max_instances=1)
    _scheduler.add_job(
        _daily_purge, CronTrigger(hour=0, minute=30), id="daily_purge", max_instances=1
    )
    _scheduler.add_job(
        _interview_reminder, CronTrigger(hour=8, minute=0), id="interview_reminder", max_instances=1
    )
    _scheduler.start()

    from agents.cv_manager.watcher import start_watcher
    start_watcher()

    from agents.comms.telegram_listener import start_listener
    start_listener()

    import atexit
    atexit.register(_shutdown_scheduler)
    log.info("Orchestrator started with %d scheduled jobs", len(_scheduler.get_jobs()))
    log_audit("orchestrator", "started", f"scout={scout_interval}min, email={email_interval}min")

    # Startup: regenerate audio + catch up on missed emails (last 7 days)
    def _startup_tasks() -> None:
        import time
        time.sleep(5)  # Let Flask finish starting
        _refresh_audio_background()
        _email_catchup()  # Process any job alert emails that were read but not processed
    threading.Thread(target=_startup_tasks, daemon=True).start()


def _shutdown_scheduler() -> None:
    """Gracefully stop the scheduler (called via atexit on any exit)."""
    global _scheduler
    if _scheduler and _scheduler.running:
        log.info("Orchestrator shutting down...")
        _scheduler.shutdown(wait=False)
        log_audit("orchestrator", "stopped", "atexit")
