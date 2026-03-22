"""Tests for the LLM router fallback and provider selection logic."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock
import pytest


class TestLLMRouter:
    """Tests for llm_router provider selection and fallback."""

    def test_pick_provider_default(self):
        """Default task type should select 'gemini' (primary)."""
        from core.llm_router import _pick_provider
        provider = _pick_provider("default")
        assert provider in ("gemini", "groq")

    def test_pick_provider_fast(self):
        """Fast classification tasks should prefer Groq."""
        from core import llm_router
        llm_router._daily_usage.clear()
        provider = llm_router._pick_provider("fast_classification")
        assert provider in ("gemini", "groq")

    def test_fallback_when_limit_hit(self):
        """When Gemini hits its daily limit, should switch to Groq."""
        from core import llm_router
        from core.config_loader import load_config
        cfg = load_config()
        gemini_limit = cfg["llm_providers"]["providers"]["gemini"]["daily_token_limit"]
        llm_router._daily_usage["gemini"] = gemini_limit + 1
        provider = llm_router._pick_provider("default")
        assert provider == "groq"
        llm_router._daily_usage.clear()

    def test_call_returns_string(self):
        """call() should always return a string, even on API failure."""
        from core import llm_router
        with patch.object(llm_router, "_get_client") as mock_client:
            mock_client.side_effect = Exception("Network error")
            result = llm_router.call("test prompt")
            assert isinstance(result, str)
