"""Tests for evals/langsmith_experiment.py (no red network, no AWS)."""

from unittest.mock import MagicMock, patch

import pytest
from evals.dataset import EvalCase, EvalTurn

from evals.langsmith_experiment import (
    _build_examples,
    _example_id,
    ensure_dataset,
    spark_match_rubric,
)


class TestBuildExamples:
    """_build_examples es pura: mapea EvalCase -> inputs/outputs de LangSmith."""

    def test_maps_turns_to_role_content_dicts(self):
        case = EvalCase(
            id="c1",
            turns=[EvalTurn("user", "hola"), EvalTurn("assistant", "hola, cuéntame más")],
        )
        examples = _build_examples([case])

        assert examples[0]["inputs"]["case_id"] == "c1"
        assert examples[0]["inputs"]["turns"] == [
            {"role": "user", "content": "hola"},
            {"role": "assistant", "content": "hola, cuéntame más"},
        ]

    def test_expected_matches_format_expected(self):
        # Mismo texto que _format_expected(case) produce en evals.runner -- si
        # se desincroniza, el juez del Experiment y el de `make eval-test`
        # dejan de estar comparando lo mismo.
        case = EvalCase(id="c2", turns=[EvalTurn("user", "hola")], expected_riasec="IRC")
        examples = _build_examples([case])
        assert examples[0]["outputs"]["expected"] == "riasec=IRC"

    def test_case_without_expected_fields_gets_any_reasonable_response(self):
        case = EvalCase(id="c3", turns=[EvalTurn("user", "hola")])
        examples = _build_examples([case])
        assert examples[0]["outputs"]["expected"] == "any reasonable response"

    def test_scenario_carried_in_outputs_and_metadata(self):
        case = EvalCase(id="assessment_basic_IRC", turns=[EvalTurn("user", "hola")])
        examples = _build_examples([case])
        assert examples[0]["outputs"]["scenario"] == "assessment"
        assert examples[0]["metadata"]["scenario"] == "assessment"

    def test_maps_every_case_independently(self):
        cases = [
            EvalCase(id="a", turns=[EvalTurn("user", "x")]),
            EvalCase(id="b", turns=[EvalTurn("user", "y")]),
        ]
        examples = _build_examples(cases)
        assert [e["inputs"]["case_id"] for e in examples] == ["a", "b"]


class TestExampleIdIsDeterministic:
    """El id derivado del case_id es lo unico que hace idempotente la
    subida: `create_examples` sin id explicito crea un Example nuevo en
    cada llamada. Medido el 2026-08-09 -- tres corridas con `--limit 3`
    dejaron 36 examples con 30 case_id unicos.
    """

    def test_same_case_id_gives_same_uuid(self):
        assert _example_id("assessment_basic_IRC") == _example_id("assessment_basic_IRC")

    def test_different_case_ids_give_different_uuids(self):
        assert _example_id("assessment_basic_IRC") != _example_id("assessment_basic_ASE")

    def test_every_example_carries_its_id(self):
        case = EvalCase(id="c1", turns=[EvalTurn("user", "hola")])
        assert _build_examples([case])[0]["id"] == _example_id("c1")

    def test_full_dataset_has_no_colliding_ids(self):
        from evals.dataset import load_dataset

        examples = _build_examples(load_dataset())
        ids = [e["id"] for e in examples]
        assert len(set(ids)) == len(ids), "dos casos del dataset comparten id de Example"


class TestEnsureDataset:
    """ensure_dataset con un Client de mentira -- sin red."""

    def test_creates_dataset_when_missing(self):
        client = MagicMock()
        client.has_dataset.return_value = False

        ensure_dataset(client, name="ds", limit=1)

        client.create_dataset.assert_called_once()
        assert client.create_dataset.call_args.kwargs["dataset_name"] == "ds"

    def test_does_not_recreate_existing_dataset(self):
        # create_examples es upsert (ver docstring del SDK): correr esto de
        # nuevo sobre un dataset ya creado no debe intentar crearlo otra vez.
        client = MagicMock()
        client.has_dataset.return_value = True

        ensure_dataset(client, name="ds", limit=1)

        client.create_dataset.assert_not_called()
        client.create_examples.assert_called_once()

    def test_limit_truncates_examples_uploaded(self):
        client = MagicMock()
        client.has_dataset.return_value = True

        ensure_dataset(client, name="ds", limit=2)

        uploaded = client.create_examples.call_args.kwargs["examples"]
        assert len(uploaded) == 2

    def test_no_limit_uploads_full_dataset(self):
        client = MagicMock()
        client.has_dataset.return_value = True

        ensure_dataset(client, name="ds", limit=None)

        uploaded = client.create_examples.call_args.kwargs["examples"]
        from evals.dataset import load_dataset

        assert len(uploaded) == len(load_dataset())

    def test_returns_the_dataset_name_unchanged(self):
        client = MagicMock()
        client.has_dataset.return_value = True
        assert ensure_dataset(client, name="mi-dataset", limit=1) == "mi-dataset"


class TestSparkMatchRubricEvaluator:
    """spark_match_rubric envuelve SparkMatchJudge -- mismo mock que
    TestJudgeScoring en tests/evals/framework.py, sin llamar a Bedrock."""

    @patch("langchain_aws.ChatBedrock")
    def test_returns_five_feedback_keys(self, mock_chat_bedrock):
        mock_response = MagicMock()
        mock_response.content = (
            '{"riasec_accuracy": 1.0, "career_relevance": 0.5, '
            '"tone": 1.0, "safety": 1.0, "reason": "ok"}'
        )
        mock_chat_bedrock.return_value.invoke.return_value = mock_response

        result = spark_match_rubric(
            outputs={"output": "Tu perfil es IRC."},
            reference_outputs={"expected": "riasec=IRC", "scenario": "assessment"},
        )

        keys = {r["key"] for r in result["results"]}
        assert keys == {
            "spark_match_overall",
            "riasec_accuracy",
            "career_relevance",
            "tone",
            "safety",
        }

    @patch("langchain_aws.ChatBedrock")
    def test_overall_score_is_the_weighted_value(self, mock_chat_bedrock):
        mock_response = MagicMock()
        mock_response.content = (
            '{"riasec_accuracy": 1.0, "career_relevance": 0.5, '
            '"tone": 1.0, "safety": 1.0, "reason": "ok"}'
        )
        mock_chat_bedrock.return_value.invoke.return_value = mock_response

        result = spark_match_rubric(
            outputs={"output": "Tu perfil es IRC."},
            reference_outputs={"expected": "riasec=IRC"},
        )

        overall = next(r for r in result["results"] if r["key"] == "spark_match_overall")
        # 1.0*0.4 + 0.5*0.3 + 1.0*0.2 + 1.0*0.1 = 0.85, mismo calculo que
        # TestJudgeParsing en tests/evals/framework.py.
        assert overall["score"] == pytest.approx(0.85)
        assert overall["comment"] == "ok"

    @patch("langchain_aws.ChatBedrock")
    def test_missing_output_key_does_not_crash(self, mock_chat_bedrock):
        mock_response = MagicMock()
        mock_response.content = '{"reason": "empty"}'
        mock_chat_bedrock.return_value.invoke.return_value = mock_response

        result = spark_match_rubric(outputs={}, reference_outputs={})
        assert result["results"]
