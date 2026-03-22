"""Main dashboard routes: stats, jobs, approvals, logs, heatmap."""

import os
from datetime import datetime
from pathlib import Path

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, send_from_directory, flash

from core.db import get_conn, get_daily_stats, get_pending_approvals, update_job_status, log_audit
from core.logger import get_logger

log = get_logger(__name__)
bp = Blueprint("main", __name__)

# ── City geocoding for real-time job heatmap ─────────────────────────────────
# Keys are lowercase substrings matched against job location strings.
# India cities listed first (priority market).
_GEO_MAP: dict[str, tuple[float, float]] = {
    # India — Priority market
    "bengaluru": (12.9716, 77.5946),
    "bangalore": (12.9716, 77.5946),
    "mumbai": (19.0760, 72.8777),
    "hyderabad": (17.3850, 78.4867),
    "pune": (18.5204, 73.8567),
    "delhi": (28.6139, 77.2090),
    "ncr": (28.6139, 77.2090),
    "noida": (28.5355, 77.3910),
    "gurugram": (28.4595, 77.0266),
    "gurgaon": (28.4595, 77.0266),
    "faridabad": (28.4089, 77.3178),
    "chennai": (13.0827, 80.2707),
    "kolkata": (22.5726, 88.3639),
    "ahmedabad": (23.0225, 72.5714),
    "kochi": (9.9312, 76.2673),
    "jaipur": (26.9124, 75.7873),
    "coimbatore": (11.0168, 76.9558),
    "indore": (22.7196, 75.8577),
    "nagpur": (21.1458, 79.0882),
    "chandigarh": (30.7333, 76.7794),
    "vizag": (17.6868, 83.2185),
    "visakhapatnam": (17.6868, 83.2185),
    # Middle East
    "dubai": (25.2048, 55.2708),
    "abu dhabi": (24.4539, 54.3773),
    "riyadh": (24.6877, 46.7219),
    "doha": (25.2854, 51.5310),
    # Asia-Pacific
    "singapore": (1.3521, 103.8198),
    "sydney": (-33.8688, 151.2093),
    "melbourne": (-37.8136, 144.9631),
    "tokyo": (35.6762, 139.6503),
    "hong kong": (22.3193, 114.1694),
    "kuala lumpur": (3.1390, 101.6869),
    # Europe
    "london": (51.5074, -0.1278),
    "berlin": (52.5200, 13.4050),
    "amsterdam": (52.3676, 4.9041),
    "paris": (48.8566, 2.3522),
    "zurich": (47.3769, 8.5417),
    "frankfurt": (50.1109, 8.6821),
    "munich": (48.1351, 11.5820),
    "stockholm": (59.3293, 18.0686),
    "dublin": (53.3498, -6.2603),
    # North America
    "new york": (40.7128, -74.0060),
    "san francisco": (37.7749, -122.4194),
    "seattle": (47.6062, -122.3321),
    "toronto": (43.6532, -79.3832),
    "vancouver": (49.2827, -123.1207),
    "chicago": (41.8781, -87.6298),
    "boston": (42.3601, -71.0589),
    "austin": (30.2672, -97.7431),
    "los angeles": (34.0522, -118.2437),
    # Country-level fallbacks (used when no city matched)
    "india": (20.5937, 78.9629),
    "united states": (37.0902, -95.7129),
    "usa": (37.0902, -95.7129),
    "canada": (56.1304, -106.3468),
    "australia": (-25.2744, 133.7751),
    "united kingdom": (55.3781, -3.4360),
    "uk": (55.3781, -3.4360),
    "germany": (51.1657, 10.4515),
}

_INDIA_MARKERS = {"bengaluru", "bangalore", "mumbai", "hyderabad", "pune",
                  "delhi", "ncr", "noida", "gurugram", "gurgaon", "chennai",
                  "kolkata", "ahmedabad", "kochi", "india"}


