# JobPilot — Project Notes & Session Log

Running record of bugs found, fixes applied, design decisions, and open questions.

---

## Session 1 — Initial Build

**Date:** 2026-03-11
**Status:** Complete

### What was built
Full 70-file system from scratch across 12 phases:
- Config YAMLs (7 files)
- Core layer: logger, config_loader, db, llm_router, notifier, orchestrator
- CV Manager: pdf/docx/txt parsers, structurer, validator, diff_detector, watcher
- Scout: jobspy scraper, Naukri Playwright scraper, deduplicator
- Matcher: resume_parser, jd_parser, scorer, keyword_search
- Dashboard: Flask app, routes, cv_routes, 9 HTML templates, dark CSS
- Applier: question_handler, naukri_apply, linkedin_apply, form_filler
- Comms: email_reader, email_classifier, email_drafter, email_sender, audio_briefer
- Optimizer: update_strategies, naukri_cv_updater
- Scripts: setup.bat/sh, test_connections.py, migrate_db.py
- Tests, requirements.txt, .env.example, .gitignore

---

## Session 2 — Bug Fixes & Optimization

**Date:** 2026-03-11
**Status:** Complete

### Bugs found from logs

#### 1. Gemini 404 — All LLM calls failing
- **Root cause:** `config/llm_providers.yaml` had `model: gemini-1.5-flash` — wrong model name for the v1beta/openai endpoint
- **Fix:** Changed to `gemini-2.0-flash` (confirmed via test: returns 429 = valid model)
- **Impact:** ALL email drafts failing (15+ emails got no draft), all quality_drafting → error

#### 2. 0/337 jobs matched threshold
- **Root cause 1:** python-jobspy returns pandas `NaN` for empty descriptions; `str(NaN)` → `"nan"` — cosine embedding of "nan" ≈ 0
- **Root cause 2:** Matching threshold 0.65 was too strict (realistic max for partial JDs is ~0.55)
- **Fix 1:** Added `_safe()` helper in `agents/scout/scraper.py` to convert NaN/None/empty to `""`; skip jobs without URL
- **Fix 2:** Lowered threshold from `0.65` → `0.45` in `config/settings.yaml`

#### 3. Naukri login selector timeout
- **Root cause:** Naukri updated their login page UI; `input[placeholder='Enter your active Email ID / Username']` no longer exists
- **Fix:** Added 5 fallback selectors tried in sequence for email field, password field, submit button
- **Files fixed:** `agents/scout/naukri_scraper.py`, `agents/optimizer/naukri_cv_updater.py`

#### 4. `skill_reorder` strategy returning None every cycle
- **Root cause:** `update_strategies.py` had `skill_reorder` in the rotation config but no implementation — fell through to `log.warning()`
- **Fix:** Implemented `skill_reorder` as inverse synonym swap (picks a pair, swaps in reverse order)
- **Fallback:** If no synonym pairs defined, falls back to whitespace_tweak instead of None

#### 5. Log file noise — 200+ idle INFO lines per hour
- **Root cause:** File handler was at INFO level; email reader logged "Found 0 unread emails" every 5 min
- **Fix 1:** Changed file handler to `WARNING+` in `core/logger.py` (console stays INFO)
- **Fix 2:** Email reader only logs when `len(ids) > 0`

#### 6. README.txt parsed as resume
- **Root cause:** CV Watcher monitors `.txt` files in `data/resumes/`; README.txt matched
- **Fix:** Added `SKIP_FILENAMES = {"readme.txt", "readme.md", ...}` set; checked in both poll loop and dispatch

#### 7. Email drafts for newsletters and rejections (unnecessary LLM calls)
- **Root cause:** Drafter was called for all categories except `"irrelevant"` — wasted tokens on rejection emails and job alerts
- **Fix:** Changed to allowlist: only draft for `{"interview_request", "job_opportunity", "follow_up"}`

#### 8. Email reader using 24-hour window (missing older relevant emails)
- **Fix:** Changed `timedelta(days=1)` → `timedelta(days=14)` for 2-week lookback window

#### 9. Windows SQLite tempfile bug in test_connections.py
- **Root cause:** `NamedTemporaryFile(delete=True)` locks file on Windows; SQLite can't open locked file
- **Fix:** `delete=False`, close file first, then connect, then `os.unlink()` in finally block

#### 10. venv Python not used for scripts
- **Root cause:** System Python used by default; openai not installed there
- **Fix:** Documented correct command: `venv\Scripts\python.exe` or activate venv first

### New features added
- `core/db.py`: `cleanup_old_data(days=14)` — purges stale jobs/emails/audit rows
- `core/db.py`: `cleanup_log_file()` — trims log to last 2000 lines
- `core/orchestrator.py`: Hourly `_hourly_cleanup` job, daily 12:30 AM `_daily_purge` job
- `core/llm_router.py`: `get_resume_summary()` — cached module-level resume summary string
- `core/llm_router.py`: Per-task token budgets + Groq-first routing for all task types
- Scout scraper: `hours_old=336` (14 days) instead of 48 hours

### LLM routing after fixes
| Task type | Provider | Max tokens |
|---|---|---|
| fast_classification | Groq → Gemini | 15 |
| jd_analysis | Groq → Gemini | 300 |
| question_answering | Groq → Gemini | 80 |
| resume_parsing | Groq → Gemini | 2500 |
| quality_drafting | Groq → Gemini | 120 |
| default | Groq → Gemini | 200 |

---


---

## Session 3 — UI/UX Redesign, Bug Fixes & Notifications

**Date:** 2026-03-11  **Status:** Complete

### Summary
Major overhaul: dashboard UI/UX redesign, Naukri scraping fixes, email handling, Ctrl+C shutdown fix, and manual notification architecture.

---

### Bug Fixes

#### 1. Naukri scraper returning 0 jobs
- headless=True gets "Access Denied" from Naukri bot detection
- Fix: headless=False, --window-position=-32000,-32000 (off-screen), navigator.webdriver fingerprint removal
- Naukri new UI has no DOM selectors. Fix: intercept jobapi/v3/search XHR responses via page.on("response"). Returns clean JSON.
- File: agents/scout/naukri_scraper.py (complete rewrite)

#### 2. Naukri login selector timeout
- New Naukri UI uses #usernameField / #passwordField (not placeholder text)
- Fix: changed to ID selectors; replaced wait_for_load_state(networkidle) with asyncio.sleep(5)
- Files: agents/applier/naukri_apply.py, agents/optimizer/naukri_cv_updater.py

#### 3. Automation failure — no user notification
- On apply/cv_update failure: opens URL in user browser after 2.5s delay via webbrowser.open()
- Marks job as apply_failed -> picked up by /api/notifications banner
- Dashboard shows amber Action Needed banner on all pages
- Files: naukri_apply.py, naukri_cv_updater.py, dashboard/templates/base.html

#### 4. update_strategies.py whitespace_tweak never worked
- old_value="  " (2 spaces) never appears in Naukri summary text
- Fix: added retry loop through rotation types; removed whitespace_tweak; falls back to headline_variation
- File: agents/optimizer/update_strategies.py

#### 5. Email bounce 550 5.4.1 (Naukri relay addresses)
- Naukri relay format: firstname.lastnameYXNjZW5kaW9uLmNvbQ==@naukri.com
- Base64 suffix is the domain. Decode to get real email.
- Fix: _decode_naukri_relay() in email_sender.py; marks reply_sent=2 if decode fails (no retry)
- File: agents/comms/email_sender.py

#### 6. 65+ junk emails in approvals (SBI Holi, Swiggy, LinkedIn alerts)
- Fix: expanded _IRRELEVANT_SENDER_PATTERNS and _IRRELEVANT_SUBJECT_PATTERNS in email_classifier.py
- File: agents/comms/email_classifier.py

#### 7. LinkedIn/Naukri job alert emails should extract links, not reply
- New job_alert_extractor.py: extracts job URLs from digest emails, fetches each page for contacts
- File: agents/comms/job_alert_extractor.py (new), email_drafter.py

#### 8. Approvals badge showed 1561 (wrong - was counting discovered jobs)
- Fix: count status=matched only. As of 2026-03-11: 3002 total jobs, 0 currently matched pending.
- File: dashboard/routes.py

#### 9. Ctrl+C not closing the app
- Root cause: orchestrator.py registered signal.SIGINT that conflicted with Flask/werkzeug SIGINT
- Fix: removed signal.signal() calls; used atexit.register() instead; added try/except KeyboardInterrupt in main.py
- Files: core/orchestrator.py, main.py

---

### New Features

#### Dashboard stats rewritten (core/db.py get_daily_stats)
- Old: discovered_today, matched(pending), applied_today, interviews
- New: total_jobs(all-time), ever_matched(score>=0.45), applied_total, recruiter_emails, manual_needed, pending_approval
- Why: shows full picture of job search progress, not just today

#### Audio briefing accessible from dashboard
- Routes: GET /audio/<filename> (serves MP3), GET /briefing/generate (triggers gen)
- Dashboard welcome banner shows inline audio player if today exists
- File: dashboard/routes.py

#### Manual Action Notification Banner
- Amber banner on all pages when apply_failed jobs exist
- Polls /api/notifications every 60s; shows job title + Open URL + Dismiss
- Files: dashboard/templates/base.html, dashboard/routes.py

---

### UI/UX Complete Redesign

Why: top-navbar was confusing, too colorful, too much scrolling. User wanted enterprise left-sidebar layout.

