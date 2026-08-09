"""Tests for the evaluation framework (Sprint 4 §4.7)."""

from unittest.mock import MagicMock, patch

import pytest
from evals.dataset import EvalCase, EvalTurn, load_dataset
from evals.judge import JudgeScore, SparkMatchJudge


class TestDatasetLoader:
    """Tests for evals/dataset.jsonl loading."""

    def test_load_default_dataset_has_cases(self):
        cases = load_dataset()
        assert len(cases) >= 5, "dataset should have at least 5 cases"

    def test_each_case_has_id_and_turns(self):
        cases = load_dataset()
        for case in cases:
            assert case.id, f"case missing id: {case}"
            assert len(case.turns) >= 1, f"case {case.id} has no turns"

    def test_turns_have_role_and_content(self):
        cases = load_dataset()
        for case in cases:
            for turn in case.turns:
                assert turn.role in {"user", "assistant", "system"}
                assert turn.content, f"empty content in {case.id}"

    def test_assessment_cases_have_expected_riasec(self):
        cases = load_dataset()
        assessment_cases = [c for c in cases if c.scenario == "assessment"]
        assert assessment_cases, "no assessment cases in dataset"
        for case in assessment_cases:
            assert case.expected_riasec, f"assessment case {case.id} missing expected_riasec"
            assert len(case.expected_riasec) == 3

    def test_scenario_auto_derived_from_id(self):
        case = EvalCase(id="assessment_basic_IRC", turns=[EvalTurn("user", "hi")])
        assert case.scenario == "assessment"


class TestJudgeParsing:
    """Tests for the judge's response parsing (no LLM calls).

    Sprint 9, task 9.B.2: the judge is now multi-dimensional
    (``riasec_accuracy``, ``career_relevance``, ``tone``, ``safety``) with
    a weighted ``value`` in [0, 1] and a ``passed`` bool derived from
    :data:`evals.judge.PASSING_SCORE`.
    """

    def test_parse_valid_full_rubric_pass_json(self):
        text = (
            '{"riasec_accuracy": 1.0, "career_relevance": 0.9, '
            '"tone": 1.0, "safety": 1.0, "reason": "matches expected"}'
        )
        score = SparkMatchJudge._parse_response(text)
        # Weighted: 1.0*0.4 + 0.9*0.3 + 1.0*0.2 + 1.0*0.1 = 0.97
        assert score.value == pytest.approx(0.97)
        assert score.passed is True
        assert score.dimensions["riasec_accuracy"] == 1.0
        assert score.dimensions["career_relevance"] == 0.9

    def test_parse_valid_full_rubric_fail_json(self):
        text = (
            '{"riasec_accuracy": 0.0, "career_relevance": 0.0, '
            '"tone": 0.5, "safety": 1.0, "reason": "wrong riasec"}'
        )
        score = SparkMatchJudge._parse_response(text)
        # Weighted: 0.0*0.4 + 0.0*0.3 + 0.5*0.2 + 1.0*0.1 = 0.20
        assert score.value == pytest.approx(0.20)
        assert score.passed is False

    def test_parse_partial_rubric_above_passing_threshold(self):
        """Mixed scores: riasec perfect, career_relevance mediocre.
        Weighted: 1.0*0.4 + 0.5*0.3 + 1.0*0.2 + 1.0*0.1 = 0.85 >= 0.7
        so this still passes -- the rubric gives weight to other dims
        even when one is weak."""
        text = (
            '{"riasec_accuracy": 1.0, "career_relevance": 0.5, '
            '"tone": 1.0, "safety": 1.0, "reason": "weak careers"}'
        )
        score = SparkMatchJudge._parse_response(text)
        assert score.value == pytest.approx(0.85)
        assert score.passed is True

    def test_parse_below_passing_threshold_with_two_dimensions_low(self):
        """If TWO heavy dimensions fail, the weighted score drops below
        0.7. Weighted: 0.3*0.4 + 0.3*0.3 + 1.0*0.2 + 1.0*0.1 = 0.51."""
        text = (
            '{"riasec_accuracy": 0.3, "career_relevance": 0.3, '
            '"tone": 1.0, "safety": 1.0, "reason": "weak everywhere"}'
        )
        score = SparkMatchJudge._parse_response(text)
        assert score.value == pytest.approx(0.51)
        assert score.passed is False

    def test_parse_json_in_markdown_fence(self):
        text = (
            '```json\n{"riasec_accuracy": 1.0, "career_relevance": 1.0, '
            '"tone": 1.0, "safety": 1.0, "reason": "PASS"}\n```'
        )
        score = SparkMatchJudge._parse_response(text)
        assert score.value == pytest.approx(1.0)
        assert score.passed is True

    def test_parse_invalid_json_returns_fail(self):
        text = "this is not json"
        score = SparkMatchJudge._parse_response(text)
        assert score.value == 0.0
        assert score.passed is False
        assert "JSON" in score.reason or "json" in score.reason

    def test_parse_non_object_json_returns_fail(self):
        text = "[1.0, 2.0]"
        score = SparkMatchJudge._parse_response(text)
        assert score.value == 0.0
        assert score.passed is False

    def test_parse_missing_dimensions_default_to_zero(self):
        text = '{"reason": "no scores at all"}'
        score = SparkMatchJudge._parse_response(text)
        assert score.value == 0.0
        assert score.passed is False
        assert all(v == 0.0 for v in score.dimensions.values())

    def test_parse_dimensions_clamped_to_unit_interval(self):
        text = (
            '{"riasec_accuracy": 1.5, "career_relevance": -0.2, '
            '"tone": 0.5, "safety": 2.0, "reason": "out of range"}'
        )
        score = SparkMatchJudge._parse_response(text)
        assert score.dimensions["riasec_accuracy"] == 1.0
        assert score.dimensions["career_relevance"] == 0.0
        assert score.dimensions["tone"] == 0.5
        assert score.dimensions["safety"] == 1.0


