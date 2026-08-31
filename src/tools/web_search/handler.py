"""Web search handler - Tavily (primary) + DuckDuckGo (fallback).

Pure business logic for searching the web. No @tool decorator.
Uses the budget guard from src.budget to refuse calls that exceed the
per-session limit.

Async (Sprint 8, task 8.1): Tavily is queried via ``AsyncTavilyClient``
(native async I/O, no event-loop blocking). DuckDuckGo has no async client
(``duckduckgo_search`` is sync-only), so its call is offloaded to a worker
thread via ``asyncio.to_thread`` instead — either path keeps the running
event loop free to serve other requests/tool calls concurrently (closes B7).

Typed Tavily errors (Sprint 8, task 8.2): ``AsyncTavilyClient.search``
raises a specific exception class per HTTP status
(``tavily.errors._handle_error_response``: 429 -> UsageLimitExceededError,
403/432/433 -> ForbiddenError, 401 -> InvalidAPIKeyError, 400 ->
BadRequestError), and wraps ``httpx.TimeoutException`` into its own
``TimeoutError``. Network-level failures (DNS, connection refused) are
*not* wrapped by tavily and surface as raw ``httpx.TransportError``.
Only ``InvalidAPIKeyError``/``MissingAPIKeyError`` (401 — our own
misconfiguration) skip the DuckDuckGo fallback: retrying via a different
provider can't fix an invalid API key, and silently falling back would
mask a persistent configuration error instead of surfacing it. Every
other Tavily failure (429, timeout, network, or anything unclassified)
keeps the previous fallback-to-DuckDuckGo behavior.

Structured return schema:
    {
        "status": "success" | "budget_exhausted" | "error",
        "data": {"results": [...], "provider": "tavily" | "duckduckgo"} | None,
        "errors": [<error_message>] | None,
    }
"""

import asyncio
import logging
from typing import Any, Literal

import httpx
from duckduckgo_search import DDGS
from tavily import AsyncTavilyClient
from tavily.errors import InvalidAPIKeyError, MissingAPIKeyError
from tavily.errors import TimeoutError as TavilyTimeoutError

from src import budget
from src.config import get_settings

logger = logging.getLogger(__name__)

SearchProvider = Literal["tavily", "duckduckgo"]

# 401-equivalent: Tavily itself refused the request because of *our*
# configuration (missing or invalid API key). Not retryable by falling
# back to a different search provider — surface it immediately instead.
_TAVILY_AUTH_ERRORS: tuple[type[Exception], ...] = (InvalidAPIKeyError, MissingAPIKeyError)


async def _search_tavily(query: str, max_results: int) -> list[dict[str, Any]]:
    """Search using Tavily API (LLM-optimized results)."""
    settings = get_settings()
    if not settings.tavily_api_key:
        raise ValueError("TAVILY_API_KEY not configured")

    async with AsyncTavilyClient(api_key=settings.tavily_api_key.get_secret_value()) as client:
        response = await client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
            include_answer=True,
        )

    results: list[dict[str, Any]] = []
    for item in response.get("results", []):
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
            }
        )

    answer = response.get("answer")
    if answer:
        results.insert(
            0,
            {
                "title": "AI Summary",
                "url": "",
                "content": answer,
            },
        )

    return results


def _search_duckduckgo(query: str, max_results: int) -> list[dict[str, Any]]:
    """Search using DuckDuckGo (free, no API key needed).

    Stays a plain sync function — ``duckduckgo_search`` has no async client.
    Callers must run it via ``asyncio.to_thread`` to avoid blocking the
    event loop (see ``web_search_handler``).
    """
    with DDGS() as ddgs:
        raw_results = ddgs.text(query, max_results=max_results)

    results: list[dict[str, Any]] = []
    for item in raw_results:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("href", ""),
                "content": item.get("body", ""),
            }
        )

    return results


def _refuse_budget_exceeded() -> dict[str, Any]:
    """Helper to build the budget_exhausted response."""
    settings = get_settings()
    return {
        "status": "budget_exhausted",
        "data": None,
        "errors": [
            f"Web search budget exceeded ({settings.max_web_searches_per_session} per session)."
        ],
    }