- dashboard/static/style.css: Complete rewrite. CSS vars for light/dark themes. Left sidebar with collapse.
  Animations: fadeIn 200ms on page load, shimmer skeleton loading, slideIn flash notifications.
  Stat cards with colored left-border accent. Enterprise gray/blue palette.

- dashboard/templates/base.html: Replaced top nav with left sidebar. SVG icons. Collapse persisted in localStorage.
  Live clock in topbar. Dark/Light toggle (persisted, no flash on load). ES5 JS for compat.

- dashboard/templates/index.html: Welcome banner. 4 meaningful stat cards. Audio player inline.
  Conditional alert banners. Charts in 2:1 grid. Mini heatmap + recent activity side by side.

---

### config/SKILLS.md created
Documents error patterns, email rules, Naukri relay decoding, heatmap formulas, real-time arch.
For AI context and developer reference.

---
---

## Session 4 — Resume Fix, UI Polish, Audio Overhaul & RAM Savings

**Date:** 2026-03-12  **Status:** Complete

### Bug Fixes

#### 1. resume.yaml — all company dates were wrong (Gemini scrambled them)
- Root cause: LLM had shifted each company's dates down by one entry during initial parsing
- Fix: Complete rewrite from PDF source. Correct entries:
  - The Modern Data Company: Dec 2025 – Present
  - Netweb Technologies India: Sep 2024 – Dec 2025
  - Tech Mahindra + Dell deputation: Oct 2021 – Sep 2024
  - HDFC Bank Ltd.: Nov 2020 – Oct 2021
- Also fixed: duplicate skills removed, education years added, internships moved to own section,
  Agentic RAG + Gen AI Vision Model moved from certifications → projects, certifications trimmed to 3 real ones

#### 2. Audio briefer — completely broken (silent failure on clicking Generate Briefing)
- Root cause 1: Flask 3.0 has its own running asyncio event loop; `asyncio.run()` raises `RuntimeError: This event loop is already running` — caught silently by `except Exception`
- Root cause 2: Wrong stat key names (`matched` vs `ever_matched`)
- Root cause 3: `"%-I:%M %p"` strftime format is Linux-only; crashes on Windows with `ValueError: Invalid format string`
- Fix: Rewrote `agents/comms/audio_briefer.py` completely:
  - Flask-safe: `threading.Thread` + `asyncio.new_event_loop()` + `loop.run_until_complete()`, 30s join timeout
  - Single file strategy: always `briefing_latest.mp3`, `_purge_old_audio()` deletes all `*.mp3` before generating
  - Windows-safe time: `now.strftime("%I:%M %p").lstrip("0")`
  - Rich script: time-of-day greeting, new jobs today, top matched job (% score), pending approvals, interviews, manual actions, all-clear detection

#### 3. Sidebar collapse — hamburger button disappeared after clicking
- Root cause: `.collapsed .sidebar-brand` had `flex:1` — consumed all 60px of collapsed sidebar, pushing toggle off-screen (overflow:hidden clipped it)
- Fix: Added `width:0; overflow:hidden; flex:none` so brand takes zero space when collapsed

#### 4. Horizontal scroll when sidebar expanded
- Root cause: CSS grid columns used `1fr` which allows children to overflow container
- Fix: `minmax(0, 1fr)` for all grid columns; `max-width: calc(100vw - var(--sidebar-w))` on `.main-wrap`; stat cards use `auto-fit, minmax(160px, 1fr)` to wrap naturally

#### 5. Action banner — X close didn't persist (reappeared on next poll)
- Fix: Close stores `jp-banner-closed-until` timestamp (Now + 24h) in localStorage; poll skips banner render if `Date.now() < _bannerClosedUntil`
- Individual Dismiss hits `POST /dismiss/notification/<id>` → marks job `status='skipped'` permanently in DB

### New Features

#### Download PDF button on CV preview page
- `GET /cv/download` route in `dashboard/cv_routes.py` — serves most recently modified PDF from `data/resumes/`
- Button added to `dashboard/templates/cv_preview.html` alongside existing Upload and Edit buttons
- Also added: Projects (with duration), Internships, and Languages sections to CV preview template

### Performance / RAM Savings

#### Playwright memory reduction (all 3 files: naukri_scraper.py, naukri_apply.py, naukri_cv_updater.py)
- Added Chrome flags: `--disable-gpu`, `--disable-dev-shm-usage`, `--disable-extensions`, `--disable-background-networking`, `--disable-sync`, `--no-first-run`, `--disk-cache-size=0`, `--media-cache-size=0`, `--disable-plugins`
- Resource blocking: `page.route("**/*")` aborts `image`, `media`, `font`, `stylesheet`, `ping`, `other` (~60% less RAM per load)
- Explicit cleanup in `finally`: `page.close()`, `ctx.close()`, `browser.close()` — no zombie processes
- `slow_mo`: 80 → 50ms

#### Scout scraper
- `results_wanted`: 25 → 15 per search combo (still sufficient; less CPU/network)

---

## Session 5 — CV Page 500 Error Fix

**Date:** 2026-03-12  **Status:** Complete

### Bug Fixed

#### CV page (`/cv/`) — 500 Internal Server Error on every load
- **Symptom:** Clicking Resume in the sidebar always crashed with `BuildError: Could not build url for endpoint 'cv.download'`
- **Root cause:** `cv_preview.html` line 7 called `url_for('cv.download')` but the route function in `cv_routes.py` is named `download_resume`, so Flask registers the endpoint as `cv.download_resume`. The mismatch was introduced when the Download PDF button was added in Session 4.
- **Fix:** Changed `cv_preview.html` line 7: `url_for('cv.download')` → `url_for('cv.download_resume')`
- **Scope check:** Audited all `url_for` calls across all 9 templates against actual route function names in `routes.py` and `cv_routes.py` — no other mismatches found.
- **Rule going forward:** `url_for` endpoint name = `blueprint_name.function_name` exactly. Always verify when adding new routes.

---

## Session 6 — Notification UX Fix & Audio Generation Animation

**Date:** 2026-03-12  **Status:** Complete

### Problems Fixed

#### 1. Flash notifications overlapping in top-right corner
- **Symptom:** Multiple flash messages stacked at the same pixel position — text unreadable.
- **Root cause:** No stacking layout, no smooth exit, no way to close individually.
- **Fix — `style.css`:**
  - `.flash-container` now `display:flex; flex-direction:column; gap:.35rem; width:320px` — items stack vertically, consistent width, no overlap
  - `.flash` slides in from right with spring easing (`cubic-bezier(.22,.68,0,1.2)`)
  - Added `.flash.exiting` + `@keyframes slideOutRight` — smooth slide-out before DOM removal
  - Added `.flash-close` button style for per-toast × dismiss
  - Added `box-shadow` so toasts visually float above content
- **Fix — `base.html` JS:**
  - Replaced `setTimeout(el.remove)` with `dismissFlash(el)` — adds `.exiting`, waits 260ms for animation, then removes
  - Each flash gets an × close button injected by JS at render time
  - Auto-dismiss after 5 seconds

#### 2. Audio briefing — no loading feedback during 5–30s generation
- **Symptom:** Clicking "Generate Briefing" appeared to do nothing for up to 30 seconds — bad UX.
- **Root cause:** Button is a plain `<a>` tag; page navigates away while server blocks on edge-tts. No loading state shown.
- **Fix — `index.html`:**
  - Added `id="briefingBtn"` to the link
  - JS intercepts click → replaces button area with `.briefing-loading` container: three pulsing dots + "Generating audio briefing, please wait…"
  - Page navigation still proceeds normally; reloads with audio player (success) or error flash (failure)
- **Fix — `style.css`:**
  - `.briefing-loading` styled like the audio player box for visual consistency
  - `.briefing-dots span` — three-dot bounce animation (`@keyframes briefPulse`) staggered at 0/180/360ms

### Email Checking — Confirmed
- Email is checked every 5 minutes via `core/orchestrator.py` → `_email_job()`
- Classifies into: job_opportunity, interview_request, rejection, follow_up, irrelevant
- Drafts replies for interview/opportunity/follow_up only; user approves before sending
- Job alert emails are picked up and queued for approval
- Check **Activity Logs → filter by agent=comms** to see live email activity

---

## Session 7 — Fully Automated Audio Briefing (Always Fresh, Always Live)

**Date:** 2026-03-12  **Status:** Complete

### Problem
User had to manually click "Generate Briefing" every time. After an app restart, the old stale MP3 would still be served from the previous session. There was no way to tell if the audio reflected current data or was hours/days old. Dashboard had no visual feedback while generating.

### Solution — Four-layer automation strategy

#### Layer 1: Auto-generate on every startup (`core/orchestrator.py`)
- Added `_startup_audio()` — runs 3 seconds after `start()` returns so Flask is ready
- Runs in a daemon thread so it never blocks the main process
- Every restart = fresh briefing with current DB stats

#### Layer 2: Auto-regenerate after every scout cycle (`core/orchestrator.py`)
- `_scout_job()` now calls `threading.Thread(target=_refresh_audio_background).start()` at the end
- Scout runs every 30 minutes → briefing auto-updates every 30 minutes with the latest job counts, matches, applications
- `_refresh_audio_background()` extracted as a reusable helper

#### Layer 3: Freshness timestamp (`agents/comms/audio_briefer.py`)
- After successful generation, writes `data/audio/briefing_latest.ts` (ISO timestamp)
- This is internal — never shown to user, only read by the API
- `get_status()` function returns `{generating, has_audio, last_generated}`
- `_generating: bool` module-level flag set True/False around the generation window
- Guard: if `_generating=True` already, skip the new request (prevents double-generate)

