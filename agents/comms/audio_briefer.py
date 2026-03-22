"""Generate audio briefings using edge-tts (free, no API key needed).

Strategy:
- Always ONE file: data/audio/briefing_latest.mp3 (old files purged on each generation)
- Timestamp stored in data/audio/briefing_latest.ts (ISO) for freshness tracking
- _generating flag lets the dashboard show a live loading animation
- Auto-triggered after every scout cycle and on app startup
- Runs edge-tts in a dedicated thread with its own event loop (Flask-safe)
"""

import asyncio
import threading
from datetime import datetime
from pathlib import Path

from core.db import get_daily_stats, get_conn
from core.logger import get_logger

log = get_logger(__name__)

AUDIO_DIR = Path("data/audio")
AUDIO_FILE = AUDIO_DIR / "briefing_latest.mp3"
TIMESTAMP_FILE = AUDIO_DIR / "briefing_latest.ts"   # ISO timestamp — internal only
VOICE = "en-IN-NeerjaNeural"

# Module-level generating flag — read by /api/briefing/status for live dashboard animation
_generating: bool = False


def get_status() -> dict:
    """Return current briefing status for the dashboard API.

    Returns:
        {generating, has_audio, last_generated}  — last_generated is ISO str or None.
    """
    last_gen: str | None = None
    if TIMESTAMP_FILE.exists():
        try:
            last_gen = TIMESTAMP_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return {
        "generating": _generating,
        "has_audio": AUDIO_FILE.exists(),
        "last_generated": last_gen,
    }


def _build_script(name: str) -> str:
    """Build a natural-language briefing from LIVE dashboard stats."""
    stats = get_daily_stats()
    conn = get_conn()
    now = datetime.now()

    total_jobs     = stats.get("total_jobs", 0)
    ever_matched   = stats.get("ever_matched", 0)
    pending_approval = stats.get("pending_approval", 0)
    applied_total  = stats.get("applied_total", 0)
    applied_today  = stats.get("applied", 0)
    recruiter_emails = stats.get("recruiter_emails", 0)
    interviews     = stats.get("interviews", 0)
    manual_needed  = stats.get("manual_needed", 0)

    pending_replies = conn.execute(
        "SELECT COUNT(*) FROM emails WHERE reply_approved=0 AND reply_draft IS NOT NULL"
    ).fetchone()[0]

    new_today = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE date(discovered_at)=date('now')"
    ).fetchone()[0]

    top_job = conn.execute(
        "SELECT title, company, match_score FROM jobs WHERE match_score IS NOT NULL "
        "ORDER BY match_score DESC LIMIT 1"
    ).fetchone()

    hour = now.hour
    if hour < 12:
        greeting = "Good morning"
    elif hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"

    time_str = now.strftime("%I:%M %p").lstrip("0")

    parts: list[str] = []
    parts.append(f"{greeting} {name}. It is {time_str}. Here is your live job search update.")

    if new_today > 0:
        parts.append(f"Your agent found {new_today} new job{'s' if new_today != 1 else ''} today, "
                     f"bringing the total to {total_jobs} jobs discovered.")
    else:
        parts.append(f"No new jobs discovered today. Total jobs in the database: {total_jobs}.")

    if ever_matched > 0:
        parts.append(f"{ever_matched} job{'s have' if ever_matched != 1 else ' has'} matched your profile so far.")
    else:
        parts.append("No jobs have matched your profile yet. The agent is still searching.")

    if pending_approval > 0:
        parts.append(f"You have {pending_approval} job{'s' if pending_approval != 1 else ''} "
                     f"waiting for your approval in the dashboard.")

    if applied_total > 0:
        if applied_today > 0:
            parts.append(f"{applied_today} application{'s were' if applied_today != 1 else ' was'} submitted today, "
                         f"{applied_total} in total.")
        else:
            parts.append(f"No new applications today. {applied_total} total submitted so far.")
    else:
        parts.append("No applications have been submitted yet.")

    if top_job:
        score_pct = int((top_job["match_score"] or 0) * 100)
        parts.append(f"Your best match is {top_job['title']} at {top_job['company']}, "
                     f"with a {score_pct} percent compatibility score.")

    if interviews > 0:
        parts.append(f"Great news! You have {interviews} interview request{'s' if interviews != 1 else ''}. "
                     f"Check your approvals immediately.")
    if recruiter_emails > 0:
        parts.append(f"{recruiter_emails} recruiter contact{'s have' if recruiter_emails != 1 else ' has'} "
                     f"reached out via email.")

    if pending_replies > 0:
        parts.append(f"{pending_replies} email repl{'ies are' if pending_replies != 1 else 'y is'} "
                     f"drafted and waiting for your approval before sending.")

    if manual_needed > 0:
        parts.append(f"Attention: {manual_needed} job application{'s require' if manual_needed != 1 else ' requires'} "
                     f"your manual action. The automation could not complete {'these' if manual_needed != 1 else 'this'}. "
                     f"Please check the Action Needed banner on your dashboard.")

    if manual_needed == 0 and pending_approval == 0 and pending_replies == 0 and interviews == 0:
        parts.append("Everything looks good. No actions required from you right now.")

    parts.append("Visit your dashboard at localhost port 5000 to review everything in detail.")

    return " ".join(parts)


async def _generate_audio(text: str, output_path: Path) -> None:
    """Generate an MP3 audio file from text using edge-tts."""
    import edge_tts
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(str(output_path))


def _purge_old_audio() -> None:
    """Delete all MP3 and timestamp files in AUDIO_DIR to keep only the latest."""
    if not AUDIO_DIR.exists():
        return
    for f in AUDIO_DIR.glob("*.mp3"):
        try:
            f.unlink()
        except Exception:
            pass


def generate_briefing(name: str = "there", force: bool = False) -> str | None:
    """Generate the audio briefing MP3 from live dashboard stats.

    Sets _generating=True while working so the dashboard can show a loading
    animation. Writes a timestamp to TIMESTAMP_FILE on success so the dashboard
    knows when the briefing was last updated without showing it to the user.

    Args:
        name: User's first name for the greeting.
        force: Kept for API compatibility. Always regenerates fresh.

    Returns:
        Path to the generated MP3 file, or None on failure.
    """
    global _generating
    if _generating:
        log.info("Audio briefing already in progress, skipping")
        return None

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    try:
        import edge_tts  # noqa: F401 — verify installed before work
    except ImportError:
        log.error("edge-tts not installed. Run: pip install edge-tts")
        return None

    _generating = True
    _purge_old_audio()

    script = _build_script(name)
    log.info("Generating audio briefing (%d chars)", len(script))

    error_box: list[Exception] = []

    def _run() -> None:
        """Run edge-tts in a dedicated thread with its own event loop (Flask-safe)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_generate_audio(script, AUDIO_FILE))
        except Exception as exc:
            error_box.append(exc)
        finally:
            loop.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=30)

    _generating = False

    if t.is_alive():
        log.error("Audio briefing timed out after 30s")
        return None
    if error_box:
        log.error("Audio briefing failed: %s", error_box[0])
        return None

    # Write timestamp so dashboard can detect freshness without showing it to user
    try:
        TIMESTAMP_FILE.write_text(datetime.now().isoformat(), encoding="utf-8")
    except Exception:
        pass

    log.info("Audio briefing saved: %s", AUDIO_FILE)
    return str(AUDIO_FILE)
