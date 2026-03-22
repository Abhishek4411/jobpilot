# JobPilot v2 — Autonomous Job Search Agent

An AI-powered job search automation system. Drop in your resume, configure your `.env`, and it discovers jobs, scores them, applies, reads/drafts recruiter emails, and keeps your Naukri profile fresh — all while you sleep.

---

## What It Does

| Feature | Description |
|---|---|
| **Job Discovery** | Scrapes LinkedIn, Indeed (via python-jobspy) + Naukri (Playwright) every 30 min; Glassdoor disabled (blocked) |
| **Smart Matching** | Scores jobs with cosine similarity + keyword overlap against your resume (configurable threshold, default 0.38) |
| **Auto Application** | Approve a job on dashboard → immediately applies via Playwright (Naukri/LinkedIn Easy Apply); on failure opens browser for manual apply |
| **Email Management** | Reads Gmail inbox, sent mail, and spam every 5 min; classifies, drafts replies; auto-saves recruiter contacts (name, email, phone) |
| **Interview Calendar** | Auto-extracts interview date/time/link from emails; monthly calendar view with flashcard popup (JD, prep topics, meeting link); daily 8 AM Telegram reminder |
| **Sent Mail Tracking** | Reads your Sent folder; LLM extracts role/company context; all outbound job emails recorded in dashboard |
| **Spam Scanning** | Checks Gmail Spam folder every 5 min for misclassified recruiter emails |
| **Naukri CV Optimizer** | Makes subtle daily profile tweaks (synonym swaps, headline rotations) to stay visible in search |
| **Daily Audio Brief** | Generates an MP3 summary of the day's activity via edge-tts at 8 PM |
| **Dashboard** | Flask + HTMX web UI at `http://localhost:5000` — stats, jobs, approvals, emails, calendar, CV editor, logs |
| **Telegram Bot** | Real-time push alerts on your phone for new jobs, recruiter emails, and interview reminders; approve/skip via commands |
| **Business Brain Memory** | Rolling markdown memory (companies, recruiters with contact details, decisions) + job search strategy YAML |
| **Token Optimization** | Tiered LLM routing (Groq for fast tasks, Gemini for quality); in-process prompt cache; resume TTL |
| **Security Hardening** | Env var filtering, log PII sanitization, optional dashboard token auth |

---

## Architecture

```
main.py
  └── core/orchestrator.py        APScheduler + browser FIFO queue
        ├── agents/scout/         Job discovery (jobspy + Naukri Playwright)
        ├── agents/matcher/       Embedding + keyword scoring
        ├── agents/applier/       Naukri + LinkedIn form-filler
        ├── agents/comms/         Gmail read/classify/draft/send
        ├── agents/optimizer/     Naukri CV updater
        └── agents/cv_manager/    Resume parse pipeline (PDF/DOCX/TXT)

core/llm_router.py    ← ONLY file that touches openai SDK
  Tier A (Groq)   →  llama-3.1-8b-instant  (fast: classification, QA)
  Tier B (Gemini) →  gemini-2.0-flash      (quality: drafting, JD analysis, parsing)

dashboard/app.py     Flask + HTMX + Chart.js dark UI
core/db.py           SQLite WAL mode, 7 tables
config/resume.yaml   Single source of truth for all resume data
```

### LLM Strategy (Tiered Routing)
- **Tier A — Groq** (`llama-3.1-8b-instant`): fast, cheap tasks (classification, QA)
- **Tier B — Gemini** (`gemini-2.0-flash`): quality tasks (drafting, JD analysis, resume parsing)
- **In-process prompt cache**: identical inputs skip the API call entirely (1-hour TTL)
- **Resume summary cache**: 1-hour TTL + daily invalidation; never re-reads YAML unnecessarily
- **Token tracking**: every call logged to audit_log (`provider, task_type, tokens_used`)
- **Per-task token budgets**: enforced to minimize waste
  - `fast_classification`: 15 tokens
  - `jd_analysis`: 300 tokens
  - `question_answering`: 80 tokens
  - `resume_parsing`: 4000 tokens
  - `quality_drafting`: 120 tokens
  - `job_extraction`: 500 tokens

