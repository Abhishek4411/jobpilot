"""SQLite database layer with WAL mode and helper functions."""

import sqlite3
import threading
from datetime import datetime
from typing import Any

from core.logger import get_logger

log = get_logger(__name__)
_local = threading.local()
DB_PATH = "data/jobpilot.db"


def get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection in WAL mode."""
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db() -> None:
    """Create all tables if they do not exist."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, company TEXT, location TEXT,
            salary_min REAL, salary_max REAL, source TEXT,
            url TEXT UNIQUE, jd_text TEXT, match_score REAL DEFAULT 0,
            status TEXT DEFAULT 'discovered',
            discovered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            applied_at DATETIME, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER, resume_version TEXT, cover_letter TEXT,
            questions_answered TEXT, applied_via TEXT,
            status TEXT DEFAULT 'submitted',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT UNIQUE, subject TEXT, sender TEXT,
            body_preview TEXT, category TEXT, job_id INTEGER,
            reply_draft TEXT, reply_approved INTEGER DEFAULT 0,
            reply_sent INTEGER DEFAULT 0,
            received_at DATETIME, processed_at DATETIME
        );
        CREATE TABLE IF NOT EXISTS cv_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            change_type TEXT, field_changed TEXT,
            old_value TEXT, new_value TEXT,
            platform TEXT DEFAULT 'naukri',
            success INTEGER DEFAULT 1, error_message TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS resume_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT, file_hash TEXT, parsed_yaml TEXT,
            missing_fields TEXT, source TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS interviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id INTEGER,
            job_id INTEGER,
            company TEXT, role TEXT, location TEXT,
            interview_type TEXT DEFAULT 'unknown',
            scheduled_at DATETIME,
            meeting_link TEXT,
            meeting_id TEXT,
            meeting_password TEXT,
            jd_snippet TEXT,
            topics_to_prepare TEXT,
            notes TEXT,
            status TEXT DEFAULT 'scheduled',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS user_inputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_path TEXT, value TEXT, status TEXT DEFAULT 'pending',
            entered_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT, action TEXT, details TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()

    # Migrations: add columns that may not exist in older databases
    for migration in [
        "ALTER TABLE emails ADD COLUMN leads_found INTEGER DEFAULT 0",
    ]:
        try:
            conn.execute(migration)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

    # Fix specific known misclassifications (safe targeted corrections only)
    try:
        # Postmaster/undeliverable emails must never be job_opportunity
        conn.execute(
            "UPDATE emails SET category='irrelevant' "
            "WHERE category='job_opportunity' "
            "AND (sender LIKE '%postmaster%' OR subject LIKE 'Undeliverable%' "
            "     OR subject LIKE 'Delivery%Fail%' OR subject LIKE 'Mail Delivery%')"
        )
        # Interview invite emails from real humans should not be irrelevant
        conn.execute(
            "UPDATE emails SET category='interview_request' "
            "WHERE category='irrelevant' "
            "AND (subject LIKE '%introductory interview%' OR subject LIKE '%interview is coming%' "
            "     OR subject LIKE '%interview scheduled%' OR subject LIKE '%your interview%') "
            "AND sender NOT LIKE '%noreply%' AND sender NOT LIKE '%no-reply%' "
            "AND sender NOT LIKE '%notification%' AND sender NOT LIKE '%automated%'"
        )
        conn.commit()
    except Exception:
        pass

    log.info("Database initialized at %s", DB_PATH)


def insert_job(job: dict[str, Any]) -> int | None:
    """Insert a job record, ignoring duplicates by URL. Uses local time for discovered_at."""
    conn = get_conn()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute(
            "INSERT OR IGNORE INTO jobs (title,company,location,salary_min,salary_max,"
            "source,url,jd_text,discovered_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (job.get("title"), job.get("company"), job.get("location"),
             job.get("salary_min"), job.get("salary_max"),
             job.get("source"), job.get("url"), job.get("description"), now),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.Error as e:
        log.error("insert_job failed: %s", e)
        return None


def update_job_status(job_id: int, status: str, score: float | None = None) -> None:
    """Update a job's status and optionally its match score."""
    conn = get_conn()
    if score is not None:
        conn.execute("UPDATE jobs SET status=?, match_score=? WHERE id=?", (status, score, job_id))
    else:
        conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    conn.commit()


