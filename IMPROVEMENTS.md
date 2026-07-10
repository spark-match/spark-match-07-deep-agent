# Mejoras para `08-deep-agent` — Análisis comparativo

> **Fecha**: 2026-07-10  
> **Repositorio analizado**: `D:\UNI\Spark\08-deep-agent` (Spark Match — Deep Agent de orientación vocacional)  
> **Repositorio de referencia**: [iusztinpaul/designing-real-world-ai-agents-workshop](https://github.com/iusztinpaul/designing-real-world-ai-agents-workshop)  
> **Alcance del análisis**: estructura de directorios, patrones de arquitectura, tooling de desarrollo, observabilidad, testing y deployment.  

---

## 1. Resumen ejecutivo

`08-deep-agent` ya tiene una **base sólida** (arquitectura `agent / api / config / memory / models / prompts / tools / subagents`, langmem para perfiles, AG-UI para streaming, Tavily + DuckDuckGo para búsqueda). El repositorio del workshop de Paul Iusztin, aunque apunta a un dominio distinto (LinkedIn Writer + Deep Research), demuestra **varios patrones de producción** que faltan o pueden reforzarse aquí.

He clasificado las mejoras en tres categorías:

| Prioridad | Categoría | # Mejoras |
|---|---|---|
| 🔴 Alta | Funcionalidad core / bugs latentes | 4 |
| 🟡 Media | Patrones de producción / DX | 7 |
| 🟢 Baja | Estilo / consistencia / nice-to-have | 5 |

**Total: 16 mejoras**.

---

## 2. Estado actual del repo (referencia rápida)

```
08-deep-agent/
├── .env.example              38 líneas
├── .gitignore                140 líneas
├── .python-version           Python 3.14
├── pyproject.toml            66 líneas (deps + ruff + pytest + mypy)
├── uv.lock                   255 KB (trackeado ✓)
├── README.md                 313 líneas
├── DEPLOYMENT.md             587 líneas
├── RESUMEN_PROYECTO.md       10 KB
├── src/
│   ├── __main__.py           10 líneas
│   ├── agent/
│   │   ├── factory.py        62 líneas
│   │   └── subagents/
│   │       ├── assessment.py 86 líneas
│   │       ├── matching.py   62 líneas
│   │       └── planning.py   83 líneas
│   ├── api/
│   │   ├── app.py            85 líneas
│   │   └── server.py         24 líneas
│   ├── config/
│   │   └── settings.py       70 líneas
│   ├── memory/
│   │   └── profile_manager.py 73 líneas
│   ├── models/
│   │   └── profile.py        121 líneas
│   ├── prompts/
│   │   └── system.py         48 líneas
│   └── tools/
│       ├── assessment.py     65 líneas
│       ├── catalog.py        141 líneas (10 carreras hard-coded)
│       ├── matching.py       61 líneas
│       └── web_search.py     102 líneas
├── skills/
│   └── vocational_advisor/
│       └── SKILL.md          1.7 KB
└── tests/
    ├── test_models.py        61 líneas (4 tests)
    └── test_tools.py         77 líneas (8 tests)
```

**Total Python**: ~2,000 LOC — bien dimensionado para un MVP.

---

## 3. Mejoras 🔴 Alta prioridad

### 3.1 ⚠️ El `.gitignore` ignora `uv.lock` y `.python-version` — **conflicto con uv**

**Hallazgo** (líneas 39 y 45 del `.gitignore`):

```gitignore
# -----------------------------------------------------------------------------
# Virtual environments
# -----------------------------------------------------------------------------
.venv/
venv/
env/
ENV/
.python-version         # ❌ .python-version está trackeado en este repo

# -----------------------------------------------------------------------------
# uv (package manager)
# -----------------------------------------------------------------------------
.uv/
uv.lock                 # ❌ uv.lock está trackeado en este repo
```

Sin embargo, `git ls-files` muestra que **ambos archivos SÍ están versionados**:

```
.env.example
.gitignore
.python-version    ← rastreado
pyproject.toml
uv.lock            ← rastreado (255 KB)
```

**Esto es un antipatrón crítico**: el `.gitignore` contradice el estado real del repo y va contra la recomendación oficial de `uv`. Como referencia, el workshop deja `.python-version` y `uv.lock` comentados en `.gitignore` (líneas 152-156 y 166-168):

```gitignore
# UV
#   Similar to Pipfile.lock, it is generally recommended to include uv.lock in version control.
#   This is especially recommended for binary packages to ensure reproducibility...
#uv.lock
```

**Acción recomendada**:

1. Eliminar las líneas 39 y 45 del `.gitignore` (mantener la consistencia con el estado real del repo y seguir las recomendaciones de uv).
2. Verificar que `.venv/` siga ignorado (línea 35 ✓).
3. Añadir nota en `.gitignore` explicando que `uv.lock` y `.python-version` se versionan a propósito (reproducibilidad + fijar versión de Python).

---

### 3.2 ❌ Falta configuración de logging centralizada

**Hallazgo**: El repo importa `logging` en algunas herramientas (`src/tools/web_search.py:15`) pero nunca configura el nivel, formato ni handler. El workshop tiene `src/research/utils/logging.py`:

```python
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=LOG_FORMAT)
```

Llamado desde el `server.py` (línea 39 del workshop):

```python
setup_logging(level=get_settings().log_level)
```

**Acción recomendada**:

1. Crear `src/utils/logging.py` con `setup_logging(level=...)` que configure formato y nivel.
2. Agregar `log_level: int = logging.INFO` a `Settings` (al igual que el workshop).
3. Llamar `setup_logging()` desde `src/api/app.py` en el `lifespan` antes de crear el agente.
4. Reemplazar el comentario del CLAUDE.md del workshop ("never prints!") por una política equivalente en nuestro README.

---

### 3.3 ❌ Falta un `Makefile` — el `README.md` lista comandos pero no son reutilizables

**Hallazgo**: El `README.md` lista 5 comandos (pytest, ruff check, ruff format, mypy, uv sync) sin estandarización. El workshop tiene un `Makefile` con 12+ targets como centro de comandos:

```makefile
format-fix:
    uv run ruff format $(QA_FOLDERS)

lint-fix:
    uv run ruff check --fix $(QA_FOLDERS)

test-research-workflow:
    uv run python scripts/test_research_workflow.py --working-dir test_logic --iterations 2
```

Con un patrón clave documentado en `CLAUDE.md` del workshop:

> *"Fallback when `make` is not installed ... every target in the Makefile is a thin wrapper around a one-line `uv run ...` invocation. When `make <target>` fails with `command not found: make`, open the `Makefile`, find the target's recipe, and run the underlying command directly."*

**Acción recomendada**:

Crear `Makefile` en la raíz con targets:

```makefile
# === QA ===
format-fix:           ; uv run ruff format src/ tests/
lint-fix:             ; uv run ruff check --fix src/ tests/
format-check:         ; uv run ruff format --check src/ tests/
lint-check:           ; uv run ruff check src/ tests/
typecheck:            ; uv run mypy src/

# === Tests ===
test:                 ; uv run pytest
test-cov:             ; uv run pytest --cov=src --cov-report=term-missing

# === Server ===
run-local:            ; uv run python -m src
run-agentcore:        ; SPARK_ENVIRONMENT=agentcore uv run python -m src

# === Eval (futuro) ===
eval-dev:             ; uv run python scripts/run_evaluation.py --split dev
eval-test:            ; uv run python scripts/run_evaluation.py --split test
```

---

### 3.4 ❌ El catálogo de carreras está hard-coded en `src/tools/catalog.py` — bloqueará `add_career` con PR por cada nueva carrera

**Hallazgo** (líneas 11-102 de `catalog.py`):

```python
CAREER_CATALOG: list[dict] = [
    {"id": "cs", "name": "Ciencias de la Computación", ...},
    {"id": "medicine", "name": "Medicina", ...},
    # ... 10 carreras hard-coded
]
```

Esto significa que **cada vez que el equipo de producto quiera agregar una carrera, hay que abrir un PR al código Python**, hacer code review, deploy a producción, etc. Es un anti-patrón para datos que cambian frecuentemente.

**El workshop maneja esto con archivos `.md` versionados**:

```
src/writing/profiles/
├── branding_profile.md    1.5 KB
├── character_profile.md   2.5 KB
├── structure_profile.md   1.7 KB
└── terminology_profile.md 1.9 KB
```

Y los carga con un `profile_loader` (en `src/writing/app/profile_loader.py`).

**Acción recomendada** (3 opciones, ordenadas por esfuerzo vs. valor):

| Opción | Esfuerzo | Valor | Notas |
|---|---|---|---|
| **A. YAML en repo** | Bajo | Medio | Cada carrera es un archivo `data/careers/{id}.yaml` con frontmatter. Se cargan al iniciar. PR por carrera, pero aislado al directorio `data/`. |
| **B. Markdown + frontmatter** | Bajo | Alto | Como el workshop. Cada carrera es `data/careers/{id}.md` con frontmatter. Fácil de editar por no-desarrolladores. |
| **C. JSON Line en S3** | Alto | Alto | Datos dinámicos. Requiere backend changes (cache + refresh). |

**Recomendación para el MVP**: **Opción B** (markdown + frontmatter), porque:
- Es trivial de implementar (1 archivo + 1 loader).
- Permite editar carreras sin entender Python.
- El campo `description` puede ser markdown largo (cursos, certificaciones, testimonios).
- Es consistente con la arquitectura DDD (`data/` separado de `src/`).

Ejemplo de archivo:

```markdown
---
id: data_science
name: Ciencia de Datos
riasec_profile: ICR
field: Tecnología / Ciencia
outlook: Altísima demanda, uno de los campos de mayor crecimiento.
---

## Descripción

Extracción de insights desde datos masivos, ML, estadística.

## Habilidades clave

- Estadística
- Programación (Python, R)
- Visualización de datos
- Pensamiento crítico

## Recursos para empezar

- [Kaggle Micro-courses](https://www.kaggle.com/learn) (gratis)
- [StatQuest con Josh Starmer](https://www.youtube.com/c/joshstarmer) (YouTube)
- [Python for Data Analysis — Wes McKinney](https://wesmckinney.com/book/)
```

---

## 4. Mejoras 🟡 Media prioridad

### 4.1 ⚠️ Falta observabilidad con **Opik** (o LangSmith)

**Hallazgo**: El README menciona:

> *"Trabajos futuros: Observabilidad con LangSmith — integrar LangSmith para tracing, monitoring y debugging de los subagentes y tools"*

Pero **LangSmith ya está declarado en `Settings`** (`langsmith_api_key`, `langsmith_project`, `langsmith_tracing`) y **no se usa en ningún sitio**. El `factory.py` nunca llama a `LangChainTracer()` ni configura callbacks.

El workshop usa **Opik** (alternativa open-source) de forma nativa y centralizada:

```python
# src/research/utils/opik_utils.py
def configure_opik() -> bool:
    if not is_opik_enabled():
        return False
    opik.configure(api_key=..., workspace=..., use_local=False, ...)
    return True

def track_genai_client(client): ...
```

Cada tool del workshop tiene `@opik.track(type="tool")` (líneas 22, 43, 67 de `routers/tools.py`):

```python
@mcp.tool()
@opik.track(type="tool")
async def deep_research(working_dir: str, query: str) -> dict[str, Any]:
    opik_context.update_thread_id()
    return await deep_research_tool(working_dir, query)
```

**Acción recomendada**:

**Opción A — Usar Opik** (consistente con el workshop, free tier generoso):

```python
# src/observability/opik_utils.py
import os
import opik
from src.config import get_settings

def configure_opik() -> bool:
    settings = get_settings()
    if not settings.opik_api_key:
        return False
    os.environ["OPIK_PROJECT_NAME"] = settings.opik_project_name
    opik.configure(
        api_key=settings.opik_api_key.get_secret_value(),
        workspace=settings.opik_workspace,
        use_local=False,
    )
    return True
```

Decorar cada tool con `@opik.track(type="tool")`.

**Opción B — Usar LangSmith** (más simple si ya está en `.env`):

```python
# src/observability/tracing.py
from langchain_core.tracers import LangChainTracer

def configure_tracing() -> LangChainTracer | None:
    settings = get_settings()
    if not settings.langsmith_tracing:
        return None
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_TRACING"] = "true"
    return LangChainTracer(project_name=settings.langsmith_project)
```

Y pasar el tracer al invocar el agente:

```python
result = agent.invoke(input, config={"callbacks": [tracer]})
```

---

### 4.2 ⚠️ Las herramientas no tienen guardas de presupuesto (budget)

**Hallazgo**: El workshop tiene un patrón explícito de **exploration budget** para evitar que el agente se descontrole (`src/research/app/exploration_budget.py`):

```python
class BudgetExceededError(Exception):
    """Raised when the exploration call budget would be exceeded."""

def record_exploration_call(memory_path, tool, query, *, max_calls=MAX_EXPLORATION_CALLS):
    """Record an exploration tool call and enforce the call budget.
    
    Raises:
        BudgetExceededError: If the budget is already exhausted.
    """
```

Constante:

```python
# src/research/config/constants.py
MAX_EXPLORATION_CALLS = 6  # 3 rounds × 2 queries
```

`08-deep-agent` tiene `max_turns: int = 50` en settings (línea 40 de `settings.py`) pero **nunca se usa** — no hay un guard que detenga al coordinador después de 50 turnos.

**Acción recomendada**:

Para nuestro caso de uso (orientación vocacional), aplicar límites a:

1. **Web searches por sesión** (riesgo de cost explosion con Tavily API):
   ```python
   # src/tools/web_search.py
   _web_search_calls_per_session: dict[str, int] = {}
   
   @tool
   def web_search(query: str, max_results: int = 5) -> list[dict]:
       thread_id = get_current_thread_id()  # vía langgraph config
       calls = _web_search_calls_per_session.setdefault(thread_id, 0)
       if calls >= MAX_WEB_SEARCH_CALLS_PER_SESSION:
           return [{"title": "Budget exceeded", "url": "", 
                    "content": "Has alcanzado el límite de búsquedas..."}]
       _web_search_calls_per_session[thread_id] = calls + 1
       ...
   ```

2. **Total turns por sesión** (evitar loops infinitos en assessment):
   ```python
   # src/agent/factory.py
   from langgraph.graph import END
   from langchain_core.runnables import RunnableConfig
   
   def should_continue(state):
       if len(state["messages"]) > settings.max_turns:
           return END
       return "continue"
   ```

3. **Token budget por sesión** (más complejo, requiere streaming del LLM):
   ```python
   from langchain_core.callbacks import get_openai_callback
   # o equivalente para Bedrock
   ```

---

### 4.3 ⚠️ `Settings` no usa `SecretStr` para API keys — riesgo de leak en logs

**Hallazgo** (líneas 48, 53 de `src/config/settings.py`):

```python
langsmith_api_key: str | None = None
...
tavily_api_key: str | None = None
```

Comparado con el workshop (`src/research/config/settings.py:38-39`):

```python
google_api_key: SecretStr = Field(
    alias="GOOGLE_API_KEY", description="The API key for the Google Gemini API"
)
```

`SecretStr` previene que el valor se imprima accidentalmente en logs o tracebacks. Es un patrón estándar de Pydantic para secrets.

**Acción recomendada**:

```python
# src/config/settings.py
from pydantic import SecretStr

class Settings(BaseSettings):
    ...
    tavily_api_key: SecretStr | None = None
    langsmith_api_key: SecretStr | None = None
```

Y al consumir:

```python
api_key = settings.tavily_api_key.get_secret_value()
```

**Bono**: Crear un `Settings` resource MCP para inspeccionar config sin exponer secrets (como el workshop en `routers/resources.py`):

```python
@mcp.resource("resource://config/spark")
async def get_spark_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "agent_name": settings.agent_name,
        "environment": settings.environment.value,
        "model": settings.model_string,
        "has_tavily_key": settings.tavily_api_key is not None,  # ← solo presencia
        "has_langsmith_key": settings.langsmith_api_key is not None,
    }
```

---

### 4.4 ⚠️ `assessment` subagent no tiene un límite duro de turnos — riesgo de loop

**Hallazgo** (`src/agent/subagents/assessment.py:67`):

```text
## Reglas

- Máximo 3-4 turnos de preguntas (no hagas 20 preguntas)
- ...
```

El límite es **convencional** (en el system prompt), no técnico. El LLM puede ignorarlo si el estudiante da respuestas ambiguas.

**Acción recomendada**: aplicar la guarda de §4.2 + agregar **explicit state tracking** en el assessment subagent:

```python
ASSESSMENT_SYSTEM_PROMPT = """\
...
## Reglas

- Máximo 3-4 turnos de preguntas (NO MÁS). Si el LLM supera 4 turnos,
  el tool `evaluate_riasec_profile` rechazará la llamada con un error
  y debes finalizar el assessment inmediatamente.
"""
```

Y tool-level guard (más robusto que prompt-level):

```python
# src/tools/assessment.py
_TURN_COUNTER: dict[str, int] = {}

@tool
def evaluate_riasec_profile(...):
    thread_id = get_current_thread_id()
    count = _TURN_COUNTER.get(thread_id, 0)
    if count >= 1:
        raise RuntimeError(
            "evaluate_riasec_profile ya fue llamado en esta sesión. "
            "Si el assessment no está completo, llama al coordinator."
        )
    _TURN_COUNTER[thread_id] = 1
    ...
```

---

### 4.5 ⚠️ No hay separación entre **lógica de negocio** y **registro de tools**

**Hallazgo**: En `08-deep-agent`, cada tool es **directamente** un `@tool` decorado (e.g., `src/tools/matching.py:31`):

```python
@tool
def calculate_affinity(riasec_code: str, top_n: int = 5) -> list[dict]:
    """..."""
    riasec_code = riasec_code.upper()[:3]
    results = []
    for career in CAREER_CATALOG:
        score = _riasec_similarity(riasec_code, career["riasec_profile"])
        ...
    return results
```

El workshop separa en **3 capas**:

```
src/research/
├── tools/                  # MCP tool wrapper (thin layer)
│   └── deep_research_tool.py    ← llama al handler
├── app/                    # Business logic handlers
│   └── research_handler.py      ← contiene la lógica
└── routers/                # Registration
    └── tools.py                ← @opik.track + @mcp.tool
```

Ejemplo del workshop (`src/research/tools/deep_research_tool.py:26`):

```python
async def deep_research_tool(working_dir: str, query: str) -> dict[str, Any]:
    """Pure business logic, no @tool decorator."""
    validate_directory(working_dir)
    memory_path = ensure_memory_dir(working_dir)
    
    try:
        call_index, calls_remaining = record_exploration_call(...)
    except BudgetExceededError as exc:
        return {"status": "budget_exceeded", ...}
    
    result = await run_grounded_search(query)
    existing_results.append(result.model_dump())
    save_json(results_path, existing_results)
    
    return {"status": "success", "query": query, "answer": result.answer, ...}
```

Y el wrapper MCP en `routers/tools.py:21`:

```python
@mcp.tool()
@opik.track(type="tool")
async def deep_research(working_dir: str, query: str) -> dict[str, Any]:
    opik_context.update_thread_id()
    return await deep_research_tool(working_dir, query)
```

**Beneficios**:
1. La lógica de negocio se puede testear **sin instanciar el LLM** o el servidor MCP.
2. Los handlers se pueden reutilizar desde múltiples superficies (API REST, CLI, MCP server, tests).
3. Las decoraciones de observability/logging se aplican de forma uniforme en un solo lugar.

**Acción recomendada** (refactor progresivo):

```
src/tools/
├── assessment/
│   ├── handler.py        # Lógica pura (sin @tool)
│   └── tool.py           # @tool decorator (thin wrapper)
├── catalog/
│   ├── handler.py
│   └── tool.py
├── matching/
│   ├── handler.py
│   └── tool.py
└── web_search/
    ├── handler.py
    └── tool.py
```

Cada `handler.py` retorna un dict estructurado (status, data, errors) — fácil de testear y extender.

---

### 4.6 ⚠️ No hay **system prompt versioning** — cambios al prompt no son rastreables en evals

**Hallazgo**: Los prompts viven en `src/prompts/system.py` y `src/agent/subagents/*.py` como **strings literales**. Cambios a un prompt no tienen historial asociado con métricas de evaluación.

El workshop tiene `profiles/*.md` versionados como archivos markdown, con su propio patrón de testing:

```
src/writing/profiles/
├── branding_profile.md
├── character_profile.md
├── structure_profile.md
└── terminology_profile.md
```

Y un binary LLM judge (`src/writing/evals/metric.py`) que evalúa automáticamente contra esos profiles.

**Acción recomendada**:

1. Mover prompts a archivos `.md` versionados:

```
src/prompts/
├── coordinator.md          # Era src/prompts/system.py
├── assessment.md           # Era src/agent/subagents/assessment.py
├── matching.md             # Era src/agent/subagents/matching.py
└── planning.md             # Era src/agent/subagents/planning.py
```

2. Crear loader:

```python
# src/prompts/loader.py
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent

def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")

SYSTEM_PROMPT = load_prompt("coordinator")
ASSESSMENT_SYSTEM_PROMPT = load_prompt("assessment")
MATCHING_SYSTEM_PROMPT = load_prompt("matching")
PLANNING_SYSTEM_PROMPT = load_prompt("planning")
```

3. Cada PR que toque un prompt puede incluir un diff del archivo `.md` + métricas de eval adjuntas.

---

### 4.7 ⚠️ No hay evaluación automatizada del agente (LLM-as-judge)

**Hallazgo**: `08-deep-agent` tiene `tests/test_models.py` y `tests/test_tools.py` — pero **ninguno evalúa el comportamiento del agente completo** (subagentes delegando correctamente, tools llamadas en orden, perfil extraído coherentemente).

El workshop tiene **dos layers de evaluación**:

1. **Binary LLM Judge** (`src/writing/evals/metric.py`) — usa Gemini como juez con few-shot examples para scoring pass/fail.
2. **Opik datasets** (`src/writing/evals/dataset.py`) — versiona datasets de evaluación en Opik para regression testing.

Ejemplo del prompt del juez (`metric.py:21-76`):

```python
JUDGE_PROMPT = """
You are an expert LinkedIn content evaluator. Your job is to evaluate a generated
LinkedIn post against a set of writing profiles and the original guideline.

**PROFILES — evaluate the generated post against these rules:**
<structure_profile>{structure_profile}</structure_profile>
...

**LABELING GUIDELINES:**
- Do NOT flag minor stylistic differences
- Flag as "fail" ONLY for major violations:
  - Uses banned AI slop expressions
  - Completely misses the core topic
  - Violates major structural rules
- If the generated post addresses the topic and follows the profiles reasonably well,
  label it "pass".
...
"""
```

**Acción recomendada** (roadmap):

1. **Tests de comportamiento del agente** (corto plazo):
   ```python
   # tests/test_agent_assessment.py
   def test_assessment_extracts_riasec_after_4_turns():
       """Conversación corta → perfil completo extraído."""
       manager = create_profile_manager()
       messages = [...]  # 4 turnos de estudiante
       result = manager.invoke({"messages": messages})
       profile = result[0].content
       assert profile.has_riasec_profile
       assert profile.riasec_code is not None
   ```

2. **LLM-as-judge** (mediano plazo):
   - Crear `evals/dataset.jsonl` con ~20 conversaciones esperadas.
   - Crear `evals/judge.py` que use Claude/Gemini como juez.
   - Integrar con Opik o un CSV de resultados para tracking histórico.

3. **Regression tests** (largo plazo):
   - Cada cambio a `prompts/*.md` corre las 20 conversaciones del dataset.
   - Diff de pass-rate reportado en el PR.

---

## 5. Mejoras 🟢 Baja prioridad

### 5.1 Crear `pyproject.toml` más explícito en herramientas

Comparar el nuestro (`pyproject.toml:46-66`) con el workshop:

**Nosotros**:

```toml
[tool.ruff]
target-version = "py314"
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.ruff.lint.isort]
known-first-party = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
pythonpath = ["."]
```

**Recomendaciones adicionales**:

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "ASYNC", "PT", "RET", "RUF"]
# Añadir: ASYNC (async correctness), PT (pytest style), RET (return statements), RUF (ruff-specific)

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101"]  # allow assert in tests

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.mypy]
python_version = "3.14"
strict = true
warn_return_any = true
warn_unused_configs = true
# Añadir:
disallow_untyped_defs = true
no_implicit_optional = true
warn_redundant_casts = true
```

---

### 5.2 Falta `.dockerignore` (cuando se haga deploy a AgentCore)

El `DEPLOYMENT.md` (líneas 261-280) menciona containerización Docker para AgentCore, pero **no existe `.dockerignore`**. Esto hará que el build de Docker incluya `.venv`, `__pycache__`, `.pytest_cache`, `.ruff_cache`, `tests/`, etc.

**Acción**:

```dockerignore
# .dockerignore
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.mypy_cache/
.git/
.github/
tests/
*.md
!README.md
.env
.env.local
.DS_Store
*.egg-info/
dist/
build/
```

---

### 5.3 No hay `__init__.py` exports — los imports son largos y frágiles

**Hallazgo** (`src/agent/factory.py:6-18`):

```python
from src.agent.subagents import (
    ASSESSMENT_SUBAGENT,
    MATCHING_SUBAGENT,
    PLANNING_SUBAGENT,
)
from src.config import get_settings
from src.prompts import SYSTEM_PROMPT
from src.tools import (
    calculate_affinity,
    evaluate_riasec_profile,
    search_careers,
    web_search,
)
```

Estos imports funcionan, pero **si refactorizamos la estructura interna** (e.g., mover `assessment.py` a `src/tools/assessment/handler.py`), hay que cambiar muchos archivos.

**Acción recomendada** (en cada `__init__.py`):

```python
# src/tools/__init__.py
from src.tools.assessment.tool import evaluate_riasec_profile
from src.tools.catalog.tool import search_careers
from src.tools.matching.tool import calculate_affinity
from src.tools.web_search.tool import web_search

