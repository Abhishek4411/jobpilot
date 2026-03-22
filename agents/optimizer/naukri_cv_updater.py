"""Apply one subtle CV change on Naukri to maintain search ranking.

Strategy:
- headless=False with off-screen window (avoids bot detection)
- Uses confirmed login selectors: #usernameField, #passwordField
- On failure: opens Naukri profile page for manual update
"""

import asyncio
import os
import threading
import time
import webbrowser
from datetime import datetime

from agents.optimizer.update_strategies import pick_random_change, get_update_rules
from core.db import get_conn, log_audit
from core.logger import get_logger

log = get_logger(__name__)

NAUKRI_PROFILE_URL = "https://www.naukri.com/mnjuser/profile"


async def _update_async(strategy: dict) -> bool:
    """Apply a single profile update on Naukri asynchronously."""
    from playwright.async_api import async_playwright

    email = os.environ.get("NAUKRI_EMAIL", "")
    password = os.environ.get("NAUKRI_PASSWORD", "")
    if not email or not password:
        log.warning("Naukri credentials not set, skipping CV update")
        return False

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            slow_mo=50,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-position=-32000,-32000",
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
        # Block images/fonts/media — profile edit doesn't need them
        async def _block_heavy(route):
            if route.request.resource_type in {"image", "media", "font", "ping", "other"}:
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", _block_heavy)
        try:
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

            await asyncio.sleep(5)  # Wait for redirect

            await page.goto(NAUKRI_PROFILE_URL, timeout=20000)
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await asyncio.sleep(2)

            change_type = strategy["change_type"]

            if change_type == "headline_variation":
                # Multiple selector fallbacks for Naukri UI variations
                headline_input = await page.query_selector(
                    "input[name='headline'], input[placeholder*='headline'], "
                    "input[placeholder*='Headline'], input[placeholder*='profile headline'], "
                    "[data-testid='headline-input'], .headline-input input"
                )
                if headline_input:
                    await headline_input.triple_click()
                    await headline_input.type(strategy["new_value"])
                    save_btn = await page.query_selector(
                        "button:has-text('Save'), button[type='submit'], "
                        "[data-testid='save-btn']"
                    )
                    if save_btn:
                        await save_btn.click()
                        await asyncio.sleep(3)
                        log.info("Headline updated on Naukri")
                        return True

            elif change_type in ("synonym_swap_skill", "summary_micro_tweak"):
                summary_area = await page.query_selector(
                    "textarea[name='summary'], div[contenteditable='true'], "
                    "textarea[placeholder*='summary'], textarea[placeholder*='Summary'], "
                    "[data-testid='summary-textarea'], .summary-edit textarea"
                )
                if summary_area:
                    try:
                        current_text = await summary_area.input_value()
                    except Exception:
                        current_text = await summary_area.inner_text()
                    if strategy["old_value"] and strategy["old_value"] in current_text:
                        new_text = current_text.replace(
                            strategy["old_value"], strategy["new_value"], 1
                        )
                        await summary_area.triple_click()
                        await summary_area.type(new_text[:2000])
                        save_btn = await page.query_selector(
                            "button:has-text('Save'), [data-testid='save-btn']"
                        )
                        if save_btn:
                            await save_btn.click()
                            await asyncio.sleep(3)
                            return True

            elif change_type == "skill_reorder":
                # Naukri key skills section — find and click edit then save (triggers recrawl)
                skills_edit = await page.query_selector(
                    "[data-testid='keySkillsEdit'], .keySkills .edit-btn, "
                    "section:has-text('Key Skills') button:has-text('Edit'), "
                    ".widgetHead:has-text('Skills') ~ div button"
                )
                if skills_edit:
                    await skills_edit.click()
                    await asyncio.sleep(2)
                    save_btn = await page.query_selector(
                        "button:has-text('Save'), [data-testid='save-btn']"
                    )
                    if save_btn:
                        await save_btn.click()
                        await asyncio.sleep(3)
                        log.info("Skills section re-saved on Naukri (reorder trigger)")
                        return True

            log.warning("Could not apply %s on Naukri profile page", change_type)
            return False
        except Exception as e:
            log.error("Naukri CV update error: %s", e)
            return False
        finally:
            await page.close()
            await ctx.close()
            await browser.close()


def run_cv_update() -> None:
    """Select a random strategy and apply one CV update on Naukri.
    On failure, open Naukri profile for manual update.
    """
    rules = get_update_rules()
    max_per_day = rules.get("max_updates_per_day", 40)

    conn = get_conn()
    today_count = conn.execute(
        "SELECT COUNT(*) FROM cv_updates WHERE date(updated_at)=date('now')"
    ).fetchone()[0]

    if today_count >= max_per_day:
        log.info("Daily CV update limit reached (%d)", max_per_day)
        return

    strategy = pick_random_change()
    if not strategy:
        log.warning("No update strategy available")
        return

    log.info("Applying CV update: %s | %s -> %s",
             strategy["change_type"], str(strategy["old_value"])[:30],
             str(strategy["new_value"])[:30])

    try:
        success = asyncio.run(_update_async(strategy))
    except Exception as e:
        log.error("CV update asyncio error: %s", e)
        success = False

    conn.execute(
        "INSERT INTO cv_updates (change_type,field_changed,old_value,new_value,success) VALUES (?,?,?,?,?)",
        (strategy["change_type"], strategy["field"],
         strategy["old_value"], strategy["new_value"], int(success)),
    )
    conn.commit()

    if success:
        log_audit("optimizer", "cv_update_ok", str(strategy))
    else:
        log_audit("optimizer", "cv_update_manual_needed", str(strategy))
        # Only open browser once per day — not every 16 minutes
        today = datetime.now().strftime("%Y-%m-%d")
        last_open = conn.execute(
            "SELECT date(updated_at) FROM cv_updates WHERE success=0 ORDER BY id DESC LIMIT 2 OFFSET 1"
        ).fetchone()
        already_opened_today = last_open and last_open[0] == today
        if not already_opened_today:
            def _open_profile():
                time.sleep(2.5)
                try:
                    webbrowser.open(NAUKRI_PROFILE_URL)
                    log.info("Opened Naukri profile for manual CV update (once today)")
                except Exception:
                    pass
            threading.Thread(target=_open_profile, daemon=True).start()
