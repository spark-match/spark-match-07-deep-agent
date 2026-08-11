"""Profile memory manager — extracts StudentProfile from conversations using langmem.

The profile manager analyzes conversation messages and progressively fills
the StudentProfile schema, persisting it directly into the LangGraph
:class:`~langgraph.store.base.BaseStore` (task 6.D). Extraction runs in the
background via :class:`~langmem.ReflectionExecutor` (task 6.E's
``ProfilePersistMiddleware``) so it never adds latency to a turn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.chat_models import init_chat_model
from langmem import ReflectionExecutor, create_memory_store_manager

from src.config import get_settings
from src.models.profile import StudentProfile

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langgraph.store.base import BaseStore

# Namespace template: langmem substitutes ``{user_id}`` from
# ``config["configurable"]["user_id"]`` at call time (see
# ``src.agent.memory_middleware.ProfilePersistMiddleware``).
PROFILE_NAMESPACE = ("spark-match", "{user_id}", "profile")

EXTRACTION_INSTRUCTIONS = """\
You are analyzing a vocational guidance conversation to extract a student's profile.

## What to extract

- **Identity**: name, age, education level, current studies
- **RIASEC scores**: Infer scores (1-10) from what the student says about their preferences.
  - Realistic: likes hands-on work, building things, physical activity, outdoors
  - Investigative: likes analyzing, researching, solving abstract problems, science
  - Artistic: likes creating, designing, expressing, unstructured environments
  - Social: likes helping, teaching, caring for others, teamwork
  - Enterprising: likes leading, persuading, taking risks, managing people
  - Conventional: likes organizing, following procedures, working with data, detail
- **Interests**: specific topics, hobbies, activities they mention enjoying
- **Strengths**: skills or abilities they mention or demonstrate
- **Dislikes**: things they explicitly say they don't enjoy or want to avoid
- **Career direction**: any career they mention being interested in
- **Search constraints**: where they want to study, public vs private,
  university vs institute, and how much they can pay per year

## How to handle search constraints — stricter than the rest

`preferred_region`, `preferred_management`, `preferred_institution_type` and
`max_annual_budget` are held to a higher bar than everything above, and the
reason is worth knowing: the agent turns them into **hard filters**. A wrong
RIASEC score produces an odd recommendation the student can argue with. A wrong
budget produces options that are never shown at all — an error nobody can see.

So, for these four fields only:

- Set them **only from an explicit statement**, never from an inference.
  "Vivo en Puno" is not "quiero estudiar en Puno": leave the region None.
- **Never turn a vague phrase into a number.** "No tengo mucha plata", "algo
  barato" and "depende de la beca" are all None, not 3000. Leave it None and
  let the agent ask for a figure.
- "Me da igual", "cualquiera" or "las dos" mean **no preference**: leave the
  field None. None means "we don't know / doesn't matter", and the agent's
  tools already treat a missing filter as no filter.
- If the student changes their mind, overwrite with the latest.

## How to score RIASEC

- Score based on STRENGTH of signal, not just mention:
  - 1-3: Low interest or actively dislikes this dimension
  - 4-6: Moderate or neutral
  - 7-10: Strong interest or passion in this dimension
- Only set a score when you have enough signal from the conversation
- It's OK to leave scores as None until there's clear evidence
- Update scores as more evidence accumulates across messages

## Rules