---

## Quick Start

### 1. Setup

```bash
# Windows
scripts\setup.bat

# Or manually:
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
python scripts\migrate_db.py
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your API keys and credentials
```

Required `.env` keys:
```
GEMINI_API_KEY=...       # https://aistudio.google.com/apikey
GROQ_API_KEY=...         # https://console.groq.com
GMAIL_ADDRESS=...        # your Gmail address
GMAIL_APP_PASSWORD=...   # 16-char app password (not your regular password)
NAUKRI_EMAIL=...         # Naukri login email
NAUKRI_PASSWORD=...      # Naukri login password
LINKEDIN_EMAIL=...       # LinkedIn email
LINKEDIN_PASSWORD=...    # LinkedIn password
```

> **Gmail App Password**: Go to Google Account → Security → 2-Step Verification → App Passwords. Regular password will NOT work.

### 3. Add Your Resume

Drop your PDF (or DOCX/TXT) into `data/resumes/`. The CV Watcher auto-parses it and updates `config/resume.yaml`.

Or edit `config/resume.yaml` directly — copy from `config/resume_template.yaml` and fill in your details.

### 4. Test Connections

```bash
venv\Scripts\python.exe scripts\test_connections.py
```

Expected output:
```
Testing SQLite...   PASS
Testing Gemini API... PASS
Testing Groq API...   PASS
Testing Gmail IMAP... PASS
```

### 5. Run

```bash
venv\Scripts\python.exe main.py
# or with venv activated:
python main.py
```

Open `http://localhost:5000` in your browser.

---

## File Structure

```
AI_Career_Agent/
├── config/
│   ├── resume.yaml                 Your resume (single source of truth)
│   ├── resume_template.yaml        Empty template for other users
│   ├── job_preferences.yaml        Target roles, locations, salary
│   ├── qa_bank.yaml                Pre-written answers for screening Qs
│   ├── llm_providers.yaml          Groq/Gemini config + routing
│   ├── settings.yaml               Scheduling, thresholds, browser settings
│   └── naukri_update_strategies.yaml  CV rotation strategies
├── core/
│   ├── llm_router.py               LLM gateway (only file using openai SDK)
│   ├── db.py                       SQLite helpers + cleanup
│   ├── logger.py                   Rotating file logger (WARNING+ to file)
│   ├── orchestrator.py             APScheduler + browser queue
│   ├── config_loader.py            Merged YAML + .env loader
│   └── notifier.py                 Desktop + email notifications
├── agents/
│   ├── scout/                      Job discovery
│   ├── matcher/                    Scoring + embeddings
│   ├── applier/                    Form-filling + application
│   ├── comms/                      Email read/classify/draft/send + Telegram bot
│   ├── memory/                     Rolling markdown memory + strategy loader
│   ├── optimizer/                  Naukri CV updater
│   └── cv_manager/                 Resume parse pipeline
├── dashboard/
│   ├── app.py                      Flask app factory
│   ├── routes.py                   Main dashboard routes
│   ├── cv_routes.py                CV management routes
│   ├── templates/                  HTMX + Chart.js dark UI
│   └── static/style.css            Dark theme CSS
├── scripts/
│   ├── setup.bat / setup.sh        One-click setup
│   ├── test_connections.py         Verify all connections
│   └── migrate_db.py               Create DB tables (idempotent)
├── tests/                          pytest test suite
├── data/
│   ├── jobpilot.db                 SQLite database
│   ├── logs/jobpilot.log           WARNING+ log file
│   ├── resumes/                    Drop your CV here
│   └── audio/                      Daily MP3 briefings
├── main.py                         Entry point
├── requirements.txt
└── .env.example
```

---

## Dashboard

