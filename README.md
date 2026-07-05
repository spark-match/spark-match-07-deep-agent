# Spark Match Agent

Deep Agent de orientación vocacional y desarrollo profesional, construido con [LangChain Deep Agents](https://github.com/langchain-ai/deepagents) y el protocolo [AG-UI](https://docs.copilotkit.ai/ag-ui/introduction).

## Descripción

Spark Match es un **agente inteligente** que acompaña a estudiantes en su camino vocacional y profesional. No es un chatbot simple — es un agente con memoria, razonamiento multi-paso, delegación a subagentes especializados y acceso a herramientas como búsqueda web.

El agente puede:
- **Descubrir** el perfil vocacional del estudiante mediante conversación natural (RIASEC)
- **Analizar** la afinidad entre el perfil y carreras disponibles
- **Recomendar** carreras con scores y explicaciones personalizadas
- **Planificar** rutas profesionales con cursos, certificaciones y timeline

## Stack técnico

| Componente | Tecnología |
|---|---|
| Framework del agente | `deepagents` v0.6.12 (Python) |
| LLM | Amazon Bedrock (Claude Sonnet) via `langchain-aws` |
| Runtime | LangGraph |
| API | FastAPI + SSE (AG-UI protocol) |
| Protocolo frontend | AG-UI (`ag-ui-langgraph`) |
| Memoria / Perfilado | `langmem` (extracción de perfil desde conversación) |
| Web search | Tavily (primary) + DuckDuckGo (fallback) |
| Package manager | `uv` |
| Python | 3.14 |

## Arquitectura

```
┌─────────────────────────────────────────────────────────────┐
│  Angular Frontend (04-frontend)                             │
│  └── AG-UI Client (SSE events)                              │
│      - Streaming de texto en tiempo real                    │
│      - Reasoning / thinking steps visibles                  │
│      - Tool call cards (assessment, matching, search)       │
│      - State sync (perfil, plan, progress)                  │
│      - Human-in-the-loop (aprobar acciones)                 │
├─────────────────────────────────────────────────────────────┤
│  FastAPI + AG-UI Server (este repo)                         │
│  └── Coordinator Agent (Deep Agent + Bedrock)               │
│      ├── assessment subagent (RIASEC conversacional)        │
│      ├── matching subagent (afinidad perfil ↔ carreras)     │
│      └── planning subagent (plan de acción + web search)    │
│                                                             │
│  Tools:                                                     │
│  ├── evaluate_riasec_profile  (scoring RIASEC)              │
│  ├── search_careers           (catálogo local → pgvector)   │
│  ├── calculate_affinity       (matching perfil ↔ carrera)   │
│  └── web_search               (Tavily + DDG fallback)       │
│                                                             │
│  Memory:                                                    │
│  └── langmem (StudentProfile extraction from conversation)  │
├─────────────────────────────────────────────────────────────┤
│  Local (StateBackend) → migrable a AgentCore Runtime        │
└─────────────────────────────────────────────────────────────┘
```

### Subagentes

| Subagente | Misión | Tools |
|---|---|---|
| `assessment` | Administra test RIASEC conversacional, infiere scores | `evaluate_riasec_profile` |
| `matching` | Calcula afinidad y genera ranking Top-5 | `calculate_affinity`, `search_careers` |
| `planning` | Genera planes de acción con recursos reales | `search_careers`, `web_search` |

### Flujo del coordinador

```
Usuario: "Quiero descubrir mi perfil"  →  delega a assessment
Usuario: "¿Qué carreras me convienen?" →  delega a matching
Usuario: "Dame un plan para Data Science" →  delega a planning
Usuario: preguntas generales  →  coordinador responde directo
```

## Requisitos previos

- [uv](https://docs.astral.sh/uv/) instalado
- Python 3.14+
- AWS credentials configuradas (para Bedrock)
- (Opcional) Tavily API key para web search optimizado

## Quickstart

```bash
# 1. Clonar e instalar dependencias
git clone git@github.com:spark-match/spark-match-08-deep-agent.git
cd spark-match-08-deep-agent
uv sync

# 2. Configurar variables de entorno
cp .env.example .env
# Editar .env con tus credenciales AWS y (opcional) Tavily key

# 3. Ejecutar el servidor
uv run python -m src
```

El servidor arranca en `http://localhost:8000`.

## Endpoints

| Método | Path | Descripción |
|--------|------|-------------|
| `POST` | `/ag-ui` | Endpoint AG-UI (SSE streaming) — el frontend se conecta aquí |
| `GET` | `/health` | Health check con info del agente y modelo |

## Desarrollo

```bash
# Instalar dependencias de desarrollo
uv sync --all-extras

# Ejecutar tests
uv run pytest

# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/

# Type check
uv run mypy src/
```

## Estructura del proyecto

```
08-deep-agent/
├── src/
│   ├── __init__.py
│   ├── __main__.py              # Entry point: uv run python -m src
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── factory.py           # create_spark_agent() — coordinador
│   │   └── subagents/
│   │       ├── __init__.py
│   │       ├── assessment.py    # RIASEC conversacional
│   │       ├── matching.py      # Ranking de carreras
│   │       └── planning.py      # Planes de acción
│   ├── api/
│   │   ├── __init__.py
│   │   ├── app.py               # FastAPI + AG-UI endpoint
│   │   └── server.py            # uvicorn runner
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py          # Pydantic settings (local ↔ AgentCore)
│   ├── memory/
│   │   ├── __init__.py
│   │   └── profile_manager.py   # langmem profile extraction
│   ├── models/
│   │   ├── __init__.py
│   │   └── profile.py           # StudentProfile schema (Pydantic)
│   ├── prompts/
│   │   ├── __init__.py
│   │   └── system.py            # System prompt del coordinador
│   └── tools/
│       ├── __init__.py
│       ├── assessment.py        # evaluate_riasec_profile
│       ├── catalog.py           # search_careers (in-memory MVP)
│       ├── matching.py          # calculate_affinity
│       └── web_search.py        # Tavily + DuckDuckGo fallback
├── skills/
│   └── vocational_advisor/
│       └── SKILL.md             # Skill on-demand (conocimiento RIASEC)
├── tests/
│   ├── __init__.py
│   ├── test_models.py           # Tests del StudentProfile
│   └── test_tools.py            # Tests de tools (assessment, catalog, matching)
├── .env.example                 # Template de variables de entorno
├── .gitignore
├── .python-version              # Python 3.14
├── pyproject.toml               # Dependencies + tooling config
├── uv.lock                      # Lock file (reproducible builds)
└── README.md
```

## Perfil del estudiante (langmem)

El agente extrae el perfil vocacional del estudiante **conversacionalmente** usando `langmem`. No necesita un formulario rígido — infiere los scores RIASEC de las respuestas naturales:

```python
from src.models.profile import StudentProfile

# El perfil se llena progresivamente
profile = StudentProfile(
    name="María",
    investigative=9,   # "Me encanta resolver puzzles lógicos"
    artistic=7,        # "Dibujo en mi tiempo libre"
    social=4,          # "Prefiero trabajar sola"
    # ... se completa conforme conversa
)

profile.has_riasec_profile    # True cuando tiene los 6 scores
profile.profile_completeness  # 0.0 → 1.0
```

## Web Search (Tavily + DuckDuckGo)

El agente busca información actualizada de carreras, cursos y mercado laboral:

- **Tavily** (primario): 1,000 búsquedas/mes gratis, resultados optimizados para LLMs
- **DuckDuckGo** (fallback): ilimitado, sin API key, se activa automáticamente si Tavily falla

```bash
# Configurar Tavily (opcional — sin key usa DDG directo)
SPARK_TAVILY_API_KEY=tvly-tu-api-key
```

## Migración a producción (AgentCore)

El agente está diseñado para migrar de local a AWS Bedrock AgentCore:

```bash
SPARK_ENVIRONMENT=agentcore
```

Esto activará:
- AgentCore Runtime (serverless, auto-scaling)
- AgentCore Memory (long-term cross-session)
- AgentCore Observability (CloudWatch / X-Ray / OpenTelemetry)
- Sandbox MicroVMs (code execution aislado)

## Protocolos soportados

| Protocolo | Estado | Uso |
|-----------|--------|-----|
| **AG-UI** | ✅ Implementado | Frontend ↔ Agent (streaming, reasoning, tools) |
| **MCP** | ✅ Ready | Agent ↔ Tools (herramientas externas) |
| **A2A** | 🔜 Futuro | Agent ↔ Agent (multi-agent cross-framework) |
| **A2UI** | 🔜 Futuro | Agent → Frontend (generative UI components) |

## Trabajos futuros

- **Observabilidad con LangSmith**: integrar [LangSmith](https://www.langchain.com/langsmith) para tracing, monitoring y debugging de los subagentes y tools. Esto nos dará visibilidad sobre:
  - Latencia por subagente (`assessment`, `matching`, `planning`)
  - Tokens consumidos por sesión y por usuario
  - Traces completos de las tool calls (input/output de `evaluate_riasec_profile`, `search_careers`, `calculate_affinity`, `web_search`)
  - Detección de alucinaciones y fallos en el razonamiento
  - Datasets de evaluación a partir de interacciones reales (para regression testing)
  
  La integración se hará mediante las variables de entorno `LANGSMITH_API_KEY`, `LANGSMITH_TRACING=true` y `LANGSMITH_PROJECT=spark-match-dev` (o `spark-match-prod`), aprovechando el soporte nativo de `langchain-aws` y `deepagents` con LangSmith.

## Contexto académico

Trabajo de Fin de Programa (TFP) — **UNI** II Programa de Especialización en IA Generativa y Machine Learning Ops.

## Licencia

MIT