__all__ = [
    "calculate_affinity",
    "evaluate_riasec_profile",
    "search_careers",
    "web_search",
]
```

Así, **el `factory.py` no cambia** si movemos archivos internamente.

---

### 5.4 El `README.md` menciona `claude-code → opencode` pero no hay configuración

El workshop tiene `opencode.json` y `.mcp.json` (líneas de workshop):

```json
// opencode.json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "deep-research": {
      "type": "local",
      "command": ["uv", "run", "fastmcp", "run", "src/research/server.py"],
      "enabled": true,
      "environment": { "ENV_FILE_PATH": ".env" }
    }
  }
}
```

`08-deep-agent` no tiene ninguno. Si vamos a ofrecer **acceso MCP al agente** desde Claude Code, Cursor o opencode, necesitamos ese archivo.

**Acción recomendada**:

```json
// .mcp.json (en raíz de 08-deep-agent)
{
  "mcpServers": {
    "spark-match-agent": {
      "type": "http",
      "url": "http://localhost:8000/ag-ui",
      "enabled": true,
      "environment": {
        "ENV_FILE_PATH": ".env"
      }
    }
  }
}
```

(Y exponer el agente como MCP server además de FastAPI, si queremos compatibilidad con Claude Code.)

---

### 5.5 Falta `CHANGELOG.md` y versionado semántico

El `pyproject.toml` tiene `version = "0.1.0"` pero no hay CHANGELOG. Para el TFP es útil para que el evaluador vea la evolución.

**Acción recomendada**:

```markdown
# Changelog