@bp.route("/")
def index():
    """Dashboard home: stats cards, charts, recent activity."""
    stats = get_daily_stats()
    conn = get_conn()
    recent_logs = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT 10"
    ).fetchall()
    pending = get_pending_approvals()
    pending_count = len(pending["emails"]) + len(pending["jobs"])
    chart_data = _get_chart_data()

    # Check for latest audio briefing (single file, always overwritten)
    audio_file = "briefing_latest.mp3" if Path("data/audio/briefing_latest.mp3").exists() else None

    return render_template("index.html", stats=stats, recent_logs=recent_logs,
                           pending_count=pending_count, chart_data=chart_data,
                           audio_file=audio_file)


@bp.route("/audio/<path:filename>")
def serve_audio(filename: str):
    """Serve audio briefing files from data/audio/."""
    audio_dir = os.path.abspath("data/audio")
    return send_from_directory(audio_dir, filename)


@bp.route("/briefing/generate")
def generate_briefing():
    """Generate today's audio briefing on demand and redirect to dashboard."""
    try:
        from core.config_loader import load_config
        from agents.comms.audio_briefer import generate_briefing as gen
        name = load_config().get("resume", {}).get("personal", {}).get("name", "there").split()[0]
        result = gen(name, force=True)
        if result:
            flash("Audio briefing generated successfully. Press play below.", "success")
            log.info("Audio briefing generated on demand: %s", result)
        else:
            flash("Audio generation failed. Check logs for details.", "error")
    except Exception as e:
        log.error("On-demand briefing failed: %s", e)
        flash(f"Audio generation error: {e}", "error")
    return redirect(url_for("main.index"))


@bp.route("/jobs")
def jobs():
    """Job listings with filter and search."""
    conn = get_conn()
    status_filter = request.args.get("status", "")
    source_filter = request.args.get("source", "")
    search = request.args.get("q", "")

    query = "SELECT * FROM jobs WHERE 1=1"
    params: list = []
    if status_filter:
        query += " AND status=?"
        params.append(status_filter)
    if source_filter:
        query += " AND source=?"
        params.append(source_filter)
    if search:
        query += " AND (title LIKE ? OR company LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    query += " ORDER BY match_score DESC, discovered_at DESC LIMIT 200"

    all_jobs = [dict(r) for r in conn.execute(query, params).fetchall()]
    return render_template("jobs.html", jobs=all_jobs, status_filter=status_filter,
                           source_filter=source_filter, search=search)


@bp.route("/approvals")
def approvals():
    """Pending approvals for emails and job applications."""
    from core.config_loader import load_config
    pending = get_pending_approvals()
    threshold = load_config().get("settings", {}).get("matching", {}).get("threshold", 0.45)
    threshold_pct = int(threshold * 100)
    return render_template("approvals.html", emails=pending["emails"], jobs=pending["jobs"],
                           threshold_pct=threshold_pct)


@bp.route("/reset", methods=["POST"])
def reset_database():
    """Clear all job/email/application data, audit log, and log file — complete fresh start."""
    conn = get_conn()
    conn.executescript("""
        DELETE FROM jobs;
        DELETE FROM applications;
        DELETE FROM emails;
        DELETE FROM cv_updates;
        DELETE FROM audit_log;
    """)
    conn.commit()

    # Also truncate the file-based Python logger output
    log_file = Path("data/logs/jobpilot.log")
    try:
        if log_file.exists():
            log_file.write_text("", encoding="utf-8")
    except Exception as e:
        log.warning("Could not clear log file during reset: %s", e)

    # Delete stale audio briefing so dashboard shows fresh state after reset
    audio_dir = Path("data/audio")
    for fname in ("briefing_latest.mp3", "briefing_latest.ts"):
        try:
            (audio_dir / fname).unlink(missing_ok=True)
        except Exception as e:
            log.warning("Could not delete audio file %s during reset: %s", fname, e)

    log_audit("dashboard", "database_reset", "User manually cleared all data — fresh start")
    flash("Database and activity logs cleared. Starting completely fresh.", "success")
    return redirect(url_for("main.index"))


