"""Load and select CV update strategies from naukri_update_strategies.yaml."""

import random
from typing import Any

from core.config_loader import load_config
from core.logger import get_logger

log = get_logger(__name__)

_rotation_index: int = 0


def pick_random_change() -> dict[str, str] | None:
    """Select one CV change strategy, rotating through change types.

    Rotation order: synonym_swap_skill, headline_variation, summary_micro_tweak,
    skill_reorder (inverse synonym), headline_variation (repeated).

    Returns:
        Dict with keys: change_type, field, old_value, new_value.
        Returns None if no strategies are available.
    """
    global _rotation_index

    cfg = load_config().get("naukri_update_strategies", {})
    synonyms = cfg.get("synonym_swaps", {})
    rotation = cfg.get("update_rules", {}).get(
        "change_types_rotation",
        ["synonym_swap_skill", "headline_variation", "summary_micro_tweak"],
    )

    # Try up to len(rotation) times to find a workable change type
    for attempt in range(len(rotation)):
        change_type = rotation[(_rotation_index + attempt) % len(rotation)]

        if change_type == "synonym_swap_skill":
            pairs = synonyms.get("skills", [])
            if pairs:
                _rotation_index += attempt + 1
                pair = random.choice(pairs)
                return {"change_type": "synonym_swap_skill", "field": "skill",
                        "old_value": pair[0], "new_value": pair[1]}

        elif change_type == "headline_variation":
            headlines = synonyms.get("headline_variations", [])
            if headlines:
                _rotation_index += attempt + 1
                new_headline = random.choice(headlines)
                return {"change_type": "headline_variation", "field": "headline",
                        "old_value": "", "new_value": new_headline}

        elif change_type == "summary_micro_tweak":
            tweaks = synonyms.get("summary_micro_tweaks", [])
            if tweaks:
                _rotation_index += attempt + 1
                tweak = random.choice(tweaks)
                # Only return if old_value is non-empty (so replace() has a target)
                if tweak[0] and tweak[0] != tweak[1]:
                    return {"change_type": "summary_micro_tweak", "field": "summary",
                            "old_value": tweak[0], "new_value": tweak[1]}

        elif change_type == "skill_reorder":
            # Reorder by swapping a synonym pair (inverse of synonym_swap)
            pairs = synonyms.get("skills", [])
            if pairs:
                _rotation_index += attempt + 1
                pair = random.choice(pairs)
                return {"change_type": "skill_reorder", "field": "skill",
                        "old_value": pair[1], "new_value": pair[0]}

    # All rotation types exhausted — use headline as safe fallback
    _rotation_index += 1
    headlines = synonyms.get("headline_variations", [])
    if headlines:
        return {"change_type": "headline_variation", "field": "headline",
                "old_value": "", "new_value": random.choice(headlines)}

    log.warning("No viable CV update strategy found in config")
    return None


def get_update_rules() -> dict[str, Any]:
    """Return the update rules config dict."""
    return load_config().get("naukri_update_strategies", {}).get("update_rules", {})