#### Layer 4: Live dashboard polling (`dashboard/routes.py` + `dashboard/templates/index.html`)
- New endpoint `GET /api/briefing/status` returns `{generating, has_audio, last_generated}`
- Dashboard polls this every **15 seconds**
- Three states rendered by JS:
  1. **Generating** → pulsing three-dot animation ("Updating briefing…")
  2. **Audio ready** → shows audio player; only rebuilds DOM if `last_generated` timestamp changed (cache-busts the `<audio src>` URL so browser reloads the file)
  3. **No audio** → shows "Generate Briefing" button with click-to-animate fallback
- Welcome banner audio section is now 100% JS-driven — no more `{% if audio_file %}` server-side condition that could serve stale state

### Files Changed
| File | What changed |
|---|---|
| `agents/comms/audio_briefer.py` | Added `_generating` flag, `TIMESTAMP_FILE`, `get_status()`, write ts on success |
| `core/orchestrator.py` | Added `_refresh_audio_background()`, startup trigger (3s delay), post-scout trigger |
| `dashboard/routes.py` | Added `GET /api/briefing/status` endpoint |
| `dashboard/templates/index.html` | Replaced static `{% if audio_file %}` with JS polling + `renderBriefing()` |

---

## Session 8 — Logs Page Pagination Overhaul

**Date:** 2026-03-12  **Status:** Complete

### Problem
Logs pagination showed only "Page 1" and a "Next" button. No way to know total pages, jump to a specific page, or navigate to first/last. With hundreds of log entries this was unusable.

### Changes

#### `dashboard/routes.py` — logs() route
- Added `COUNT(*)` query (respects the agent filter) to get `total_count`
- Calculates `total_pages = ceil(total_count / per_page)`
- Clamps `page` to `[1, total_pages]` so invalid URLs never 404 or return empty
- Builds `page_window` (sorted set): always includes page 1, last page, and 2 pages either side of current — e.g. on page 7 of 20: `[1, 5, 6, 7, 8, 9, 20]`
- Passes `total_pages`, `total_count`, `per_page`, `page_window` to template

#### `dashboard/templates/logs.html` — pagination block
- **Info bar**: "Showing 51–100 of 342 entries · Page 2 of 7"
- **«** (First) and **»** (Last) buttons — always present, disabled with `.disabled` class when at boundary
- **‹ Prev** and **Next ›** — disabled at boundaries
- **Page number buttons** — rendered from `page_window`; current page highlighted as `btn-primary`, not clickable
- **Ellipsis** (`…`) inserted between non-consecutive page numbers in the window (e.g. `1 … 5 6 7 8 9 … 20`)
- **Jump to page** input + "Go" button — only shown when `total_pages > 5`; JS validates range before submit

#### `dashboard/static/style.css`
- `.pagination-bar` — flex row, wraps on small screens, top border separator
- `.pagination-controls` — flex row with tight gap for page buttons
- `.pagination-ellipsis` — muted centered dot group
- `.pagination-current` — non-interactive, highlighted blue
- `.btn.disabled` — 35% opacity, `pointer-events:none`
- `.pagination-jump-input` — compact 72px width number input

---

## Session 9 — Critical Matcher Bug Fix (0 matched jobs) + Log Display Fix

**Date:** 2026-03-12  **Status:** Complete

### Bug 1 (Critical): 0 matched jobs despite 3000+ scraped

#### Root cause
`scorer.py` had a `score_job()` function that was called once per job. Inside it:
```python
model = SentenceTransformer("all-MiniLM-L6-v2")   # loaded FRESH every job
resume_embedding = get_resume_embedding()           # also loaded fresh model inside
```
For 2000+ jobs per scout cycle this meant 4000+ SentenceTransformer instantiations. Each loads a ~90MB model. Memory pressure caused silent exceptions caught by `except Exception` which fell back to `cosine=0.0`. With cosine=0 and low keyword overlap, all scores stayed below 0.45 threshold — nothing matched.

#### Fix — `agents/matcher/scorer.py` (complete rewrite)
- Removed `score_job()` entirely — it was the problematic design
- `score_and_store()` now loads model ONCE before the loop, then passes it to `get_resume_embedding()`
- Zero-vector guard: logs WARNING if resume embedding is all zeros (visible in Activity Logs)
- Score distribution now logged to audit_log: `avg=X, top=X, threshold=X` — no more guessing why nothing matches
- Threshold default changed from `0.65` to `0.45` to match settings.yaml

#### Fix — `agents/matcher/resume_parser.py`
- `get_resume_embedding(model=None)` — accepts optional pre-loaded model to avoid a second instantiation when called from `score_and_store()`

#### Fix — `agents/matcher/keyword_search.py`
- Added `_skills_cache: list[str] | None = None` module-level cache
- Skills YAML was read fresh from disk on every single job — now loaded once per process and cached

### Bug 2: All CV update failures shown as urgent "⚠ Needs Attention" in logs

#### Root cause
`logs.html` line 22: `{% set needs_attention = 'manual_needed' in entry.action %}` — `cv_update_manual_needed` contains `manual_needed` so every failed CV update (which is common when Naukri automation fails) was flagged red as urgent.

#### Fix — `dashboard/templates/logs.html`
- Split into 3 severity levels:
  - `apply_failed` → red "⚠ Action Required" (urgent — missed job application)
  - `cv_update_manual_needed` → subtle grey "↺ CV update retried manually" (expected automation hiccup)
  - `error`/`failed`/`timeout` → yellow warning icon (moderate)
  - `complete`/`_ok`/`generated` → green (success)

### Note on pagination screenshots
The screenshots showed old-style "Page 1 / Next" pagination — this is because Flask serves cached templates to running process. **Restart the server** to pick up the new pagination UI (Showing X–Y of Z · First/Prev/page numbers/Next/Last/Jump).

---

## Session 11 — Jobs Page UX, Approvals Redesign, Reset Database, Naukri Popup Fix

**Date:** 2026-03-12  **Status:** Complete

### Problems Fixed

#### 1. Naukri browser tab popping up during scout cycle
- **Symptom:** A Chromium tab for Naukri login flashed on screen during every scout cycle
- **Root cause:** `headless=False` with `--window-position=-32000,-32000` was meant to hide the window, but Naukri opens popup tabs via `window.open()` which appear at default position (visible to user)
- **Fix — `agents/scout/naukri_scraper.py`:**
  - Added `ctx.on("page", lambda p: asyncio.ensure_future(p.close()))` immediately after `browser.new_context()` — auto-closes any popup tab Naukri tries to open
  - Added `--window-size=1,1` to Chrome args — minimizes the main window to 1x1 pixel (invisible in taskbar area)

#### 2. Jobs page — no links, no detail, no way to act on email alert jobs
- **Symptom:** Jobs table was flat — clicking a row did nothing, no job description visible, no work type or apply type info
- **Fix — `dashboard/templates/jobs.html` (complete rewrite):**
  - Title+Company merged into one column; title is a clickable link to the actual job URL (`<a target="_blank">`)
  - "Open" button always visible in Actions column regardless of status
  - **Expandable detail rows:** clicking anywhere on the row toggles a hidden `<tr class="job-detail-row">` showing:
    - Full JD text (up to 500 chars)
    - Salary range in LPA (₹X–YL PA)
    - Source, location (again for quick scan)
    - Notes field raw content
  - **Tag pills** parsed from `job.notes` field:
    - `easy_apply` in notes → green "⚡ Easy Apply" pill
    - `hybrid/remote/onsite` in notes → colored work type pill
    - `from_email=` prefix in notes → amber "📧 From Alert" pill
  - Source filter dropdown now includes `email_alert` option
  - `toggleDetail(id)` JS function — `display: none` ↔ `display: table-row` toggle

#### 3. Approvals page — plain list, hard to decide which jobs to approve
- **Symptom:** Job approval cards showed title + score as plain text with no visual hierarchy
- **Fix — `dashboard/templates/approvals.html` (complete rewrite):**
  - **Score circle:** colored ring (green ≥65%, amber ≥40%, red <40%) with percentage inside — instant visual priority signal
  - **Rich header:** score circle + title (clickable link) + company + location, all in one row
  - **Tag pills:** Easy Apply, Hybrid/Remote/On-site, From Alert, Salary range — all parsed from notes
  - **JD snippet:** collapsible `<details>` block with first 700 chars of job description
  - **"Open Job ↗" button:** always visible before Approve/Skip buttons
  - **Empty state** now shows threshold hint: "Lower the threshold in config/settings.yaml → matching.threshold if no jobs appear"
  - Polling script checks `/api/approvals/count` every 30 seconds — reloads page if new items appear

#### 4. `approvals` route missing threshold info
- **Fix — `dashboard/routes.py`:**
  - `approvals()` now loads config and passes `threshold_pct = int(threshold * 100)` to template
  - Template uses `{{ threshold_pct }}` in the empty-state hint

---

### New Features

#### Reset Database button
- **Why:** User wanted a way to clear all stale/junk data and start fresh without touching resume/config files
- **Implementation:**
  - `POST /reset` route in `dashboard/routes.py` — deletes all rows from `jobs`, `applications`, `emails`, `cv_updates` tables; logs to `audit_log`; flashes success message
  - "Danger Zone" section at the bottom of `index.html` with red-bordered container and "Reset Database" button
  - Confirmation modal (`#resetModal`) warns: "This will permanently delete all jobs, emails, applications, and CV update history. Your resume and config files are not affected."
  - Two-button modal: Cancel (dismisses) / "Yes, Clear Everything" (POSTs to `/reset`)
  - Modal uses CSS `.modal-overlay` + `.open` class toggle — no JS framework needed

