"""Tests de la reparacion de llamadas a herramienta sin resultado.

El fallo real que motiva esto (dev, 2026-08-08): una conversacion quedo
inutilizable de forma permanente porque el checkpoint tenia un ``AIMessage``
con ``tool_calls`` y sin su ``ToolMessage``. Cada turno posterior fallaba
identico, siempre en el mismo indice::

    ValidationException: messages.22: `tool_use` ids were found without
    `tool_result` blocks immediately after: toolu_bdrk_013v2T9o6kDS2QarNA7DVroF
"""

from typing import Any

from langchain.agents.middleware import ModelRequest
from langchain_aws.chat_models.bedrock import _format_anthropic_messages
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from src.agent.tool_call_repair import (
    ORPHAN_TOOL_RESULT,
    ToolCallRepairMiddleware,
    repair_tool_calls,
    unpaired_tool_use_ids,
)


def _call(call_id: str, name: str = "web_search") -> dict[str, Any]:
    return {"name": name, "args": {"query": "beca 18"}, "id": call_id, "type": "tool_call"}


def _orphaned_history() -> list[AnyMessage]:
    """El caso de produccion: la llamada se emitio y el resultado no llego."""
    return [
        HumanMessage(content="cuando abren las inscripciones de beca 18"),
        AIMessage(content="Dejame buscar informacion actualizada.", tool_calls=[_call("tc-1")]),
    ]


def _ids_without_result(messages: list[AnyMessage]) -> set[str]:
    """Lo mismo que valida la API: ids de tool_use sin su tool_result detras."""
    return set(unpaired_tool_use_ids(messages))


class TestRepairToolCalls:
    def test_completes_a_call_that_never_got_a_result(self):
        repaired = repair_tool_calls(_orphaned_history())

        assert isinstance(repaired[-1], ToolMessage)
        assert repaired[-1].tool_call_id == "tc-1"
        assert repaired[-1].status == "error"

    def test_the_repaired_history_is_valid_for_the_api(self):
        # La comprobacion que importa: despues de reparar no queda ni un
        # tool_use sin su tool_result, que es literalmente lo que rechazaba
        # Bedrock.
        assert _ids_without_result(_orphaned_history()) == {"tc-1"}
        assert _ids_without_result(repair_tool_calls(_orphaned_history())) == set()

    def test_says_what_happened_instead_of_faking_a_result(self):
        # Un resultado vacio ("[]", "{}") le diria al modelo que la busqueda
        # se hizo y no encontro nada, y lo contaria como tal al estudiante.
        content = repair_tool_calls(_orphaned_history())[-1].content

        assert content == ORPHAN_TOOL_RESULT
        assert "no llego a ejecutarse" in content

    def test_leaves_a_healthy_history_untouched(self):
        messages: list[AnyMessage] = [
            HumanMessage(content="hola"),
            AIMessage(content="", tool_calls=[_call("tc-1")]),
            ToolMessage(content="[]", tool_call_id="tc-1"),
            AIMessage(content="listo"),
        ]

        assert repair_tool_calls(messages) == messages

    def test_does_not_mutate_the_list_it_receives(self):
        messages = _orphaned_history()

        repair_tool_calls(messages)

        assert len(messages) == 2

    def test_completes_only_the_call_that_is_missing(self):
        # El modelo puede pedir varias herramientas a la vez y quedarse a
        # medias: reponer las dos duplicaria un resultado que si existe.
        messages: list[AnyMessage] = [
            AIMessage(content="", tool_calls=[_call("tc-1"), _call("tc-2", "search_programs")]),
            ToolMessage(content="[]", tool_call_id="tc-1"),
        ]

        repaired = repair_tool_calls(messages)

        assert [type(m).__name__ for m in repaired] == ["AIMessage", "ToolMessage", "ToolMessage"]
        assert repaired[2].tool_call_id == "tc-2"
        assert repaired[1].content == "[]"

    def test_repairs_every_broken_turn_in_a_long_conversation(self):
        messages: list[AnyMessage] = [
            HumanMessage(content="uno"),
            AIMessage(content="", tool_calls=[_call("tc-1")]),
            HumanMessage(content="dos"),
            AIMessage(content="", tool_calls=[_call("tc-2")]),
            HumanMessage(content="tres"),
        ]

        repaired = repair_tool_calls(messages)

        assert [str(m.tool_call_id) for m in repaired if isinstance(m, ToolMessage)] == [
            "tc-1",
            "tc-2",
        ]
        # Y cada uno queda pegado a SU AIMessage, no todos juntos al final.
        assert isinstance(repaired[2], ToolMessage)
        assert isinstance(repaired[5], ToolMessage)

    def test_a_result_that_is_not_contiguous_does_not_count_as_answered(self):
        # Este es el matiz que hace que no baste con buscar el id por todo el
        # historial: el ToolMessage existe, pero colocado donde la API no lo
        # acepta. Sin reponerlo, el historial seguiria siendo invalido.
        messages: list[AnyMessage] = [
            AIMessage(content="", tool_calls=[_call("tc-1")]),
            HumanMessage(content="perdona, otra cosa"),
            ToolMessage(content="[]", tool_call_id="tc-1"),
        ]

        repaired = repair_tool_calls(messages)

        assert isinstance(repaired[1], ToolMessage)
        assert repaired[1].content == ORPHAN_TOOL_RESULT

    def test_survives_a_tool_call_with_no_id(self):
        messages: list[AnyMessage] = [
            AIMessage(content="", tool_calls=[{"name": "web_search", "args": {}, "id": None}])
        ]

        # Sin id no hay tool_result que reponer, y tampoco hay tool_use que
        # la API pueda reclamar: se deja como esta en vez de reventar.
        assert repair_tool_calls(messages) == messages

    def test_an_empty_history_is_not_a_special_case(self):
        assert repair_tool_calls([]) == []


