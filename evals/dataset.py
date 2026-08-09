"""Evaluation dataset for the Spark Match Agent.

Loads JSONL conversations from ``evals/dataset.jsonl`` and exposes them
as a list of :class:`EvalCase` for use by :mod:`evals.runner`.

Each row in the dataset has:
- ``id``: unique case identifier
- ``turns``: list of {role, content} messages that drive the conversation
- ``expected_riasec``: expected RIASEC code (for assessment cases)
- ``expected_careers_count``: expected number of career matches
- ``expected_career``: expected specific career, por NOMBRE. Se llamaba
  ``expected_career_id`` y traia el id de una ficha de ``data/careers/*.md``;
  al retirarse ese catalogo el 2026-08-09 (ADR-019) los ids dejaron de existir
- ``expected_status``: expected agent behavior ("ready_for_matching",
  "ready_for_planning", "chitchat", "redirect", "needs_more_info", "plan_ready")
- ``expected_no_tool_calls``: assert the agent does NOT call any tool
- ``expected_invokes_assessment``: assert the agent invokes the assessment subagent
"""

import json
from dataclasses import dataclass
from pathlib import Path

# Import ligero a proposito: `judge` solo trae json/dataclasses al importarse
# (ChatBedrock se importa dentro de `__init__`), asi que esto no arrastra
# langchain ni AWS a quien solo quiera cargar el dataset.
from evals.judge import RUBRIC_WEIGHTS

RUBRIC_DIMS: tuple[str, ...] = tuple(RUBRIC_WEIGHTS)


@dataclass
class EvalTurn:
    """One turn in the conversation."""

    role: str
    content: str


@dataclass
class EvalCase:
    """One evaluation case loaded from the dataset."""

    id: str
    turns: list[EvalTurn]
    expected_riasec: str | None = None
    expected_careers_count: int | None = None
    expected_career: str | None = None
    expected_status: str | None = None
    expected_no_tool_calls: bool = False
    expected_invokes_assessment: bool = False
    scenario: str = ""

    def __post_init__(self) -> None:
        if not self.scenario:
            # Auto-derive scenario from the id prefix (e.g. "assessment_basic_IRC")
            self.scenario = self.id.split("_", 2)[0] if "_" in self.id else self.id

    @property
    def applicable_dims(self) -> set[str]:
        """Dimensiones del rubric que este caso puede ejercer de verdad.

        `riasec_accuracy` pesa 0.4 pero solo tiene sentido donde el caso
        espera un codigo RIASEC (o que el agente delegue en el assessment
        que lo produce). En los 16 casos restantes el juez la puntuaba
        igualmente, midiendo "clasifico bien el turno" bajo el mismo nombre
        -- dos magnitudes distintas en una sola key. Medido el 2026-08-09 en
        spark-match-agent-80dad4e5: sd=0.461 y una distribucion bimodal que
        no separa casos faciles de dificiles sino constructos distintos.

        Las otras tres se dejan siempre aplicables **a proposito**: marcar
        `career_relevance` como no-aplicable en los casos auth_*/budget_*
        los subiria a ~0.95 (tone y safety van casi perfectos) y
        convertiria un test que no comprueba nada en un aprobado. Ver
        `docs/` y el hilo de auth/budget: esos casos se arreglan
        reescribiendolos o moviendolos a un test de nivel API, no pesando
        distinto.
        """
        dims = set(RUBRIC_DIMS)
        if not (self.expected_riasec or self.expected_invokes_assessment):
            dims.discard("riasec_accuracy")
        return dims


DEFAULT_DATASET_PATH = Path(__file__).resolve().parent / "dataset.jsonl"


def load_dataset(path: Path | None = None) -> list[EvalCase]:
    """Load the evaluation dataset from a JSONL file.

    Args:
        path: Path to the JSONL dataset. Defaults to ``evals/dataset.jsonl``.

    Returns:
        List of EvalCase instances.
    """
    dataset_path = path or DEFAULT_DATASET_PATH

    if not dataset_path.exists():
        raise FileNotFoundError(f"dataset not found: {dataset_path}")

    cases: list[EvalCase] = []
    for line_no, raw in enumerate(dataset_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at {dataset_path}:{line_no}: {exc}") from exc

        turns = [EvalTurn(role=t["role"], content=t["content"]) for t in data.get("turns", [])]
        cases.append(
            EvalCase(
                id=data["id"],
                turns=turns,
                expected_riasec=data.get("expected_riasec"),
                expected_careers_count=data.get("expected_careers_count"),
                expected_career=data.get("expected_career"),
                expected_status=data.get("expected_status"),
                expected_no_tool_calls=bool(data.get("expected_no_tool_calls", False)),
                expected_invokes_assessment=bool(data.get("expected_invokes_assessment", False)),
            )
        )

    return cases