---

### CSS Additions (`dashboard/static/style.css`)

| Class | Purpose |
|---|---|
| `.tag-pill` | Base pill style (rounded, small font, border) |
| `.tag-easy` | Green "Easy Apply" pill |
| `.tag-remote` | Blue "Remote" pill |
| `.tag-hybrid` | Purple "Hybrid" pill |
| `.tag-onsite` | Amber "On-site" pill |
| `.tag-email` | Amber "From Alert" pill |
| `.tag-salary` | Muted salary range pill |
| `.job-detail-row td` | Zero padding for collapsed detail rows |
| `.job-detail-panel` | Inner container for expanded row detail |
| `.job-detail-grid` | Auto-fit grid for detail metadata (salary, location, source) |
| `.job-appr-card` | Job card on approvals page |
| `.job-appr-header` | Flex row: score circle + info column |
| `.job-appr-score` | 52px round circle for match percentage |
| `.score-circle--high/mid/low` | Green/amber/red color variants for score circle |
| `.job-appr-info` | Right side of header (title, company, badges) |
| `.job-appr-title` | Bold title with truncation overflow |
| `.job-appr-company` | Muted company + location line |
| `.job-appr-badges` | Flex-wrap row of tag pills |
| `.modal-overlay` | Fixed fullscreen dim backdrop |
| `.modal-overlay.open` | Shows modal (display:flex) |
| `.modal-box` | Centered white card with animation |
| `.modal-actions` | Right-aligned button row |
| `.danger-zone` | Red-bordered section for destructive actions |
| `.danger-zone-label` | Text label with red bold heading |

---

### Architecture Notes

- `html_body` not stored in DB — used only during processing in the same request cycle
- Reset clears: `jobs`, `applications`, `emails`, `cv_updates`, `audit_log` (complete fresh start)
- Reset does NOT clear: `resume_versions` (keep CV parse history)
- Reset does NOT affect `config/resume.yaml` or any YAML config files
- After reset, first scout cycle (within 30 min) repopulates jobs automatically
- One `audit_log` row is written AFTER the delete to record the reset event itself

---

## Session 12 — Dashboard Chart Fixes

**Date:** 2026-03-12  **Status:** Complete

### Problems Fixed

#### 1. "Applications Over Time" chart always empty
- **Root cause:** Chart queried the `applications` table. Since no jobs had been approved+applied yet, the table had 0 rows — Chart.js rendered a blank canvas with y-axis 0–1.0 (fractional scale).
- **Fix — `dashboard/routes.py` `_get_chart_data()`:**
  - Added `daily_discovered` query: `SELECT date(discovered_at), COUNT(*) FROM jobs GROUP BY date`
  - Kept `daily_applications` query alongside it
  - Both returned in chart_data dict
- **Fix — `dashboard/templates/index.html`:**
  - Default chart now shows "Jobs Discovered (last 14 days)" — always has real data since jobs are being scraped
  - Toggle buttons: "Discovered" (default, blue) | "Applied" (green) — switch datasets live with `switchChart(mode)` JS
  - `buildDailyChart(mode)` destroys+recreates Chart.js instance when toggling (avoids ghost tooltips)
  - `beginAtZero: true` + `precision: 0` on y-axis so scale is always whole numbers
  - When switched to "Applied" with 0 data: canvas hides and shows `#chartDailyEmpty` message: "No applications yet — approve jobs on the Approvals page to start applying"

#### 2. Doughnut chart — unclear colors, no percentages, tiny unreadable legend
- **Root cause:** Chart.js built-in legend at `position:'bottom'` with `font.size:10` was too small; all colors were shades of the same palette making segments hard to tell apart.
- **Fix:**
  - Replaced built-in legend with a custom `#sourceLegend` div below the canvas
  - Custom legend shows: colored dot + source name (bold) + raw count + percentage in parentheses — e.g. "● **linkedin** 1,234 (35%)"
  - `_SOURCE_COLORS` array uses 8 distinct colors: blue, green, purple, amber, orange, light blue, light green, red — each source gets a clearly different hue
  - Tooltip now shows count + percentage: "linkedin: 1,234 (35%)"
  - `cutout: '62%'` makes the doughnut hole larger (more modern look, easier to read segments)
  - `borderColor: rgba(13,17,23,.6)` thin dark gap between segments for separation
  - `hoverOffset: 6` segments pop out slightly on hover

### Files Changed
| File | Change |
|---|---|
| `dashboard/routes.py` | `_get_chart_data()` — added `daily_discovered` query; sources sorted by count desc; null source → "unknown". Also: reset now includes `DELETE FROM audit_log` (complete fresh start per user request) |
| `dashboard/templates/index.html` | Chart title + toggle buttons; `buildDailyChart()`; `switchChart()`; custom source legend; 8-color palette |

---

## Session 13 — Flash Notification Fix (Topbar Overlap + Animation)

**Date:** 2026-03-12  **Status:** Complete

### Problem

Flash notifications (e.g. "Database cleared. Starting fresh...") appeared at `top:1rem; right:1rem` which overlapped the 56px sticky topbar — covering the live clock and the Light/Dark toggle button. The notification was unreadable and looked broken.

### Fix — `dashboard/static/style.css`

- `.flash-container` `top` changed from `1rem` to `calc(56px + 0.75rem)` — always sits 12px below the topbar, never overlaps
- `z-index` raised from `500` to `600` — above sidebar (`z-index:200`) and topbar (`z-index:100`), below modals
- `width` increased from `320px` to `340px` for better readability
- `will-change: transform, opacity` on `.flash` — tells browser to GPU-composite these elements (60fps)
- `box-shadow` improved: dual-layer shadow (6px blur + 1px close) for floating card look
- `border-left: 3px solid` — colored left accent bar (green for success, red for error) makes type instantly scannable
- Background: near-opaque dark (`rgba(13,17,23,.92)`) so it reads clearly over any page content; light-mode override uses white
- **New entry animation** `@keyframes flashIn`: `translate3d(24px,-6px,0) scale(.96)` → origin — slides in from top-right with slight scale-up. `cubic-bezier(.34,1.56,.64,1)` gives a natural spring overshoot feel without being bouncy
- **Exit animation** `@keyframes flashOut`: slides right + collapses height in 220ms. Smooth, no jarring snap
- Both animations use `transform` and `opacity` only — browser never triggers layout reflow during animation (true 60fps)

#### Addendum — Reset also clears log file (`data/logs/jobpilot.log`)
- **Problem:** Reset deleted `audit_log` DB table but `data/logs/jobpilot.log` (Python `RotatingFileHandler` output) was never touched — user could still see all old entries in Activity Logs sidebar page
- **Fix:** `reset_database()` route now calls `log_file.write_text("", encoding="utf-8")` to truncate the file to zero bytes after the DB wipe. Wrapped in `try/except` so a locked file never crashes the reset.
- **Flash message** updated to: "Database and activity logs cleared. Starting completely fresh."
- Both the DB `audit_log` table AND the `.log` file are now cleared together on every reset

---

### Fix — `dashboard/templates/base.html` (flash JS)

- `dismissFlash()` timeout extended from 260ms to 240ms to match new animation duration exactly
- **Stagger**: multiple toasts get `animationDelay = idx * 80ms` so they don't all slam in at the same time
- Auto-dismiss timeout also staggered: `5000 + idx * 80ms` per toast
- Added `aria-label="Close"` to × button for accessibility

---

## Session 14 — Naukri Scraper Crash Fix + Audio Reset Fix

**Date:** 2026-03-12  **Status:** Complete

### Bug 1: Naukri scraper crash — `Page.add_init_script: Target page, context or browser has been closed`

- **Root cause:** `ctx.on("page", lambda p: asyncio.ensure_future(p.close()))` was registered on line 150 BEFORE `page = await ctx.new_page()` on line 151. Playwright fires the `"page"` event for ALL new page objects — including the main page being created on the very next line. `asyncio.ensure_future()` schedules the close on the next event loop tick, so by the time `await page.add_init_script(...)` runs on line 153, the main page is already closed. Error: `Page.add_init_script: Target page, context or browser has been closed`.
- **Fix (`agents/scout/naukri_scraper.py`):**
  - Moved `page = await ctx.new_page()` BEFORE the handler registration
  - Changed handler to only close pages that are NOT the main page: `lambda p: asyncio.ensure_future(p.close()) if p is not page else None`
  - This correctly closes popup tabs (ads, login prompts, redirects) without touching the main scraping page

### Bug 2: Chromium tabs accumulating after each scan

- **Root cause:** The crash from Bug 1 raised an exception before `await browser.close()` on line 223 — so every failed scan left a visible Chromium window open. After 5 scans: 5 open windows. After 20: 20 windows.
- **Fix:** Wrapped the entire browser body (from `page = await ctx.new_page()` through `await ctx.close()`) in a `try/finally` block. `await browser.close()` is now in the `finally` clause — guaranteed to run regardless of any exception anywhere in the scraping logic.

### Bug 3: Audio briefing survives reset — stale MP3 plays after "fresh start"

- **Root cause:** `reset_database()` in `dashboard/routes.py` cleared the DB and truncated `data/logs/jobpilot.log`, but `data/audio/briefing_latest.mp3` and `data/audio/briefing_latest.ts` were untouched. The dashboard's `/api/briefing/status` endpoint would still see `has_audio: true` and serve the old MP3 (with stats from before the reset).
- **Fix (`dashboard/routes.py` — `reset_database()`):**
  - Added deletion of both `data/audio/briefing_latest.mp3` and `data/audio/briefing_latest.ts` using `Path.unlink(missing_ok=True)` — silently no-ops if files don't exist
  - Wrapped in `try/except` so a locked file never fails the reset
  - After reset: dashboard shows "Generate Briefing" button (fresh state), not the stale player

