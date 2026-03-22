"""Load all YAML configs and environment variables into a single dict.

Security: env vars are split into two namespaces:
  cfg["env"]       — ALL env vars (used only by internal modules like llm_router)
  cfg["env_safe"]  — Only non-secret vars safe to surface in logs/LLM prompts
"""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_config: dict[str, Any] | None = None
CONFIG_DIR = Path("config")
YAML_FILES = [
    "settings", "resume", "job_preferences",
    "qa_bank", "llm_providers", "naukri_update_strategies",
    "user_strategy",
]

# Keys safe to expose in logs and LLM-facing code
_SAFE_ENV_KEYS = {"GMAIL_ADDRESS", "NOTIFICATION_EMAIL"}

# Keys that must never appear in logs or LLM prompts
_SECRET_ENV_KEYS = {
    "GMAIL_APP_PASSWORD", "NAUKRI_PASSWORD", "LINKEDIN_PASSWORD",
    "GEMINI_API_KEY", "GROQ_API_KEY", "TELEGRAM_BOT_TOKEN",
    "LINKEDIN_EMAIL", "NAUKRI_EMAIL", "DASHBOARD_TOKEN",
}


def load_config() -> dict[str, Any]:
    """Load and merge all YAML configs and .env into one dict.

    Subsequent calls return the cached config.

    Returns:
        Merged configuration dictionary.
    """
    global _config
    if _config is not None:
        return _config

    load_dotenv()

    cfg: dict[str, Any] = {"env": {}, "env_safe": {}}
    for name in YAML_FILES:
        path = CONFIG_DIR / f"{name}.yaml"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                cfg[name] = yaml.safe_load(f) or {}
        else:
            cfg[name] = {}

    for key, val in os.environ.items():
        cfg["env"][key] = val
        if key in _SAFE_ENV_KEYS:
            cfg["env_safe"][key] = val

    _config = cfg
    return _config


def reload_config() -> dict[str, Any]:
    """Force reload all config files (used after CV update)."""
    global _config
    _config = None
    return load_config()
