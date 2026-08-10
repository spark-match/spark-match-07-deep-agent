"""PII redaction before persistence (Sprint 9, task 9.A.2).

Strips email addresses, Peru DNI numbers, and Peru phone numbers out of
text before it reaches a persistent store. Two integration points cover
the persistence paths this project actually controls in Python code:

1. :func:`redact_messages` — called by
   ``src.agent.memory_middleware.ProfilePersistMiddleware`` before
   submitting the conversation to the background ``StudentProfile``
   extractor (``src/memory/profile_manager.py``). This is the main,
   always-on, whole-conversation persistence path: redacting here means
   the extraction model never sees the raw PII in the first place.
2. :class:`PIIRedactionMiddleware` — wraps the ``manage_memory`` tool
   call (langmem's ``create_manage_memory_tool`` — the actual tool name
   the model calls; confirmed by introspection, *not* "manage_prefs",
   which is only this project's Python variable name for it in
   ``src/agent/factory.py``) and redacts its ``content`` argument before
   the memory is actually written.

Non-goal, stated explicitly rather than silently skipped: ``/memories/
AGENTS.md`` writes via deepagents' own built-in ``write_file``/
``edit_file`` tools (exposed through the ``StoreBackend`` route — see
``src/agent/backends.py``) are not covered here. Intercepting a
third-party library's own filesystem tools by path would need either a
``wrap_tool_call`` keyed on ``/memories/`` argument content (fragile:
the path lives inside a generic ``file_path`` argument shared by every
filesystem tool, not a dedicated argument like ``manage_memory``'s
``content``) or monkey-patching deepagents internals. Out of scope for
this task; the seeded template written there
(:data:`src.prompts.USER_MEMORY_SEED`) never contains PII, and this is
the smaller, less systematic of the two write paths.

Credentials (passwords, API keys, tokens) are a separate, pre-existing
concern already covered by the memory system prompt's explicit
instruction to never store them — this module only targets the 3
categories the roadmap names for this task: email, phone, DNI.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import BaseMessage, ToolCall, ToolMessage
from langgraph.types import Command

# El lookbehind y los cuantificadores posesivos no son cosmetica: sin
# ellos este patron es cuadratico sobre la longitud del texto, y el texto
# aqui son mensajes de chat de usuario, o sea entrada no confiable.
#
# Con "[\\w.+-]+@[\\w-]+\\." una cadena larga de caracteres de palabra
# seguida de "@" y sin TLD valido -- "aaa...aaa@aaa...aaa" -- obliga al
# motor a probar cada posicion de inicio (O(n) de ellas) y, en cada una, a
# retroceder por todo el dominio (O(n) mas). Medido: 44 ms con n=1600,
# 697 ms con n=6400. Doblar n cuadruplica el tiempo.
#
# El lookbehind es lo que arregla la complejidad: impide arrancar en mitad
# de un token, asi que las posiciones de inicio dejan de ser O(n). No
# cambia que se empareja -- si un match empieza en i y el caracter en i-1
# pertenece a la clase, entonces tambien habria match empezando en i-1, y
# el motor, que va de izquierda a derecha, ya habria devuelto ese. Los
# cuantificadores posesivos eliminan ademas el retroceso interno; por si
# solos NO bastan (siguen siendo cuadraticos, medido igual que el
# original), van aqui para que el patron no vuelva a degradarse si alguien
# le quita el lookbehind.
#
# Mismo resultado que el patron anterior en 60 367 casos de prueba
# (fijos, aleatorios y exhaustivos hasta longitud 4). Ver
# tests/agent/pii.py::TestEmailRegexComplexity.
#
# La supresion del final de la linea silencia python:S8786, que marca este
# patron como vulnerable a backtracking. Es un falso positivo: el analizador
# no interpreta los cuantificadores posesivos (`++`), que son precisamente la
# cura de lo que denuncia. Silenciarlo no tapa nada — la complejidad esta
# medida en el test citado arriba, y ese test es lo que se rompe si alguien
# degrada el patron.
#
# Y se dice «la supresion» en vez de nombrar la marca: python:S7632 lee
# cualquier comentario que contenga esa palabra como una supresion, asi que
# escribirla aqui para EXPLICARLA convertia este parrafo en una supresion
# malformada. Tumbo el quality gate de dev el 2026-08-10.
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]++@[\w-]++\.[a-zA-Z]{2,}")  # NOSONAR

# Peru DNI: 8 digits, only redacted when a recognizable identity-document
# context word appears nearby. A bare 8-digit number is too ambiguous
# (years, course codes, other identifiers) to redact unconditionally --
# 0 false positives on legitimate content is the same discipline already
# applied to the injection guardrail in src/agent/guardrails.py.
# "[^\\d.!?]{0,20}" (rather than a rigid "\\s*[:-]?\\s*") deliberately
# allows a connector word between the label and the number -- "mi DNI es
# 12345678" is real, expected phrasing, not just "DNI: 12345678" -- while
# excluding sentence-ending punctuation so the gap can't jump from one
# sentence's "DNI" to an unrelated number in the next.
_DNI_RE = re.compile(
    r"\b(?:dni|documento\s+de\s+identidad|carn[eé]\s+de\s+identidad)\b[^\d.!?]{0,20}(\d{8})\b",
    re.IGNORECASE,
)

# Peru phone numbers: either an explicit context word (teléfono/celular/
# whatsapp/tel/phone/número) followed (allowing a connector word, same
# reasoning as _DNI_RE above) by a digit sequence, or the distinctive
# Peru mobile shape (a leading 9 followed by 8 more digits -- Peru's
# numbering plan reserves the leading 9 for mobiles specifically, so this
# is a low-false-positive-risk shape, unlike matching "any 9-digit
# number" which could collide with e.g. a concatenated date or ID).
_PHONE_CONTEXT_RE = re.compile(
    r"\b(?:tel[eé]fono|cel(?:ular)?|whats?app|phone|tel|n[uú]mero)\b[^\d.!?]{0,20}(\+?\d[\d\s.-]{6,14}\d)",
    re.IGNORECASE,
)
_PERU_MOBILE_RE = re.compile(r"(?:\+?51[\s-]?)?\b9\d{8}\b")


def redact_pii(text: str) -> str:
    """Replace email/DNI/phone occurrences in ``text`` with placeholders.

    Order matters: DNI and phone-context patterns run before the bare
    Peru-mobile shape, so e.g. "mi DNI es 87654321" is redacted as a DNI
    (not left for a less specific pattern to chew on partially).
    """
    redacted = _EMAIL_RE.sub("[EMAIL_REDACTED]", text)
    redacted = _DNI_RE.sub("[DNI_REDACTED]", redacted)
    redacted = _PHONE_CONTEXT_RE.sub("[PHONE_REDACTED]", redacted)
    return _PERU_MOBILE_RE.sub("[PHONE_REDACTED]", redacted)


def _redact_message_content(message: BaseMessage) -> BaseMessage:
    """Return a copy of ``message`` with PII redacted from string content.

    Non-string content (rare: some providers use structured content
    blocks) is left untouched — redaction only ever operates on the
    plain-string case this project's messages actually use.
    """
    if not isinstance(message.content, str):
        return message
    redacted_text = redact_pii(message.content)
    if redacted_text == message.content:
        return message
    return message.model_copy(update={"content": redacted_text})


def redact_messages(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Return a new list with PII redacted from every message's content.

    Called by ``ProfilePersistMiddleware`` before submitting the
    conversation to the background ``StudentProfile`` extractor — the
    extraction model never sees the raw PII in the first place.
    """
    return [_redact_message_content(m) for m in messages]


