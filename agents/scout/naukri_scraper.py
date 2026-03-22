"""Naukri job scraper using Playwright with API response interception.

Strategy:
- headless=False with off-screen window position (avoids bot detection)
- Logs in once per session; reuses browser context for multiple searches
- Intercepts Naukri's internal jobapi/v3/search JSON responses
- Extracts structured data (title, company, location, salary, jd, url) from API JSON
- Never depends on DOM selectors (Naukri changes their CSS classes frequently)

API fields captured:
  title, companyName, placeholders[location/salary/experience], jdURL,
  tagsAndSkills, jobDescription, jobId
"""

import asyncio
import os
import random
from typing import Any

from core.config_loader import load_config
from core.logger import get_logger

log = get_logger(__name__)

NAUKRI_URL = "https://www.naukri.com"

# Build search URL: e.g. https://www.naukri.com/business-analyst-jobs-in-bengaluru
def _search_url(keyword: str, location: str) -> str:
    kw_slug = keyword.strip().lower().replace(" ", "-")
    loc_slug = location.strip().lower().replace(" ", "-")
    return f"{NAUKRI_URL}/{kw_slug}-jobs-in-{loc_slug}"


def _parse_job(raw: dict) -> dict[str, Any]:
    """Convert Naukri API job object to internal format."""
    placeholders = {p["type"]: p["label"] for p in raw.get("placeholders", [])}

    location = placeholders.get("location", "")
    salary_label = placeholders.get("salary", "")
    sal_min = sal_max = None
    if salary_label and "not disclosed" not in salary_label.lower():
        # Parse "12-18 LPA" style
        import re
        nums = re.findall(r"[\d.]+", salary_label)
        if len(nums) >= 2:
            sal_min, sal_max = float(nums[0]) * 100000, float(nums[1]) * 100000
        elif len(nums) == 1:
            sal_min = float(nums[0]) * 100000

    jd_url = raw.get("jdURL", "")
    full_url = (NAUKRI_URL + jd_url) if jd_url.startswith("/") else jd_url

    desc = raw.get("jobDescription", "") or raw.get("tagsAndSkills", "")
    # Strip HTML tags from description
    import re
    desc = re.sub(r"<[^>]+>", " ", desc)
    desc = re.sub(r"\s{2,}", " ", desc).strip()

    return {
        "title": raw.get("title", "").strip(),
        "company": raw.get("companyName", "").strip(),
        "location": location.strip(),
        "salary_min": sal_min,
        "salary_max": sal_max,
        "url": full_url,
        "description": desc[:3000],
        "source": "naukri",
    }


async def _login(page: Any, email: str, password: str) -> bool:
    """Log in to Naukri. Returns True on success."""
    try:
        await page.goto(f"{NAUKRI_URL}/nlogin/login", timeout=30000)
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await asyncio.sleep(1)

        await page.fill("#usernameField", email, timeout=8000)
        await page.fill("#passwordField", password, timeout=8000)

        # Click Login button (Naukri uses button text "Login", not type=submit always)
        for sel in ["button:has-text('Login')", "button[type='submit']", "input[type='submit']"]:
            try:
                await page.click(sel, timeout=4000)
                break
            except Exception:
                continue

        # Wait for redirect to homepage (not networkidle — too strict)
        await asyncio.sleep(5)

        if "mnjuser" in page.url or "homepage" in page.url or "naukri.com" in page.url and "login" not in page.url:
            log.info("Naukri login successful — at %s", page.url)
            return True

        log.warning("Naukri login may have failed — URL: %s", page.url)
        return True  # Continue anyway; search may still work
    except Exception as e:
        log.error("Naukri login error: %s", e)
        return False


