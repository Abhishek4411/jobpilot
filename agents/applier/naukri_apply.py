"""Automate job applications on Naukri.com using Playwright.

Strategy:
- headless=False with off-screen window (avoids bot detection)
- Uses confirmed login selectors: #usernameField, #passwordField
- On automation failure: opens job URL in user's default browser
  and stores as apply_failed so dashboard picks it up
"""

import asyncio
import os
import random
import webbrowser

from agents.applier.question_handler import answer_question
from core.db import log_audit, get_conn
from core.logger import get_logger

log = get_logger(__name__)


async def _apply_async(job_id: int, job_url: str) -> bool:
    """Apply to a single Naukri job asynchronously."""
    from playwright.async_api import async_playwright

    email = os.environ.get("NAUKRI_EMAIL", "")
    password = os.environ.get("NAUKRI_PASSWORD", "")
    if not email or not password:
        log.error("Naukri credentials not configured in .env")
        return False

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            slow_mo=50,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-position=-32000,-32000",  # off-screen, invisible
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
        )
        page = await ctx.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        # Block images/fonts/media — form interactions don't need them
        async def _block_heavy(route):
            if route.request.resource_type in {"image", "media", "font", "ping", "other"}:
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", _block_heavy)
        try:
            # Login
            await page.goto("https://www.naukri.com/nlogin/login", timeout=30000)
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await asyncio.sleep(1)

            await page.fill("#usernameField", email, timeout=8000)
            await page.fill("#passwordField", password, timeout=8000)

            for sel in ["button:has-text('Login')", "button[type='submit']", "input[type='submit']"]:
                try:
                    await page.click(sel, timeout=4000)
                    break
                except Exception:
                    continue

            await asyncio.sleep(5)  # Wait for redirect (networkidle is too strict)

            # Navigate to job
            await page.goto(job_url, timeout=30000)
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await asyncio.sleep(random.uniform(2, 4))

            apply_btn = await page.query_selector(
                "button:has-text('Apply'), a:has-text('Apply now'), button:has-text('Apply now')"
            )
            if not apply_btn:
                log.warning("Apply button not found on %s", job_url)
                return False
            await apply_btn.click()
            await asyncio.sleep(random.uniform(1, 2))

            # Handle multi-step application forms
            for _attempt in range(5):
                questions = await page.query_selector_all(
                    "input[type='text']:visible, textarea:visible"
                )
                for q in questions:
                    label_el = await q.query_selector("xpath=preceding::label[1]")
                    label_text = await label_el.inner_text() if label_el else ""
                    if label_text:
                        answer = answer_question(label_text)
                        await q.fill(answer)
                        await asyncio.sleep(0.3)

                next_btn = await page.query_selector(
                    "button:has-text('Next'), button:has-text('Submit')"
                )
                if not next_btn:
                    break
                btn_text = await next_btn.inner_text()
                await next_btn.click()
                await asyncio.sleep(random.uniform(1, 2))
                if "submit" in btn_text.lower():
                    break

            log.info("Applied to job_id=%d on Naukri", job_id)
            return True
        except Exception as e:
            log.error("Naukri apply error for job_id=%d: %s", job_id, e)
            return False
        finally:
            await page.close()
            await ctx.close()
            await browser.close()


def apply_naukri(job_id: int, job_url: str) -> bool:
    """Apply to a Naukri job. On failure, open URL for manual application.

    Args:
        job_id: Database ID of the job record.
        job_url: Full URL of the Naukri job posting.

    Returns:
        True if application was submitted automatically.
    """
    try:
        success = asyncio.run(_apply_async(job_id, job_url))
    except Exception as e:
        log.error("asyncio.run failed for Naukri apply: %s", e)
        success = False

    conn = get_conn()
    if success:
        from datetime import datetime as _dt
        now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE jobs SET status='applied', applied_at=? WHERE id=?", (now, job_id))
        conn.execute(
            "INSERT INTO applications (job_id, applied_via, status, created_at) VALUES (?,?,?,?)",
            (job_id, "naukri", "submitted", now),
        )
        conn.commit()
        log_audit("applier", "naukri_apply_success", f"job_id={job_id}")
    else:
        conn.execute("UPDATE jobs SET status='apply_failed' WHERE id=?", (job_id,))
        conn.commit()
        log_audit("applier", "naukri_apply_manual_needed",
                  f"job_id={job_id} url={job_url}")
        # Open the job URL in the user's browser after a short pause
        import threading
        def _open_after_delay():
            import time
            time.sleep(2.5)
            try:
                webbrowser.open(job_url)
                log.info("Opened job URL for manual application: %s", job_url[:80])
            except Exception:
                pass
        threading.Thread(target=_open_after_delay, daemon=True).start()

    return success