## [Unreleased]

### Added

- (futuro)

## [0.1.0] - 2026-07-10

### Added

- Deep Agent coordinator con 3 subagentes (assessment, matching, planning)
- AG-UI streaming endpoint en `/ag-ui`
- Memory con langmem para extracción de StudentProfile
- Web search con Tavily + DuckDuckGo fallback
- Catálogo de 10 carreras hard-coded
- Tests unitarios para modelos y tools
```

---

## 6. Tabla resumen de mejoras

| # | Mejora | Prioridad | Esfuerzo | Valor |
|---|---|---|---|---|
| 3.1 | Limpiar `.gitignore` (uv.lock + .python-version) | 🔴 | 5 min | Alto |
| 3.2 | Agregar logging centralizado | 🔴 | 30 min | Alto |
| 3.3 | Crear `Makefile` con targets | 🔴 | 1 hora | Alto |
| 3.4 | Mover catálogo a `data/careers/*.md` | 🔴 | 3 horas | Alto |
| 4.1 | Integrar observabilidad (Opik o LangSmith) | 🟡 | 4 horas | Alto |
| 4.2 | Agregar budget guards (web_search, max_turns) | 🟡 | 2 horas | Medio |
| 4.3 | `SecretStr` para API keys en `Settings` | 🟡 | 30 min | Medio |
| 4.4 | Guard técnico en assessment subagent | 🟡 | 2 horas | Medio |
| 4.5 | Refactor: separar handler vs. tool wrapper | 🟡 | 6 horas | Alto |
| 4.6 | Mover prompts a archivos `.md` versionados | 🟡 | 3 horas | Medio |
| 4.7 | Evaluaciones automatizadas (LLM-as-judge) | 🟡 | 8 horas | Alto |
| 5.1 | Ampliar `pyproject.toml` (ruff + mypy más estricto) | 🟢 | 30 min | Bajo |
| 5.2 | Crear `.dockerignore` | 🟢 | 10 min | Bajo |
| 5.3 | Refinar `__init__.py` con re-exports | 🟢 | 1 hora | Medio |
| 5.4 | Crear `.mcp.json` para harnesses | 🟢 | 30 min | Bajo |
| 5.5 | Crear `CHANGELOG.md` | 🟢 | 30 min | Bajo |

**Total esfuerzo estimado**: ~32 horas (~4 días de trabajo).

---

## 7. Roadmap sugerido

> **Estado**: ✅ Sprint 1 y Sprint 2 completados (PR #5 y PR #6). Pendiente Sprint 3 (observabilidad + prompts .md) y Sprint 4 (refactor + evals).

### Sprint 1 (1-2 días) — Higiene ✅ Completado (PR #5)

- [x] §3.1 Limpiar `.gitignore` — commit `476c0aa`
- [x] §3.2 Crear logging centralizado (`src/utils/logging.py`)
- [x] §3.3 Crear `Makefile` (14 targets)
- [x] §4.3 `SecretStr` en Settings
- [x] §5.2 Crear `.dockerignore`
- [x] §5.5 Crear `CHANGELOG.md`

### Sprint 2 (2-3 días) — Funcionalidad core ✅ Completado (PR #6)

- [x] §3.4 Mover catálogo a `data/careers/*.md` con loader YAML
- [x] §4.2 Budget guards para `web_search` (per-session counter, ContextVar-based)
- [x] §5.3 Refinar `__init__.py` (re-exports en `src/`, `src/agent/`, `src/config/`)
- [x] **FIX-1**: Reemplazar `create_endpoint` deprecado por `LangGraphAgent` + endpoint manual con `EventEncoder` + `set_active_session` per-request
- [x] **FIX-2**: Resolver 16 errores pre-existentes de mypy strict (TypedDicts, generics, type annotations)
- [ ] §4.4 Guard técnico en assessment (turn-count para `evaluate_riasec_profile`) — movido a Sprint 4

### Sprint 3 (3-5 días) — Observabilidad y eval ✅ Completado (PR #7)

- [x] §4.1 Integrar **LangSmith** (no Opik) — wiring automático via `LANGSMITH_*` env vars. `src/observability/langsmith.py` con `configure_langsmith()` idempotente.
- [x] §4.6 Mover los 4 prompts a `src/prompts/*.md` con YAML frontmatter + loader cacheable (`load_prompt()`, `reload_prompts()`, `list_prompts()`).
- [x] §5.1 ruff: `select = [..., "ASYNC", "PT", "RET", "RUF"]` + `per-file-ignores` para tests. mypy: `disallow_untyped_defs`, `no_implicit_optional`, `warn_redundant_casts`, `warn_unused_ignores`.

### Sprint 4 (opcional) — Refactor y evals

- [ ] §4.2 max_turns middleware (post-model hook en factory)
- [ ] §4.4 Guard técnico en assessment
- [ ] §4.5 Refactor handler vs. tool wrapper
- [ ] §4.7 LLM-as-judge + Opik datasets

---

## 8. Conclusión

`08-deep-agent` tiene una **arquitectura sólida y bien pensada** (factory pattern, subagents explícitos, AG-UI streaming, langmem para memoria). Las mejoras críticas son de **higiene operativa** (`.gitignore` contradictorio, logging, Makefile) y de **preparación para producción** (observabilidad, budget guards, datos desacoplados del código).

El workshop de Paul Iusztin es un buen benchmark porque demuestra **los mismos patrones pero ya pasados por producción**: budgets explícitos, separación handler/MCP, observabilidad nativa con Opik, datasets versionados para regression testing.

**Sprint 1 y Sprint 2 completados** (PR #5 + PR #6). El repo está listo para deploy a AgentCore desde el punto de vista de:
- Higiene operativa (logging, make, gitignore, secrets, dockerignore, changelog).
- Catálogo desacoplado (datos en markdown, no en código Python).
- Budget guard de `web_search` (Tavily quota protegida).
- Tipado estricto (mypy `strict = true` pasa 0 errores).
- AG-UI endpoint actualizado a la API actual.

Pendiente Sprint 3 (Opik + prompts .md) y Sprint 4 (refactor handler/tool + max_turns middleware + LLM-as-judge).