@bp.route("/approve/email/<int:email_id>", methods=["POST"])
def approve_email(email_id: int):
    """Mark an email reply as approved and send it immediately."""
    conn = get_conn()
    conn.execute("UPDATE emails SET reply_approved=1 WHERE id=?", (email_id,))
    conn.commit()
    log_audit("dashboard", "email_approved", f"email_id={email_id}")

    # Send immediately — do not wait for the scheduler cycle
    try:
        from agents.comms.email_sender import send_approved_replies
        sent = send_approved_replies()
        if sent:
            log.info("Immediately sent %d reply after approval", sent)
    except Exception as e:
        log.error("Immediate send after approval failed: %s", e)

    return redirect(url_for("main.approvals"))


@bp.route("/reject/email/<int:email_id>", methods=["POST"])
def reject_email(email_id: int):
    """Discard a drafted email reply."""
    conn = get_conn()
    conn.execute("UPDATE emails SET reply_draft=NULL WHERE id=?", (email_id,))
    conn.commit()
    log_audit("dashboard", "email_rejected", f"email_id={email_id}")
    return redirect(url_for("main.approvals"))


@bp.route("/approve/job/<int:job_id>", methods=["POST"])
def approve_job(job_id: int):
    """Mark a job as approved and immediately trigger application in background."""
    update_job_status(job_id, "approved")
    log_audit("dashboard", "job_approved", f"job_id={job_id}")

    # Trigger apply immediately — don't wait for the 2-minute scheduler cycle
    import threading
    def _trigger_apply():
        try:
            from core.orchestrator import _apply_approved_jobs
            _apply_approved_jobs()
        except Exception as e:
            log.error("Immediate apply trigger failed: %s", e)
    threading.Thread(target=_trigger_apply, daemon=True).start()
    flash(f"Job approved — applying now in background. Check Activity Logs for result.", "success")
    return redirect(url_for("main.approvals"))


@bp.route("/reject/job/<int:job_id>", methods=["POST"])
def reject_job(job_id: int):
    """Mark a job as skipped."""
    update_job_status(job_id, "skipped")
    log_audit("dashboard", "job_skipped", f"job_id={job_id}")
    return redirect(url_for("main.approvals"))


@bp.route("/logs")
def logs():
    """Paginated audit log feed with total count for smart pagination."""
    conn = get_conn()
    per_page = 50
    agent_filter = request.args.get("agent", "")

    # Get total count for this filter (needed to calculate total pages)
    count_query = "SELECT COUNT(*) FROM audit_log"
    count_params: list = []
    if agent_filter:
        count_query += " WHERE agent=?"
        count_params.append(agent_filter)
    total_count = conn.execute(count_query, count_params).fetchone()[0]
    total_pages = max(1, (total_count + per_page - 1) // per_page)

    page = max(1, min(int(request.args.get("page", 1)), total_pages))
    offset = (page - 1) * per_page

    query = "SELECT * FROM audit_log"
    params: list = []
    if agent_filter:
        query += " WHERE agent=?"
        params.append(agent_filter)
    query += f" ORDER BY id DESC LIMIT {per_page} OFFSET {offset}"

    entries = [dict(r) for r in conn.execute(query, params).fetchall()]
    agents = [r[0] for r in conn.execute("SELECT DISTINCT agent FROM audit_log").fetchall()]

    # Build visible page window: always show first, last, and 2 pages either side of current
    window = set()
    window.update([1, total_pages])
    for i in range(max(1, page - 2), min(total_pages, page + 2) + 1):
        window.add(i)
    page_window = sorted(window)

    return render_template("logs.html", entries=entries, agents=agents,
                           agent_filter=agent_filter, page=page,
                           total_pages=total_pages, total_count=total_count,
                           per_page=per_page, page_window=page_window)


@bp.route("/api/stats")
def api_stats():
    """JSON endpoint for Chart.js data."""
    return jsonify(_get_chart_data())


@bp.route("/api/approvals/count")
def api_approvals_count():
    """Return pending approval counts for real-time badge polling."""
    conn = get_conn()
    email_count = conn.execute(
        "SELECT COUNT(*) FROM emails WHERE reply_approved=0 AND reply_draft IS NOT NULL "
        "AND category NOT IN ('sent_mail','bounce_detected','job_alert','application_confirmation','irrelevant')"
    ).fetchone()[0]
    job_count = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='matched'"
    ).fetchone()[0]
    manual_count = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='apply_failed'"
    ).fetchone()[0]
    return jsonify({"emails": email_count, "jobs": job_count, "manual": manual_count})