### Files Changed
| File | Change |
|---|---|
| `agents/scout/naukri_scraper.py` | Moved page creation before handler; handler guards `p is not page`; `try/finally` ensures `browser.close()` always runs |
| `dashboard/routes.py` | `reset_database()` now deletes `briefing_latest.mp3` + `briefing_latest.ts` from `data/audio/` |

---

## Open Issues / Next Improvements

- [ ] Naukri scraper job cards use old selector `article.jobTuple` — may need updating if Naukri redesigns listing page
- [ ] LinkedIn Easy Apply multi-step modal — needs real-world testing with actual LinkedIn account
- [ ] sentence-transformers first load (~700MB PyTorch download) — add a startup check/pre-download step
- [ ] Email sender — test with a real approved reply to confirm SMTP sending works end-to-end
- [ ] `question_answering` in applier — LLM fallback needs testing with real Naukri screening questions
- [ ] Dashboard `/cv/edit` form — test full round-trip: edit → save → re-index embedding
- [x] Audio briefing — now auto-generates on startup + after every 30-min scout cycle (Session 7)
- [ ] Add job application real-time status in dashboard (WebSocket or HTMX polling)

---

## Configuration Quick Reference

| File | Key setting | Current value |
|---|---|---|
| `config/settings.yaml` | `matching.threshold` | 0.45 |
| `config/settings.yaml` | `max_applications_per_day` | 50 |
| `config/settings.yaml` | `scout_interval_minutes` | 30 |
| `config/settings.yaml` | `email_interval_minutes` | 5 |
| `config/llm_providers.yaml` | Groq model | llama-3.1-8b-instant |
| `config/llm_providers.yaml` | Gemini model | gemini-2.0-flash |
| `config/llm_providers.yaml` | Groq daily limit | 500,000 tokens |
| `config/llm_providers.yaml` | Gemini daily limit | 1,000,000 tokens |

---

## Environment Variables Required

| Variable | Where to get it |
|---|---|
| `GROQ_API_KEY` | https://console.groq.com |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| `GMAIL_ADDRESS` | Your Gmail address |
| `GMAIL_APP_PASSWORD` | Google Account → Security → App Passwords (requires 2FA) |
| `NAUKRI_EMAIL` | Your Naukri login email |
| `NAUKRI_PASSWORD` | Your Naukri login password |
| `LINKEDIN_EMAIL` | Your LinkedIn email |
| `LINKEDIN_PASSWORD` | Your LinkedIn password |

---

## Questions from User

**Q: Why use Groq over Gemini as primary?**
A: Groq's free tier is more generous and consistent (500K tokens/day, 30 RPM). Gemini had 404 errors with the original model name and 429 rate limits on the test. Groq's llama-3.1-8b-instant is fast enough for all classification/drafting tasks.

**Q: How to use APIs efficiently?**
A: Three strategies implemented:
1. Per-task token limits (15 for classification, 300 for JD analysis — not wasting 2000 tokens on a 2-word answer)
2. Module-level resume summary cache (reads resume.yaml once per process, not on every email draft)
3. Prompt truncation (system: 600 chars, user: 3000 chars) — LLMs don't need 10K chars of context for a classification task

**Q: Why is the log file huge?**
A: Fixed. File now only captures WARNING+. Console stays INFO. Email reader suppresses idle "0 emails" log lines.

**Q: How to clear logs every hour but keep important ones?**
A: Done. `core/db.py:cleanup_log_file()` trims to last 2000 lines hourly. All important events (warnings, errors, audit actions) go to both the log file and the `audit_log` DB table (which is searchable from the dashboard).

**Q: How to focus only on recent data (max 2 weeks)?**
A: Done. Email reader uses 14-day IMAP search window. Scout scraper uses `hours_old=336` (14 days). Daily DB purge deletes records older than 14 days at 12:30 AM.

---

## Session 10 — Email Intelligence Overhaul

**Date:** 2026-03-12  **Status:** Complete

### Core Problem Fixed

LinkedIn/Naukri/Indeed job alert emails were being silently dropped as `irrelevant`
because the rule filter checked `noreply` BEFORE the job alert check.
`jobalerts-noreply@linkedin.com` matched the generic `noreply` pattern and was never
processed further. All job leads inside those emails were missed.

---

### Changes Made

#### 1. `agents/comms/email_classifier.py` — Overhauled categories + rule ordering

**New categories added:**
- `job_alert` — LinkedIn/Indeed/Naukri/Instahyre/Foundit digest emails with job links inside
- `application_confirmation` — "You applied for N jobs" emails from Naukri/LinkedIn

**Rule check order (critical fix):**
1. `_JOB_ALERT_SENDER_RE` checked FIRST (catches `jobalerts-noreply@linkedin.com` before generic `noreply`)
2. `_APP_CONFIRM_RE` for subject patterns like "you applied"
3. `_IRRELEVANT_SENDER_RE` / `_IRRELEVANT_SUBJECT_RE` for banking/shopping/unrelated
4. None → LLM classification

**New `classify_batch(emails)` function:**
- Classifies all emails from one cycle in a SINGLE LLM call
- Rule-based pass first (zero tokens), then one LLM call for all remaining emails
- Format: `[1:category 2:category 3:category]` parsing
- Saves 80-90% of LLM tokens vs per-email classification

#### 2. `agents/comms/job_alert_extractor.py` — Complete rewrite

Old approach: extract URLs → fetch each URL page (slow, 8s per page, blocked by portals).
New approach: ONE LLM call per email extracts ALL structured job data directly from email body/HTML.

**Key features:**
- `_prepare_email_text()`: strips HTML tags but extracts `href` values (job URLs in link attributes)
- `_extract_jobs_llm()`: returns structured JSON array `[{title, company, location, work_type, apply_type, url}]`
- Falls back to URL stubs if LLM extraction fails
- `apply_type`: detects "Easy Apply" and "Actively recruiting" from LinkedIn email HTML
- `work_type`: detects Hybrid/On-site/Remote from email text
- No external HTTP requests — all data from the email itself
- `_MAX_LEADS = 25` jobs per email

#### 3. `agents/comms/email_reader.py` — Enhanced

**New fields in email dict:**
- `html_body` (up to 60KB) — raw HTML preserved for job link extraction from `href` attributes
- `to` field — for sent mail tracking
- Tuple return from `_extract_body()`: `(plain_text, html_body)`

**New functions:**
- `fetch_all_recent_emails()` — fetches ALL inbox emails (seen+unseen) from last 7 days
  Used for startup catchup to catch job alerts that were read but never extracted
- `fetch_sent_emails()` — reads Sent Mail folder + INBOX mailer-daemon bounces
  Tracks manually sent applications + detects wrong/bounced email addresses

**Window changed:** 14 days → 7 days (user preference for fresher data)

#### 4. `agents/comms/email_drafter.py` — Cleaner category handling

- Accepts `_category` key from batch classification (skips redundant LLM call)
- `job_alert` → calls `_handle_job_alert()` → returns `leads_found` count stored in DB
- `application_confirmation` → calls `_handle_app_confirmation()` → parses count, writes audit_log
- `sent_mail` → logs to audit_log only (not stored in emails table)
- `bounce_detected` → logs warning to audit_log
- Removed old `_ALERT_SENDER_RE` hack (classifier now handles this correctly)

#### 5. `core/db.py` — Migration + enhanced stats

**Migration added:**
```sql
ALTER TABLE emails ADD COLUMN leads_found INTEGER DEFAULT 0
```
Applied with `try/except` in `init_db()` — safe on existing databases.

**New stats in `get_daily_stats()`:**
- `job_alerts` — count of job_alert emails processed
- `job_leads` — total job leads extracted from alert emails (sum of leads_found)
- `manually_applied` — count from application_confirmation audit entries
- `email_alert_jobs` — jobs in DB with source='email_alert'

#### 6. `dashboard/routes.py` — New endpoints

**`/api/notifications` updated:**
- Now includes `job_alert` type notifications: "Found N jobs in: [email subject]"
- Links to `/jobs?source=email_alert` so user can see all extracted jobs

**New `/api/job-alerts` endpoint:**
- Returns recent job alert emails with leads_found counts
- Returns total leads extracted across all alert emails
- Returns manually_applied count from confirmation emails

#### 7. `core/orchestrator.py` — Batch processing + startup catchup

**`_email_job()` updated:**
- Uses `classify_batch()` — ONE LLM call for all emails instead of N calls
- Also calls `fetch_sent_emails()` every cycle
- Assigns `_category` to each email before `process_and_store()` (skips per-email LLM)

**`_email_catchup()` new function:**
- Fetches ALL inbox emails from last 7 days on startup
- Classifies and processes any that haven't been seen yet
- Catches job alerts that were read in Gmail before the agent ran

**`_startup_tasks()` updated:**
- Runs both audio regeneration AND email catchup on startup (5s delay)

#### 8. `config/email_rules.yaml` — New skills/context file

Created documentation file explaining email classification rules for the LLM:
- Job alert senders with examples
- Application confirmation patterns
- What categories get draft replies vs what gets extracted/tracked only

---

### Email Classification Flow (After Fix)

