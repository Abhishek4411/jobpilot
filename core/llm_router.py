"""LLM gateway: all agents call this module. Never import openai directly elsewhere.

Strategy:
  Tier A (fast/cheap)  — Groq: classification, QA, heartbeat
  Tier B (quality)     — Gemini: resume parsing, JD analysis, email drafting
  Fallback             — always the other tier on failure

Token efficiency: cached resume summary, tight per-task limits, in-process
prompt cache (dedup repeated JD scoring), 1-hour resume cache TTL.
"""

import hashlib
import os
import time
from typing import Any

from openai import OpenAI, RateLimitError, APIError

from core.logger import get_logger
from core.config_loader import load_config

log = get_logger(__name__)

_clients: dict[str, OpenAI] = {}
_daily_usage: dict[str, int] = {}
_resume_summary_cache: str | None = None
_resume_cache_ts: float = 0.0
_prompt_cache: dict[str, str] = {}   # key=(task+content_hash) -> response text

_RESUME_CACHE_TTL = 3600  # seconds

# Per-task max_tokens budget
_TOKEN_LIMITS: dict[str, int] = {
    "fast_classification": 15,
    "jd_analysis": 300,
    "question_answering": 80,
    "resume_parsing": 4000,
    "quality_drafting": 120,
    "job_extraction": 500,
    "default": 200,
}

# Tier A: Groq (fast, cheap) — simple tasks
# Tier B: Gemini (quality) — complex reasoning and drafting
_ROUTING: dict[str, str] = {
    "fast_classification": "groq",
    "question_answering": "groq",
    "default": "groq",
    "jd_analysis": "gemini",
    "resume_parsing": "gemini",
    "quality_drafting": "gemini",
    "job_extraction": "gemini",
}


def get_resume_summary() -> str:
    """Return a cached 200-token resume summary for prompt injection.

    Re-generates after 1 hour (TTL) or when explicitly invalidated.
    """
    global _resume_summary_cache, _resume_cache_ts
    now = time.time()
    if _resume_summary_cache and (now - _resume_cache_ts) < _RESUME_CACHE_TTL:
        return _resume_summary_cache
    try:
        import yaml
        from pathlib import Path
        data = yaml.safe_load(Path("config/resume.yaml").read_text(encoding="utf-8"))
        p = data.get("personal", {})
        skills: list[str] = []
        for v in data.get("skills", {}).values():
            if isinstance(v, list):
                skills.extend(v)
        _resume_summary_cache = (
            f"Name:{p.get('name','')} Title:{p.get('current_title','')} "
            f"Exp:{p.get('total_experience','')} Loc:{p.get('location','')} "
            f"Email:{p.get('email','')} Phone:{p.get('phone','')} "
            f"Skills:{','.join(str(s) for s in skills[:25])}"
        )
        _resume_cache_ts = now
    except Exception:
        _resume_summary_cache = ""
    return _resume_summary_cache


def invalidate_resume_cache() -> None:
    """Clear cached resume summary (call after CV update)."""
    global _resume_summary_cache, _resume_cache_ts
    _resume_summary_cache = None
    _resume_cache_ts = 0.0


def clear_prompt_cache() -> None:
    """Wipe the in-process prompt cache (called by daily cleanup)."""
    global _prompt_cache
    _prompt_cache = {}


def _get_client(provider: str) -> OpenAI:
    if provider in _clients:
        return _clients[provider]
    cfg = load_config()["llm_providers"]["providers"][provider]
    api_key = os.environ.get(cfg["api_key_env"], "")
    client = OpenAI(api_key=api_key, base_url=cfg["base_url"])
    _clients[provider] = client
    return client


def _pick_provider(task_type: str) -> tuple[str, str]:
    """Return (primary, fallback) provider for a task type."""
    cfg = load_config()["llm_providers"]["providers"]
    primary = _ROUTING.get(task_type, "groq")
    fallback = "gemini" if primary == "groq" else "groq"
    if _daily_usage.get(primary, 0) >= cfg[primary]["daily_token_limit"]:
        log.warning("Provider %s at daily limit, switching to %s", primary, fallback)
        return fallback, primary
    return primary, fallback


def _cache_key(task_type: str, prompt: str, system: str) -> str:
    content = f"{task_type}:{system[:200]}:{prompt[:500]}"
    return hashlib.md5(content.encode()).hexdigest()


def _log_token_usage(provider: str, task_type: str, tokens: int) -> None:
    """Track token usage in audit_log for dashboard display."""
    try:
        from core.db import log_audit
        log_audit("llm_router", "token_usage",
                  f"provider={provider}, task={task_type}, tokens={tokens}")
    except Exception:
        pass


def call(
    prompt: str,
    system: str = "",
    task_type: str = "default",
    max_tokens: int | None = None,
) -> str:
    """Send a prompt to the appropriate LLM and return response text.

    Checks in-process cache first (deduplicates repeated identical calls).
    Tier B (Gemini) handles quality tasks; Tier A (Groq) handles fast tasks.

    Args:
        prompt: The user message.
        system: Optional system prompt.
        task_type: Routing key for provider and token limit selection.
        max_tokens: Override token limit (defaults to per-task budget).

    Returns:
        Response text, or empty string on complete failure.
    """
    tokens = max_tokens or _TOKEN_LIMITS.get(task_type, 200)

    # Truncate prompts to conserve tokens (configurable via settings)
    max_chars = load_config().get("settings", {}).get("notifications", {}).get("max_prompt_chars", 4000)
    prompt_limit = 15000 if task_type == "resume_parsing" else max_chars
    prompt = prompt[:prompt_limit]
    if system:
        system = system[:1000]

    # Cache hit: skip API call for identical repeated inputs
    ck = _cache_key(task_type, prompt, system)
    if ck in _prompt_cache:
        log.debug("Prompt cache hit: %s", task_type)
        return _prompt_cache[ck]

    primary, fallback = _pick_provider(task_type)
    retry_cfg = load_config()["llm_providers"].get("retry", {})
    backoff = retry_cfg.get("backoff_base_seconds", 2)

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    for provider in (primary, fallback):
        cfg = load_config()["llm_providers"]["providers"][provider]
        model = cfg["model"]
        for attempt in range(2):
            try:
                client = _get_client(provider)
                resp = client.chat.completions.create(
                    model=model, messages=messages, max_tokens=tokens, temperature=0.3
                )
                text = resp.choices[0].message.content or ""
                used = getattr(resp.usage, "total_tokens", len(text) // 4)
                _daily_usage[provider] = _daily_usage.get(provider, 0) + used
                _log_token_usage(provider, task_type, used)
                # Cache the result (skip caching for resume parsing — large + unique)
                if task_type != "resume_parsing":
                    _prompt_cache[ck] = text
                return text
            except RateLimitError:
                wait = backoff * (attempt + 1)
                log.warning("Rate limit on %s, waiting %ss", provider, wait)
                time.sleep(wait)
            except APIError as e:
                log.error("API error from %s [%s]: %s", provider, task_type, str(e)[:120])
                break
            except Exception as e:
                log.error("LLM error from %s: %s", provider, str(e)[:120])
                break

    log.error("All providers failed for task_type=%s", task_type)
    return ""