@bp.route("/api/notifications")
def api_notifications():
    """Return pending manual actions + job alert discoveries for notification panel."""
    conn = get_conn()
    notifications = []

    # Failed job applications — user needs to apply manually
    failed_jobs = conn.execute(
        "SELECT id, title, company, location, url FROM jobs "
        "WHERE status='apply_failed' ORDER BY discovered_at DESC LIMIT 20"
    ).fetchall()
    for j in failed_jobs:
        notifications.append({
            "id": j["id"],
            "type": "apply_failed",
            "title": f"Apply manually: {j['title'] or 'Job'} at {j['company'] or 'Company'}",
            "subtitle": j["location"] or "",
            "url": j["url"] or "",
            "action": "Apply Now",
        })

    # Recent job alert emails with extracted leads
    recent_alerts = conn.execute(
        "SELECT id, subject, sender, leads_found, received_at FROM emails "
        "WHERE category='job_alert' AND leads_found > 0 "
        "ORDER BY id DESC LIMIT 10"
    ).fetchall()
    for a in recent_alerts:
        notifications.append({
            "id": a["id"],
            "type": "job_alert",
            "title": f"Found {a['leads_found']} jobs in: {a['subject'] or 'Job Alert'}",
            "subtitle": f"From {a['sender'][:40] if a['sender'] else 'Job Portal'}",
            "url": "/jobs?source=email_alert",
            "action": "View Jobs",
        })

    return jsonify(notifications)


@bp.route("/api/job-alerts")
def api_job_alerts():
    """Return recent job alert emails with lead counts — for the dashboard stats panel."""
    import re as _re
    conn = get_conn()
    alerts = conn.execute(
        "SELECT id, subject, sender, leads_found, received_at FROM emails "
        "WHERE category='job_alert' ORDER BY id DESC LIMIT 20"
    ).fetchall()
    total_leads = conn.execute(
        "SELECT COALESCE(SUM(leads_found),0) FROM emails WHERE category='job_alert'"
    ).fetchone()[0]
    # Recent confirmed applications from Naukri/LinkedIn
    confirmed = conn.execute(
        "SELECT details FROM audit_log WHERE action='application_confirmed' "
        "ORDER BY id DESC LIMIT 5"
    ).fetchall()
    manually_applied = sum(
        int(m.group(1)) for r in confirmed
        if r["details"] and (m := _re.search(r"count=(\d+)", r["details"]))
    )
    return jsonify({
        "alerts": [dict(a) for a in alerts],
        "total_leads_found": int(total_leads),
        "manually_applied": manually_applied,
    })


@bp.route("/dismiss/notification/<int:job_id>", methods=["POST"])
def dismiss_notification(job_id: int):
    """Dismiss a manual action notification by marking the job skipped."""
    conn = get_conn()
    conn.execute("UPDATE jobs SET status='skipped' WHERE id=? AND status='apply_failed'", (job_id,))
    conn.commit()
    log_audit("dashboard", "notification_dismissed", f"job_id={job_id}")
    return jsonify({"ok": True})


