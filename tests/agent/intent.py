"""Tests for the heuristic intent classifier (Sprint 8, task 8.4)."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agent.intent import FAST_INTENTS, classify_intent


def _human(text: str) -> HumanMessage:
    return HumanMessage(content=text)


def _ai(text: str) -> AIMessage:
    return AIMessage(content=text)


class TestClassifyIntentGreeting:
    def test_plain_greeting(self):
        assert classify_intent([_human("Hola")]) == "greeting"

    def test_greeting_with_question(self):
        assert classify_intent([_human("Hola, ¿cómo estás?")]) == "greeting"

    def test_buenos_dias(self):
        assert classify_intent([_human("Buenos días")]) == "greeting"

    def test_greeting_prefix_but_long_narrative_is_not_a_greeting(self):
        """A message that happens to start with a greeting word but then
        carries substantive content past the greeting-length cutoff must
        not be short-circuited as a simple greeting."""
        text = (
            "Hola, quería contarte que me gusta mucho resolver problemas "
            "lógicos y programar desde que era niño, y quiero saber qué "
            "carrera se ajusta mejor a ese perfil."
        )
        assert classify_intent([_human(text)]) != "greeting"


class TestClassifyIntentChitchat:
    def test_joke_request(self):
        assert classify_intent([_human("Cuéntame un chiste")]) == "chitchat"

    def test_laughing(self):
        assert classify_intent([_human("jaja que bueno")]) == "chitchat"


class TestClassifyIntentComplex:
    """Rich RIASEC narrative must stay on the strong model regardless of
    length — this is exactly the content the assessment subagent needs
    careful reasoning to score."""

    def test_long_narrative_is_complex(self):
        text = "Resuelvo problemas lógicos mejor que la gente. Quiero ser científico de datos."
        assert classify_intent([_human(text)]) == "complex"

    def test_short_but_substantive_trait_statement_is_complex(self):
        """8 words -- short enough to trip a naive length-only heuristic,
        but 'trabajo como tutor' is exactly the personal-trait narrative
        that must stay on the strong model."""
        text = "Trabajo como tutor y me siento muy realizado."
        assert classify_intent([_human(text)]) == "complex"

    def test_career_plan_request_is_complex(self):
        text = "Quiero ser Científico de la Computación, ¿cómo llego ahí?"
        assert classify_intent([_human(text)]) == "complex"

    def test_no_human_message_is_complex(self):
        """No HumanMessage to classify (e.g. only a SystemMessage present)
        -- default to the strong model rather than guessing."""
        assert classify_intent([SystemMessage(content="system prompt")]) == "complex"

    def test_empty_messages_is_complex(self):
        assert classify_intent([]) == "complex"

    def test_non_string_content_is_complex(self):
        """Multimodal content blocks (list, not str) aren't parsed by this
        heuristic -- default to the strong model rather than crash."""
        message = HumanMessage(content=[{"type": "text", "text": "hola"}])
        assert classify_intent([message]) == "complex"


class TestClassifyIntentClarification:
    def test_short_structured_query_is_clarification(self):
        """Not a personal narrative -- just relays a code and asks for
        matches. Short, mechanical, no reasoning needed."""
        text = "Tengo IAS. ¿Qué carreras me convienen?"
        assert classify_intent([_human(text)]) == "clarification"

    def test_short_uncertain_opener_is_clarification(self):
        text = "No sé qué quiero estudiar, ayúdame a descubrirlo."
        assert classify_intent([_human(text)]) == "clarification"

    def test_short_off_topic_question_is_clarification(self):
        assert classify_intent([_human("¿Cómo invierto en la bolsa?")]) == "clarification"


class TestClassifyIntentAssessmentAnswer:
    """Requires a preceding AIMessage that looks like a scored question --
    only present in real (interleaved) conversations, not the synthetic
    eval dataset (see TestFastIntentCoverageOnEvalDataset below)."""

    def test_short_reply_to_a_scored_question_is_assessment_answer(self):
        messages = [
            _human("Quiero explorar mi perfil vocacional."),
            _ai("En una escala del 1 al 10, ¿qué tanto disfrutas resolver problemas lógicos?"),
            _human("Un 8"),
        ]
        assert classify_intent(messages) == "assessment_answer"

    def test_short_reply_without_a_preceding_question_is_clarification(self):
        """Same short reply, but nothing before it looks like a scored
        question -- falls back to the generic short-turn bucket."""
        messages = [_ai("Hola, ¿en qué te puedo ayudar?"), _human("Un 8")]
        assert classify_intent(messages) == "clarification"


class TestPedirElInformeNuncaVaPorElCarrilRapido:
    """La peticion mas importante del producto cabe en cinco palabras.

    Sin esto se clasificaba "clarification" por corta y se atendia con el
    modelo rapido, que no delega en el subagente de report -- el unico que
    tiene `publish_orientation_report`. Medido en dev el 2026-08-11: el
    modelo escribia el informe a mano en el chat (contra D6 del ADR-019) y
    sus `write_file` volvian troceados por `max_tokens`.
    """

    @pytest.mark.parametrize(
        "frase",
        [
            "Podrias generar mi reporte para poder revisarlo?",
            # La que manda el boton de la web (enmienda a D4 del ADR-019).
            "generame mi reporte de orientacion",
            "quiero mi informe",
            "genera el PDF de mi informe",
            # Empieza por "hola" y son cuatro palabras: sin el orden correcto
            # de las comprobaciones, esta se iria por "greeting".
            "hola, generame mi informe",
        ],
    )
    def test_pedir_el_informe_es_complex(self, frase):
        assert classify_intent([_human(frase)]) == "complex"

    def test_tambien_cuando_responde_a_una_pregunta_del_cuestionario(self):
        """El informe gana al carril de `assessment_answer`: si el estudiante
        cambia de tema para pedirlo, la peticion sigue siendo lo importante."""
        historia = [
            _ai("En una escala del 1 al 10, cuanto disfrutas construir cosas?"),
            _human("mejor dame mi informe"),
        ]
        assert classify_intent(historia) == "complex"


class TestElCarrilRapidoSigueVivo:
    """El arreglo de arriba no puede vaciar el carril rapido: la cobertura de
    Haiku es lo que sostiene el coste por turno (leccion 9 del POC v2)."""

    @pytest.mark.parametrize(
        ("frase", "esperado"),
        [
            ("Hola", "greeting"),
            ("jaja que buena", "chitchat"),
            ("si", "clarification"),
        ],
    )
    def test_lo_corto_y_sin_sustancia_sigue_yendo_rapido(self, frase, esperado):
        assert classify_intent([_human(frase)]) == esperado


class TestFastIntentsConstant:
    def test_all_four_documented_intents_are_present(self):
        assert {"greeting", "chitchat", "assessment_answer", "clarification"} == FAST_INTENTS

    def test_complex_is_not_a_fast_intent(self):
        assert "complex" not in FAST_INTENTS


class TestFastIntentCoverageOnEvalDataset:
    """Sprint 8, task 8.4 DoD: '>=30% de turnos por Haiku en el dataset de
    evals'.

    Measured by replaying evals/dataset.jsonl's real user turns through
    classify_intent, incrementally (each turn classified against only the
    HumanMessages that would have already arrived by that point in a real
    conversation) -- not the final full-history snapshot, which would let
    a later turn's classification see turns that haven't "happened" yet.

    The dataset's turns are all `role: "user"` (no interleaved scripted
    assistant replies), so `assessment_answer` never fires here; coverage
    on this dataset comes entirely from greeting/chitchat/clarification.
    That's expected and consistent with the dataset's own purpose (RIASEC
    extraction stress-testing, not routing-representative traffic) --
    broadening evals/dataset.jsonl itself is Sprint 9 scope (task 9.B.1).
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "El DoD de Sprint 8 (>=30%) NO se cumple desde el 2026-08-09: la "
            "cobertura real es 28.9% (11/38). No es una regresion del router, "
            "que no ha cambiado; es que el dataset dejo de contener los cinco "
            "casos auth_*/budget_*, que se fueron a tests/auth/ y "
            "tests/test_budget.py por ser de capa HTTP. Dos de sus turnos "
            "(uno de ellos un 'Hola' pelado) contaban como rapidos, y sin "
            "ellos el numero cae de 30.2% a 28.9%: el DoD se venia cumpliendo "
            "por 0.2 puntos que ponian casos que no debian estar en el "
            "fichero.\n\n"
            "Se deja el umbral en 30% y se marca xfail en vez de bajarlo a "
            "28%: nadie eligio nunca un 28%, y rebajar el liston para que de "
            "verde convertiria el gate en un control que aparenta medir y no "
            "mide. Los 27 turnos que hoy salen 'complex' se revisaron uno a "
            "uno y ninguno deberia ir al modelo barato -- son señal RIASEC, "
            "los cuatro intentos de inyeccion y el caso de PII, justo donde "
            "no interesa ahorrar.\n\n"
            "Lo que lo resuelve de verdad es la tarea 9.B.1: este dataset es "
            "para estresar la extraccion RIASEC, no es trafico representativo "
            "de enrutado, y medir el router contra el es medir contra la "
            "poblacion equivocada (lo dice el propio docstring de esta "
            "clase). strict=True a proposito: si alguien amplia el dataset y "
            "esto pasa, el test falla y obliga a retirar este marcador."
        ),
    )
    def test_fast_intent_coverage_meets_the_dod_threshold(self):
        from evals.dataset import load_dataset

        cases = load_dataset()

        total_turns = 0
        fast_turns = 0
        for case in cases:
            history: list[HumanMessage] = []
            for turn in case.turns:
                if turn.role != "user":
                    continue
                history.append(_human(turn.content))
                total_turns += 1
                if classify_intent(history) in FAST_INTENTS:
                    fast_turns += 1

        assert total_turns > 0, "eval dataset has no user turns to measure"
        coverage = fast_turns / total_turns
        assert coverage >= 0.30, (
            f"fast-intent coverage on evals/dataset.jsonl is {coverage:.1%} "
            f"({fast_turns}/{total_turns}), below the Sprint 8 DoD's 30% threshold"
        )