| Page | URL | Description |
|---|---|---|
| Dashboard | `/` | Stats cards + charts + activity feed |
| Jobs | `/jobs` | All discovered jobs, filterable/searchable |
| Approvals | `/approvals` | Email drafts + job applications to approve |
| Emails | `/emails` | All processed emails by category (inbox, sent, spam, recruiter contacts); "Fetch Now" button |
| Calendar | `/calendar` | Monthly interview calendar; click event for flashcard with company, role, date, meeting link, prep topics, JD snippet, and recruiter contact — all on a solid high-contrast card |
| CV Manager | `/cv` | Upload/edit/preview your resume |
| Job Heatmap | `/heatmap` | World heatmap of discovered job locations |
| Logs | `/logs` | Color-coded audit feed |

### Approval Flow
1. Jobs are discovered and scored automatically
2. Matched jobs (above your configured threshold) appear in **Approvals**
3. Click **Approve** → JobPilot applies automatically
4. Email replies appear in **Approvals** → Approve to send

---

## Configuration Reference

### `config/settings.yaml`
```yaml
matching:
  threshold: 0.45        # min score to show in approvals
  max_applications_per_day: 50

scheduling:
  scout_interval_minutes: 30
  email_interval_minutes: 5
  cv_update_min_minutes: 15
  cv_update_max_minutes: 30
  audio_briefing_hour: 20
```

### `config/job_preferences.yaml`
Edit `target_roles`, `locations`, `min_salary_lpa`, and `must_have_keywords` to target the right jobs.

### `config/qa_bank.yaml`
Pre-written answers for common screening questions. Add patterns to match automatically without LLM calls.

---

## Scheduled Tasks

| Job | Interval | What it does |
|---|---|---|
| Scout | Every 30 min | Scrape + score new jobs |
| Email | Every 5 min | Read, classify, draft replies |
| Apply | Every 2 min | Apply to approved jobs |
| CV Update | Every 15–30 min (random) | One subtle Naukri profile tweak |
| Audio | 8:00 PM daily | Generate MP3 briefing |
| Log Cleanup | Every hour | Trim log file to last 2000 lines |
| Data Purge | 12:30 AM daily | Delete records older than 14 days |

---

## Troubleshooting

### 0 jobs matched
- Check `data/logs/jobpilot.log` for scraper errors
- Lower `matching.threshold` in `config/settings.yaml` (default: 0.45)
- Verify `config/job_preferences.yaml` has valid keywords and locations

### Gemini 404 errors
- The correct model is `gemini-2.0-flash` (set in `config/llm_providers.yaml`)
- Groq is primary — Gemini is only the fallback. Most tasks won't hit Gemini at all.

### Naukri login fails
- Naukri changes their UI frequently; the scraper tries 5 selector fallbacks
- If all fail, check the Naukri login page manually and update selectors in:
  - `agents/scout/naukri_scraper.py`
  - `agents/optimizer/naukri_cv_updater.py`

### Gmail IMAP error: "socket error: EOF"
This means Gmail is closing the connection. Fix in order:
1. **Enable IMAP in Gmail**: Gmail > gear icon > See all settings > Forwarding and POP/IMAP > **Enable IMAP** > Save Changes
2. **Enable 2-Step Verification** on your Google Account (required for App Passwords)
3. **Generate App Password**: Google Account > Security > 2-Step Verification > App Passwords > Select "Mail" > Generate
4. Paste the 16-char code (with spaces) into `.env` as `GMAIL_APP_PASSWORD`
5. Restart JobPilot

### Gmail not sending emails
1. Ensure `GMAIL_APP_PASSWORD` is a 16-char app password, not your regular password
2. Enable 2-Step Verification on your Google account first
3. In Gmail settings, ensure IMAP is enabled (Settings → See all settings → Forwarding and POP/IMAP)
4. Emails are only sent after you **Approve** them on the `/approvals` page

### Approvals page shows nothing
- Go to `/emails` and click **Fetch Now** — this forces an immediate Gmail scan
- Check category breakdown — if emails show as `irrelevant`, the classifier needs tuning
- Only `interview_request`, `job_opportunity`, and `follow_up` emails generate drafts for approval
- Sent mails and job alerts do NOT appear in approvals (by design)