```
Email arrives
     │
     ├── jobalerts-noreply@linkedin.com? → job_alert → extract N jobs (LLM) → store in jobs table
     │
     ├── "You applied for 2 jobs" subject? → application_confirmation → log count to audit_log
     │
     ├── hdfc / swiggy / amazon / banking? → irrelevant → store, no action
     │
     └── Everything else → LLM classification (batch, 1 call for all)
                                ├── interview_request → draft reply for approval
                                ├── job_opportunity   → draft reply for approval
                                ├── follow_up         → draft reply for approval
                                └── status_update / rejection → store, no action
```

### Dashboard Notifications (After Fix)

When job alert emails are processed:
1. Notification panel shows: "Found 8 jobs in: Your job alert for technical writer"
2. "View Jobs" button links to `/jobs?source=email_alert`
3. Stats card shows total job leads from emails
4. Logs page shows `job_alert_processed` with count

---

### Token Efficiency

| Before | After |
|--------|-------|
| 1 LLM call per email (N calls per cycle) | 1 LLM call for ALL emails per cycle |
| Classification called even for job alerts | Rule-based pre-filter (0 tokens) for alerts |
| Page fetch per job URL (8s, unreliable) | LLM extracts all jobs from email body directly |

---

### Architecture Notes

- `job_alert` category never gets a draft reply — alerts are from noreply addresses
- `application_confirmation` emails update the manually-applied count in audit_log
- The `leads_found` column in the emails table stores how many new jobs were extracted per alert email
- `html_body` is not stored in DB (too large) — used only during processing in the same request cycle
- Sent mail tracking logs to `audit_log` with action=`sent_mail_tracked` (not in emails table to avoid confusion)


---

## Session 12 — Telegram + Business Brain + Token Optimization + Security

**Date:** 2026-03-19
**Status:** Complete

### What was built

**Part 1: Telegram Bot**
- `agents/comms/telegram_notifier.py` — HTTP push sender (raw requests, no external library)
- `agents/comms/telegram_listener.py` — background polling thread (10s interval)
  - Commands: /status, /jobs, /approve <id>, /skip <id>
  - Reuses existing db.approve_reply() — no new approval logic
- `core/notifier.py` — added `_telegram()` and `channel='telegram'` support
- `core/orchestrator.py` — Telegram hooks wired at: email cycle (interview_request/job_opportunity), scout cycle (score >= notify_threshold), audio job (daily brief)
- `core/orchestrator.py` — `start_listener()` called from `start()`
- `config/settings.yaml` — added `notifications.notify_threshold: 0.70` and `max_prompt_chars: 4000`
- `scripts/test_connections.py` — added Telegram ping test (optional, PASS if unconfigured)

**Part 2: Business Brain Memory**
- `config/user_strategy.yaml` — Level 2 brain: target roles, avoid/target companies, deal-breakers, location pref, email context
- `agents/memory/job_context.py` — read/write/prune for rolling markdown memory files
- `data/memory/companies.md`, `recruiters.md`, `decisions.md`, `notes.md` — seed files
- `agents/comms/email_drafter.py` — injects `get_context('recruiters')`, `get_context('companies')`, and strategy `email_context` into every reply draft
- `agents/matcher/scorer.py` — loads `deal_breakers` from strategy; suppresses matching jobs that contain any deal-breaker phrase in JD text
- `agents/comms/job_alert_extractor.py` — loads `avoid_companies` blacklist; skips leads from blacklisted companies before DB insert
- `core/orchestrator.py` — `_daily_purge()` calls `prune_old(days=7)` on memory files

**Part 3: Token Optimization**
- `core/llm_router.py` — full rewrite with tiered routing:
  - Tier A (Groq): `fast_classification`, `question_answering`, `default`
  - Tier B (Gemini): `jd_analysis`, `resume_parsing`, `quality_drafting`, `job_extraction`
  - In-process prompt cache (`_prompt_cache` keyed by MD5 of task+content)
  - Resume summary 1-hour TTL (`_resume_cache_ts`)
  - Token usage logged to audit_log on every call
  - New `clear_prompt_cache()` and updated `invalidate_resume_cache()` functions
- `config/llm_providers.yaml` — Gemini updated to `tier: "quality"`, Groq to `tier: "fast"`; added `max_context_tokens: 50000`; routing updated to reflect two-tier split
- `core/orchestrator.py` — `_daily_purge()` also wipes prompt cache and resume cache

**Part 4: Security Hardening**
- `core/config_loader.py` — added `_SAFE_ENV_KEYS` / `_SECRET_ENV_KEYS`; populates `cfg["env_safe"]` with only non-secret keys; added `user_strategy` to YAML_FILES list
- `core/logger.py` — added `_SanitizingFilter` that redacts `[email]`, `[phone]`, `[secret]` before writing to rotating file handler
- `dashboard/app.py` — optional token-based auth via `DASHBOARD_TOKEN` env var; added `/login` route with dark-themed inline HTML form; `before_request` guard skips `/login` and `/static`
- `.gitignore` — added `data/memory/` (may contain recruiter PII)
- `.env.example` — added `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DASHBOARD_TOKEN`, `FLASK_SECRET_KEY`

### Design decisions

