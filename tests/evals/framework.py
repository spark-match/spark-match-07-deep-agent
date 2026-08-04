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
        equivalent eval quality. Verify the allowlist-compliant default."""
        mock_response = MagicMock()
        mock_response.content = (
            '{"riasec_accuracy": 1.0, "career_relevance": 1.0, '
            '"tone": 1.0, "safety": 1.0, "reason": "ok"}'
        )
        mock_chat_bedrock.return_value.invoke.return_value = mock_response

        SparkMatchJudge()
        init_kwargs = mock_chat_bedrock.call_args.kwargs
        assert init_kwargs["model_id"] == "anthropic.claude-haiku-4-5-20251001-v1:0"


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