async def web_search_handler(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the web with budget enforcement.

    Pure business logic - no @tool decorator. Testable without LLM.

    Budget enforcement (Sprint 2 §4.2): if the active session has already
    consumed ``settings.max_web_searches_per_session`` web searches, refuse
    the call with ``status="budget_exhausted"`` instead of burning more
    Tavily quota. A cap of ``0`` disables the budget entirely (unlimited).

    Async (Sprint 8, task 8.1): awaits Tavily directly (native async I/O)
    and offloads DuckDuckGo to a worker thread via ``asyncio.to_thread``,
    so neither provider blocks the event loop while a request is in flight.

    Args:
        query: Search query
        max_results: Max results to return (default: 5)

    Returns:
        Structured dict with status, data, errors.
    """
    settings = get_settings()
    cap = settings.max_web_searches_per_session

    # Budget guard - check before doing any work. cap <= 0 means unlimited.
    current_count = budget.get_web_search_count()
    if cap > 0 and current_count >= cap:
        return _refuse_budget_exceeded()

    if not query or not query.strip():
        return {
            "status": "error",
            "data": None,
            "errors": ["query must be a non-empty string"],
        }

    if not isinstance(max_results, int) or max_results < 1:
        max_results = 5

    # Try Tavily first (better results for LLM consumption). Only count
    # this attempt against the budget if it actually produced results —
    # otherwise we fall through to DuckDuckGo below and must not charge
    # the session twice for one logical web_search call.
    try:
        results = await _search_tavily(query, max_results)
        if results:
            budget.increment_web_search()
            logger.info("Web search completed via Tavily: %d results", len(results))
            return {
                "status": "success",
                "data": {"results": results, "provider": "tavily"},
                "errors": None,
            }
    except _TAVILY_AUTH_ERRORS as e:
        # 401-equivalent: our own API key is missing/invalid. Falling back
        # to DuckDuckGo would silently mask a persistent config problem
        # instead of surfacing it, so we stop here without trying DDG.
        # `exception` y no `error`: esta rama es justo la que hay que
        # diagnosticar desde los logs de ECS, y `error` tira el traceback.
        logger.exception("Tavily rejected the request due to API key configuration: %s", e)
        return {
            "status": "error",
            "data": None,
            "errors": [
                "Tavily API key is missing or invalid (check SPARK_TAVILY_API_KEY); "
                "DuckDuckGo fallback was not attempted since a different search "
                "provider cannot fix a Tavily configuration error."
            ],
        }
    except (TavilyTimeoutError, httpx.TransportError) as e:
        # Transient: request timed out or a network-level failure occurred
        # before Tavily could even respond with a status code. Retryable
        # via DuckDuckGo.
        logger.warning(
            "Tavily request timed out or hit a network error, falling back to DuckDuckGo: %s",
            e,
        )
    except Exception as e:
        # Everything else Tavily can raise (rate limit, bad request,
        # forbidden, or anything unclassified) — keep the previous safe
        # default of falling back to DuckDuckGo rather than failing hard
        # on an error category we haven't explicitly reasoned about.
        logger.warning(
            "Tavily search failed (%s), falling back to DuckDuckGo: %s",
            type(e).__name__,
            e,
        )

    # Fallback to DuckDuckGo. _search_duckduckgo is sync (no async client
    # exists upstream) — run it in a worker thread so it can't block the
    # event loop while other requests/tool calls are in flight.
    try:
        results = await asyncio.to_thread(_search_duckduckgo, query, max_results)
        if not results:
            # Simetrico con el `if results:` de la rama Tavily, y por un
            # motivo que va mas alla de la coherencia: devolver
            # status=success con `results: []` hace que langchain_aws
            # DESCARTE el tool_result al convertir el mensaje, el tool_use
            # se queda huerfano y Bedrock rechaza la peticion entera con
            # "ValidationException: tool_use ids were found without
            # tool_result blocks". Medido el 2026-08-09: mato
            # planning_query_cs en 2 de 3 repeticiones del experiment
            # spark-match-agent-80dad4e5. Un tool_result nunca puede ir
            # vacio -- mejor un error explicito que el agente puede leer.
            #
            # Tampoco se incrementa el presupuesto: una busqueda sin
            # resultados no ha consumido cuota de nadie, y la rama Tavily
            # ya sigue ese criterio.
            logger.info("Web search via DuckDuckGo (fallback) returned no results")
            return {
                "status": "error",
                "data": None,
                "errors": [
                    f"No results found for {query!r} (tried Tavily and DuckDuckGo). "
                    "Try rephrasing the query with different or broader terms."
                ],
            }

        budget.increment_web_search()
        logger.info(
            "Web search completed via DuckDuckGo (fallback): %d results",
            len(results),
        )
        return {
            "status": "success",
            "data": {"results": results, "provider": "duckduckgo"},
            "errors": None,
        }
    except Exception as e:
        # `exception` y no `error`, por lo mismo que en la rama de Tavily de
        # arriba: aqui han fallado LOS DOS proveedores, y sin el traceback el
        # log de ECS solo dice que la busqueda no va, no por donde.
        logger.exception("Both search providers failed: %s", e)
        return {
            "status": "error",
            "data": None,
            "errors": [f"Search unavailable: {e}"],
        }
