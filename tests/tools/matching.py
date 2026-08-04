"""Tests for the matching handler."""

import itertools

from src.tools.matching.handler import (
    _raw_riasec_score,
    _riasec_similarity,
    calculate_affinity_handler,
)

_RIASEC_LETTERS = "RIASEC"


class TestMatchingHandler:
    """Tests for the RIASEC affinity calculation handler."""

    def test_returns_top_n(self):
        result = calculate_affinity_handler(riasec_code="IAS", top_n=3)

        assert result["status"] == "success"
        matches = result["data"]["matches"]
        assert len(matches) == 3
        assert all("affinity_score" in m for m in matches)
        assert result["data"]["top_n"] == 3

    def test_sorted_descending(self):
        result = calculate_affinity_handler(riasec_code="IRC")
        scores = [m["affinity_score"] for m in result["data"]["matches"]]
        assert scores == sorted(scores, reverse=True)

    def test_high_affinity_for_matching_profile(self):
        """A profile matching CS (IRC) should score CS highly."""
        result = calculate_affinity_handler(riasec_code="IRC")
        cs_result = next(m for m in result["data"]["matches"] if m["career_id"] == "cs")
        assert cs_result["affinity_score"] > 50

    def test_uppercase_normalization(self):
        result = calculate_affinity_handler(riasec_code="ias")
        assert result["data"]["riasec_code"] == "IAS"

    def test_invalid_code_returns_error(self):
        result = calculate_affinity_handler(riasec_code="")
        assert result["status"] == "error"
        assert result["data"] is None

    def test_default_top_n(self):
        result = calculate_affinity_handler(riasec_code="IRC")
        assert result["data"]["top_n"] == 5
        assert len(result["data"]["matches"]) == 5


class TestRiasecSimilarityBounds:
    """B4 regression: affinity must never exceed 100%, even for malformed
    (repeated-letter) codes that should not occur from a real
    evaluate_riasec_profile call but were never validated against here.

    The previous fix (PR #19) patched a dead module
    (src/tools/matching.py, removed in the Sprint 4 refactor) and never
    reached this handler.
    """

    def test_property_score_bounded_for_all_216_combinations(self):
        """Exhaustive: every ordered 3-letter code (with repeats) against
        every other, both directions, must stay within [0, 100]."""
        codes = ["".join(c) for c in itertools.product(_RIASEC_LETTERS, repeat=3)]
        assert len(codes) == 216

        for profile in codes:
            for career in codes:
                score = _riasec_similarity(profile, career)
                assert 0 <= score <= 100, f"{profile} vs {career} -> {score}"

    def test_degenerate_repeated_letter_profile_used_to_exceed_100(self):
        """Concrete regression case: with the old fixed denominator (60),
        a profile of a single repeated letter against itself raw-scored
        120, i.e. 200% — this is the exact scenario B4 describes."""
        raw = _raw_riasec_score("III", "III")
        assert raw == 120.0  # confirms the degenerate case really is > the
        # old fixed denominator of 60; the bug was real, not hypothetical.
        assert _riasec_similarity("III", "III") == 100.0

    def test_self_match_is_always_100_percent(self):
        """A profile matched against itself is always the ceiling: 100%,
        regardless of whether the code has distinct or repeated letters."""
        codes = ["".join(c) for c in itertools.product(_RIASEC_LETTERS, repeat=3)]
        for code in codes:
            assert _riasec_similarity(code, code) == 100.0

    def test_distinct_letter_codes_are_unaffected_by_the_fix(self):
        """For real (3-distinct-letter) RIASEC codes, self-match raw score
        is exactly 60 — identical to the pre-fix fixed denominator — so
        legitimate scores are byte-for-byte unchanged by this fix."""
        assert _raw_riasec_score("IAS", "IAS") == 60.0
        assert _raw_riasec_score("RIC", "RIC") == 60.0
