# JobPilot Skills & Error Pattern Guide

This file documents known error patterns, fixes, and decision rules.
Used as context when the LLM needs to make classification or action decisions.

---

## Email Address Validation Rules

### Naukri Relay Address Decoding
Naukri sends recruiter emails via a relay proxy format:
```
Format:  firstname.lastname<base64(domain)>@naukri.com
Example: piyusha.singhYXNjZW5kaW9uLmNvbQ==@naukri.com
Decoded: piyusha.singh@ascendion.com
```
**Rule:** Any `@naukri.com` address with mixed-case local part = relay address.
Decode by: find first potential base64 split, try decoding, validate domain regex.
If decode fails — DO NOT send email. Apply via website instead.

### Always-Skip Sender Patterns (will bounce or go to void)
```
noreply, no-reply, donotreply, do-not-reply, do_not_reply
jobalert@, jobalerts-noreply@, jobs-noreply@
@instahyre.com, @alerts., @email., @communications.
glassdoor.com, foundit.sg, foundit.in
sbi@, @communications.sbi, eazydiner, swiggy, amazon.in
mailer-daemon, postmaster@, talenttitan letters
```

### Job Alert Email Senders (contain job listings inside, not direct recruiter)
```
LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>
Naukri <jobalert@naukri.com>
Indeed <donotreply@match.indeed.com>
1mg Careers <noreply@instahyre.com>
GeoIQ Careers <noreply@instahyre.com>
LinkedIn <jobs-noreply@linkedin.com>
foundit Updates <info@alerts.foundit.sg>
Alyssa from foundit <jobmessenger@monster.com.sg>
```
**Rule:** These are digest emails. DO NOT reply to sender. Instead:
1. Extract job URLs from email body
2. Visit each URL
3. Scan for real recruiter email or apply button
4. Store as job lead in DB

---

## Email Classification Rules

### irrelevant (never draft a reply)
- Subject contains: "job alert", "jobs for you", "be an early applicant", "new jobs matching"
- Subject contains: "OTP", "transaction", "bank alert", "statement", "Happy Holi"
- Subject contains: "friend request", "is your location still", "how else will we know"
- Sender matches any skip pattern above

### job_opportunity (draft only if NOT from noreply)
- Direct recruiter outreach about a specific role
- Must have a personal name + company email (not relay/noreply)
- Naukri relay emails = decode address first, then draft to decoded email

### interview_request
- Contains: "interview", "schedule", "call", "availability", "slot", "meeting"

### follow_up
- Contains: "following up", "application status", "heard back", "update"
- Only if sender is NOT an automated digest

---

## Common Bounce Causes & Fixes

| Error | Root Cause | Fix |
|-------|-----------|-----|
| `550 5.4.1 Recipient address rejected` | Naukri relay address used directly | Decode base64 domain from local part |
| `Address not found` | Sent to `name<b64>@naukri.com` directly | Use `_decode_naukri_relay()` in email_sender.py |
| Reply bounces to LinkedIn alert | Drafted reply to `jobalerts-noreply@linkedin.com` | `_ALERT_SENDER_RE` filter in email_drafter.py |
| SBI Holi email in approvals | SBI marketing classified as job_opportunity | Subject filter: "happy holi", "smart banking" |
| EazyDiner "quick update" in approvals | Sender `@email.eazydiner.com` not blocked | Added `eazydiner` to sender patterns |
| Swiggy "how else will we know" | Subject not matching old patterns | Added phrase to `_IRRELEVANT_SUBJECT_PATTERNS` |
| Glassdoor "I can refer you in EY" | `noreply@glassdoor.com` not caught | Added `glassdoor.com` to sender patterns |

---

## Job Scraping Rules

### India-First Priority (3-phase search)
- Phase 1: Top 5 keywords × Top 4 India cities (Bengaluru, Mumbai, Hyderabad, Pune)
- Phase 2: Remaining keywords × Top 2 India cities
- Phase 3: Top 3 keywords × International cities

### Matching Threshold
- Threshold: 0.45 (realistic for partial JD matches from job boards)
- If threshold too high (>0.65): zero matches returned
- If threshold too low (<0.25): noise matches

### Deduplication
- Jobs deduped by URL (UNIQUE constraint in DB)
- Scout runs every 30 min; email alert extractor runs when new alerts arrive
- 14-day retention window

---

## Resume Parsing Rules

### 2-Pass Extraction (prevents token budget overflow)
- Pass 1: Personal info + skills + summary (max 4000 tokens)
- Pass 2: Experience + education + certifications + projects (max 4000 tokens)
- Text sent: first 8000 chars of raw resume text

### Missing Fields Checklist
Required fields: name, email, phone, location, current_title, total_experience
Skills: primary[], ai_ml[], programming[], databases_tools[], domain[]
Experience: [{title, company, location, duration, highlights[]}]
Education: [{degree, institution, year, score}]

---

## Heatmap Rendering Rules

### Scroll Behavior
- `scrollWheelZoom: false` always set on map init
- Re-enabled only on map click; disabled on mouseout
- Both fullmap and minimap must have this setting

### Marker Size
- Formula: `r = Math.max(4, Math.min(10, 3 + Math.log2(count + 1) * 1.2))`
- Range: 4px to 10px (never overlapping adjacent cities)
- Color: India=#22c55e, Global=#4f8ef7

### City Location Accuracy (key coords)
- Bengaluru: (12.9716, 77.5946) — NOT Hyderabad
- Mumbai: (19.0760, 72.8777)
- Pune: (18.5204, 73.8567)
- Hyderabad: (17.3850, 78.4867)
- Delhi: (28.6139, 77.2090)
- Chennai: (13.0827, 80.2707)

---

## LLM Context Minimization Rules

To minimize token usage without losing accuracy:
1. Rule-based pre-filter FIRST — catches ~80% of junk at 0 tokens
2. Send only: From(80 chars) + Subject + Body(250 chars) to LLM
3. Cache resume summary in memory (invalidate on CV update)
4. Use task_type routing: fast_classification (Groq haiku), quality_drafting (Groq)
5. Never send full email thread — only latest message

---

## Real-Time Update Architecture

- Approvals page polls `/api/approvals/count` every 30s
- If count increased → auto-reload page (new items available)
- Heatmap refreshes every 120s via `setInterval`
- Dashboard stats refresh every 60s
- No websocket needed — simple fetch polling is sufficient