def _redact_manage_memory_call(request: ToolCallRequest) -> ToolCallRequest:
    """Return ``request`` with its ``content`` arg redacted, if applicable.

    A no-op (returns ``request`` unchanged) unless the call targets the
    ``manage_memory`` tool AND its ``content`` argument is a non-empty
    string that actually contains something to redact — avoids
    needlessly rebuilding the request on every other tool call.
    """
    tool_call = request.tool_call
    if tool_call.get("name") != "manage_memory":
        return request

    args = tool_call.get("args") or {}
    content = args.get("content")
    if not isinstance(content, str) or not content:
        return request

    redacted = redact_pii(content)
    if redacted == content:
        return request

    new_tool_call: ToolCall = {**tool_call, "args": {**args, "content": redacted}}
    return request.override(tool_call=new_tool_call)


class PIIRedactionMiddleware(AgentMiddleware):
    """Redacts PII from the ``manage_memory`` tool's ``content`` argument
    before the memory is actually written to the store.

    Implements both the sync (``wrap_tool_call``) and async
    (``awrap_tool_call``) hooks — same requirement already documented in
    ``src/agent/middleware.py``'s ``AssessmentOnceMiddleware``: LangChain's
    middleware framework raises ``NotImplementedError`` for a tool call in
    whichever mode (sync/async) isn't implemented, and the production API
    (``ag-ui-langgraph``) drives the graph exclusively via
    ``astream_events``.
    """

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return handler(_redact_manage_memory_call(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        return await handler(_redact_manage_memory_call(request))


__all__ = [
    "PIIRedactionMiddleware",
    "redact_messages",
    "redact_pii",
]
