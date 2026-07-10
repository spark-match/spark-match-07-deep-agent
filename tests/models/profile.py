"""Tests for the domain models."""

from src.models.profile import StudentProfile


class TestStudentProfile:
    """Tests for the StudentProfile schema."""

    def test_empty_profile(self):
        profile = StudentProfile()
        assert profile.name is None
        assert profile.has_riasec_profile is False
        assert profile.profile_completeness == 0.0

    def test_partial_profile(self):
        profile = StudentProfile(
            name="Alice",
            realistic=3,
            investigative=9,
            interests=["programación", "ciencia"],
        )
        assert profile.name == "Alice"
        assert profile.has_riasec_profile is False  # Missing 4 scores
        assert profile.profile_completeness > 0.0

    def test_complete_riasec(self):
        profile = StudentProfile(
            name="Bob",
            age=17,
            education_level="preparatoria",
            realistic=3,
            investigative=9,
            artistic=7,
            social=5,
            enterprising=4,
            conventional=2,
            interests=["math", "physics", "coding"],
        )
        assert profile.has_riasec_profile is True
        assert profile.profile_completeness == 1.0

    def test_profile_serialization(self):
        profile = StudentProfile(
            name="Carlos",
            realistic=5,
            investigative=8,
            artistic=6,
            social=4,
            enterprising=3,
            conventional=7,
            riasec_code="ICR",
            interests=["datos", "estadística"],
        )
        data = profile.model_dump()
        assert data["name"] == "Carlos"
        assert data["riasec_code"] == "ICR"
        assert "datos" in data["interests"]

        # Round-trip
        restored = StudentProfile.model_validate(data)
        assert restored == profile