def get_pending_approvals() -> dict[str, list]:
    """Return pending email replies and job applications awaiting approval."""
    conn = get_conn()
    emails = conn.execute(
        "SELECT * FROM emails WHERE reply_draft IS NOT NULL AND reply_approved=0 AND reply_sent=0 "
        "AND category NOT IN ('sent_mail','bounce_detected','job_alert','application_confirmation','irrelevant')"
    ).fetchall()
    jobs = conn.execute(
        "SELECT * FROM jobs WHERE status='matched' ORDER BY match_score DESC"
    ).fetchall()
    return {"emails": [dict(r) for r in emails], "jobs": [dict(r) for r in jobs]}


def log_audit(agent: str, action: str, details: str = "") -> None:
    """Write one row to the audit_log table using local (IST) time."""
    conn = get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO audit_log (agent,action,details,created_at) VALUES (?,?,?,?)",
        (agent, action, details, now),
    )
    conn.commit()


def get_daily_stats() -> dict[str, int]:
    """Return dashboard stats (mix of today and all-time)."""
    conn = get_conn()
    today = datetime.now().strftime("%Y-%m-%d")
    discovered_today = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE date(discovered_at)=?", (today,)
    ).fetchone()[0]
    total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    # Jobs that passed the match threshold (status set by scorer — threshold-agnostic)
    ever_matched = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status IN "
        "('matched','approved','applying','applied','apply_failed','interview_scheduled')"
    ).fetchone()[0]
    pending_approval = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='matched'"
    ).fetchone()[0]
    applied_total = conn.execute(
        "SELECT COUNT(*) FROM applications"
    ).fetchone()[0]
    applied_today = conn.execute(
        "SELECT COUNT(*) FROM applications WHERE date(created_at)=?", (today,)
    ).fetchone()[0]
    recruiter_emails = conn.execute(
        "SELECT COUNT(*) FROM emails WHERE category IN ('job_opportunity','interview_request','follow_up')"
    ).fetchone()[0]
    # Use interviews table (actual confirmed interviews) not just email category
    interviews = conn.execute(
        "SELECT COUNT(*) FROM interviews WHERE status != 'cancelled'"
    ).fetchone()[0]
    manual_needed = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='apply_failed'"
    ).fetchone()[0]
    job_alerts = conn.execute(
        "SELECT COUNT(*) FROM emails WHERE category='job_alert'"
    ).fetchone()[0]
    job_leads = conn.execute(
        "SELECT COALESCE(SUM(leads_found),0) FROM emails WHERE category='job_alert'"
    ).fetchone()[0]
    manually_applied = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='application_confirmed'"
    ).fetchone()[0]
    email_alert_jobs = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE source='email_alert'"
    ).fetchone()[0]
    return {
        "discovered": discovered_today, "total_jobs": total_jobs,
        "ever_matched": ever_matched, "pending_approval": pending_approval,
        "applied": applied_today, "applied_total": applied_total,
        "recruiter_emails": recruiter_emails, "interviews": interviews,
        "manual_needed": manual_needed,
        "job_alerts": job_alerts,         # number of job alert emails processed
        "job_leads": int(job_leads),      # total job leads extracted from alerts
        "manually_applied": manually_applied,  # confirmed by Naukri/LinkedIn emails
        "email_alert_jobs": email_alert_jobs,  # jobs in DB from email alerts
    }


def get_resume_version() -> dict | None:
    """Return the most recent resume version record."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM resume_versions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def cleanup_old_data(days: int = 14) -> None:
    """Delete stale records older than `days` days to keep DB lean.

    Keeps all applications, approved emails, matched/approved jobs.
    Purges old irrelevant emails, rejected jobs, old audit rows.
    """
    conn = get_conn()
    conn.executescript(f"""
        DELETE FROM jobs
            WHERE status IN ('discovered', 'skipped', 'rejected')
            AND discovered_at < datetime('now', '-{days} days');

        DELETE FROM emails
            WHERE category IN ('irrelevant', 'rejection', 'status_update')
            AND reply_sent=0
            AND received_at < datetime('now', '-{days} days');

        DELETE FROM audit_log
            WHERE created_at < datetime('now', '-{days} days');
    """)
    conn.commit()
    log.info("DB cleanup: removed records older than %d days", days)


def cleanup_log_file(log_path: str = "data/logs/jobpilot.log", max_lines: int = 2000) -> None:
    """Trim the log file to the last `max_lines` lines to prevent runaway growth."""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            with open(log_path, "w", encoding="utf-8") as f:
                f.writelines(lines[-max_lines:])
            log.info("Log trimmed to last %d lines", max_lines)
    except Exception as e:
        log.warning("Log cleanup failed: %s", e)