class TestACallThatOnlyExistsInsideContent:
    """El caso real de dev, y el que la primera version de esto no veia.

    ``langchain_aws/chat_models/bedrock.py`` (lineas 611-637) construye el
    payload desde los DOS sitios donde puede vivir una llamada: la lista
    ``tool_calls`` y los bloques ``tool_use`` de ``content``. Un bloque cuyo
    id no esta en ``tool_calls`` se manda tal cual. Un turno cortado a mitad
    deja exactamente eso, y mirar solo ``tool_calls`` daba el historial por
    sano mientras la API lo rechazaba.
    """

    @staticmethod
    def _content_only() -> list[AnyMessage]:
        return [
            HumanMessage(content="cuando abren las inscripciones de beca 18"),
            AIMessage(
                content=[
                    {"type": "text", "text": "Dejame buscar."},
                    {
                        "type": "tool_use",
                        "id": "toolu_bdrk_013v2T9o6kDS2QarNA7DVroF",
                        "name": "web_search",
                        "input": {"query": "beca 18"},
                    },
                ],
                # Vacio a proposito: es lo que hace invisible al bloque.
                tool_calls=[],
            ),
        ]

    def test_the_orphan_is_seen_even_though_tool_calls_is_empty(self):
        assert unpaired_tool_use_ids(self._content_only()) == [
            "toolu_bdrk_013v2T9o6kDS2QarNA7DVroF"
        ]

    def test_and_it_gets_repaired(self):
        repaired = repair_tool_calls(self._content_only())

        assert unpaired_tool_use_ids(repaired) == []
        assert repaired[-1].tool_call_id == "toolu_bdrk_013v2T9o6kDS2QarNA7DVroF"

    def test_a_content_block_that_does_have_its_result_is_left_alone(self):
        messages = [
            *self._content_only(),
            ToolMessage(content="[]", tool_call_id="toolu_bdrk_013v2T9o6kDS2QarNA7DVroF"),
        ]

        assert unpaired_tool_use_ids(messages) == []
        assert repair_tool_calls(messages) == messages

    def test_the_same_id_in_both_places_counts_once(self):
        # `tool_calls` y el bloque de `content` describen LA MISMA llamada:
        # reponer dos resultados para un solo `tool_use` es otro payload
        # invalido, no una reparacion.
        messages: list[AnyMessage] = [
            AIMessage(
                content=[{"type": "tool_use", "id": "tc-1", "name": "web_search", "input": {}}],
                tool_calls=[_call("tc-1")],
            )
        ]

        repaired = repair_tool_calls(messages)

        assert [m for m in repaired if isinstance(m, ToolMessage)].__len__() == 1

    def test_a_result_that_arrives_as_a_content_block_also_counts(self):
        # La contraparte: un `tool_result` puede venir como bloque en vez de
        # como ToolMessage. Tratarlo como "sin responder" insertaria un
        # resultado duplicado.
        messages: list[AnyMessage] = [
            AIMessage(content="", tool_calls=[_call("tc-1")]),
            HumanMessage(content=[{"type": "tool_result", "tool_use_id": "tc-1", "content": "[]"}]),
        ]

        assert unpaired_tool_use_ids(messages) == []

    def test_a_block_with_no_id_is_not_invented(self):
        messages: list[AnyMessage] = [
            AIMessage(content=[{"type": "tool_use", "name": "web_search", "input": {}}])
        ]

        assert unpaired_tool_use_ids(messages) == []


