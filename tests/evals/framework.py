"""Tests for the evaluation framework (Sprint 4 §4.7)."""

from unittest.mock import MagicMock, patch

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
    """Tests for the judge's response parsing (no LLM calls)."""

    def test_parse_valid_pass_json(self):
        text = '{"score": 1.0, "reason": "PASS: matches expected"}'
        score = SparkMatchJudge._parse_response(text)
        assert score.value == 1.0
        assert "PASS" in score.reason

    def test_parse_valid_fail_json(self):
        text = '{"score": 0.0, "reason": "FAIL: wrong riasec"}'
        score = SparkMatchJudge._parse_response(text)
        assert score.value == 0.0
        assert "FAIL" in score.reason

    def test_parse_json_in_markdown_fence(self):
        text = '```json\n{"score": 1.0, "reason": "PASS"}\n```'
        score = SparkMatchJudge._parse_response(text)
        assert score.value == 1.0

    def test_parse_invalid_json_returns_fail(self):
        text = "this is not json"
        score = SparkMatchJudge._parse_response(text)
        assert score.value == 0.0
        assert "JSON" in score.reason or "json" in score.reason

    def test_parse_missing_score_defaults_to_fail(self):
        text = '{"reason": "no score field"}'
        score = SparkMatchJudge._parse_response(text)
        assert score.value == 0.0


class TestJudgeScoring:
    """Tests for the judge with a mocked LLM (no AWS calls)."""

    @patch("langchain_aws.ChatBedrock")
    def test_score_uses_mocked_llm(self, mock_chat_bedrock):
        # Configure mock to return a valid JSON response
        mock_response = MagicMock()
        mock_response.content = '{"score": 1.0, "reason": "PASS: matches"}'
        mock_chat_bedrock.return_value.invoke.return_value = mock_response

        judge = SparkMatchJudge()
        score = judge.score(output="some output", expected="IRC")

        assert isinstance(score, JudgeScore)
        assert score.value == 1.0
        assert "PASS" in score.reason

    @patch("langchain_aws.ChatBedrock")
    def test_score_truncates_long_output(self, mock_chat_bedrock):
        mock_response = MagicMock()
        mock_response.content = '{"score": 1.0, "reason": "PASS"}'
        mock_chat_bedrock.return_value.invoke.return_value = mock_response

        judge = SparkMatchJudge()
        long_output = "x" * 5000
        judge.score(output=long_output, expected="IRC")

        # Verify the prompt passed to the model contains truncated output
        call_args = mock_chat_bedrock.return_value.invoke.call_args
        prompt = call_args[0][0]
        assert "xxxxxx" in prompt
        # Truncated to 2000 chars worth of content
        # (plus some prompt overhead, so prompt < 5000)
        assert len(prompt) < 5000


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