@bp.route("/api/briefing/status")
def api_briefing_status():
    """Return current audio briefing state for live dashboard polling.

    Response: {generating, has_audio, last_generated}
    - generating: true while edge-tts is running (show animation)
    - has_audio: true if briefing_latest.mp3 exists
    - last_generated: ISO timestamp string or null (internal, not shown to user)
    """
    from agents.comms.audio_briefer import get_status
    return jsonify(get_status())


@bp.route("/api/heatmap")
def api_heatmap():
    """Return city-level job counts for the world heatmap.

    Matches job location strings against _GEO_MAP and returns:
    [{lat, lng, count, city, is_india}]
    Sorted by count descending so the heaviest nodes render on top.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT location, COUNT(*) as c FROM jobs "
        "WHERE location IS NOT NULL AND location != '' "
        "GROUP BY location ORDER BY c DESC"
    ).fetchall()

    # Aggregate matched points — multiple location strings may map to the same city
    city_totals: dict[str, dict] = {}  # city_key → {lat, lng, count, city, is_india}

    for row in rows:
        loc_str = (row["location"] or "").strip()
        loc_lower = loc_str.lower()

        # Skip remote/unlocated
        if "remote" in loc_lower or not loc_str:
            continue

        matched_key: str | None = None
        coords: tuple[float, float] | None = None

        # Try longest-key matches first for precision
        for city_key in sorted(_GEO_MAP.keys(), key=len, reverse=True):
            if city_key in loc_lower:
                matched_key = city_key
                coords = _GEO_MAP[city_key]
                break

        if not coords or not matched_key:
            continue

        if matched_key not in city_totals:
            city_totals[matched_key] = {
                "lat": coords[0],
                "lng": coords[1],
                "count": 0,
                "city": loc_str,
                "is_india": matched_key in _INDIA_MARKERS,
            }
        city_totals[matched_key]["count"] += row["c"]

    points = sorted(city_totals.values(), key=lambda p: p["count"], reverse=True)
    return jsonify(points)


@bp.route("/heatmap")
def heatmap_page():
    """Full-page world heatmap view."""
    conn = get_conn()
    total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    india_jobs = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE "
        "LOWER(location) LIKE '%bengaluru%' OR LOWER(location) LIKE '%mumbai%' OR "
        "LOWER(location) LIKE '%hyderabad%' OR LOWER(location) LIKE '%pune%' OR "
        "LOWER(location) LIKE '%delhi%' OR LOWER(location) LIKE '%noida%' OR "
        "LOWER(location) LIKE '%chennai%' OR LOWER(location) LIKE '%kolkata%' OR "
        "LOWER(location) LIKE '%india%'"
    ).fetchone()[0]
    return render_template("heatmap.html", total_jobs=total_jobs, india_jobs=india_jobs)


@bp.route("/emails")
def emails_page():
    """All processed emails with category filter — includes sent mail, recruiter contacts."""
    conn = get_conn()
    cat_filter = request.args.get("cat", "")
    q = request.args.get("q", "")
    query = "SELECT * FROM emails WHERE 1=1"
    params: list = []
    if cat_filter:
        query += " AND category=?"
        params.append(cat_filter)
    if q:
        query += " AND (subject LIKE ? OR sender LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    query += " ORDER BY id DESC LIMIT 300"
    all_emails = [dict(r) for r in conn.execute(query, params).fetchall()]
    cats = [r[0] for r in conn.execute("SELECT DISTINCT category FROM emails ORDER BY category").fetchall()]
    counts = {r["category"]: r["cnt"] for r in conn.execute(
        "SELECT category, COUNT(*) as cnt FROM emails GROUP BY category"
    ).fetchall()}
    return render_template("emails.html", emails=all_emails, cats=cats,
                           cat_filter=cat_filter, search=q, counts=counts)


@bp.route("/api/email/force-fetch", methods=["POST"])
def api_force_fetch():
    """Trigger an immediate email fetch cycle for debugging."""
    try:
        from agents.comms.email_reader import fetch_all_recent_emails, fetch_sent_emails
        from agents.comms.email_classifier import classify_batch
        from agents.comms.email_drafter import process_and_store
        emails = fetch_all_recent_emails()
        if emails:
            cats = classify_batch(emails)
            for em, cat in zip(emails, cats):
                em["_category"] = cat
                process_and_store(em)
        for em in fetch_sent_emails():
            process_and_store(em)
        total = len(emails)
        log_audit("dashboard", "force_fetch", f"fetched={total}")
        return jsonify({"ok": True, "fetched": total})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/calendar")
def calendar_page():
    """Interview calendar: monthly view with interview events."""
    from datetime import date
    now = date.today()
    year = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))
    return render_template("calendar.html", year=year, month=month,
                           today=now.isoformat())


@bp.route("/api/calendar/<int:year>/<int:month>")
def api_calendar(year: int, month: int):
    """Return interview events for the given month as JSON."""
    conn = get_conn()
    interviews = conn.execute(
        "SELECT id, company, role, interview_type, scheduled_at, meeting_link, "
        "meeting_id, meeting_password, jd_snippet, topics_to_prepare, status "
        "FROM interviews "
        "WHERE strftime('%Y', scheduled_at) = ? AND strftime('%m', scheduled_at) = ? "
        "ORDER BY scheduled_at",
        (str(year), f"{month:02d}"),
    ).fetchall()
    return jsonify([dict(r) for r in interviews])


@bp.route("/api/interview/<int:interview_id>")
def api_interview_detail(interview_id: int):
    """Return full interview details for flashcard popup."""
    conn = get_conn()
    row = conn.execute(
        "SELECT i.*, e.subject as email_subject, e.sender as recruiter_email, "
        "e.body_preview as email_body "
        "FROM interviews i LEFT JOIN emails e ON i.email_id = e.id "
        "WHERE i.id = ?", (interview_id,)
    ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


@bp.route("/interview/<int:interview_id>/update", methods=["POST"])
def update_interview(interview_id: int):
    """Update interview status or notes."""
    conn = get_conn()
    status = request.form.get("status", "")
    notes = request.form.get("notes", "")
    if status:
        conn.execute("UPDATE interviews SET status=? WHERE id=?", (status, interview_id))
    if notes:
        conn.execute("UPDATE interviews SET notes=? WHERE id=?", (notes, interview_id))
    conn.commit()
    log_audit("dashboard", "interview_updated", f"id={interview_id}, status={status}")
    return redirect(url_for("main.calendar_page"))


def _get_chart_data() -> dict:
    """Build chart data for jobs discovered over time, applications, and job sources."""
    conn = get_conn()
    daily_discovered = conn.execute(
        "SELECT date(discovered_at) as d, COUNT(*) as c FROM jobs "
        "GROUP BY d ORDER BY d DESC LIMIT 14"
    ).fetchall()
    daily_applications = conn.execute(
        "SELECT date(created_at) as d, COUNT(*) as c FROM applications "
        "GROUP BY d ORDER BY d DESC LIMIT 14"
    ).fetchall()
    sources = conn.execute(
        "SELECT source, COUNT(*) as c FROM jobs GROUP BY source ORDER BY c DESC"
    ).fetchall()
    top_locations = conn.execute(
        "SELECT location, COUNT(*) as c FROM jobs "
        "WHERE location IS NOT NULL AND location != '' AND LOWER(location) != 'remote' "
        "GROUP BY location ORDER BY c DESC LIMIT 10"
    ).fetchall()
    return {
        "daily_discovered": [{"date": r["d"], "count": r["c"]} for r in daily_discovered],
        "daily_applications": [{"date": r["d"], "count": r["c"]} for r in daily_applications],
        "job_sources": [{"source": r["source"] or "unknown", "count": r["c"]} for r in sources],
        "top_locations": [{"location": r["location"], "count": r["c"]} for r in top_locations],
    }