def _anthropic_payload(messages: list[AnyMessage]) -> list[dict[str, Any]]:
    """Los mensajes tal como los va a recibir la API, por el conversor de verdad.

    Se usa `_format_anthropic_messages` de langchain_aws en vez de una copia
    nuestra de sus reglas. Es la unica forma de comprobar lo que importa: dos
    intentos de arreglar esto pasaron sus tests y fallaron en produccion
    justamente porque los tests validaban mi lectura de los mensajes, no la
    peticion que sale hacia Bedrock.
    """
    _system, formatted = _format_anthropic_messages([m.model_copy(deep=True) for m in messages])
    return formatted


def _payload_orphans(payload: list[dict[str, Any]]) -> list[str]:
    """La misma regla que aplica Anthropic, sobre el payload ya convertido."""
    orphans: list[str] = []
    for index, message in enumerate(payload):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        used = [
            block["id"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        if not used:
            continue

        following = payload[index + 1] if index + 1 < len(payload) else None
        answered: set[str] = set()
        if following is not None and isinstance(following.get("content"), list):
            answered = {
                block.get("tool_use_id")
                for block in following["content"]
                if isinstance(block, dict) and block.get("type") == "tool_result"
            }
        orphans.extend(call_id for call_id in used if call_id not in answered)
    return orphans


class TestAgainstTheRealBedrockConverter:
    """Contra la peticion que sale, no contra mi lectura de los mensajes.

    El bloque `tool_call_chunk` es lo que deja un stream cortado a mitad: el
    trozo llego, `tool_calls` nunca se consolido.
    `_convert_from_v1_to_anthropic` lo convierte igualmente en un `tool_use`,
    asi que la API ve una llamada que `tool_calls` no menciona.
    """

    @staticmethod
    def _cut_stream() -> list[AnyMessage]:
        return [
            HumanMessage(content="cuando abren las inscripciones de beca 18"),
            AIMessage(
                content=[
                    {"type": "text", "text": "Dejame buscar."},
                    {
                        "type": "tool_call_chunk",
                        "name": "web_search",
                        "args": '{"query": "beca 18"}',
                        "id": "toolu_bdrk_013v2T9o6kDS2QarNA7DVroF",
                        "index": 0,
                    },
                ],
                tool_calls=[],
                response_metadata={"output_version": "v1", "model_provider": "bedrock"},
            ),
        ]

    def test_a_cut_stream_produces_a_payload_the_api_rejects(self):
        # Sin esto el test de abajo seria vacuo: primero hay que demostrar
        # que el payload esta roto de verdad.
        assert _payload_orphans(_anthropic_payload(self._cut_stream())) == [
            "toolu_bdrk_013v2T9o6kDS2QarNA7DVroF"
        ]

    def test_and_after_repairing_it_the_payload_is_valid(self):
        repaired = repair_tool_calls(self._cut_stream())

        assert _payload_orphans(_anthropic_payload(repaired)) == []

    def test_the_repair_sees_it_where_the_two_previous_versions_did_not(self):
        assert unpaired_tool_use_ids(self._cut_stream()) == ["toolu_bdrk_013v2T9o6kDS2QarNA7DVroF"]

    def test_a_consolidated_tool_call_block_counts_too(self):
        # El otro tipo que el conversor traduce a tool_use.
        messages: list[AnyMessage] = [
            AIMessage(
                content=[{"type": "tool_call", "name": "web_search", "args": {}, "id": "toolu_Y"}],
                tool_calls=[],
                response_metadata={"output_version": "v1", "model_provider": "bedrock"},
            )
        ]

        assert unpaired_tool_use_ids(messages) == ["toolu_Y"]
        assert _payload_orphans(_anthropic_payload(repair_tool_calls(messages))) == []

    def test_a_healthy_conversation_survives_the_round_trip(self):
        messages: list[AnyMessage] = [
            HumanMessage(content="hola"),
            AIMessage(content="", tool_calls=[_call("tc-1")]),
            ToolMessage(content="[]", tool_call_id="tc-1"),
            AIMessage(content="listo"),
        ]

        assert _payload_orphans(_anthropic_payload(messages)) == []
        assert repair_tool_calls(messages) == messages


class TestToolCallRepairMiddleware:
    """El middleware, por el hook que usa produccion (async)."""

    def _request(self, messages: list[AnyMessage]) -> ModelRequest[Any]:
        return ModelRequest(
            model=None,
            messages=messages,
            system_message=None,
            tool_choice=None,
            tools=[],
            response_format=None,
            state={},
            runtime=None,
        )

    async def test_repairs_the_history_before_the_model_sees_it(self):
        seen: list[list[AnyMessage]] = []

        async def handler(request: ModelRequest[Any]) -> str:
            seen.append(list(request.messages))
            return "ok"

        await ToolCallRepairMiddleware().awrap_model_call(
            self._request(_orphaned_history()), handler
        )

        assert isinstance(seen[0][-1], ToolMessage)
        assert _ids_without_result(seen[0]) == set()

    async def test_passes_a_healthy_history_through_unchanged(self):
        healthy: list[AnyMessage] = [HumanMessage(content="hola")]
        seen: list[list[AnyMessage]] = []

        async def handler(request: ModelRequest[Any]) -> str:
            seen.append(list(request.messages))
            return "ok"

        await ToolCallRepairMiddleware().awrap_model_call(self._request(healthy), handler)

        assert seen[0] == healthy

    async def test_logs_the_ids_it_had_to_complete(self, caplog):
        # Con los ids delante, un fallo posterior se cruza contra el que
        # reporta la API en vez de adivinar. La primera version no registraba
        # nada y por eso parecia estar funcionando cuando no lo hacia.
        async def handler(_request: ModelRequest[Any]) -> str:
            return "ok"

        with caplog.at_level("WARNING"):
            await ToolCallRepairMiddleware().awrap_model_call(
                self._request(_orphaned_history()), handler
            )

        assert "tool_call_repair" in caplog.text
        assert "tc-1" in caplog.text

    async def test_warns_about_a_call_shaped_block_it_cannot_read(self, caplog):
        # El modo de fallo de esto es silencioso: si langchain empieza a
        # representar las llamadas de otra forma, "no hay nada que reparar" y
        # "no supe verlo" se leen igual en el log. Costo dos despliegues.
        messages: list[AnyMessage] = [
            AIMessage(
                content=[
                    {
                        "type": "formato_del_futuro",
                        "id": "toolu_Z",
                        "name": "web_search",
                        "args": {},
                    }
                ]
            )
        ]

        async def handler(_request: ModelRequest[Any]) -> str:
            return "ok"

        with caplog.at_level("WARNING"):
            await ToolCallRepairMiddleware().awrap_model_call(self._request(messages), handler)

        assert "formato_del_futuro" in caplog.text

    async def test_does_not_cry_wolf_over_ordinary_blocks(self, caplog):
        messages: list[AnyMessage] = [
            AIMessage(content=[{"type": "text", "text": "hola"}]),
            AIMessage(content="", tool_calls=[_call("tc-1")]),
            ToolMessage(content="[]", tool_call_id="tc-1"),
        ]

        async def handler(_request: ModelRequest[Any]) -> str:
            return "ok"

        with caplog.at_level("WARNING"):
            await ToolCallRepairMiddleware().awrap_model_call(self._request(messages), handler)

        assert caplog.text == ""

    def test_the_sync_hook_repairs_too(self):
        # ag_ui_langgraph solo usa el async, pero una invocacion directa del
        # grafo (tests, scripts) pasa por el sincrono y se romperia igual.
        seen: list[list[AnyMessage]] = []

        def handler(request: ModelRequest[Any]) -> str:
            seen.append(list(request.messages))
            return "ok"

        ToolCallRepairMiddleware().wrap_model_call(self._request(_orphaned_history()), handler)

        assert _ids_without_result(seen[0]) == set()
