"""LLM-as-judge for evaluating Spark Match Agent responses.

Inspired by Paul Iusztin's workshop pattern (``src/writing/evals/metric.py``):
a binary pass/fail judge using Claude (Bedrock) with few-shot examples.

Usage:
    >>> from evals.judge import SparkMatchJudge
    >>> judge = SparkMatchJudge()
    >>> score = judge.score(
    ...     output="Tu perfil es IRC...",
    ...     expected="IRC",
    ...     context="assessment conversation about programming"
    ... )
    >>> print(score.value, score.reason)
    1.0 "PASS: profile code matches expected"

Opik integration (optional):
    If ``opik`` is installed, the judge can be wrapped in a ``BaseMetric``
    for use with ``opik.evaluate(dataset, metric)``.
"""

import json
from dataclasses import dataclass


@dataclass
class JudgeScore:
    """Binary pass/fail score with a textual reason."""

    value: float  # 1.0 for pass, 0.0 for fail
    reason: str


JUDGE_PROMPT = """You are an expert evaluator for the Spark Match agent, a vocational \
counseling assistant for students. Your job is to evaluate whether the agent's \
output meets the expected behavior for a given scenario.

**SCENARIO** - the type of interaction being evaluated:
{scenario}

**EXPECTED** - what the agent should do / produce:
{expected}

**OUTPUT** - the agent's actual response (truncated):
{output}

**LABELING GUIDELINES:**
- Score 1.0 (pass) if the output reasonably meets the expectation.
- Score 0.0 (fail) if the output:
  - Hallucinates a wrong RIASEC code (3 letters, not matching expected)
  - Invokes the wrong tools for the scenario
  - Misses the core intent of the user message
  - Produces a clearly incorrect or broken response
- For chitchat/redirect scenarios: pass if the agent does NOT call career tools.
- For assessment scenarios: pass if the extracted RIASEC code matches expected.

**OUTPUT FORMAT (strict JSON):**
{{"score": 1.0 or 0.0, "reason": "PASS: <reason>" or "FAIL: <reason>"}}

Respond with ONLY the JSON object, no prose.
"""


class SparkMatchJudge:
    """Binary LLM-as-judge using Claude (Bedrock)."""

    def __init__(self, model_id: str | None = None):
        # Lazy import so tests that mock the LLM don't need bedrock.
        from langchain_aws import ChatBedrock

        from src.config import get_settings

        settings = get_settings()
        # ChatBedrock needs the raw model ID (no provider prefix).
        self._model_id = model_id or settings.model_id
        self._model = ChatBedrock(
            model_id=self._model_id,
            region_name=settings.aws_region,
        )

    def score(
        self,
        output: str,
        expected: str,
        scenario: str = "agent response evaluation",
        context: str = "",
    ) -> JudgeScore:
        """Score one agent output against the expected behavior.

        Args:
            output: The agent's response text (truncated to first 2000 chars).
            expected: Expected behavior (e.g., RIASEC code, status, career_id).
            scenario: Description of the scenario type.
            context: Optional extra context (conversation history, etc.)

        Returns:
            JudgeScore with value (1.0/0.0) and reason.
        """
        prompt = JUDGE_PROMPT.format(
            scenario=scenario,
            expected=expected,
            output=output[:2000],
        )

        response = self._model.invoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)

        return self._parse_response(text)

    @staticmethod
    def _parse_response(text: str) -> JudgeScore:
        """Parse the judge's JSON response into a JudgeScore."""
        # Strip markdown fences if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return JudgeScore(
                value=0.0,
                reason=f"FAIL: judge did not return valid JSON: {text[:200]!r}",
            )

        value = float(data.get("score", 0.0))
        reason = str(data.get("reason", "no reason provided"))

        return JudgeScore(value=value, reason=reason)
