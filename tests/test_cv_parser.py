"""Tests for CV parsing and validation modules."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


class TestValidator:
    """Tests for the resume validator."""

    def test_valid_resume(self):
        """A fully populated resume should have no missing fields."""
        from agents.cv_manager.validator import validate
        resume = {
            "personal": {
                "name": "Test User",
                "email": "test@example.com",
                "phone": "+1 123 456 7890",
                "total_experience": "3 Years",
            },
            "skills": {"primary": ["Python", "SQL"]},
            "experience": [{"title": "Engineer", "company": "Corp", "highlights": []}],
        }
        assert validate(resume) == []

    def test_missing_name(self):
        """Missing name should be reported."""
        from agents.cv_manager.validator import validate
        resume = {
            "personal": {"email": "x@x.com", "phone": "1234", "total_experience": "1yr"},
            "skills": {"primary": ["Python"]},
            "experience": [{"title": "Dev", "company": "X"}],
        }
        missing = validate(resume)
        assert any("name" in m for m in missing)

    def test_missing_skills(self):
        """Empty skills should be reported."""
        from agents.cv_manager.validator import validate
        resume = {
            "personal": {"name": "A", "email": "a@a.com", "phone": "1", "total_experience": "1yr"},
            "skills": {"primary": []},
            "experience": [{"title": "Dev", "company": "X"}],
        }
        missing = validate(resume)
        assert any("skill" in m.lower() for m in missing)

    def test_missing_experience(self):
        """Empty experience list should be reported."""
        from agents.cv_manager.validator import validate
        resume = {
            "personal": {"name": "A", "email": "a@a.com", "phone": "1", "total_experience": "1yr"},
            "skills": {"primary": ["Python"]},
            "experience": [],
        }
        missing = validate(resume)
        assert any("experience" in m.lower() for m in missing)


class TestDiffDetector:
    """Tests for the file hash and diff detection."""

    def test_hash_consistency(self, tmp_path):
        """Same file content should always produce the same hash."""
        from agents.cv_manager.diff_detector import file_hash
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        h1 = file_hash(f)
        h2 = file_hash(f)
        assert h1 == h2

    def test_hash_changes(self, tmp_path):
        """Different file content should produce different hashes."""
        from agents.cv_manager.diff_detector import file_hash
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content one")
        f2.write_text("content two")
        assert file_hash(f1) != file_hash(f2)
