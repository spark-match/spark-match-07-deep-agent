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
                # Que dimensiones puede ejercer el caso. Viaja en el Example
                # para que el juez de la ruta LangSmith pondere igual que el
                # de `make eval-test` (ver EvalCase.applicable_dims).
                "applicable_dims": sorted(case.applicable_dims),
            },
            "metadata": {"scenario": case.scenario},
        }
        for case in cases
    ]


def ensure_dataset(
    client: Client, name: str = DEFAULT_DATASET_NAME, limit: int | None = None
) -> str:
    """Crea el dataset si no existe y sube o actualiza sus examples.

    ``create_examples`` no es upsert **en ninguna de las dos direcciones**:
    sin id explicito clona el caso en cada llamada, y con el id derivado del
    ``case_id`` (ver :func:`_example_id`) responde 409
    ``LangSmithConflictError`` en cuanto el Example ya existe. La
    idempotencia hay que construirla aqui: se listan los ids que ya estan en
    el dataset, se dan de alta solo los que faltan y los demas van por
    ``update_examples``, que es lo que el propio SDK recomienda desde que
    deprecio ``upsert_examples_multipart`` en 0.3.9.

    ``limit`` corta la lista de casos antes de subir. Ojo: solo limita lo
    que se SUBE -- ``evaluate(data=<nombre>)`` corre igual todos los
    examples que el dataset ya tenga.

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

    examples = _build_examples(cases)
    ya_estan = {example.id for example in client.list_examples(dataset_name=name)}
    altas = [e for e in examples if e["id"] not in ya_estan]
    cambios = [e for e in examples if e["id"] in ya_estan]

    if altas:
        client.create_examples(dataset_name=name, examples=altas)
    if cambios:
        client.update_examples(dataset_name=name, updates=cambios)

    # Retirar un caso del .jsonl no lo saca de LangSmith, y
    # `evaluate(data=<nombre>)` corre TODOS los examples del dataset remoto
    # -- asi que un caso retirado seguiria puntuando en cada experiment
    # nuevo. No se borra solo (destructivo, y `--limit` daria falsos
    # huerfanos): se avisa y se borra a mano.
    if limit is None:
        huerfanos = ya_estan - {e["id"] for e in examples}
        if huerfanos:
            print(
                f"AVISO: {len(huerfanos)} examples en LangSmith que ya no estan en "
                f"evals/dataset.jsonl y van a seguir corriendo. Borrarlos con "
                f"`client.delete_examples(example_ids=[...])`."
            )
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
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    from src.agent.factory import create_spark_agent
    from src.auth.roles import DEFAULT_ROLE
    from src.budget import reset_session_budget

    case_id = inputs["case_id"]
    reset_session_budget(case_id)

    # Mismo montaje que produccion (`src/api/app.py`): checkpointer + store.
    # Sin store, las middlewares de memoria quedan desactivadas
    # (`factory.py:273`) y el caso mide un grafo mas pobre que el real.
    # In-memory y recreados por llamada, asi cada run arranca limpio.
    agent = create_spark_agent(checkpointer=InMemorySaver(), store=InMemoryStore())

    result = asyncio.run(
        agent.ainvoke(
            {"messages": inputs["turns"]},
            # Las CUATRO claves que pone produccion, no solo thread_id: las
            # tools de langmem resuelven su namespace desde
            # `configurable["user_id"]` y sin el levantan ConfigurationError
            # (a proposito, ver `factory.py:262`). Medido el 2026-08-09: esa
            # excepcion escapaba de `run_target`, mataba el pipeline entero de
            # `evaluate()` a mitad y -- como nadie iteraba las filas -- salia
            # como un experiment "exitoso" con la mitad de los runs sin
            # puntuar. Un `user_id` por caso evita ademas que la memoria de
            # un caso contamine al siguiente.
            config={
                "configurable": {
                    "thread_id": case_id,
                    "user_id": f"eval-{case_id}",
                    "role": str(DEFAULT_ROLE),
                    "email": f"{case_id}@evals.spark-match.local",
                }
            },
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
        applicable_dims=reference_outputs.get("applicable_dims"),
    )

    results = [
        {"key": "spark_match_overall", "score": score.value, "comment": score.reason},
    ]
    # Las dimensiones que no aplican se OMITEN en vez de mandarse como 0.0:
    # una feedback key con cero cuenta en la media de esa key en LangSmith y
    # la hunde con casos que nunca la ejercieron. Sin la key, la media de
    # `riasec_accuracy` pasa a ser la de los casos que si esperan un codigo.
    results += [
        {"key": dim, "score": value} for dim, value in score.dimensions.items() if value is not None
    ]
    return {"results": results}


def _tiene_score_util(row: Any) -> bool:
    """True si la fila aporta al menos un score que cuenta para las medias.

    ``EvaluationResult`` marca los fallos del evaluador con
    ``extra={"error": True}``; esos no son notas, son lapidas.
    """
    for result in row["evaluation_results"]["results"]:
        extra = getattr(result, "extra", None)
        if extra is None and isinstance(result, dict):
            extra = result.get("extra")
        if not (extra or {}).get("error"):
            return True
    return False


def coverage_report(rows: list[Any], expected: int) -> tuple[int, str | None]:
    """Cuenta las filas realmente puntuadas y avisa si no cuadran.

    Una fila sin ninguna feedback key util es un run que existe en LangSmith
    pero que no entra en ningun promedio: la media que muestra la UI se
    calcula sobre las que si tienen, asi que un experiment a medias se lee
    como uno completo y mas o menos igual de bueno.

    "Util" excluye las filas donde el evaluador reventó. Cuando eso pasa,
    ``evaluate()`` no deja la fila vacia: escribe una feedback key con el
    nombre del evaluador y ``extra={"error": True}`` en vez de las 5 del
    rubric. Contarlas como puntuadas es justo el error que este chequeo
    existe para evitar -- medido el 2026-08-09 en spark-match-agent-80dad4e5,
    6 de 90 runs cayeron asi (2 por output ``None`` de un caso que reventó, 4
    por throttling de Bedrock en el juez) y aun asi el conteo decia 90/90.

    Returns:
        ``(puntuadas, mensaje_de_aviso_o_None)``.
    """
    scored = sum(1 for row in rows if _tiene_score_util(row))
    if scored == expected:
        return scored, None
    return scored, (
        f"Solo {scored} de {expected} runs quedaron puntuados. Las medias de "
        f"este experiment cubren esa fraccion y NO son comparables contra una "
        f"corrida completa; ver docs/runbook-langsmith.md."
    )


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
        "--experiment-prefix",
        default="spark-match-agent",
        help=(
            "Prefijo del experiment. Se le pega la etiqueta del modelo que "
            "diga SPARK_MODEL_ID, para poder comparar modelos entre si "
            "(default: spark-match-agent)"
        ),
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

    # El modelo se toma de SPARK_MODEL_ID, asi que dos corridas del mismo
    # script pueden medir agentes distintos. Va al nombre del experiment y a
    # su metadata: sin eso, comparar "spark-match-agent-a1b2" contra
    # "spark-match-agent-c3d4" en LangSmith es adivinar cual era cual.
    from src.config import get_settings

    model_id = get_settings().model_id
    etiqueta = model_id.split(".")[-1].removesuffix("-v1:0")

    # `evaluate(data=<nombre>)` corre TODOS los examples del dataset remoto,
    # incluidos los de casos ya retirados del .jsonl. Se filtra a los
    # vigentes en vez de borrarlos: el fichero local es la fuente de verdad,
    # y asi retirar un caso surte efecto sin tocar datos en LangSmith (que
    # ademas siguen ahi para leer experiments viejos).
    vigentes_ids = {_example_id(case.id) for case in load_dataset()}
    vigentes = [
        example
        for example in client.list_examples(dataset_name=dataset_name)
        if example.id in vigentes_ids
    ]
    print(f"Casos a correr: {len(vigentes)}")

    results = evaluate(
        run_target,
        data=vigentes,
        evaluators=[spark_match_rubric],
        experiment_prefix=f"{args.experiment_prefix}-{etiqueta}",
        metadata={"model_id": model_id, "repetitions": args.repetitions},
        max_concurrency=args.max_concurrency,
        num_repetitions=args.repetitions,
        client=client,
    )

    # `evaluate()` devuelve un stream perezoso, y `ExperimentResults` GUARDA
    # cualquier excepcion del pipeline en `_processing_error` en vez de
    # lanzarla: solo la re-lanza si alguien itera las filas. Leer nada mas
    # `.experiment_name` / `.url` no las itera, asi que una corrida rota
    # imprimia su enlace y salia con codigo 0.
    #
    # Medido el 2026-08-09 en spark-match-agent-94b49560: tqdm marco "50it",
    # el script dijo "Experiment: ..." tan tranquilo, y en LangSmith quedaron
    # 90 runs de los cuales 39 sin una sola feedback key -- todos los del
    # final. Los promedios que publicamos (n=50) eran de la primera mitad.
    rows = list(results)

    # Sobre los examples que REALMENTE se corrieron, no sobre
    # `dataset.example_count`: el dataset remoto conserva los casos
    # retirados y compararse contra el daba un falso "faltan 15 runs".
    expected = len(vigentes) * args.repetitions
    scored, warning = coverage_report(rows, expected)

    print(f"Experiment: {results.experiment_name}")
    print(f"Runs puntuados: {scored}/{expected}")
    print(f"Ver en LangSmith: {results.url}")
    if warning:
        raise SystemExit(f"\nADVERTENCIA: {warning}")


if __name__ == "__main__":
    main()
