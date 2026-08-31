"""Tests for evals/langsmith_experiment.py (no red network, no AWS)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from evals.dataset import EvalCase, EvalTurn, load_dataset

from evals.langsmith_experiment import (
    _build_examples,
    _example_id,
    coverage_report,
    ensure_dataset,
    run_target,
    spark_match_rubric,
)


def _row(*, scored: bool):
    """Fila al estilo ExperimentResultRow con una nota util."""
    keys = [{"key": "spark_match_overall", "score": 1.0}] if scored else []
    return {"run": object(), "example": object(), "evaluation_results": {"results": keys}}


def _row_evaluador_reventado():
    """Lo que escribe `evaluate()` cuando el evaluador lanza: una feedback key
    con el nombre del evaluador y `extra={"error": True}`, no las 5 del
    rubric. La fila NO viene vacia, y por eso hay que mirar el `extra`."""
    return {
        "run": object(),
        "example": object(),
        "evaluation_results": {
            "results": [
                {"key": "spark_match_rubric", "comment": "TypeError(...)", "extra": {"error": True}}
            ]
        },
    }


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


class TestRunTargetMatchesProduction:
    """El target tiene que invocar al agente igual que `src/api/app.py`.

    Si se desvia, el experiment mide un grafo que no es el que sirve la API.
    Medido el 2026-08-09: faltaba `user_id` en configurable, las tools de
    langmem levantaban ConfigurationError, la excepcion escapaba de
    `run_target` y tumbaba el pipeline entero de `evaluate()`.
    """

    def _invoke(self, case_id="c1"):
        agent = MagicMock()
        agent.ainvoke = AsyncMock(return_value={"messages": [SimpleNamespace(content="respuesta")]})
        with patch("src.agent.factory.create_spark_agent", return_value=agent) as create:
            out = run_target({"case_id": case_id, "turns": [{"role": "user", "content": "hola"}]})
        return agent, create, out

    def test_passes_the_four_configurable_keys_production_passes(self):
        agent, _, _ = self._invoke()
        configurable = agent.ainvoke.call_args.kwargs["config"]["configurable"]
        assert set(configurable) == {"thread_id", "user_id", "role", "email"}

    def test_user_id_is_scoped_per_case(self):
        # Namespaces de langmem distintos: la memoria de un caso no puede
        # filtrarse al siguiente y falsear el resultado.
        agent_a, _, _ = self._invoke(case_id="caso_a")
        agent_b, _, _ = self._invoke(case_id="caso_b")
        uid = lambda a: a.ainvoke.call_args.kwargs["config"]["configurable"]["user_id"]  # noqa: E731
        assert uid(agent_a) != uid(agent_b)

    def test_builds_the_agent_with_checkpointer_and_store(self):
        # Sin store, factory.py:273 apaga las middlewares de memoria y el
        # caso mide un grafo mas pobre que el de produccion.
        _, create, _ = self._invoke()
        assert create.call_args.kwargs["store"] is not None
        assert create.call_args.kwargs["checkpointer"] is not None

    def test_returns_the_last_message_content(self):
        _, _, out = self._invoke()
        assert out == {"output": "respuesta"}


class TestCoverageReport:
    """Un experiment a medias se ve igual de sano que uno completo en la UI:
    la media se calcula sobre los runs que SI tienen feedback. Medido el
    2026-08-09 en spark-match-agent-94b49560 -- 90 runs, 39 sin una sola
    feedback key, y el script salio con codigo 0 anunciando el enlace.
    """

    def test_no_warning_when_every_run_is_scored(self):
        scored, warning = coverage_report([_row(scored=True)] * 90, expected=90)
        assert scored == 90
        assert warning is None

    def test_warns_when_some_runs_have_no_feedback(self):
        rows = [_row(scored=True)] * 51 + [_row(scored=False)] * 39
        scored, warning = coverage_report(rows, expected=90)
        assert scored == 51
        assert warning is not None
        assert "51" in warning
        assert "90" in warning

    def test_warns_when_the_stream_ends_early(self):
        # El caso real: el pipeline se corta y ni siquiera devuelve las filas.
        scored, warning = coverage_report([_row(scored=True)] * 50, expected=90)
        assert scored == 50
        assert warning is not None

    def test_empty_run_is_reported_not_silently_ok(self):
        scored, warning = coverage_report([], expected=90)
        assert scored == 0
        assert warning is not None

    def test_failed_evaluator_does_not_count_as_scored(self):
        # La regresion de mi propio chequeo: en spark-match-agent-80dad4e5
        # decia 90/90 cuando 6 runs solo llevaban la lapida del evaluador.
        rows = [_row(scored=True)] * 84 + [_row_evaluador_reventado()] * 6
        scored, warning = coverage_report(rows, expected=90)
        assert scored == 84
        assert warning is not None


def _fake_client(*, has_dataset=True, existing_ids=()):
    """Client de mentira -- sin red. `list_examples` devuelve los Example que
    el dataset ya tendria, que es lo que decide alta vs actualizacion."""
    client = MagicMock()
    client.has_dataset.return_value = has_dataset
    client.list_examples.return_value = [SimpleNamespace(id=i) for i in existing_ids]
    return client


class TestEnsureDataset:
    """ensure_dataset con un Client de mentira -- sin red."""

    def test_creates_dataset_when_missing(self):
        client = _fake_client(has_dataset=False)

        ensure_dataset(client, name="ds", limit=1)

        client.create_dataset.assert_called_once()
        assert client.create_dataset.call_args.kwargs["dataset_name"] == "ds"

    def test_does_not_recreate_existing_dataset(self):
        client = _fake_client()

        ensure_dataset(client, name="ds", limit=1)

        client.create_dataset.assert_not_called()

    def test_brand_new_cases_are_created(self):
        client = _fake_client(existing_ids=())

        ensure_dataset(client, name="ds", limit=2)

        assert len(client.create_examples.call_args.kwargs["examples"]) == 2
        client.update_examples.assert_not_called()

    def test_cases_already_in_the_dataset_are_updated_not_recreated(self):
        # La regresion concreta: `create_examples` con un id que ya existe
        # responde 409 LangSmithConflictError y tumba la corrida ANTES de
        # arrancar el experiment. Medido el 2026-08-09 -- el segundo intento
        # de correr 30x3 murio asi, con los 30 ids en el cuerpo del error.
        cases = load_dataset()[:2]
        client = _fake_client(existing_ids=[_example_id(c.id) for c in cases])

        ensure_dataset(client, name="ds", limit=2)

        client.create_examples.assert_not_called()
        assert len(client.update_examples.call_args.kwargs["updates"]) == 2

    def test_mixed_dataset_splits_creates_from_updates(self):
        cases = load_dataset()[:3]
        client = _fake_client(existing_ids=[_example_id(cases[0].id)])

        ensure_dataset(client, name="ds", limit=3)

        assert len(client.create_examples.call_args.kwargs["examples"]) == 2
        assert len(client.update_examples.call_args.kwargs["updates"]) == 1

    def test_no_limit_uploads_full_dataset(self):
        client = _fake_client()

        ensure_dataset(client, name="ds", limit=None)

        uploaded = client.create_examples.call_args.kwargs["examples"]
        assert len(uploaded) == len(load_dataset())

    def test_returns_the_dataset_name_unchanged(self):
        assert ensure_dataset(_fake_client(), name="mi-dataset", limit=1) == "mi-dataset"


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
