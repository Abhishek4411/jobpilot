"""Tests for email classification and QA bank matching."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
import pytest


class TestEmailClassifier:
    """Tests for the email category classifier."""

    def test_known_categories(self):
        """Classifier should return one of the valid category strings."""
        from agents.comms.email_classifier import CATEGORIES

        with patch("agents.comms.email_classifier.call", return_value="interview_request"):
            from agents.comms.email_classifier import classify_email
            result = classify_email("Interview Invitation", "We'd like to schedule a call")
            assert result in CATEGORIES

    def test_fallback_to_irrelevant(self):
        """Unknown LLM output should default to 'irrelevant'."""
        with patch("agents.comms.email_classifier.call", return_value="nonsense xyz"):
            from agents.comms.email_classifier import classify_email
            result = classify_email("Random", "blah blah blah")
            assert result == "irrelevant"


class TestQuestionHandler:
    """Tests for QA bank pattern matching."""

    def test_notice_period_match(self):
        """'notice period' pattern should match the QA bank entry."""
        from agents.applier.question_handler import answer_question
        with patch("agents.applier.question_handler.call") as mock_call:
            result = answer_question("What is your notice period?")
            assert isinstance(result, str)
            assert len(result) > 0
            mock_call.assert_not_called()

    def test_total_experience_match(self):
        """'years of experience' pattern should match without LLM call."""
        with patch("agents.applier.question_handler.call") as mock_call:
            from agents.applier.question_handler import answer_question
            result = answer_question("How many years of experience do you have?")
            assert "5" in result or "years" in result.lower()
            mock_call.assert_not_called()

    def test_unknown_question_uses_llm(self):
        """Unknown question should call the LLM."""
        with patch("agents.applier.question_handler.call", return_value="test answer") as mock_call:
            from agents.applier.question_handler import answer_question
            result = answer_question("What is your spirit animal?")
            mock_call.assert_called_once()
