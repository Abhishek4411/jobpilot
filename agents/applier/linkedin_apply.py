"""Automate LinkedIn Easy Apply using Playwright."""

import asyncio
import os
import random
from pathlib import Path

from agents.applier.question_handler import answer_question
from core.db import log_audit, get_conn
from core.logger import get_logger

log = get_logger(__name__)

RESUME_DIR = Path("data/resumes")


def _find_resume_file() -> str | None:
    """Return path to the first PDF resume found in data/resumes/."""
    for f in RESUME_DIR.glob("*.pdf"):
        return str(f.resolve())
    return None


async def _apply_async(job_id: int, job_url: str) -> bool:
    """Apply to a LinkedIn job via Easy Apply asynchronously.

    Args:
        job_id: Database ID of the job record.
        job_url: Full LinkedIn job URL.

    Returns:
        True if the application was submitted.
    """
    from playwright.async_api import async_playwright

    email = os.environ.get("LINKEDIN_EMAIL", "")
    password = os.environ.get("LINKEDIN_PASSWORD", "")
    if not email or not password:
        log.error("LinkedIn credentials not configured in .env")
        return False

    resume_path = _find_resume_file()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, slow_mo=100)
        page = await browser.new_page()
        try:
            await page.goto("https://www.linkedin.com/login", timeout=30000)
            await page.fill("input#username", email)
            await page.fill("input#password", password)
            await page.click("button[type='submit']")
            await page.wait_for_load_state("networkidle", timeout=15000)

            await page.goto(job_url, timeout=20000)
            await page.wait_for_load_state("networkidle", timeout=10000)
            await asyncio.sleep(random.uniform(2, 4))

            easy_apply_btn = await page.query_selector("button.jobs-apply-button")
            if not easy_apply_btn:
                log.warning("Easy Apply button not found on %s", job_url)
                return False
            await easy_apply_btn.click()
            await asyncio.sleep(1)

            for _step in range(8):
                await asyncio.sleep(random.uniform(1, 2))

                if resume_path:
                    upload_input = await page.query_selector("input[type='file']")
                    if upload_input:
                        await upload_input.set_input_files(resume_path)
                        await asyncio.sleep(1)

                text_inputs = await page.query_selector_all(
                    "input[type='text']:visible, input[type='number']:visible, textarea:visible"
                )
                for inp in text_inputs:
                    label_el = await inp.query_selector("xpath=preceding::label[1]")
                    label_text = await label_el.inner_text() if label_el else ""
                    if label_text:
                        value = answer_question(label_text)
                        current = await inp.input_value()
                        if not current:
                            await inp.fill(value)
                            await asyncio.sleep(0.3)

                submit_btn = await page.query_selector("button[aria-label='Submit application']")
                if submit_btn:
                    await submit_btn.click()
                    log.info("LinkedIn Easy Apply submitted for job_id=%d", job_id)
                    return True

                next_btn = await page.query_selector(
                    "button[aria-label='Continue to next step'], button[aria-label='Review your application']"
                )
                if next_btn:
                    await next_btn.click()
                else:
                    break

            return False
        except Exception as e:
            log.error("LinkedIn apply error for job_id=%d: %s", job_id, e)
            return False
        finally:
            await browser.close()


def apply_linkedin(job_id: int, job_url: str) -> bool:
    """Synchronous wrapper to apply to a LinkedIn job.

    Args:
        job_id: Database ID of the job record.
        job_url: Full LinkedIn job URL.

    Returns:
        True if the application was submitted successfully.
    """
    try:
        success = asyncio.run(_apply_async(job_id, job_url))
    except Exception as e:
        log.error("asyncio.run failed for LinkedIn apply: %s", e)
        success = False

    conn = get_conn()
    status = "applied" if success else "apply_failed"
    conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    if success:
        from datetime import datetime as _dt
        now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE jobs SET applied_at=? WHERE id=?", (now, job_id))
        conn.execute(
            "INSERT INTO applications (job_id, applied_via, status, created_at) VALUES (?,?,?,?)",
            (job_id, "linkedin", "submitted", now),
        )
    conn.commit()
    log_audit("applier", f"linkedin_apply_{'success' if success else 'fail'}", f"job_id={job_id}")
    return success