class TestJudgeScoring:
    """Tests for the judge with a mocked LLM (no AWS calls).

    Sprint 9, task 9.B.2: default model is now Haiku 4.5 (was Sonnet);
    the rubric is multi-dimensional and the pass threshold is 0.7 (was
    0.5 binary).
    """

    @patch("langchain_aws.ChatBedrock")
    def test_score_uses_mocked_llm(self, mock_chat_bedrock):
        mock_response = MagicMock()
        mock_response.content = (
            '{"riasec_accuracy": 1.0, "career_relevance": 1.0, '
            '"tone": 1.0, "safety": 1.0, "reason": "PASS: matches"}'
        )
        mock_chat_bedrock.return_value.invoke.return_value = mock_response

        judge = SparkMatchJudge()
        score = judge.score(output="some output", expected="IRC")

        assert isinstance(score, JudgeScore)
        assert score.value == pytest.approx(1.0)
        assert score.passed is True
        assert "PASS" in score.reason

    @patch("langchain_aws.ChatBedrock")
    def test_score_truncates_long_output(self, mock_chat_bedrock):
        mock_response = MagicMock()
        mock_response.content = (
            '{"riasec_accuracy": 1.0, "career_relevance": 1.0, '
            '"tone": 1.0, "safety": 1.0, "reason": "PASS"}'
        )
        mock_chat_bedrock.return_value.invoke.return_value = mock_response

        judge = SparkMatchJudge()
        long_output = "x" * 5000
        judge.score(output=long_output, expected="IRC")

        call_args = mock_chat_bedrock.return_value.invoke.call_args
        prompt = call_args[0][0]
        assert "xxxxxx" in prompt
        assert len(prompt) < 5000

    @patch("langchain_aws.ChatBedrock")
    def test_score_default_model_id_is_haiku_4_5(self, mock_chat_bedrock):
        """POC v2 leccion 4: Haiku 4.5 = 10x cheaper than Sonnet with
        equivalent eval quality. Verify the allowlist-compliant default.

        El prefijo `us.` no es cosmetico: sin el, ChatBedrock.invoke()
        revienta con ValidationException porque el foundation model a
        secas no admite on-demand throughput en esta cuenta (medido el
        2026-08-09 corriendo el judge de verdad, no mockeado). Este test
        mockea ChatBedrock, asi que un regreso al id sin prefijo lo
        dejaria pasar igual -- por eso el string se comprueba exacto en
        vez de solo el sufijo.
        """
        mock_response = MagicMock()
        mock_response.content = (
            '{"riasec_accuracy": 1.0, "career_relevance": 1.0, '
            '"tone": 1.0, "safety": 1.0, "reason": "ok"}'
        )
        mock_chat_bedrock.return_value.invoke.return_value = mock_response

        SparkMatchJudge()
        init_kwargs = mock_chat_bedrock.call_args.kwargs
        assert init_kwargs["model_id"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"


class TestRunnerMock:
    """Tests for the runner in mock mode (no AWS calls)."""

    def test_run_eval_mock_completes(self):
        # In mock mode, no LLM judge is called - heuristics are used.
        from evals.runner import run_eval

        results = run_eval(mode="mock")

        assert len(results) >= 5
        # Each result should have a non-empty reason
        for r in results:
            assert r.reason, f"empty reason for {r.case_id}"
            assert "mock" in r.reason.lower()

    def test_mock_evaluate_riasec_case(self):
        from evals.runner import _mock_evaluate

        case = EvalCase(id="test", turns=[EvalTurn("user", "hi")], expected_riasec="IRC")
        passed, reason = _mock_evaluate(case, "Tu perfil es IRC.")
        assert passed is True
        assert "IRC" in reason

    def test_mock_evaluate_chitchat_case(self):
        from evals.runner import _mock_evaluate

        case = EvalCase(id="test", turns=[EvalTurn("user", "hi")], expected_no_tool_calls=True)
        passed, _ = _mock_evaluate(case, "Hola! Estoy bien, gracias.")
        assert passed is True

    def test_run_eval_unknown_case_still_runs(self):
        """Cases with no specific expected fields still produce output."""
        from evals.runner import _format_expected, _run_mock_case

        case = EvalCase(id="custom_xyz", turns=[EvalTurn("user", "hola")])
        assert _format_expected(case) == "any reasonable response"

        output = _run_mock_case(case)
        assert output  # non-empty


class TestMockModeDetectsHandlerRegressions:
    """Sprint 9, task 9.B.3 -- "the mock fails when a handler is broken
    on purpose".

    Sprint 5, task 5.7 closed the *tautological* mock (B9): ``_run_mock_case``
    now invokes the real ``evaluate_riasec_profile_handler`` /
    ``calculate_affinity_handler`` instead of embedding ``expected_riasec``
    directly. This class proves the bar has teeth -- injecting a regression
    in either handler must surface as a failing ``run_eval(mode="mock")``
    case. Without these tests, a future regression that turns the handler
    into a no-op or a constant could pass ``make qa`` silently (the dataset
    would still load, mock would still produce output, only the *content*
    would be wrong).

    The patching targets the source module (``src.tools.assessment.handler``,
    ``src.tools.matching.handler``). The local ``from ... import ...``
    inside ``_run_mock_case`` re-reads the attribute on every call, so
    ``monkeypatch.setattr(src_module, "handler_name", buggy)`` reaches the
    mock runner without re-wiring imports.
    """

    def test_baseline_no_injection_all_assessment_cases_pass(self):
        """Sanity check: without any injected bug, the dataset's
        assessment cases all pass in mock mode. If THIS test fails the
        injected-bug tests below are meaningless (they would fail too,
        for the wrong reason)."""
        from evals.dataset import load_dataset
        from evals.runner import _mock_evaluate, _run_mock_case

        cases = [c for c in load_dataset() if c.expected_riasec]
        assert cases, "no assessment cases in the dataset"

        for case in cases:
            output = _run_mock_case(case)
            passed, reason = _mock_evaluate(case, output)
            assert passed is True, (
                f"baseline case {case.id} failed without any injected bug: {reason!r}"
            )

    def test_injected_assessment_handler_bug_fails_at_least_one_case(self):
        """Inject a "always return XXX" bug into the assessment handler.
        The mock runner calls the real handler, so the resulting output
        will contain ``XXX`` (or no RIASEC code at all) instead of the
        expected code, and the dataset's RIASEC-overlap heuristic in
        ``_mock_evaluate`` must reject it.

        Verifies that a regression in the assessment handler cannot
        sneak past CI by accident."""
        from src.tools import assessment as assessment_pkg

        def always_xxx(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {
                "status": "success",
                "data": {
                    "riasec_code": "XXX",
                    "scores": {"R": 1, "I": 1, "A": 1, "S": 1, "E": 1, "C": 1},
                    "dominant_types": [],
                    "interpretation": "injected-bug output",
                },
                "errors": None,
            }

        # Bind through the package to reach the symbol ``_run_mock_case``
        # imports locally.
        original = assessment_pkg.handler.evaluate_riasec_profile_handler
        assessment_pkg.handler.evaluate_riasec_profile_handler = always_xxx
        try:
            from evals.runner import run_eval

            results = run_eval(mode="mock")
        finally:
            assessment_pkg.handler.evaluate_riasec_profile_handler = original

        assessment_failures = [r for r in results if r.case_id.startswith("assessment_")]
        assert assessment_failures, "no assessment cases in the dataset"
        failed = [r for r in assessment_failures if not r.passed]
        assert failed, (
            "injected 'always-XXX' bug went undetected: all assessment "
            "cases still passed the mock-mode bar"
        )
        # Sanity: the heuristic must have caught the wrong code, not
        # passed it through for some unrelated reason.
        assert any("XXX" in r.output or "FAIL" in r.reason for r in failed), (
            f"unexpected failure mode: {[r.reason for r in failed]!r}"
        )

    def test_injected_matching_handler_bug_fails_at_least_one_case(self):
        """Inject a "return empty match list" bug into the matching
        handler. The matching cases (``expected_careers_count`` set)
        feed the handler's output through ``_run_mock_case`` and then
        expect a non-empty match list with the requested code -- an
        empty list must trip the bar.

        Verifies the matching-side half of the same regression-detection
        contract."""
        from src.tools import matching as matching_pkg

        def empty_matches(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"status": "success", "data": {"matches": []}, "errors": None}

        original = matching_pkg.handler.calculate_affinity_handler
        matching_pkg.handler.calculate_affinity_handler = empty_matches
        try:
            from evals.runner import run_eval

            results = run_eval(mode="mock")
        finally:
            matching_pkg.handler.calculate_affinity_handler = original

        matching_failures = [r for r in results if r.case_id.startswith("matching_")]
        assert matching_failures, "no matching cases in the dataset"
        failed = [r for r in matching_failures if not r.passed]
        assert failed, (
            "injected 'empty matches' bug went undetected: all matching "
            "cases still passed the mock-mode bar"
        )
