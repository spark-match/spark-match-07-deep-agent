# HANDOFF — Continuar el desarrollo de `spark-match-08-deep-agent`

> **Para**: el siguiente agente (IA) o dev que continúe este repo.
> **Fecha**: 2026-07-28
> **Objetivo**: llevar el Deep Agent a **paridad de features con el POC v2 (AWS Bedrock AgentCore Harness)** manteniéndolo **vendor-neutral**, con foco en **memoria por usuario, por chat y cross-session**.
> **Estado base**: POC cerrada y verde (código muerto eliminado, `pytest 111 passed`, `ruff` limpio, `mypy --strict` limpio, build OK en Python 3.14).
> **Cómo usar este archivo**: leé §1–§3 para el contexto, §4 para el modelo de memoria (lo más importante), §5 para el mapa completo Harness→Deep Agents, §6 para el código concreto, §7 para el roadmap por sprints, §8 para testing.

---

## Índice

1. [Estado actual verificado](#1-estado-actual-verificado)
2. [Objetivo y principios](#2-objetivo-y-principios)
3. [Aviso de nombres (colisiones)](#3-aviso-de-nombres-colisiones)
4. [Modelo de memoria (núcleo)](#4-modelo-de-memoria-núcleo)
5. [Mapa completo de paridad Harness → Deep Agents](#5-mapa-completo-de-paridad-harness--deep-agents)
6. [Implementación concreta (código)](#6-implementación-concreta-código)
7. [Roadmap por sprints](#7-roadmap-por-sprints)
8. [Testing y criterios de aceptación](#8-testing-y-criterios-de-aceptación)
9. [Dependencias a agregar](#9-dependencias-a-agregar)
10. [Referencias](#10-referencias)

---

## 1. Estado actual verificado

### 1.1 Lo que YA funciona (no tocar salvo mejora)
- **Coordinador Deep Agent** (`src/agent/factory.py`) con `create_deep_agent(...)`.
- **3 subagentes** (`assessment`, `matching`, `planning`) con prompts en `src/prompts/*.md`.
- **4 tools** con patrón 3-capas (`src/tools/<t>/handler.py` + `tool.py`): `evaluate_riasec_profile`, `search_careers`, `calculate_affinity`, `web_search`.
- **Catálogo** de 10 carreras como Markdown (`data/careers/*.md`) + loader.
- **Middleware** (`src/agent/middleware.py`): `MaxTurnsMiddleware`, `AssessmentOnceMiddleware`.
- **Budget guards** por sesión (`src/budget.py`, ContextVar).
- **Observabilidad** LangSmith opcional (`src/observability/langsmith.py`).
- **API AG-UI SSE** en `POST /ag-ui` (`src/api/app.py`).
- **Tests**: 111 (pytest) verdes; `ruff` y `mypy --strict` limpios.

### 1.2 Lo que NO está cableado todavía (el trabajo pendiente)
| Feature | Estado | Archivo relevante |
|---|---|---|
| **Checkpointer** (persistencia de estado por chat) | ❌ NO pasado a `create_deep_agent` | `src/agent/factory.py` |
| **Store** (memoria por usuario / cross-session) | ❌ NO pasado a `create_deep_agent` | `src/agent/factory.py` |
| **langmem profile manager** | ⚠️ Existe pero **nunca se invoca** | `src/memory/profile_manager.py` |
| **user_id / actor_id** (identidad) | ❌ No existe (sin auth) | `src/api/app.py`, `settings.py` |
| **thread_id → config del grafo** | ⚠️ Solo se usa para budget, no para el grafo | `src/api/app.py` |
| **Router de intención (Haiku/Sonnet)** | ❌ No portado del POC v2 V8 | — |
| **JWT auth** | ❌ No existe | — |
| **Dockerfile / deploy prod** | ❌ No existe | — |
| **Persistencia durable (SQLite/Postgres)** | ❌ Solo in-memory disponible | — |

### 1.3 Hecho clave: hoy el agente NO tiene memoria de largo plazo
`create_spark_agent()` llama a `create_deep_agent(...)` **sin `checkpointer` ni `store`**, y `app.py` hace `agent.clone()` por request. Resultado: **cada request es efímero**. La única "memoria" es que el frontend AG-UI reenvía el historial de mensajes. No hay memoria por usuario ni cross-session. **Cerrar esto es la prioridad #1.**

---

## 2. Objetivo y principios

- **Paridad con Harness, sin lock-in**: replicar lo que AWS Harness daba "gratis" (memoria gestionada, streaming, idempotencia, auth) con primitivas open-source (LangGraph + langmem + FastAPI).
- **Dev → Prod sin reescribir**: mismos APIs, distinto backend. Dev = in-memory; Prod = Postgres/pgvector. Se cambia por settings, no por código.
- **No romper lo verde**: cada cambio mantiene `pytest/ruff/mypy` en verde.

---

## 3. Aviso de nombres (colisiones)

Dos colisiones importantes de nomenclatura (¡no confundir!):

1. **`deepagents.HarnessProfile` ≠ AWS Bedrock AgentCore Harness.** En `deepagents`, un `HarnessProfile` es un perfil **por modelo/proveedor** (ajusta `system_prompt_suffix`, `tool_description_overrides`, `excluded_tools`, etc.). No tiene relación con el managed service de AWS.
2. **`deepagents.MemoryMiddleware` ≠ memoria conversacional del usuario.** Ese middleware carga archivos **`AGENTS.md`** (contexto de proyecto, spec agents.md) al system prompt. **NO** es memoria por usuario. Para memoria de usuario usá **LangGraph Store + langmem** (§4).

---

## 4. Modelo de memoria (núcleo)

### 4.1 Los dos ejes de identidad

| Eje | Qué identifica | Alcance de memoria | Clave en LangGraph |
|---|---|---|---|
| **`thread_id`** | Un chat / conversación | **Por chat / sesión** (corto plazo) | `config["configurable"]["thread_id"]` |
| **`user_id`** | Un estudiante (a través de todos sus chats) | **Por usuario + cross-session** (largo plazo) | `config["configurable"]["langgraph_user_id"]` |

> Mapeo directo con Harness: `session_id` (Harness) = `thread_id` (LangGraph); `actor_id` (Harness) = `user_id` (LangGraph).

### 4.2 Las dos primitivas de LangGraph (ambas ya instaladas)

| Primitiva | Qué persiste | Habilita | Backend dev → prod |
|---|---|---|---|
| **Checkpointer** (`BaseCheckpointSaver`) | El **estado del grafo** por `thread_id` (mensajes, scratchpad, todos) | Reanudar un chat, memoria **por chat**, human-in-the-loop, time-travel, **idempotencia** | `InMemorySaver` → `PostgresSaver`/`SqliteSaver` |
| **Store** (`BaseStore`) | Documentos JSON en **namespaces** arbitrarios, con búsqueda opcional semántica | Memoria **por usuario** y **cross-session**, búsqueda semántica de recuerdos | `InMemoryStore(index=...)` → `PostgresStore` (pgvector) |

Clave conceptual:
- **Checkpointer = memoria del *hilo*** (se borra/rota por chat).
- **Store = memoria del *usuario*** (namespaced por `user_id`, sobrevive a todos los hilos → cross-session).

### 4.3 Los 3 tipos de memoria que necesitamos → cómo se implementan

| Necesidad | Clave de alcance | Primitiva | Implementación |
|---|---|---|---|
| **Memoria por chat** | `thread_id` | **Checkpointer** | Pasar `checkpointer=` a `create_deep_agent` + `config={"configurable":{"thread_id": ...}}` |
| **Memoria por usuario** | `(user_id, ...)` | **Store** | Pasar `store=` a `create_deep_agent` + namespace `("students", user_id, "profile")` |
| **Cross-session** | `(user_id, ...)` | **Store** (misma que por-usuario) | El Store persiste entre hilos; con backend Postgres es durable entre reinicios |

> "Por usuario" y "cross-session" usan **la misma primitiva** (Store namespaced por `user_id`). La diferencia es el **backend**: in-memory (se pierde al reiniciar) vs Postgres (durable). Cross-session real ⇒ backend durable.

### 4.4 Las 4 estrategias de Memory del Harness → equivalentes en Deep Agents

| Harness Memory strategy | Qué hace | Equivalente Deep Agents / langmem (instalado) | Cómo |
|---|---|---|---|
| **SEMANTIC** | Extrae hechos/relaciones a un vector store; recuperables por similitud | `Store` con índice de embeddings + `langmem.create_memory_store_manager` (o `create_manage_memory_tool`/`create_search_memory_tool`) | `store.search(("students", uid, "facts"), query=...)`; el manager hace extract→upsert en background |
| **SUMMARY** | Resumen rodante de la conversación para no reventar el contexto | `deepagents.SummarizationMiddleware` **o** `langmem.short_term.SummarizationNode` / `summarize_messages` (`RunningSummary`) | Añadir como middleware/pre-model hook |
| **USER_PREFERENCE** | Persiste preferencias/perfil del usuario | `langmem.create_memory_manager(schemas=[StudentProfile])` → escribir al `Store` en `("students", uid, "profile")` | **Ya tenemos `StudentProfile`** — solo falta cablearlo |
| **EPISODIC** | Recuerdos de episodios completos (few-shot de interacciones pasadas) | `langmem.create_thread_extractor` + `langmem.ReflectionExecutor` (consolidación en background) | Extraer episodio al cierre del hilo y guardarlo como few-shot en el Store |

### 4.5 Mapa Actor/Session

| Harness | Deep Agents / LangGraph |
|---|---|
| `actor_id` | `config["configurable"]["langgraph_user_id"]` + namespace del Store `("students", user_id, ...)` |
| `session_id` | `config["configurable"]["thread_id"]` + Checkpointer |
| Namespace regex restrictivo (`[a-zA-Z0-9\-_/]`, sin `_`) | Namespaces = tuplas libres (`("students", uid, "facts")`) — **sin restricción de regex** |
| Idempotencia por `session_id` | Checkpointer por `thread_id` (dedup de estado) |
| Persistencia gestionada por AWS | `PostgresSaver` + `PostgresStore` self-hosted (o RDS) |

---

## 5. Mapa completo de paridad Harness → Deep Agents

| # | Feature AWS Harness | Equivalente Deep Agents (vendor-neutral) | Estado en repo | Esfuerzo |
|---|---|---|---|---|
| 1 | **Memory** (4 strategies, managed) | Checkpointer + Store + langmem (§4) | ❌ no cableado | 🔴 Alto |
| 2 | **Actor/Session** (`actor_id`/`session_id`) | `user_id`/`thread_id` en `config.configurable` | ❌ | 🟡 Medio |
| 3 | **Streaming SSE** (`response.stream`) | AG-UI SSE (`ag-ui-langgraph`) | ✅ hecho | — |
| 4 | **JWT auth** (Cognito nativo) | Dependencia FastAPI (`python-jose`/`PyJWT`) → extrae `user_id` | ❌ | 🟡 Medio |
| 5 | **Gateway MCP tools** | `@tool` in-process (+ `langchain-mcp-adapters` si se quiere MCP) | ✅ in-process | — (opcional MCP) |
| 6 | **Skills declarativas (S3)** | `skills/*/SKILL.md` + `prompts/*.md` (+ `deepagents.SkillsMiddleware` si se quiere on-demand) | ✅ hecho | — |
| 7 | **Intent router (Haiku/Sonnet)** | Middleware pre-model que elige modelo por heurística (portar V8) | ❌ | 🟡 Medio |
| 8 | **Idempotencia** (`session_id`) | Checkpointer (`thread_id`) | ❌ (viene con #1) | 🟢 (incluido) |
| 9 | **Observabilidad** (CloudWatch) | LangSmith (ya) / OpenTelemetry | ✅ hecho | — |
| 10 | **Infra gestionada / scaling** | Dockerfile + ECS/EKS/Modal/Fly | ❌ Dockerfile | 🟡 Medio |
| 11 | **Memoria semántica gestionada** | `InMemoryStore(index=...)` dev → `PostgresStore` (pgvector) prod | ❌ | 🟡 Medio |
| 12 | **Resiliencia/retry gestionado** | `tenacity`/backoff en tools + `langgraph` retries | ⚠️ parcial | 🟢 Bajo |

---

## 6. Implementación concreta (código)

> Todas las firmas fueron verificadas contra las versiones instaladas: `deepagents==0.6.12`, `langmem` (con `create_memory_store_manager`, `create_manage_memory_tool`, `ReflectionExecutor`, `short_term.SummarizationNode`), `langgraph.store.memory.InMemoryStore`, `langgraph.checkpoint.memory.InMemorySaver`. **`create_deep_agent` acepta `checkpointer=` y `store=` directamente.**

### 6.1 Settings — agregar config de memoria/persistencia/auth

```python
# src/config/settings.py  (añadir a Settings)
class MemoryBackend(StrEnum):
    MEMORY = "memory"      # dev: InMemoryStore + InMemorySaver
    SQLITE = "sqlite"      # local durable
    POSTGRES = "postgres"  # prod durable + pgvector

# --- Memory / Persistence ---
memory_backend: MemoryBackend = MemoryBackend.MEMORY
database_url: SecretStr | None = None          # postgres://... o sqlite path
enable_long_term_memory: bool = True           # wire del Store + langmem
embeddings_model: str = "bedrock:amazon.titan-embed-text-v2:0"  # para memoria semántica
memory_query_limit: int = 5

# --- Auth ---
auth_enabled: bool = False                     # dev: off; prod: on
jwt_issuer: str | None = None                  # p.ej. Cognito issuer URL
jwt_audience: str | None = None
jwks_url: str | None = None
```

### 6.2 Módulo de persistencia — construir checkpointer + store por entorno

```python
# src/memory/backends.py  (NUEVO)
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from src.config import get_settings, MemoryBackend


def build_store() -> BaseStore:
    """Store para memoria por-usuario / cross-session (semántica opcional)."""
    settings = get_settings()
    # Índice de embeddings → habilita store.search(query=...) semántico.
    index = None
    if settings.enable_long_term_memory:
        index = {
            "dims": 1024,                       # Titan v2 = 1024 dims
            "embed": settings.embeddings_model, # "bedrock:amazon.titan-embed-text-v2:0"
            "fields": ["text"],                 # qué campo del value se embebe
        }
    if settings.memory_backend == MemoryBackend.MEMORY:
        return InMemoryStore(index=index)
    if settings.memory_backend == MemoryBackend.POSTGRES:
        # requiere dep langgraph-checkpoint-postgres (ver §9)
        from langgraph.store.postgres import PostgresStore
        store = PostgresStore.from_conn_string(settings.database_url.get_secret_value(), index=index)
        store.setup()   # crea tablas/índices (idempotente)
        return store
    raise NotImplementedError(settings.memory_backend)


def build_checkpointer() -> BaseCheckpointSaver:
    """Checkpointer para memoria por-chat (estado del hilo) + idempotencia."""
    settings = get_settings()
    if settings.memory_backend == MemoryBackend.MEMORY:
        return InMemorySaver()
    if settings.memory_backend == MemoryBackend.SQLITE:
        from langgraph.checkpoint.sqlite import SqliteSaver          # dep aparte (§9)
        return SqliteSaver.from_conn_string(settings.database_url.get_secret_value())
    if settings.memory_backend == MemoryBackend.POSTGRES:
        from langgraph.checkpoint.postgres import PostgresSaver      # dep aparte (§9)
        cp = PostgresSaver.from_conn_string(settings.database_url.get_secret_value())
        cp.setup()
        return cp
    raise NotImplementedError(settings.memory_backend)
```

### 6.3 Factory — pasar checkpointer + store

```python
# src/agent/factory.py  (cambios)
from src.memory.backends import build_checkpointer, build_store

def create_spark_agent() -> CompiledStateGraph[Any, Any, Any, Any]:
    settings = get_settings()
    ...
    agent = create_deep_agent(
        model=settings.model_string,
        tools=[evaluate_riasec_profile, search_careers, calculate_affinity, web_search],
        subagents=subagents,
        system_prompt=SYSTEM_PROMPT,
        name=settings.agent_name,
        middleware=[MaxTurnsMiddleware(), AssessmentOnceMiddleware()],
        checkpointer=build_checkpointer(),   # ← memoria por chat
        store=build_store(),                 # ← memoria por usuario / cross-session
    )
    return agent
```

### 6.4 API — extraer `user_id` y pasar `config.configurable`

```python
# src/api/app.py  (dentro de ag_ui_endpoint)
# 1) user_id: de JWT si auth_enabled, si no fallback a header o al thread (dev).
user_id = get_user_id_from_request(request)   # ver §6.6 (auth)

# 2) Pasar thread_id + user_id al grafo vía config.configurable.
#    langmem usa 'langgraph_user_id' para resolver el namespace {langgraph_user_id}.
run_config = {
    "configurable": {
        "thread_id": input_data.thread_id,
        "langgraph_user_id": user_id,
    }
}
set_active_session(input_data.thread_id)
reset_session_budget(input_data.thread_id)

# ag-ui-langgraph: pasar config al run del agente (ver API de LangGraphAgent.run;
# si no acepta config directo, envolver el grafo o setear via contextvar).
async def event_generator():
    async for event in request_agent.run(input_data, config=run_config):
        yield encoder.encode(event)
```

> ⚠️ Verificar cómo `ag_ui_langgraph.LangGraphAgent.run()` propaga `config`. Si no lo expone, dos opciones: (a) compilar el grafo con `checkpointer/store` (ya lo hace) y setear `thread_id`/`user_id` vía un ContextVar que un pre-model hook lea; (b) usar el grafo LangGraph directo en el endpoint en lugar del wrapper AG-UI para runs con config.

### 6.5 Cablear langmem (USER_PREFERENCE + SEMANTIC) — background manager

```python
# src/memory/profile_manager.py  (evolución)
from langmem import create_memory_store_manager
from src.models.profile import StudentProfile

def create_profile_store_manager():
    """Background manager: extrae StudentProfile + hechos y los upserta al Store.

    Namespace templado por usuario: ("students", "{langgraph_user_id}", "profile").
    langmem resuelve {langgraph_user_id} desde config.configurable en cada run.
    """
    settings = get_settings()
    return create_memory_store_manager(
        settings.model_string,
        schemas=[StudentProfile],
        namespace=("students", "{langgraph_user_id}", "profile"),
        instructions=EXTRACTION_INSTRUCTIONS,   # ya existe
        enable_inserts=True,
        query_limit=settings.memory_query_limit,
        # store=... se resuelve del grafo si se ejecuta dentro del run
    )
```

Dos formas de disparar la formación de memoria:
- **Background (recomendado)**: ejecutar el manager en un `after_model`/`ReflectionExecutor` para no añadir latencia al hot-path.
- **Hot-path (opcional)**: dar al agente `create_manage_memory_tool(namespace=("students","{langgraph_user_id}","facts"))` y `create_search_memory_tool(...)` como tools, para que gestione memoria explícitamente.

### 6.6 Recuperar memoria al inicio del turno (inyección de contexto)

```python
# src/agent/memory_middleware.py  (NUEVO — pre-model hook)
from langchain.agents.middleware import AgentMiddleware, AgentState

class RecallProfileMiddleware(AgentMiddleware):
    """Antes de llamar al modelo, recupera el perfil/recuerdos del usuario
    desde el Store y los inyecta como contexto de sistema."""
    def before_model(self, state: AgentState, runtime) -> dict | None:
        store = runtime.store                      # el Store del grafo
        user_id = runtime.config["configurable"].get("langgraph_user_id")
        if not (store and user_id):
            return None
        items = store.search(("students", user_id, "profile"), query="perfil RIASEC", limit=3)
        if not items:
            return None
        context = "\n".join(i.value.get("text", "") for i in items)
        return {"messages": [SystemMessage(content=f"[Memoria del estudiante]\n{context}")]}
```

### 6.7 SUMMARY strategy (evitar overflow de contexto)

```python
# Opción A: middleware de deepagents
from deepagents.middleware import SummarizationMiddleware
# ... añadir a middleware=[...]

# Opción B: langmem short-term
from langmem.short_term import SummarizationNode  # o summarize_messages / RunningSummary
```

### 6.8 EPISODIC strategy (few-shot de episodios pasados)

```python
from langmem import create_thread_extractor, ReflectionExecutor
# create_thread_extractor(model, schema=EpisodeSchema): resume el hilo al cerrarse.
# ReflectionExecutor: consolida episodios en background y los guarda en el Store
#   namespace ("students", user_id, "episodes") para recuperarlos como few-shot.
```

---

## 7. Roadmap por sprints

> Cada sprint termina con `pytest/ruff/mypy` en verde y un criterio de aceptación medible.

### Sprint 5 — Fundación de memoria (🔴 prioridad) — ~1.5 días
- [ ] `src/memory/backends.py` (`build_store`, `build_checkpointer`) con backend `memory` (dev).
- [ ] Pasar `checkpointer` + `store` en `factory.py`.
- [ ] Plumbing `thread_id` + `user_id` en `app.py` (dev: `user_id` = header `X-User-Id` o el `thread_id` si no hay auth).
- [ ] Settings de memoria (§6.1).
- **Aceptación**: en el mismo `thread_id`, el agente recuerda lo dicho antes sin que el frontend reenvíe todo; test que lo pruebe con `InMemorySaver`.

### Sprint 6 — Memoria por usuario + cross-session + semántica — ~2 días
- [ ] Cablear `create_memory_store_manager` (USER_PREFERENCE + SEMANTIC) en background.
- [ ] `RecallProfileMiddleware` (before_model) que inyecta perfil del Store.
- [ ] `InMemoryStore(index=...)` con embeddings Titan v2 → `store.search(query=...)`.
- **Aceptación**: dos `thread_id` distintos del **mismo `user_id`** comparten perfil (el 2º chat ya "conoce" al estudiante). Test con dos hilos + un `user_id`.

### Sprint 7 — Auth (JWT) + idempotencia — ~1 día
- [ ] Dependencia FastAPI que valida JWT (Cognito/Auth0) y extrae `sub` → `user_id`.
- [ ] `auth_enabled` en settings; dev sigue funcionando sin auth.
- [ ] Documentar que la idempotencia ya la da el checkpointer (`thread_id`).
- **Aceptación**: request sin token válido → 401 (cuando `auth_enabled=true`); con token → `user_id` = `sub`.

### Sprint 8 — Prod: Postgres/pgvector + router + Dockerfile — ~2–3 días
- [ ] Backend `postgres`: `PostgresStore` + `PostgresSaver` (deps §9), `.setup()` en startup.
- [ ] Portar **router V8** (Haiku para first-turn/self-statement/short-msg; Sonnet resto) como middleware pre-model.
- [ ] `Dockerfile` + `.dockerignore` (ya existe) para ECS/EKS/Modal.
- **Aceptación**: memoria sobrevive a reinicio del proceso (durabilidad); router baja latencia/coste sin degradar calidad (medir con evals `--mode live`).

### Sprint 9 (opcional) — EPISODIC + reflexión — ~1.5 días
- [ ] `create_thread_extractor` + `ReflectionExecutor` para episodios como few-shot.
- **Aceptación**: el agente cita/usa un episodio pasado del mismo usuario.

---

## 8. Testing y criterios de aceptación

### 8.1 Patrón de test de memoria (sin AWS, con InMemory)
```python
# tests/memory/store_persistence.py
def test_profile_persists_across_threads_same_user():
    store = InMemoryStore()
    uid = "student-123"
    store.put(("students", uid, "profile"), "riasec", {"text": "RIASEC=ICR, le gusta análisis"})
    # Nuevo "chat" (otro thread_id) del mismo user:
    hits = store.search(("students", uid, "profile"), query="RIASEC")
    assert hits and "ICR" in hits[0].value["text"]

def test_thread_memory_isolated_by_thread():
    # con checkpointer InMemorySaver, dos thread_id no comparten estado de hilo
    ...
```

### 8.2 Comandos
```bash
uv sync
uv run pytest -q
uv run ruff check src/ tests/
uv run mypy src/
uv run python -m src            # levanta API en :8000
# eval de calidad (para comparar con Harness 4.42):
uv run python -m evals.runner --mode live
```

### 8.3 Definición de "paridad con Harness" (lo que hay que demostrar)
- Memoria por chat ✅ (checkpointer), por usuario ✅ (store), cross-session ✅ (store durable).
- Streaming ✅ (ya). Auth ✅ (Sprint 7). Router ✅ (Sprint 8). Observabilidad ✅ (LangSmith).
- Métricas de calidad medidas con evals `--mode live` (objetivo: ≥ helpfulness 4.4 del POC v2 V8).

---

## 9. Dependencias a agregar

> Verificar nombres/versión exactos al instalar (algunas viven en paquetes separados de `langgraph`).

```toml
# pyproject.toml [project].dependencies  (para prod / durabilidad)
"langgraph-checkpoint-sqlite>=2.0.0",     # SqliteSaver (local durable)
"langgraph-checkpoint-postgres>=2.0.0",   # PostgresSaver + langgraph.store.postgres (PostgresStore)
"psycopg[binary,pool]>=3.2.0",            # driver Postgres para los backends anteriores
"python-jose[cryptography]>=3.3.0",       # validación JWT (o PyJWT)
```
- Dev (`memory` backend) **no requiere** nada nuevo: `InMemoryStore` e `InMemorySaver` ya están.
- Embeddings semánticos usan Bedrock Titan v2 (`amazon.titan-embed-text-v2:0`, 1024 dims) vía `langchain-aws` (ya instalado).

---

## 10. Referencias

### 10.1 Repo (este proyecto)
- `src/agent/factory.py` — donde se pasan `checkpointer`/`store`.
- `src/api/app.py` — donde se inyecta `thread_id`/`user_id` en `config.configurable`.
- `src/memory/profile_manager.py` — langmem (evolucionar a `create_memory_store_manager`).
- `src/models/profile.py` — `StudentProfile` (schema de USER_PREFERENCE).
- `IMPROVEMENTS.md` — roadmap histórico (Sprints 1–4).

### 10.2 Evidencia de lo que hay que igualar (Harness POC v2)
- `D:\Continental\orion\AWS-HARNESS-POC-V10.md` — API + JWT + memoria del Harness.
- `D:\Continental\orion\AWS-HARNESS-POC-V8.md` — router V8 (a portar en Sprint 8).
- `D:\Continental\orion\spark-match-poc-v2\docs\poc-v2-decision.md` — 4 memory strategies del Harness.
- `D:\Continental\orion\AWS-ARCHITECTURE-EVALUATION-OPENCODE-2026-07-28.md` — evaluación + por qué Deep Agents.

### 10.3 APIs externas (versiones instaladas)
- `deepagents==0.6.12`: `create_deep_agent(..., checkpointer=, store=, skills=, memory=)`; middleware `SummarizationMiddleware`, `SkillsMiddleware`, `SubAgentMiddleware`. (`HarnessProfile` = perfil de modelo, no AWS.)
- `langmem`: `create_memory_manager`, `create_memory_store_manager` (namespace `('memories','{langgraph_user_id}')` + `store`), `create_manage_memory_tool`, `create_search_memory_tool`, `create_thread_extractor`, `ReflectionExecutor`, `short_term.SummarizationNode`.
- `langgraph`: `store.memory.InMemoryStore(index=...)`, `checkpoint.memory.InMemorySaver`; prod: `store.postgres.PostgresStore`, `checkpoint.postgres.PostgresSaver`, `checkpoint.sqlite.SqliteSaver` (paquetes aparte).
- `BaseStore.put(ns, key, value, index=[...])` y `BaseStore.search(ns, query=..., filter=..., limit=...)` (búsqueda semántica).

### 10.4 Docs oficiales (buscar por estas claves)
- LangGraph Persistence (Checkpointer), LangGraph Store & Memory, langmem (long-term memory), deepagents (middleware, store/checkpointer), AG-UI protocol.

---

*Fin del handoff. Mantener `pytest/ruff/mypy` en verde en cada PR. Prioridad: Sprint 5 (fundación de memoria).*