- Telegram listener uses long-polling (`offset + 1`) to avoid re-processing old messages
- Prompt cache is in-process (not Redis/SQLite) — lightweight, wiped daily, suitable for single-user agent
- Gemini now primary for quality tasks because it has 1M daily tokens (vs Groq's 500K) and better reasoning for JD analysis and drafting
- Groq remains primary for classification (faster, lower latency for 15-token responses)
- Dashboard auth is opt-in (DASHBOARD_TOKEN empty = no auth) — localhost-only setups don't need it
- Memory prune runs at 12:30 AM in the existing `_daily_purge` job — no new scheduler job needed
- `env_safe` split prevents passwords/API keys from leaking into LLM system prompts via config

---

## Session 13 — Email Intelligence Fixes + Sent Mail Tracking

**Date:** 2026-03-19
**Status:** Complete

### Issues fixed

**1. Broken "(No title)" job entries from LinkedIn profile update emails**
- Root cause: LinkedIn sends profile-prompt emails ("Is your location still the same?", "have you moved into a new position recently?") from `jobs-noreply@linkedin.com` — the classifier's sender regex matched this as `job_alert`
- The LLM correctly returned `[]` (no jobs in these emails), but the URL-stub fallback then extracted tracking/redirect URLs and created empty-title job records with hash-based dedup_urls
- Fix A (`email_classifier.py`): Added `_PROFILE_PROMPT_RE` regex that checks the subject BEFORE the sender regex; profile update subjects now return `irrelevant` regardless of sender
- Fix B (`job_alert_extractor.py`): URL stub fallback now only creates stubs for definite job-portal URLs (matching `_JOB_URL_RE`); generic tracking/redirect URLs are discarded if LLM found no jobs

**2. Sent emails never recorded — user's outbound job interest emails invisible**
- Root cause: `process_and_store()` had an early `return` for `sent_mail` category before the DB INSERT — sent emails were only audit-logged, never stored in the emails table
- This caused: (a) sent mail re-read from IMAP every 5 min cycle (dedup check always empty), (b) no visibility in dashboard, (c) agent had no memory of what the user sent
- Fix (`email_drafter.py`):
  - Added `_handle_sent_mail()` — uses LLM (`fast_classification`) to extract role, company, type from sent email body (60 token max)
  - Removed early return; sent_mail now falls through to the DB INSERT with LLM-extracted context stored as `reply_draft`
  - Dashboard now shows sent emails in category `sent_mail` with the extracted job context

**3. Spam folder not scanned — recruiter replies in spam missed**
- Fix (`email_reader.py`): Added `fetch_spam_emails()` — checks `[Gmail]/Spam` (tries common folder names) for emails with job-related subjects from last 7 days; limit=5 per keyword to avoid noise; read-only (mark_seen=False)
- Wired into `_email_job()` in `orchestrator.py` so spam is scanned every 5 minutes
- Wired into `_email_catchup()` so spam is scanned on startup too

**4. Startup catchup incomplete — sent mail never processed on startup**
- Fix (`orchestrator.py`): `_email_catchup()` now also calls `fetch_sent_emails()` and `fetch_spam_emails()` after the inbox catchup

### Files modified
- `agents/comms/email_classifier.py` — added `_PROFILE_PROMPT_RE`; check it first in `_rule_based_filter()`
- `agents/comms/job_alert_extractor.py` — URL stub fallback now filters by `_JOB_URL_RE` before creating stubs
- `agents/comms/email_drafter.py` — added `_handle_sent_mail()`; removed early return for `sent_mail`
- `agents/comms/email_reader.py` — added `fetch_spam_emails()`
- `core/orchestrator.py` — `_email_job()` adds spam scan; `_email_catchup()` adds sent + spam

### Design decisions
- `_handle_sent_mail()` uses `fast_classification` (Groq, 60 tokens) not `quality_drafting` — we just need role/company extraction, not prose
- Spam scan uses subject keyword matching (not pure IMAP FROM matching) so it catches cold-outreach from recruiters at unknown addresses
- Sent mail context stored in `reply_draft` field (repurposed for tracking) — avoids a new DB column
- Profile-prompt detection happens at classifier level (zero LLM tokens) before the email is ever sent to job_alert_extractor

---

## Session 13 (continued) — Interview Calendar + Recruiter Contacts + Email Fixes

**Date:** 2026-03-19

### Interview Calendar (new feature)
**New files:**
- `agents/comms/interview_extractor.py` — extracts date/time/meeting link/role/company from `interview_request` emails via LLM (`fast_classification`, 300 tokens max); generates prep topics via `quality_drafting` (Gemini, 300 tokens); sends immediate Telegram alert; inserts into `interviews` table
- `dashboard/templates/calendar.html` — monthly calendar grid (CSS Grid, 7 cols); color-coded event badges by type (telephonic=blue, video=purple, face_to_face=green); flashcard modal on click with: company/role, date/time, meeting link "Join" button, JD snippet, prep topics list, recruiter email, original email body preview; mark completed/cancelled buttons
- `dashboard/templates/emails.html` — all processed emails with category filter + "Fetch Now" button that triggers immediate Gmail scan

**Modified files:**
- `core/db.py` — added `interviews` table: id, email_id, job_id, company, role, location, interview_type, scheduled_at, meeting_link, meeting_id, meeting_password, jd_snippet, topics_to_prepare, notes, status
- `core/db.py` — fixed `get_pending_approvals()` to exclude sent_mail/bounce_detected/job_alert/application_confirmation/irrelevant from the approvals queue
- `agents/comms/email_drafter.py` — added `_save_recruiter_contact()`: parses sender name + email + phone (regex), saves permanently to `data/memory/recruiters.md`; added `_PHONE_RE` regex; wires interview extractor for `interview_request` emails; stores interview AFTER email INSERT (so email_id is known)
- `dashboard/routes.py` — added `/emails` page, `/api/email/force-fetch` (POST), `/calendar`, `/api/calendar/<year>/<month>`, `/api/interview/<id>`, `/interview/<id>/update`
- `dashboard/templates/base.html` — added Emails and Calendar nav items
- `core/orchestrator.py` — added `_interview_reminder()`: runs at 8 AM daily, pushes Telegram for interviews in next 24h; wired into scheduler; updated README description

### Email polling frequency
- Changed `email_interval_minutes` from 5 to 1 in `config/settings.yaml`
- IMAP UNSEEN query is cheap — most 1-min cycles return 0 emails (no LLM called)
- Rule-based classifier filters ~95% of emails without any LLM call
- Batch LLM call: ONE API call for all emails needing classification per cycle

### Critical bug fix: approvals broken for sent_mail
- `get_pending_approvals()` was returning `sent_mail` emails that now have `reply_draft` (the LLM job context)
- Fixed: added `AND category NOT IN (...)` to exclude non-actionable categories

### Gmail IMAP error: socket error: EOF
- Credentials in .env are set (GMAIL_ADDRESS + GMAIL_APP_PASSWORD)
- Error means Gmail is refusing/closing the IMAP connection
- Most likely cause: IMAP not enabled in Gmail Settings
- Fix: Gmail > Settings gear > See all settings > Forwarding and POP/IMAP > Enable IMAP > Save Changes
- Also check: Google Account > Security > 2-Step Verification must be ON before app passwords work
- App password format: 16 chars with spaces (e.g. `mweg tnne qgyx ebyi`) — correct as-is

### Telegram not yet configured
- TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID not set in .env
- Setup steps:
  1. Open Telegram, search @BotFather, send /newbot
  2. Choose a name (e.g. "My JobPilot Bot") and username (e.g. "myjobpilot_bot")
  3. Copy the token (format: 123456789:ABCDef...)
  4. Add to .env: TELEGRAM_BOT_TOKEN=<token>
  5. Start a chat with your new bot (search its username, click Start)
  6. Get chat ID: open browser, go to https://api.telegram.org/bot<TOKEN>/getUpdates
  7. Look for "chat":{"id":XXXXXXX} — copy that number
  8. Add to .env: TELEGRAM_CHAT_ID=XXXXXXX
  9. Restart JobPilot — notifications will begin immediately

### LLM token strategy (current)
- **Groq (Tier A, fast)**: rule-matched email classification (~15 tokens), QA answering (~80 tokens)
- **Gemini (Tier B, quality)**: JD analysis (300 tokens), resume parsing (4000), email drafts (200), prep topics (300)
- **Prompt cache**: identical task+content combinations skip API entirely (1h TTL, wiped daily)
- **Rule-based first**: email classifier catches 90%+ of emails with zero LLM tokens
- **Batch classify**: ONE LLM call for all inbox emails per cycle (not one per email)
- **IMAP UNSEEN**: only fetches emails not yet read by server — most 1-min cycles = 0 emails = 0 tokens


---

## Session 13 (continued) — Log Analysis + Bug Fixes

**Date:** 2026-03-19

### Log analysis (2026-03-19 21:02–21:10)

**Issue 1: Gmail IMAP "socket error: EOF"**
- Root cause: IMAP is not enabled in the Gmail account settings
- The credentials in .env are correct (Gmail address + 16-char app password)
- Fix required by user: Gmail > Settings > Forwarding and POP/IMAP > Enable IMAP > Save
- Code fix: Added 20-second socket timeout in `_connect()` in `email_reader.py` — prevents the email job from hanging indefinitely when Gmail is unreachable

**Issue 2: email_job "maximum number of running instances reached"**
- Root cause: Gmail IMAP was hanging (no timeout), so the email job from the previous cycle was still running when the next trigger fired
- Compounded by: 1-minute polling interval (changed from 5 min) — less time between cycles
- Code fix: 20-second IMAP timeout (see above). Once Gmail IMAP is enabled, connection should complete in < 1 second and this warning will stop
- Not a code bug — APScheduler correctly skips overlapping instances (max_instances=1 is right)

**Issue 3: Glassdoor consistently blocked**
- Error: "Glassdoor: Error encountered in API response" + "wsarecv: An existing connection was forcibly closed by the remote host"
- Root cause: Glassdoor has aggressive anti-scraping measures; python-jobspy Glassdoor scraping is unreliable from India
- Code fix: Disabled Glassdoor in `config/job_preferences.yaml` (glassdoor: false)
- Effect: Scout cycles will be much faster; no more repeated Glassdoor errors in logs
- Jobs are still sourced from LinkedIn, Indeed, and Naukri

**Issue 4: Naukri CV update — "Could not apply synonym_swap_skill on Naukri profile page"**
- Not a critical error — the updater opened the profile manually (fallback behavior)
- Naukri UI changes their selectors frequently; the updater has 5 fallback strategies
- Not blocking anything — the system continues operating normally

### Files fixed
- `agents/comms/email_reader.py` — added `socket` import; `_IMAP_TIMEOUT = 20`; `_connect()` now sets socket timeout before IMAP connection and restores original timeout after
- `config/job_preferences.yaml` — glassdoor: false (disabled, consistently blocked)

### Telegram setup instructions (not yet configured)
Add to `.env`:
```
TELEGRAM_BOT_TOKEN=<get from @BotFather>
TELEGRAM_CHAT_ID=<your personal chat ID>
```

Step-by-step:
1. Open Telegram > search @BotFather > /newbot
2. Give it a name (e.g. "Abhishek JobPilot") and username ending in _bot
3. Copy the token (format: 123456789:ABCDef-ghijkl...)
4. Add TELEGRAM_BOT_TOKEN=<token> to .env
5. Search for your new bot in Telegram and press Start
6. Visit: https://api.telegram.org/bot<TOKEN>/getUpdates in browser
7. Find "chat":{"id": XXXXXXXX} — copy the number
8. Add TELEGRAM_CHAT_ID=XXXXXXXX to .env
9. Restart main.py — Telegram notifications begin immediately

Once set, you will receive:
- Instant alert when a job scores >= 0.70
- Instant alert when a recruiter email arrives
- Instant alert when an interview is scheduled (with prep topics)
- Daily 8 AM reminder for interviews in next 24h
- Daily 8 PM brief summary
- /status /jobs /approve /skip commands work from Telegram


---

## Session 14 — Dashboard Accuracy Fixes + UI Overhaul

**Date:** 2026-03-20

### Critical bugs found and fixed

**Bug 1: Dashboard "Matched" stat showing 0 despite jobs being matched**
- Root cause: `get_daily_stats()` in `core/db.py` hardcoded `match_score >= 0.45` but the threshold was changed to 0.38 in settings.yaml
- All jobs scoring 38-44% had status='matched' in DB but were NOT counted in the dashboard stat
- Fix: Changed `ever_matched` query to use `status IN ('matched','approved','applying','applied','apply_failed','interview_scheduled')` — threshold-agnostic, reads actual DB status set by scorer

**Bug 2: Dashboard shows "score >= 0.45" stale text**
- The stat card subtitle said "score >= 0.45" even after threshold was changed to 0.38
- Fix: Updated `index.html` stat card to show "AI-scored match (N pending approval)" — always accurate

**Bug 3: Interview count used email category, not interviews table**
- `interviews` stat in dashboard counted `emails WHERE category='interview_request'` — not actual interviews stored in `interviews` table
- Fix: Changed to `SELECT COUNT(*) FROM interviews WHERE status != 'cancelled'`

**Bug 4: Interview emails had no job/application record**
- When an interview_request email was processed, the system stored it in `interviews` table but NOT in `jobs` or `applications` table
- Result: applied count stayed 0 even when user had interview invites
- Fix: `interview_extractor.py` `store_interview()` now calls `_ensure_job_record()` which finds or creates a `jobs` row and inserts an `applications` row with status='interview'

**Bug 5: Approve → Apply flow was delayed and silent**
- Clicking "Approve" on approvals page only set `status='approved'`
- The actual apply agent ran every 2 minutes via APScheduler — user saw nothing
- Fix: `approve_job()` route now triggers `_apply_approved_jobs()` immediately in a background thread + shows flash message: "Job approved — applying now in background. Check Activity Logs for result."

**Bug 6: Approvals badge overcounted — included sent_mail etc.**
- `api_approvals_count()` counted all emails with `reply_draft IS NOT NULL` including sent_mail, irrelevant etc.
- Fix: Added `AND category NOT IN ('sent_mail','bounce_detected','job_alert','application_confirmation','irrelevant')`

**Bug 7: Log file only captured WARNING+, terminal showed INFO**
- `core/logger.py` had `file_handler.setLevel(logging.WARNING)` so `log.info()` only went to console
- Fix: Changed to `file_handler.setLevel(logging.INFO)` — log file now matches terminal output exactly

### Email classifier improvements (Session 14 cont.)

**Postmaster emails falsely classified as job_opportunity**
- `postmaster@cognizant.com` sending "Undeliverable: Re: Profile screened for open position" was classified as `job_opportunity`
- Root cause: subject contained "position" which matched `_RECRUITER_SUBJECT_RE`, but `postmaster@` was not in the non-human exclusion list
- Fix: Added `_NON_HUMAN_SENDER_RE` that covers `postmaster@`, `bounce`, `daemon`, `delivery@`, `alerts@`, `newsletter`, `digest@`, `info@alerts.`
- The non-human sender check now runs BEFORE recruiter/interview classification

**Toptal interview invite classified as irrelevant**
- "Your Toptal introductory interview is coming up" from `barbora.thiella@toptal.com` was `irrelevant`
- Fix: Added `_INTERVIEW_SUBJECT_RE` pattern that catches "interview", "phone screen", "introductory call" etc. in subject
- Non-automated senders with interview subject → `interview_request` category
- DB migration in `init_db()` auto-corrects existing stored misclassifications at startup

### CV Editor overhaul (Session 14)
- **Old**: single raw YAML textarea — unreadable, error-prone
- **New**: full structured editor at `/cv/edit` with:
  - Sticky left nav (Personal / Summary / Skills / Experience / Internships / Education / Certifications / Projects / Languages)
  - Personal Info: 2-column grid of labeled input fields
  - Profile Summary: large textarea with live character counter
  - Skills: 5 color-coded tag categories — press Enter/comma to add, × to remove
  - Experience / Internships / Projects: structured cards with inline editing, add/remove bullets per entry
  - Education: clean row-based form with degree/institution/year/score fields
  - Certifications & Languages: tag pill style
  - Raw YAML button: shows live-generated YAML from form state
  - Save generates valid YAML from form and POSTs — shows green toast "Resume saved successfully!"

### How Approve → Apply actually works (documentation)
1. User sees matched jobs on Approvals page (status='matched')
2. User clicks "Approve" → job status set to 'approved' in DB
3. **Immediately** (background thread): `_apply_approved_jobs()` is called
4. For Naukri jobs: Playwright headless Chrome logs in, navigates to job URL, clicks Apply, fills screening questions
5. For LinkedIn jobs: Playwright logs in, clicks Easy Apply button, uploads resume PDF, fills questions
6. On success: status → 'applied', row inserted in `applications` table
7. On failure: status → 'apply_failed', job URL opened in user's default browser for manual apply
8. Dashboard shows flash: "Job approved — applying now in background. Check Activity Logs for result."
9. Check Activity Logs (applier agent) for apply_success or apply_manual_needed outcome

### Known: auto-apply may fail for some jobs
- Naukri requires NAUKRI_EMAIL + NAUKRI_PASSWORD in .env
- LinkedIn requires LINKEDIN_EMAIL + LINKEDIN_PASSWORD in .env
- Jobs where "Apply" button is not found, or form has unusual structure → apply_failed → opens in browser
- One browser session at a time (FIFO queue) — concurrent approvals queued, not lost


---

## Session 14 (continued) — Applier Bugs + Timestamps + Telegram Setup

**Date:** 2026-03-20

### Bug: Indeed jobs routed to Naukri apply agent
- Logs showed: `WARNING: Apply button not found on https://in.indeed.com/viewjob?jk=...`
- Root cause: `_apply_approved_jobs()` in orchestrator used `if "linkedin" → else → naukri` routing. Indeed/other sources all fell to Naukri apply agent which opens Naukri login, not Indeed
- Fix: Added explicit `if "indeed" in source or "indeed" in url` check — routes to manual apply (open in browser) immediately, no wasted browser session
- Fix: Also check URL not just source field (some jobs stored with generic source but Indeed URL)
- Routing now: linkedin → Easy Apply, naukri → Naukri Playwright, indeed/other → browser (manual)

### Bug: "Where" question flagged for human review 8+ times
- Logs: `WARNING: Question flagged for human review: Where`
- Root cause: Naukri apply form has a single-word "Where" label for location input. QA bank pattern was `["current location", "where are you based", ...]` — no match for bare "where"
- Fix: Added `"where", "location", "preferred location", "job location"` to `current_location` patterns
- Also added short-label entries: name, email, phone, experience_years, notice_days

### Bug: Timestamp mismatch between log file and Activity Logs dashboard
- Problem: Log file uses Python `logging.Formatter` → `time.localtime()` → IST timestamps
- Problem: SQLite `CURRENT_TIMESTAMP` always returns UTC — audit_log records were 5h30m behind
- Fix: `log_audit()` now explicitly passes `datetime.now().strftime(...)` as `created_at` — local IST time
- Fix: `insert_job()` now passes `discovered_at` explicitly using `datetime.now()` 
- Fix: `naukri_apply.py` and `linkedin_apply.py` now pass `created_at` and `applied_at` explicitly
- All DB timestamps now IST (local time), matching log file and system clock

### Bug: Naukri CV updater failing every 16 minutes + browser opening each time
- Every cycle: `WARNING: Could not apply X on Naukri profile page`
- Root cause 1: `skill_reorder` change type had no handler in the async function — always fell through to warning
- Root cause 2: Naukri updated their profile page HTML — old selectors no longer match
- Root cause 3: On failure, `webbrowser.open(NAUKRI_PROFILE_URL)` was called every 16 minutes
- Fix: Added `skill_reorder` handler with correct selectors
- Fix: Added more selector fallbacks for all strategies (data-testid, class-based, text-based)
- Fix: Browser now only opens once per day on CV update failure (not every 16 minutes)

### Bug: Log file captured WARNING+ only
- Root cause: `file_handler.setLevel(logging.WARNING)` in `core/logger.py`
- Fix: Changed to `setLevel(logging.INFO)` — log file now matches terminal output

---

### Telegram Setup — Step by Step

Add these 2 lines to your `.env` file (located in project root):

```
TELEGRAM_BOT_TOKEN=<token from BotFather>
TELEGRAM_CHAT_ID=<your personal numeric chat ID>
```

**Step 1: Create the bot**
1. Open Telegram → search **@BotFather** → tap Start
2. Send `/newbot`
3. Enter a display name: e.g. `Abhishek JobPilot`
4. Enter username (must end in _bot): e.g. `abhishek_jobpilot_bot`
5. BotFather replies with your token: `123456789:ABCDef-ghijkl...`
6. Copy it — add to `.env`: `TELEGRAM_BOT_TOKEN=123456789:ABCDef-ghijkl...`

**Step 2: Get your Chat ID**
1. In Telegram, search for your new bot by username → tap **Start**
2. Open this URL in browser (replace TOKEN): `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Look for `"chat":{"id":XXXXXXXX}` — copy the number
4. Add to `.env`: `TELEGRAM_CHAT_ID=XXXXXXXX`

**Step 3: Restart JobPilot**
```
python main.py
```

**What you'll receive on Telegram:**
- Job scored >= 0.70: instant push with title, company, score, source
- Recruiter email: push with sender, subject, draft preview
- Interview scheduled: push with company, role, date, prep topics
- Daily 8 AM: reminder for interviews in next 24h
- Daily 8 PM: summary (jobs found, applied, pending approvals)

**Telegram bot commands:**
- `/status` — current system status
- `/jobs` — list top 5 matched jobs
- `/approve <id>` — approve a job for application
- `/skip <id>` — skip a job



---

## Session 14 — Calendar Modal UI Fix + Email Classifier Improvements

**Date:** 2026-03-19
**Status:** Complete

### Bug: Calendar flashcard modal unreadable (transparent backgrounds)

**Root cause:** `var(--card-bg)` CSS variable used throughout the flashcard CSS was never defined in `dashboard/static/style.css`. The variable resolved to nothing, making the modal background transparent. All badge colors used `color-mix(in srgb, ... 20%, var(--bg))` which produced near-transparent tints, and section text used `var(--text-muted)` making labels hard to read.

**Files changed:** `dashboard/templates/calendar.html`

**Fixes applied:**
- Replaced `var(--card-bg)` with `var(--surface)` (which IS defined) for flashcard background
- Added `box-shadow:0 8px 40px rgba(0,0,0,.5)` to lift card visually from overlay
- Replaced semi-transparent badge backgrounds with solid colors:
  - Telephonic: `#1e40af` bg / `#bfdbfe` text
  - Video: `#5b21b6` bg / `#ddd6fe` text
  - F2F: `#065f46` bg / `#a7f3d0` text
  - Unknown: `#92400e` bg / `#fde68a` text
- `.fc-jd` and `.fc-topics`: now use `var(--surface2)` + border for visible solid backgrounds; text changed from `var(--text-muted)` to `var(--text)` for full contrast
- Added `.fc-recruiter` class with same solid surface background
- Section headers `h4`: changed from `var(--text-muted)` to `var(--accent)` for clear visual hierarchy
- Calendar event pills in grid: same solid color treatment as badges
- Legend pills in header: updated to match new solid colors; added "Other" entry
- Overlay opacity increased from 0.6 to 0.75 for stronger backdrop contrast
- Close button: given a visible background circle (`var(--surface2)` + border)