### "No module named 'openai'" when running scripts
- Use the venv Python: `venv\Scripts\python.exe script.py`
- Or activate venv first: `venv\Scripts\activate`, then `python script.py`

### Resume not being parsed
- Ensure the file is in `data/resumes/` with a `.pdf`, `.docx`, or `.txt` extension
- Files named `readme.txt`, `readme.pdf`, etc. are automatically skipped
- Check the console output for parse errors

---

## Adding Your Own Resume

1. Drop your PDF/DOCX/TXT in `data/resumes/`
2. The CV Watcher detects it within 10 seconds and parses it automatically
3. `config/resume.yaml` is updated — this is used for ALL LLM prompts
4. Or upload via the dashboard: `http://localhost:5000/cv/upload`
5. Or edit directly at `http://localhost:5000/cv/edit`

---

## Tech Stack

- **Python 3.10+** — core language
- **Flask 3** + HTMX + Chart.js — dashboard
- **SQLite WAL** — persistent storage
- **APScheduler** — task scheduling
- **Playwright** — browser automation (Naukri, LinkedIn)
- **python-jobspy** — multi-portal job scraping
- **sentence-transformers** (all-MiniLM-L6-v2) — resume/JD embeddings
- **PyMuPDF** — PDF text extraction
- **openai SDK** — used for BOTH Groq and Gemini via custom `base_url`
- **edge-tts** — free text-to-speech for audio briefings
- **Gmail IMAP/SMTP** — email reading and sending

---

---

## Telegram Bot Setup

1. Open Telegram and message **@BotFather** → `/newbot` → follow prompts
2. Copy the token and add to `.env`: `TELEGRAM_BOT_TOKEN=<your_token>`
3. Start a chat with your new bot, then get your chat ID:
   ```
   curl https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
   Look for `"chat":{"id":...}` in the response
4. Add to `.env`: `TELEGRAM_CHAT_ID=<your_chat_id>`
5. Run `python scripts/test_connections.py` — expect `Telegram Bot: PASS`

### Telegram Commands
| Command | Description |
|---|---|
| `/status` | Today's discovery, match, apply, and pending approval counts |
| `/jobs` | Top 5 recently matched jobs |
| `/approve <id>` | Approve email reply draft (get ID from Telegram alert) |
| `/skip <id>` | Skip (reject) email reply draft |

---

## Business Brain Memory

### Level 2 — Job Search Strategy (`config/user_strategy.yaml`)
Edit this file to control:
- `target_roles` — which job titles to prioritize
- `avoid_companies` / `target_companies` — blacklist/whitelist
- `deal_breakers` — phrases that suppress a job from matching (e.g. "service agreement")
- `location_preference` / `work_type_preference`
- `email_context` — extra context injected into every reply draft

### Level 3 — Rolling Memory (`data/memory/`)
| File | Contents | TTL |
|---|---|---|
| `companies.md` | Target/avoid companies with reasons | Permanent by default |
| `recruiters.md` | Past recruiter interactions | 7 days (unless `[keep]`) |
| `decisions.md` | Why jobs were skipped or approved | 7 days |
| `notes.md` | Freeform notes | 7 days |

Memory files are pruned automatically at 12:30 AM daily. Mark any entry with `[keep]` to make it permanent.

---

## Privacy & Security

- All data is stored locally in `data/jobpilot.db` — nothing leaves your machine except API calls
- API keys are stored in `.env` which is excluded from git via `.gitignore`
- `data/memory/` is also excluded from git (may contain recruiter PII)
- The system never fabricates resume facts — all LLM prompts are grounded in `config/resume.yaml`
- Naukri and LinkedIn passwords are only used for Playwright automation and never logged
- Log files sanitize email addresses, phone numbers, and secret values before writing to disk
- Set `DASHBOARD_TOKEN` in `.env` to enable login protection on the dashboard
- LLM-facing code only receives non-secret env vars via `cfg["env_safe"]`