async def _scrape_async(
    keywords: list[str],
    india_locations: list[str],
) -> list[dict[str, Any]]:
    """Main async scraper. Intercepts Naukri's jobapi/v3/search responses."""
    from playwright.async_api import async_playwright

    email = os.environ.get("NAUKRI_EMAIL", "")
    password = os.environ.get("NAUKRI_PASSWORD", "")
    if not email or not password:
        log.warning("NAUKRI_EMAIL / NAUKRI_PASSWORD not set — skipping Naukri")
        return []

    all_jobs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,   # headless=True gets "Access Denied" from Naukri
            slow_mo=50,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-position=-32000,-32000",  # far off-screen, invisible to user
                "--window-size=1,1",               # minimal 1×1 window (no taskbar flash)
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-sync",
                "--no-first-run",
                "--disk-cache-size=0",
                "--media-cache-size=0",
                "--disable-plugins",
            ],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            viewport={"width": 1280, "height": 720},
        )
        try:
            page = await ctx.new_page()
            # Auto-close popup tabs Naukri opens (ads, login prompts) — but NOT our main page
            ctx.on("page", lambda p: asyncio.ensure_future(p.close()) if p is not page else None)
            # Remove webdriver fingerprint
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            # Block images, fonts, media, stylesheets — saves ~60% RAM per page load
            async def _block_heavy(route):
                if route.request.resource_type in {"image", "media", "font", "stylesheet", "ping", "other"}:
                    await route.abort()
                else:
                    await route.continue_()
            await page.route("**/*", _block_heavy)

            # ── Login ─────────────────────────────────────────────────────────────
            logged_in = await _login(page, email, password)
            if not logged_in:
                return []

            # ── Search each keyword × location combo ──────────────────────────────
            # Use India-first: search top 5 keywords × top 3 India locations
            combos: list[tuple[str, str]] = []
            for kw in keywords[:5]:
                for loc in india_locations[:3]:
                    combos.append((kw, loc))
            # Shuffle to look less bot-like
            random.shuffle(combos)

            for kw, loc in combos:
                captured: list[dict] = []

                async def _capture(response, _kw=kw):
                    """Intercept Naukri jobapi/v3/search JSON."""
                    if "jobapi/v3/search" in response.url and response.status == 200:
                        try:
                            body = await response.json()
                            jobs_raw = body.get("jobDetails", [])
                            captured.extend(jobs_raw)
                        except Exception:
                            pass

                page.on("response", _capture)

                url = _search_url(kw, loc)
                log.info("Naukri: searching '%s' in '%s'", kw, loc)
                try:
                    await page.goto(url, timeout=30000)
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    await asyncio.sleep(random.uniform(3, 5))  # wait for API call
                except Exception as e:
                    log.warning("Naukri page load error for %s: %s", url[:60], e)

                page.remove_listener("response", _capture)

                for raw in captured:
                    job_id = str(raw.get("jobId", ""))
                    if not job_id or job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)
                    try:
                        job = _parse_job(raw)
                        if job["title"]:
                            all_jobs.append(job)
                    except Exception as e:
                        log.debug("Naukri parse error: %s", e)

                log.info("Naukri '%s' @ '%s': captured %d jobs (total=%d)",
                         kw, loc, len(captured), len(all_jobs))
                await asyncio.sleep(random.uniform(1.5, 3))

            await page.close()
            await ctx.close()
        finally:
            await browser.close()

    log.info("Naukri scrape complete — %d unique jobs", len(all_jobs))
    return all_jobs


def scrape_naukri(keywords: list[str] | None = None) -> list[dict[str, Any]]:
    """Synchronous wrapper for the Naukri async scraper.

    Args:
        keywords: Optional keyword list. If None, reads from job_preferences.yaml.

    Returns:
        List of job dicts from Naukri. Empty list on failure.
    """
    try:
        cfg = load_config()
        prefs = cfg.get("job_preferences", {})
        if keywords is None:
            groups = prefs.get("search_keywords", {}).get("groups", [])
            keywords = [g[0] for g in groups if g]

        india_locs = (
            prefs.get("locations", {})
            .get("india_priority", ["Bengaluru", "Mumbai", "Hyderabad", "Pune", "Delhi NCR"])
        )
        return asyncio.run(_scrape_async(keywords, india_locs))
    except RuntimeError as e:
        # Handle "event loop already running" in some environments
        if "already running" in str(e):
            import nest_asyncio
            nest_asyncio.apply()
            return asyncio.get_event_loop().run_until_complete(
                _scrape_async(keywords or [], [])
            )
        log.error("Naukri scrape RuntimeError: %s", e)
        return []
    except Exception as e:
        log.error("Naukri scrape failed: %s", e)
        return []
