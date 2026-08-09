"""Sube evals/dataset.jsonl como Dataset de LangSmith y corre un Experiment.

Complementa a `evals.runner` (que imprime pass/fail a consola) sin
reemplazarlo: mismo dataset, mismo `SparkMatchJudge`, pero corridos a través
del SDK de LangSmith (`langsmith.evaluate`) para obtener la vista de
Experiments — comparar corridas entre sí en el tiempo, cosa que
`evals.runner` no ofrece.

Nada de la lógica de negocio se reimplementa: `_build_examples` reusa
`evals.dataset.load_dataset` y `evals.runner._format_expected`, y
`spark_match_rubric` envuelve `evals.judge.SparkMatchJudge` tal cual.

Quick start::

    uv run python -m evals.langsmith_experiment              # las 29 de siempre
    uv run python -m evals.langsmith_experiment --limit 3     # smoke, barato
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from evals.dataset import EvalCase, load_dataset
from evals.runner import _format_expected

if TYPE_CHECKING:
    from langsmith import Client

DEFAULT_DATASET_NAME = "spark-match-agent-evals"

# Namespace fijo para derivar el id de cada Example de su `case_id`.
# `create_examples` NO deduplica por contenido -- medido el 2026-08-09:
# tres corridas con `--limit 3` dejaron el dataset con 36 examples y solo 30
# case_id unicos. Con un id derivado del case_id, subir el mismo caso otra
# vez actualiza el Example en vez de crear un gemelo.
_EXAMPLE_ID_NAMESPACE = uuid.UUID("6f1b2c3d-4e5a-4b7c-8d9e-0a1b2c3d4e5f")


def _example_id(case_id: str) -> uuid.UUID:
    """Id estable y reproducible para el Example de un caso."""
    return uuid.uuid5(_EXAMPLE_ID_NAMESPACE, case_id)


def _build_examples(cases: list[EvalCase]) -> list[dict[str, Any]]:
    """Traduce los EvalCase del dataset propio al formato inputs/outputs
    que espera un Example de LangSmith.

    ``outputs`` aquí es el "ground truth" del Example (lo que LangSmith le
    pasará al evaluador como ``reference_outputs``), no la respuesta del
    agente -- esa la produce ``run_target`` en cada corrida.
    """
    return [
        {
            "id": _example_id(case.id),
            "inputs": {
                "case_id": case.id,
                "turns": [{"role": t.role, "content": t.content} for t in case.turns],
            },
            "outputs": {
                # Calculado una sola vez aquí, con la misma función que usa
                # evals.runner, para que el texto de "lo esperado" que ve el
                # juez sea idéntico en las dos rutas de evaluación.
                "expected": _format_expected(case),
                "scenario": case.scenario,
            },
            "metadata": {"scenario": case.scenario},
        }
        for case in cases
    ]


def ensure_dataset(
    client: Client, name: str = DEFAULT_DATASET_NAME, limit: int | None = None
) -> str:
    """Crea el dataset si no existe y sube/actualiza sus examples.

    Idempotente **gracias al id derivado del ``case_id``** (ver
    :func:`_example_id`), no porque ``create_examples`` lo sea: sin id
    explicito crea un Example nuevo en cada llamada, aunque el contenido
    sea identico. ``limit`` corta la lista de casos antes de subir, para
    una corrida barata de humo en vez de las 29 completas.

    Returns:
        El nombre del dataset (para pasarlo tal cual a ``evaluate(data=...)``).
    """
    cases = load_dataset()
    if limit is not None:
        cases = cases[:limit]

    if not client.has_dataset(dataset_name=name):
        client.create_dataset(
            dataset_name=name,
            description=(
                "Casos de evals/dataset.jsonl del deep-agent, subidos por "
                "evals/langsmith_experiment.py. Misma fuente que "
                "`make eval-test`; ver evals/dataset.py."
            ),
        )

    client.create_examples(dataset_name=name, examples=_build_examples(cases))
    return name


def run_target(inputs: dict[str, Any]) -> dict[str, Any]:
    """Corre UN caso contra el agente real. Es el ``target`` de `evaluate()`.

    Agente fresco por llamada (no se comparte entre corridas concurrentes)
    y el thread_id es el id del caso, para que cada uno tenga su propio
    checkpoint.

    **`ainvoke` y no `invoke`**, aunque `evaluate()` llame a este target
    desde un thread pool y obligue a un `asyncio.run` por caso. La API de
    produccion mueve al agente con `astream_events` (ver
    `src/api/app.py` y el ag_ui adapter), y varias piezas del grafo se
    comportan distinto en modo sincrono:

    - `web_search` es `async def`, asi que bajo `.invoke()` su
      `StructuredTool` levanta `NotImplementedError: StructuredTool does
      not support sync invocation` y mata el turno entero.
    - Los hooks `wrap_model_call` / `wrap_tool_call` de los middlewares
      "genuinely require separate sync/async implementations or raise
      NotImplementedError in the mismatched mode" (`src/agent/guardrails.py`).

    Medido el 2026-08-09: con `.invoke()` el run completo dio
    NotImplementedError en los casos que tocaban web_search, hundiendo
    `riasec_accuracy` por un fallo del harness y no del agente. Evaluar por
    un camino que produccion no usa mide otra cosa.
    """
    from src.agent.factory import create_spark_agent
    from src.budget import reset_session_budget

    agent = create_spark_agent()
    case_id = inputs["case_id"]
    reset_session_budget(case_id)

    result = asyncio.run(
        agent.ainvoke(
            {"messages": inputs["turns"]},
            config={"configurable": {"thread_id": case_id}},
        )
    )

    final_messages = result.get("messages", [])
    output = str(final_messages[-1].content) if final_messages else "(no messages)"
    return {"output": output}


def spark_match_rubric(
    *, outputs: dict[str, Any], reference_outputs: dict[str, Any]
) -> dict[str, Any]:
    """Evaluador de LangSmith: envuelve ``SparkMatchJudge`` sin tocar su rubric.

    La firma con kwargs ``outputs``/``reference_outputs`` la reconoce
    ``evaluate()`` automáticamente (ver
    ``langsmith.evaluation.evaluator._normalize_evaluator_func``) y los
    llena solo: ``outputs`` es lo que devolvió ``run_target`` en ESTA
    corrida, ``reference_outputs`` es el campo ``outputs`` del Example que
    subió ``ensure_dataset``.

    Devuelve las 4 dimensiones del rubric MÁS el score ponderado como 5
    feedback keys separadas -- así la tabla de comparación de Experiments
    en LangSmith puede ordenar/filtrar por ``tone`` o ``safety`` sola, no
    solo por el promedio.
    """
    from evals.judge import SparkMatchJudge

    judge = SparkMatchJudge()
    score = judge.score(
        output=outputs.get("output", ""),
        expected=reference_outputs.get("expected", "any reasonable response"),
        scenario=reference_outputs.get("scenario", "agent response evaluation"),
    )

    results = [
        {"key": "spark_match_overall", "score": score.value, "comment": score.reason},
    ]
    results += [{"key": dim, "score": value} for dim, value in score.dimensions.items()]
    return {"results": results}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sube evals/dataset.jsonl a LangSmith y corre un Experiment"
    )
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET_NAME,
        help=f"Nombre del dataset en LangSmith (default: {DEFAULT_DATASET_NAME})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Solo subir/correr los primeros N casos (smoke test barato)",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=3,
        help="Casos en paralelo contra el agente real (default: 3)",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        help=(
            "Correr cada caso N veces dentro del mismo Experiment. Con N>1 el "
            "stdev de cada feedback key deja de ser ruido y empieza a medir la "
            "varianza real del agente + el juez, que es lo que separa una "
            "regresion de un mal dia del LLM (default: 1)"
        ),
    )
    args = parser.parse_args()

    from langsmith import Client
    from langsmith.evaluation import evaluate

    from src.observability.langsmith import configure_langsmith

    if not configure_langsmith():
        raise SystemExit(
            "LangSmith no está configurado (SPARK_LANGSMITH_TRACING/API_KEY). "
            "Ver docs/runbook-langsmith.md."
        )

    client = Client()
    dataset_name = ensure_dataset(client, name=args.dataset, limit=args.limit)

    results = evaluate(
        run_target,
        data=dataset_name,
        evaluators=[spark_match_rubric],
        experiment_prefix="spark-match-agent",
        max_concurrency=args.max_concurrency,
        num_repetitions=args.repetitions,
        client=client,
    )

    print(f"Experiment: {results.experiment_name}")
    print(f"Ver en LangSmith: {results.url}")


if __name__ == "__main__":
    main()
