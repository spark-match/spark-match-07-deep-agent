"""Evaluation dataset for the Spark Match Agent.

Loads JSONL conversations from ``evals/dataset.jsonl`` and exposes them
as a list of :class:`EvalCase` for use by :mod:`evals.runner`.

Each row in the dataset has:
- ``id``: unique case identifier
- ``turns``: list of {role, content} messages that drive the conversation
- ``expected_riasec``: expected RIASEC code (for assessment cases)
- ``expected_careers_count``: expected number of career matches
- ``expected_career_id``: expected specific career
- ``expected_status``: expected agent behavior ("ready_for_matching",
  "ready_for_planning", "chitchat", "redirect", "needs_more_info", "plan_ready")
- ``expected_no_tool_calls``: assert the agent does NOT call any tool
- ``expected_invokes_assessment``: assert the agent invokes the assessment subagent
"""

import json
from dataclasses import dataclass
from pathlib import Path


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
    expected_career_id: str | None = None
    expected_status: str | None = None
    expected_no_tool_calls: bool = False
    expected_invokes_assessment: bool = False
    scenario: str = ""

    def __post_init__(self) -> None:
        if not self.scenario:
            # Auto-derive scenario from the id prefix (e.g. "assessment_basic_IRC")
            self.scenario = self.id.split("_", 2)[0] if "_" in self.id else self.id


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
                expected_career_id=data.get("expected_career_id"),
                expected_status=data.get("expected_status"),
                expected_no_tool_calls=bool(data.get("expected_no_tool_calls", False)),
                expected_invokes_assessment=bool(data.get("expected_invokes_assessment", False)),
            )
        )

    return cases
