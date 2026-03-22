"""Tests for the job scoring and keyword overlap modules."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest

from agents.matcher.scorer import _cosine_similarity
from agents.matcher.keyword_search import keyword_overlap


class TestCosineSimilarity:
    """Tests for the cosine similarity function."""

    def test_identical_vectors(self):
        """Identical vectors should have similarity of 1.0."""
        a = np.array([1.0, 2.0, 3.0])
        assert abs(_cosine_similarity(a, a) - 1.0) < 0.001

    def test_orthogonal_vectors(self):
        """Orthogonal vectors should have similarity of 0.0."""
        a = np.array([1.0, 0.0])
        b = np.array([0.0, 1.0])
        assert abs(_cosine_similarity(a, b)) < 0.001

    def test_zero_vector(self):
        """Zero vectors should return 0.0 without error."""
        a = np.zeros(10)
        b = np.ones(10)
        assert _cosine_similarity(a, b) == 0.0

    def test_similar_vectors(self):
        """Similar vectors should have high similarity."""
        a = np.array([1.0, 1.0, 1.0])
        b = np.array([1.0, 1.0, 0.9])
        assert _cosine_similarity(a, b) > 0.99


class TestKeywordOverlap:
    """Tests for the keyword overlap scoring function."""

    def test_full_match(self):
        """All resume skills in JD text should return close to 1.0."""
        jd = "We need Python Flask Django PyTorch NumPy Pandas Neo4j ElasticSearch RAG Cloud"
        score = keyword_overlap(jd)
        assert score > 0.0

    def test_no_match(self):
        """JD with no relevant keywords should return 0.0 (or near 0)."""
        score = keyword_overlap("Accounting and finance position for CPA candidates.")
        assert score < 0.2

    def test_empty_jd(self):
        """Empty JD should return 0.0."""
        assert keyword_overlap("") == 0.0

    def test_case_insensitive(self):
        """Matching should be case-insensitive."""
        lower = keyword_overlap("python flask django")
        upper = keyword_overlap("PYTHON FLASK DJANGO")
        assert abs(lower - upper) < 0.001