- Extract ONLY what the student explicitly says or clearly implies
- Do NOT guess or fill in fields without evidence
- Update incrementally — keep existing data, add new data
- If a student contradicts earlier info, update to the latest
"""


def _modelo_de_extraccion(model: str | BaseChatModel | None) -> str | BaseChatModel:
    """El modelo del extractor, ya con su techo de salida puesto.

    Existe por un fallo que costo encontrar porque no dejaba rastro. langmem
    resuelve un modelo en texto con ``init_chat_model(model)`` a secas
    (``langmem/knowledge/extraction.py``), sin ``max_tokens``, asi que
    ``ChatBedrock`` aplicaba su default de proveedor: **1024 tokens de
    salida**. Con una conversacion de verdad detras -- 53k tokens de entrada
    en el caso que lo destapo -- la llamada a ``PatchDoc`` no cabia y volvia
    con ``stop_reason=max_tokens``, o sea troceada. Una tool call troceada no
    es un perfil a medias: no es nada. Y como la extraccion va en un future
    que nadie mira, no se enteraba ni el log.

    La consecuencia no era perder un campo suelto. ``name``, ``age``,
    ``education_level`` e ``interests`` solo se rellenan por aqui, y sin
    ellos ``profile_completeness`` se queda clavado en 6/12 = 0.50, por
    debajo del 0.60 que pide la puerta de D8. El agente pedia al estudiante
    justo los datos que el sistema era incapaz de guardar, y volvia a
    pedirlos despues. El informe no se podia emitir nunca.

    Se reutiliza ``settings.max_tokens`` en vez de estrenar un ajuste propio:
    es el mismo techo que ``src.agent.factory._resolve_model`` ya le pone a
    los dos modelos del agente, y el problema aqui no era afinar un numero
    sino que no hubiera ninguno. Un ``BaseChatModel`` ya construido (los
    falsos de los tests) pasa intacto, igual que alli.
    """
    if model is not None and not isinstance(model, str):
        return model
    settings = get_settings()
    return init_chat_model(
        model if model is not None else settings.fast_model_string,
        max_tokens=settings.max_tokens,
    )


def build_profile_manager(store: BaseStore, model: str | BaseChatModel | None = None) -> object:
    """Create a langmem store manager configured for StudentProfile extraction.

    Unlike ``create_memory_manager`` (the previous, unused implementation),
    ``create_memory_store_manager`` writes directly into ``store`` under
    :data:`PROFILE_NAMESPACE`, so the extracted profile survives across
    sessions without any extra glue code.

    Args:
        store: The LangGraph store to persist the extracted profile into.
            Comes from :func:`src.persistence.build_persistence` in
            production; any ``BaseStore`` (e.g. ``InMemoryStore``) works in
            tests.
        model: Chat model to use for extraction. Defaults to
            ``settings.fast_model_string`` (Haiku) when omitted. Un texto se
            resuelve en :func:`_modelo_de_extraccion`, que es quien le pone
            el ``max_tokens`` — leer alli por que sin el no se emitia ningun
            informe. Accepting an already-constructed ``BaseChatModel`` lets
            tests inject a fake (e.g. ``GenericFakeChatModel``) instead of
            resolving a real ``ChatBedrock`` through ``init_chat_model`` —
            that resolution is eager (antes dentro de
            ``create_memory_store_manager.__init__``, ahora un paso antes,
            en el mismo momento) and requires an AWS region/credentials even
            if the model is never actually invoked, which would otherwise
            break the no-AWS-required guarantee (hard rule #7) in CI.
    """
    return create_memory_store_manager(
        _modelo_de_extraccion(model),
        schemas=[StudentProfile],
        instructions=EXTRACTION_INSTRUCTIONS,
        namespace=PROFILE_NAMESPACE,
        enable_inserts=False,  # Single profile per user, update in-place.
        store=store,
    )


def build_reflection_executor(
    store: BaseStore, model: str | BaseChatModel | None = None
) -> ReflectionExecutor:
    """Wrap :func:`build_profile_manager` in a background reflection executor.

    ``ProfilePersistMiddleware.after_agent`` calls ``.submit(...)`` on the
    result of this function, debounced by
    ``settings.reflection_delay_seconds``, so extraction never blocks the
    user-facing turn.
    """
    return ReflectionExecutor(build_profile_manager(store, model=model), store=store)


__all__ = [
    "EXTRACTION_INSTRUCTIONS",
    "PROFILE_NAMESPACE",
    "build_profile_manager",
    "build_reflection_executor",
]
