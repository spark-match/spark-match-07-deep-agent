# Spark Match Deep Agent — Hoja de Ruta de Finalización

> **Fecha de emisión**: 2026-08-03
> **Repositorio objetivo**: `spark-match-07-deep-agent` (rama base `dev`, HEAD `f0b139b`)
> **Ventana de ejecución**: agosto → octubre 2026 (Sprints 5 → 11)
> **Audiencia**: agente de IA o ingeniero que va a implementar. Este documento es autocontenido.
> **Sustituye/extiende**: `IMPROVEMENTS.md` (Sprints 1–4, ya cerrados)
> **Documentos fuente**: `../orion/AWS-DEEPAGENT-VS-AWS-RUNTIME-VS-AWS-HARNESS.md`, `../orion/AWS-HARNESS-HARNESS-POC.md`, `../orion/AWS-HARNESS-POC-V10.md`, `../orion/AGENT-PROMPT-ARCHITECTURE-EVALUATION.md`

---

## 0. Cómo usar este documento

1. **No asumas nada del `README.md` ni del `IMPROVEMENTS.md` actuales.** Ambos están desactualizados y describen features que no existen (persistencia langmem, human-in-the-loop, MCP "ready"). La sección §2 de este documento es la única fuente de verdad verificada al 2026-08-03.
2. Cada sprint tiene: **objetivo**, **tareas con archivo concreto**, **snippets de referencia verificados contra el venv instalado**, y **Definition of Done (DoD) medible**.
3. Las secciones §6.1 y §6.2 son **solicitudes formales a otros repos** (`01-devops`, `02-infrastructure`). Deben abrirse como issues/PRs en esos repos, no implementarse aquí.
4. Todos los snippets de este documento fueron validados ejecutando `inspect.signature()` contra el `.venv` real del repo (deepagents 0.6.12 / langchain 1.3.11 / langgraph 1.2.7). No son inventados.
5. Convención de ramas: `feat/sprint-N-<slug>` → PR a `dev` → squash merge. Conventional Commits obligatorio.

---

## 1. Resumen ejecutivo

### 1.1 Dónde estamos

El agente **funciona conversacionalmente pero es completamente amnésico y completamente abierto**:

| Capacidad | Estado real |
|---|---|
| Memoria por sesión (thread) | ❌ **No existe.** `create_deep_agent()` se invoca sin `checkpointer=`. Cada request HTTP arranca de cero. |
| Memoria entre sesiones (usuario) | ❌ **No existe.** Sin `store=`, sin `backend=`. `src/memory/profile_manager.py` es código muerto: `create_profile_manager()` no se llama en ningún sitio. |
| Autenticación / autorización | ❌ **No existe.** `POST /ag-ui` es público. `thread_id` lo elige el cliente y no se valida → secuestro trivial de sesión. |
| Skills | ❌ `skills/vocational_advisor/SKILL.md` nunca se carga (`skills=` nunca se pasa). |
| Web search | ⚠️ Funciona (Tavily + DDG fallback) pero con 3 bugs confirmados y **0 tests**. |
| Guardrails de turnos | ❌ **Roto.** `MaxTurnsMiddleware` devuelve `{"goto": END}`; LangChain 1.x espera `jump_to`. La clave se descarta en silencio. `SPARK_MAX_TURNS` es inerte. |
| Modelo configurado | ⚠️ `us.anthropic.claude-sonnet-4-20250514` — **NO está en el allowlist de IAM** de `02-infrastructure`. Fallará con `AccessDenied` en AWS. |
| Contenedor / deploy | ❌ No hay `Dockerfile`. Solo un `.dockerignore` huérfano. |
| CI | ⚠️ Workflow propio, sin coverage gate, sin security scan, sin reusables del catálogo org. |

### 1.2 A dónde vamos

Replicar **en código portable** las bondades que AWS Bedrock AgentCore Harness da *out of the box* (validadas empíricamente en POC v2: 100% success rate en 195 turnos, 4.42/5 helpfulness, P50 8.5s, $0.0043/turno), sin pagar el lock-in:

- Memoria de 4 capas equivalente a las 4 memory strategies del Harness.
- JWT + roles consumiendo el contrato **exacto** del backend `03-backend` (HS256, `iss=spark-match-backend`, `aud=spark-match-api`).
- Skills on-demand, tools vía MCP, guardrails de contenido, intent router Haiku/Sonnet.
- Contenedor + pipeline reutilizable + infraestructura Terraform.

### 1.3 Esfuerzo estimado

| Sprint | Tema | Esfuerzo | Bloquea a |
|---|---|---|---|
| 5 | Correcciones críticas + deuda técnica | 16 h | 6, 7 |
| 6 | **Memoria** (checkpointer + store + langmem) | 28 h | 7, 9 |
| 7 | **Auth JWT + roles + aislamiento multi-usuario** | 24 h | 10 |
| 8 | Tools, web_search, MCP, intent router | 24 h | 9 |
| 9 | **Guardrails + evals ampliados** ✅ | 20 h | 11 |
| 10 | Contenedor + CI/CD + infraestructura | 24 h | 11 |
| 11 | Deploy, observabilidad, cierre TFP | 20 h | — |
| | **Total** | **~156 h** | ~7 semanas a 22 h/sem |

---

## 2. Estado verificado (2026-08-03)

### 2.1 `spark-match-07-deep-agent`

**Stack instalado (de `uv.lock`, no de los floors de `pyproject.toml`):**

| Paquete | Declarado | Resuelto |
|---|---|---|
| `deepagents` | `>=0.6.12` | **0.6.12** |
| `langchain` | (transitivo) | 1.3.11 |
| `langchain-core` | (transitivo) | 1.4.8 |
| `langgraph` | (transitivo) | 1.2.7 |
| `langgraph-checkpoint` | (transitivo) | 4.1.1 |
| `langchain-aws` | `>=1.6.1` | 1.6.1 |
| `langmem` | `>=0.0.30` | 0.0.30 |
| `ag-ui-langgraph` | `>=0.0.11` | **0.0.42** |
| `fastapi` | `>=0.115.0` | 0.139.0 |
| `tavily-python` | `>=0.5.0` | 0.7.26 |
| `duckduckgo-search` | `>=7.0.0` | **8.1.1** (bump mayor) |
| `pytest` | `>=8.3.0` | 9.1.1 |
| `mypy` | `>=1.15.0` | **2.1.0** (bump mayor) |
| Python | `>=3.14` | 3.14 |

**NO instalados (hay que agregarlos):** `langgraph-checkpoint-postgres`, `langgraph-checkpoint-sqlite`, `psycopg`, `PyJWT`/`python-jose`, `pytest-cov`.

**Wiring actual (`src/agent/factory.py`):**

```python
agent = create_deep_agent(
    model=settings.model_string,
    tools=[evaluate_riasec_profile, search_careers, calculate_affinity, web_search],
    subagents=subagents,                       # 3 dicts planos
    system_prompt=SYSTEM_PROMPT,
    name=settings.agent_name,
    middleware=[MaxTurnsMiddleware(), AssessmentOnceMiddleware()],
)
```

**No se pasa:** `checkpointer=`, `store=`, `backend=`, `skills=`, `memory=`, `permissions=`, `interrupt_on=`, `context_schema=`, `state_schema=`, `response_format=`, `cache=`.

**Consecuencia:** el backend por defecto es `StateBackend()` (filesystem virtual en el state del grafo, se descarta al terminar el run) y el grafo se compila sin persistencia.

**Middleware implícito que sí está activo** (lo inyecta `create_deep_agent`): `TodoListMiddleware` → `FilesystemMiddleware` → `SubAgentMiddleware` → `SummarizationMiddleware` → `PatchToolCallsMiddleware` → *[middleware del usuario]* → `AnthropicPromptCachingMiddleware` → `BedrockPromptCachingMiddleware`.

**Tools implícitas que el LLM ya tiene y nadie documenta:** `write_todos`, `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `execute`, `task`.

#### Bugs confirmados (leyendo el código de las librerías, no especulación)

| # | Severidad | Archivo | Descripción |
|---|---|---|---|
| **B1** | 🔴 | `src/agent/middleware.py` | `MaxTurnsMiddleware.after_model` devuelve `{"messages": [...], "goto": END}`. `AgentState` de LangChain 1.x declara `jump_to: NotRequired[Annotated[JumpTo \| None, ...]]` con `JumpTo = Literal["tools","model","end"]`. LangGraph filtra claves desconocidas en `state.py:1449`. **El agente no se detiene**; solo se añade un mensaje engañoso. El test pasa porque asserta sobre el dict retornado, no sobre el grafo. |
| **B2** | 🔴 | `src/tools/web_search/handler.py` | `max_web_searches_per_session=0` **bloquea todas** las búsquedas (`0 >= 0`), en vez de desactivar el cap como documenta `.env.example:38`. |
| **B3** | 🟡 | `src/tools/web_search/handler.py` | Doble incremento del budget: si Tavily responde OK pero con lista vacía, ya se incrementó, y luego DDG incrementa otra vez. |
| **B4** | 🟡 | `src/tools/matching/handler.py` | `_riasec_similarity` puede superar 100%: los matches cross-position suman a un numerador normalizado por el máximo same-position (60). **Ojo**: el PR #19 (`f6dac3d`, mergeado directo a `main` el 2026-07-26) parchó `src/tools/matching.py`, el módulo plano muerto que el Sprint 4 sustituyó. El código vivo nunca recibió el fix y en `dev` el fichero plano no existe. **B4 sigue abierto en ambas ramas.** Ver `AGENTS.md` §13.1. |
| **B5** | 🟡 | `src/tools/*/handler.py` | Mojibake (UTF-8 doblemente codificado) commiteado: `"ArtÃƒÂ­stico"` (assessment/handler.py:39, **se filtra al usuario** en `interpretation`), `'TecnologÃƒÂ­a'` (catalog/handler.py:54), `Sprint 2 Ã‚Â§4.2` (web_search/handler.py:102). |
| **B6** | 🔴 | `src/config/settings.py` | `model_id = "us.anthropic.claude-sonnet-4-20250514"`. El allowlist IAM de `02-infrastructure` (`modules/oidc-github/policies/*/spark-match-agentcore-runtime.json`) solo permite `anthropic.claude-sonnet-4-5-20250929-v1:0` y `anthropic.claude-haiku-4-5-20251001-v1:0`. **En AWS dará `AccessDeniedException`.** |
| **B7** | 🟡 | `src/tools/web_search/handler.py` | `DDGS()` es síncrono y se ejecuta dentro de un `@tool` invocado desde un grafo async → bloquea el event loop. `tavily-python` 0.7.26 **sí expone `AsyncTavilyClient`** (verificado). |
| **B8** | 🟢 | `Makefile` | `make test-cov` corre `pytest --cov=src` pero `pytest-cov` no está declarado → falla. Targets `eval-dev`/`eval-test` siguen imprimiendo `TODO` aunque `evals/runner.py` existe. |
| **B9** | 🟢 | `evals/runner.py:71-72` | El modo mock copia `case.expected_riasec` al output y luego asserta que el output lo contiene → **los casos de assessment pasan tautológicamente en CI**. Cero señal de regresión. |
| **B10** | 🟢 | `pyproject.toml` | `version = "0.1.0"` mientras `CHANGELOG.md` declara `0.3.0` released. |

#### Cobertura de tests

92 tests recolectados, 0 errores. **Módulos con 0 tests**: `src/api/app.py`, `src/api/server.py`, `src/agent/subagents/*`, `src/tools/web_search/handler.py`, `src/memory/profile_manager.py`. `create_spark_agent()` **nunca se invoca** en tests (solo un `hasattr`). Sin `pytest-cov`, sin gate de coverage.

---

### 2.2 Contrato de autenticación de `spark-match-03-backend` (crítico — replicar exacto)

Es **JWT custom HS256, NO Cognito**. No hay JWKS, no hay RS256, no hay refresh token, no hay revocación.

| Propiedad | Valor exacto |
|---|---|
| Librería (TS) | `jose` 6.2.4 |
| Algoritmo | **HS256** (simétrico) |
| `iss` | `spark-match-backend` |
| `aud` | `spark-match-api` (string, no array) |
| `sub` | UUID v4 del usuario (`identity.users.id`) |
| Claims custom | `email` (string), `role` (string) |
| Claims estándar | `iat`, `exp` |
| **Ausentes** | `jti`, `nbf`, `scope`, `tenant`, `groups`, `cognito:*` |
| TTL | **86400 s (24 h)** |
| Clave de firma | Bytes **UTF-8 crudos** del `SecretString` de Secrets Manager |
| Descubrimiento de clave | SSM `/spark-match/secret/jwt-arn` → ARN → Secrets Manager `GetSecretValue` → `SecretString` |

Payload decodificado real:

```json
{
  "email": "user@example.com",
  "role": "admin",
  "iat": 1785312000,
  "exp": 1785398400,
  "iss": "spark-match-backend",
  "aud": "spark-match-api",
  "sub": "3f1a...-uuid-v4"
}
```

**Gotcha crítico**: la clave es `new TextEncoder().encode(secretValue)` — **NO** se hace base64-decode ni JSON-parse. Si en Python haces `base64.b64decode()`, las firmas no validarán.

**Roles**: hoy existe **exactamente uno**: `"admin"`. Está fijado por un `CHECK` constraint en Postgres:

```sql
ALTER TABLE identity.users ADD CONSTRAINT users_role_check CHECK (role IN ('admin'));
```

Todos los usuarios registrados reciben `role='admin'`. **No existe `student`, `teacher`, `estudiante` ni `alumno`.** Los roles futuros documentados en `migrations/003` son **`docente`** y **`graduado`**.

> **Decisión requerida (§8 D-3)**: el agente necesita distinguir estudiante vs docente. Hoy el backend no lo puede expresar. Ver opciones en §8.

Endpoints relevantes: `POST /v1/auth/register`, `POST /v1/auth/login` (públicos), `GET /v1/users/me` (Bearer). Sobre el resto de rutas hay un Lambda Authorizer (`AuthorizerResultTtlInSeconds: 0`) que inyecta `event.requestContext.authorizer.lambda = {userId, email, role}`.

---

### 2.3 Catálogo de pipelines de `spark-match-01-devops`

**Convención de consumo**: `uses: spark-match/spark-match-01-devops/.github/workflows/reusable-<name>.yml@main`
Los tags `v0.1.x` existen (release-please) pero **la convención documentada es `@main`**. Prefijo `reusable-` obligatorio; sin prefijo = CI interno, no consumible.

**Disponibles hoy y aplicables a este repo:**

| Recipe | Uso aquí |
|---|---|
| `reusable-actionlint.yml` | Lint de workflows |
| `reusable-yamllint.yml` | Lint YAML |
| `reusable-gitleaks.yml` | Secret scanning (requiere secret `GITLEAKS_LICENSE`) |
| `reusable-codeql.yml` | SAST — **soporta `languages: 'python,actions'`** |
| `reusable-commitlint.yml` | Conventional Commits |
| `reusable-release-please.yml` | Versionado automático |
| `reusable-terraform-{plan,apply,destroy}.yml` | (solo para el repo de infra) |

**Borradas el 2026-08-02 — estado verificado el 2026-08-06:**

| Workflow borrado | Commit | PR | Hoy |
|---|---|---|---|
| `python-ci.yml` (uv + ruff + mypy + pytest + coverage) | `7ea5a88` | #203 | ✅ `reusable-python-ci.yml`, **en uso** |
| `container-deploy-ecr.yml` (buildx + ECR + cosign + SBOM) | `7ea5a88` | #203 | ✅ `reusable-container-deploy-ecr.yml`, **en uso** |
| `trivy.yml` | `7ea5a88` | #203 | ✅ `reusable-trivy.yml`, **en uso** |
| `checkov.yml` | `7ea5a88` | #203 | ❌ no restaurada, y no hace falta (ver abajo) |
| `sonar-python.yml` | `c007ce6` | #202 | ✅ `reusable-sonar-python.yml`, **en uso** |
| `.github/actions/run-pytest-with-args/` | `58924b8` | #206 | ✅ restaurada |

**Cinco de las seis volvieron** (restauradas el 2026-08-04, PR #297 de `01-devops`), y este repo consume las cuatro que le hacían falta. Esta sección afirmaba lo contrario durante dos días.

`checkov` es la única que no volvió, y no la necesitamos: `spark-match-02-infrastructure` lo corre localmente con una matriz por módulo (`terraform-security-scan.yml`), algo que el reusable —pensado para escanear un solo path— no podía hacer.

Un aviso que salió de cablear trivy: `reusable-trivy.yml` se restauró con el pin `aquasecurity/trivy-action@0.36.0`, un tag que **no existe** (los de ese repositorio llevan prefijo `v`). Nunca resolvió, y nadie lo notó porque la receta no tenía ni un consumidor en toda la organización. Corregido en `01-devops#321`. Restaurar una receta no equivale a que funcione: hay que ejercitarla.

**Gobernanza**: `spark-match-07-deep-agent` ya está registrado en `governance/repository-governance.json` con `reviewerTeam: ai-devs` y `statusChecks: []` (vacío). Hay que poblarlo.

**Patrón OIDC**: `aws-actions/configure-aws-credentials@v6`, `permissions: id-token: write`. **El ARN del rol se pasa como input string** (desde `${{ vars.* }}`), no como secret, porque GitHub enmascara secrets a `-` cruzando owner y rompe el assume-role.

---

### 2.4 Infraestructura disponible en `spark-match-02-infrastructure`

- **Terraform** HCL puro. `required_version >= 1.6.0`, provider `hashicorp/aws ~> 6.0` (lock 6.57.1). `TF_VERSION = 1.15.7` en CI.
- **Cuenta**: `681526276858` · **Región**: `us-east-1` · **Prefijo**: `spark-match`
- **State**: S3 por env (`spark-match-tfstate-{dev,prod}`), lockfile nativo S3 (`use_lockfile=true`), **sin DynamoDB**.
- **Envs**: `live/dev` (6 módulos cableados) y **`live/prod` VACÍO** (`main.tf` son 19 líneas de comentarios).

**Ya existe y sirve al agente:**

| Recurso | Detalle |
|---|---|
| `spark-match-bedrock-agentcore-deploy-{env}` | Rol OIDC que **ya confía en el repo `spark-match/spark-match-07-deep-agent`** (branches `dev`/`main` + `environment:{env}`) |
| `spark-match-agentcore-runtime-{env}` | Rol de ejecución. Trust policy acepta **`bedrock-agentcore.amazonaws.com` Y `ecs-tasks.amazonaws.com`** → sirve igual si se despliega en ECS/Fargate |
| KMS CMK | `alias/spark-match-{env}-main`, rotación activa |
| Permisos ECR | 25 acciones sobre `arn:aws:ecr:us-east-1:681526276858:repository/spark-match-agent-*-{env}` |
| Permisos Secrets | `spark-match/agent-*-{env}` (lectura) y **`spark-match/agent-user-*-{env}` (Create/Put/Delete — pensado para memoria por usuario)** |
| Permisos SSM | `/spark-match/*` (canal de wiring cross-repo) |
| Permisos CloudWatch | Log groups `/aws/spark-match/agent/{env}/*`, `/aws/bedrock-agentcore/{env}/*`; `PutMetricData` en namespace **`spark-match-agent`** |
| X-Ray | `AWSXRayDaemonWriteAccess` adjunto |
| SG | `spark-match-sg-lambda-{env}` (egress 443 → 0.0.0.0/0 para Bedrock/Tavily/LangSmith), `sg-rds` (5432 desde lambda), `sg-endpoints` |
| VPC endpoints | 10 interface + S3 gateway — **solo en prod**; dev solo tiene S3 gateway |

**Allowlist de modelos Bedrock (hard-pinned en IAM, solo 2):**

```
arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0
arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0
```

Acciones runtime permitidas: `InvokeModel`, `InvokeModelWithResponseStream`, `Converse`, `ConverseStream`.

**NO existe (hay que pedirlo — §6.2):** ECR repository, Aurora/RDS, DynamoDB, ElastiCache, ALB/NLB, ECS cluster/service/task-definition, secretos concretos, parámetros SSM concretos, alarmas CloudWatch, `modules/database`, `modules/bedrock`, `modules/monitoring`.

**Decisión de red vigente en dev** (`live/dev/terraform.tfvars`, "Opción A"): Lambdas **fuera** de la VPC, NAT off, Aurora accesible por RDS Data API sobre HTTPS público → $0/mes de networking.

> ⚠️ **Conflicto arquitectónico a resolver (§8 D-2)**: `langgraph-checkpoint-postgres` usa `psycopg` sobre TCP 5432, **no** RDS Data API. Un agente contenedorizado que use Postgres como checkpointer necesita estar **dentro** de la VPC (o Aurora con acceso público en dev).

**Bedrock AgentCore**: el IAM del control-plane (`bedrock-agentcorecontrol` / `bedrock-agentcoreruntime`) **no está mapeado** — `docs/IAM_ROLES.md` lo marca como riesgo conocido pendiente para "Fase 4".

---

### 2.5 `spark-match-04-frontend`

- Angular **22.0.8**, zoneless, signals. `typescript ~6.0.3`. Vitest 4.
- **JWT en `localStorage` bajo la clave `spark-match:token`**, espejado en un `signal` readonly: `AuthService.token()`.
- El campo de la respuesta de login es **`token`**, no `accessToken` (`AuthResponse { user, token }`) — **desalineado con el backend**, que devuelve `data.accessToken`.
- `authInterceptor` adjunta `Authorization: Bearer <token>` a **todas** las requests de `HttpClient` (sin allowlist de URL).
- **NO existe cliente AG-UI**: cero ocurrencias de `ag-ui`, `@ag-ui/*`, `CopilotKit`, `EventSource`, `thread_id`, streaming.
- `ChatComponent` (ruta `/assessment`) existe pero es **100% mock**: `HttpClient.post` request/response contra `${apiUrl}/chat`.
- `environment.ts` dev: `apiUrl: 'http://localhost:8000/api'`, **`useMocks: true`** → `AuthService` fabrica `'mock-initial-token'` y nunca hace login real.
- `environment.production.ts`: `apiUrl: 'https://api.sparkmatch.pe/v1'`, `useMocks: false`.
- **Conflicto de puerto**: el frontend ya apunta a `localhost:8000` para el backend, y el agente FastAPI también usa `8000` por defecto.

**Implicación**: `EventSource` **no puede enviar headers**. El cliente AG-UI debe usar `fetch()` + `ReadableStream` + parser SSE manual (que es lo que hace `HttpAgent` de `@ag-ui/client`), y adjuntar el Bearer manualmente porque el `authInterceptor` solo decora `HttpClient`.

---

### 2.6 APIs reales de las librerías (verificadas por introspección del venv)

Esto es lo que **realmente** soporta la versión instalada. Úsalo tal cual.

#### `create_deep_agent` — firma completa

```python
create_deep_agent(
    model: str | BaseChatModel | None = None,
    tools: Sequence[BaseTool | Callable | dict] | None = None,
    *,
    system_prompt: str | SystemMessage | None = None,
    middleware: Sequence[AgentMiddleware] = (),
    subagents: Sequence[SubAgent | CompiledSubAgent | AsyncSubAgent] | None = None,
    skills: list[str] | None = None,              # ← rutas de skills
    memory: list[str] | None = None,              # ← rutas de ficheros AGENTS.md
    permissions: list[FilesystemPermission] | None = None,
    backend: BackendProtocol | Callable[[ToolRuntime], BackendProtocol] | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    response_format: ... | None = None,
    state_schema: type[DeepAgentState] | None = None,
    context_schema: type[ContextT] | None = None,
    checkpointer: None | bool | BaseCheckpointSaver = None,   # ← memoria por sesión
    store: BaseStore | None = None,                            # ← memoria entre sesiones
    debug: bool = False,
    name: str | None = None,
    cache: BaseCache | None = None,
) -> CompiledStateGraph
```

#### Backends disponibles (`deepagents.backends`)

`StateBackend` · `StoreBackend` · `FilesystemBackend` · `CompositeBackend` · `LocalShellBackend` · `LangSmithSandbox` · `ContextHubBackend` · `BackendProtocol` · `NamespaceFactory`

```python
StoreBackend(
    runtime=None,                       # deprecated
    *,
    store: BaseStore | None = None,     # None ⇒ se obtiene con get_store() en runtime
    namespace: Callable[[Runtime], tuple[str, ...]] | None = None,   # wildcards prohibidos
    file_format: Literal["v1", "v2"] = "v2",
)

CompositeBackend(
    default: BackendProtocol | StateBackend,
    routes: dict[str, BackendProtocol],   # p.ej. {"/memories/": StoreBackend()}
    *,
    artifacts_root: str = "/",
)
```

#### `MemoryMiddleware`

```python
MemoryMiddleware(
    *,
    backend: BACKEND_TYPES,
    sources: list[str],                 # rutas a ficheros tipo AGENTS.md
    add_cache_control: bool = False,
    system_prompt: str | None = <plantilla larga por defecto>,
)
```
Carga el contenido de los ficheros y lo **inyecta en el system prompt** dentro de `<agent_memory>`. El agente puede persistir aprendizajes llamando a `edit_file`.

#### Hooks de middleware (`AgentMiddleware`)

`before_agent` · `after_agent` · `before_model` · `after_model` · `wrap_model_call` · `wrap_tool_call` (+ variantes `a*` async)

```python
def wrap_model_call(self, request: ModelRequest[ContextT],
                    handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse | AIMessage
```
`request.model` es reemplazable → **esto es el intent router Haiku/Sonnet**.

#### Contrato de salto de grafo (arregla B1)

```python
from langchain.agents.middleware.types import AgentState, JumpTo, hook_config
# AgentState.jump_to : NotRequired[Annotated[JumpTo | None, EphemeralValue, PrivateStateAttr]]
# JumpTo = Literal["tools", "model", "end"]

@hook_config(can_jump_to=["end"])
def after_model(self, state, runtime):
    ...
    return {"messages": [...], "jump_to": "end"}   # ← NO "goto"
```

#### `Runtime` y cómo llega el contexto (clave para JWT)

`Runtime` expone: `context`, `store`, `stream_writer`, `previous`, `server_info`, `execution_info`, `control`, `merge`, `override`.

En `ag_ui_langgraph.LangGraphAgent` (v0.0.42), líneas ~1601-1606:

```python
if isinstance(config, dict) and 'configurable' in config and isinstance(config['configurable'], dict):
    base_context.update(config['configurable'])
if context:
    base_context.update(context)
kwargs['context'] = base_context
```

➡️ **Todo lo que pongas en `config["configurable"]` acaba en `runtime.context`.** Ese es el canal para inyectar `user_id`, `role`, `email`.

```python
LangGraphAgent(*, name: str, graph: CompiledStateGraph, description: str | None = None,
               config: RunnableConfig | dict | None = None)
LangGraphAgent.clone() -> Self      # copia dict(self.config); no acepta argumentos
LangGraphAgent.run(input: RunAgentInput) -> AsyncGenerator[...]
```

#### `langmem` 0.0.30

```python
create_memory_store_manager(
    model, /, *, schemas=None, instructions=..., default=None, default_factory=None,
    enable_inserts=True, enable_deletes=False, query_model=None, query_limit=5,
    namespace: tuple[str, ...] = ("memories", "{langgraph_user_id}"),
    store: BaseStore | None = None, phases=None,
) -> MemoryStoreManager

create_manage_memory_tool(namespace, *, instructions=..., schema=str,
                          actions_permitted=("create","update","delete"),
                          store=None, name="manage_memory")

create_search_memory_tool(namespace, *, instructions="", store=None,
                          response_format="content", name="search_memory")

ReflectionExecutor(reflector, namespace=None, /, *, url=None, client=None,
                   sync_client=None, store=None)   # ejecución diferida en background
```

`NamespaceTemplate` resuelve `{var}` desde `config["configurable"]`:

```python
NamespaceTemplate(("spark-match", "{user_id}", "profile"))({"configurable": {"user_id": "u-123"}})
# → ("spark-match", "u-123", "profile")
```

#### Persistencia disponible / faltante

| Módulo | Estado |
|---|---|
| `langgraph.checkpoint.memory` (`InMemorySaver`) | ✅ instalado |
| `langgraph.store.memory` (`InMemoryStore`) | ✅ instalado |
| `langgraph.checkpoint.sqlite` | ❌ **falta** → `langgraph-checkpoint-sqlite` |
| `langgraph.checkpoint.postgres` | ❌ **falta** → `langgraph-checkpoint-postgres` |
| `langgraph.store.postgres` | ❌ **falta** (viene en el mismo paquete) |
| `psycopg` | ❌ **falta** |
| `boto3` | ✅ 1.43.40 |

> Nota: `PostgresStore` **solo requiere pgvector si configuras `index=`**. Sin índice vectorial funciona con búsqueda por namespace/key/filtros. Esto es compatible con ADR-008 (pgvector descartado).

#### Tavily

`AsyncTavilyClient` **existe** en `tavily-python` 0.7.26 (métodos: `search`, `qna_search`, `extract`, `crawl`, `map`, `research`, `get_search_context`, `close`). Arregla B7.

---

## 3. Arquitectura objetivo

```
┌─────────────────────────────────────────────────────────────────┐
│ Angular 22 (04-frontend)                                        │
│  AuthService.token()  →  localStorage 'spark-match:token'       │
│  AgentClient: fetch() + ReadableStream + parser SSE             │
│    headers: Authorization: Bearer <jwt>                         │
│    body: RunAgentInput { thread_id, messages, ... }             │
└───────────────────────────┬─────────────────────────────────────┘
                            │ POST /ag-ui  (SSE)
┌───────────────────────────▼─────────────────────────────────────┐
│ FastAPI (08-deep-agent)                                         │
│  ① Depends(require_auth) → valida HS256                         │
│       iss=spark-match-backend, aud=spark-match-api              │
│       clave: SecretString UTF-8 crudo (SSM → Secrets Manager)   │
│       → AuthContext { user_id, email, role }                    │
│  ② thread_id ownership guard  (thread ↔ user_id en el store)    │
│  ③ config = {"configurable": {                                  │
│         "thread_id":  <validado>,                               │
│         "user_id":    auth.user_id,                             │
│         "role":       auth.role,                                │
│         "email":      auth.email }}                             │
│  ④ LangGraphAgent(graph=..., config=config).run(input)          │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│ Deep Agent (LangGraph)                                          │
│                                                                 │
│  checkpointer  ── memoria POR SESIÓN (thread_id)                │
│      local: InMemorySaver | dev: SqliteSaver | prod: Postgres   │
│      + SummarizationMiddleware (ya activo) ≈ Harness SUMMARIZATION│
│                                                                 │
│  store  ── memoria ENTRE SESIONES (user_id)                     │
│      InMemoryStore | PostgresStore                              │
│      ns ("spark-match", user_id, "profile")   ≈ SEMANTIC        │
│      ns ("spark-match", user_id, "prefs")     ≈ USER_PREFERENCE │
│      ns ("spark-match", user_id, "episodes")  ≈ EPISODIC        │
│                                                                 │
│  backend = CompositeBackend(                                    │
│      default=StateBackend(),                    # scratchpad    │
│      routes={"/memories/": StoreBackend(namespace=by_user)})    │
│  memory=["/memories/AGENTS.md"]   → MemoryMiddleware            │
│  skills=["/skills/"]              → SkillsMiddleware            │
│                                                                 │
│  middleware (orden):                                            │
│    IntentRouterMiddleware   (wrap_model_call → Haiku|Sonnet)    │
│    ProfileHydrationMiddleware (before_agent: store → prompt)    │
│    MaxTurnsMiddleware        (jump_to="end")   [FIX B1]         │
│    AssessmentOnceMiddleware                                     │
│    GuardrailsMiddleware      (wrap_model_call: PII + prompt inj)│
│    ProfilePersistMiddleware  (after_agent → ReflectionExecutor) │
│                                                                 │
│  tools: evaluate_riasec_profile, search_careers,                │
│         calculate_affinity, web_search (async Tavily+DDG),      │
│         manage_memory, search_memory  (langmem)                 │
│         + MCP tools opcionales                                  │
│                                                                 │
│  subagents: assessment · matching · planning                    │
└─────────────────────────────────────────────────────────────────┘
```

**Perfiles de persistencia por entorno** (`SPARK_PERSISTENCE_BACKEND`):

| Valor | Checkpointer | Store | Uso |
|---|---|---|---|
| `memory` | `InMemorySaver` | `InMemoryStore` | tests, CI, evals mock |
| `sqlite` | `AsyncSqliteSaver` | `InMemoryStore` + dump JSON | **demo local del TFP sin AWS** |
| `postgres` | `AsyncPostgresSaver` | `AsyncPostgresStore` | dev/prod en AWS |

> Este diseño en 3 niveles es **requisito del TFP**: el evaluador debe poder hacer `uv sync && uv run python -m src` y ver memoria funcionando sin cuenta AWS (criterio §7.1 del documento de decisión: *"Runs locally without cloud: ✅"*).

---

## 4. Matriz de paridad vs AWS Bedrock AgentCore Harness

| # | Bondad del Harness (out of the box) | Equivalente Deep Agents | Sprint |
|---|---|---|---|
| 1 | Memory strategy **SEMANTIC** (perfil, 90d) | `store` + `langmem.create_memory_store_manager(schemas=[StudentProfile])`, ns `(spark-match, user_id, profile)` | 6 |
| 2 | Memory strategy **SUMMARIZATION** (30d) | `SummarizationMiddleware` (ya activo por defecto) + checkpointer | 6 |
| 3 | Memory strategy **USER_PREFERENCE** (365d) | `store` ns `(spark-match, user_id, prefs)` + `create_manage_memory_tool` | 6 |
| 4 | Memory strategy **EPISODIC** (24h) | `store` ns `(spark-match, user_id, episodes)` + job de expiración | 6 |
| 5 | **Skills on-demand** desde S3 | `skills=["/skills/"]` + `SkillsMiddleware`; fuente = repo (dev) o `StoreBackend`/S3 (prod) | 8 |
| 6 | **Gateway MCP** (11 Lambda targets) | `langchain-mcp-adapters` + servidor MCP local; tools siguen in-process | 8 |
| 7 | **Streaming SSE** nativo | Ya resuelto con `ag-ui-langgraph` + `sse-starlette` | ✅ |
| 8 | **JWT nativo** (Cognito) | `Depends(require_auth)` HS256 contra el contrato de `03-backend` | 7 |
| 9 | **Idempotencia** (`session_id` + `actor_id`) | `thread_id` + `user_id` con guard de ownership + `run_id` dedupe | 7 |
| 10 | **Intent router** Haiku/Sonnet (−26% latencia, −44% coste) | `IntentRouterMiddleware.wrap_model_call` swapping `request.model` | 8 |
| 11 | **Policy engine** (content filter, prompt-attack, PII) | `GuardrailsMiddleware` + opcional Bedrock Guardrails vía `langchain-aws` | 9 |
| 12 | **Evaluators** LLM-as-judge + Online Eval | `evals/` ampliado a ≥30 casos + judge multi-dimensión (Haiku) | 9 |
| 13 | **Observabilidad** CloudWatch built-in | LangSmith (ya) + EMF a namespace `spark-match-agent` + X-Ray | 11 |
| 14 | **ConfigBundle** versionado | Prompts/skills `.md` versionados en git + release-please | 10 |
| 15 | **Knowledge Base** Bedrock | `data/careers/*.md` (10 carreras) — suficiente para el scope; sin vectores | ✅ |
| 16 | Escalado/lifecycle gestionado | ECS Fargate o AgentCore Runtime + ALB | 10-11 |

**Sin equivalente y aceptado como fuera de scope**: Online Insights (failure-pattern analysis), Cedar policies custom, A/B testing declarativo, multi-region.

---

## 5. Plan por sprints

### Sprint 5 — Correcciones críticas y deuda técnica (16 h)

**Objetivo**: dejar la base sana antes de tocar arquitectura. Nada de lo que sigue funciona bien si B1/B6 siguen vivos.

| # | Tarea | Archivo(s) | Detalle |
|---|---|---|---|
| 5.1 | **Fix B1** — `MaxTurnsMiddleware` no detiene el grafo | `src/agent/middleware.py` | Ver snippet abajo. Test debe asertar sobre el **grafo compilado**, no sobre el dict. |
| 5.2 | **Fix B6** — model id fuera del allowlist IAM | `src/config/settings.py`, `.env.example` | `model_id` → `us.anthropic.claude-sonnet-4-5-20250929-v1:0`. Añadir `fast_model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"` (lo usará el router en S8). |
| 5.3 | **Fix B2/B3** — budget de web_search | `src/tools/web_search/handler.py` | `0` ⇒ ilimitado (`if cap > 0 and count >= cap`). Incrementar **una sola vez**, después de confirmar resultados no vacíos. |
| 5.4 | **Fix B4** — afinidad >100% | `src/tools/matching/handler.py` | Normalizar: `min(100.0, ...)` no basta, hay que corregir el denominador. El parche del PR #19 **no sirve**: se aplicó sobre `src/tools/matching.py`, un módulo muerto (ver `AGENTS.md` §13.1). Rehacer sobre `handler.py`. Test de propiedad: `0 <= score <= 100` para las 6³ combinaciones de códigos. |
| 5.5 | **Fix B5** — mojibake | `src/tools/{assessment,catalog,web_search}/handler.py` | `ArtÃƒÂ­stico` → `Artístico`, `TecnologÃƒÂ­a` → `Tecnología`, `Ã‚Â§` → `§`. Añadir a `.gitattributes`: `*.py text eol=lf working-tree-encoding=UTF-8`. |
| 5.6 | **Fix B8** — herramientas rotas | `pyproject.toml`, `Makefile` | Añadir `pytest-cov>=7.0` al grupo dev. `eval-dev`/`eval-test` → `uv run python -m evals.runner --mode {mock,live}`. `QA_FOLDERS := src/ tests/ evals/` (alinear con `ruff check .` de CI). |
| 5.7 | **Fix B9** — evals tautológicos | `evals/runner.py` | El modo mock debe llamar al **handler real** (`evaluate_riasec_handler`), no copiar `expected_riasec`. |
| 5.8 | **Fix B10** — version drift | `pyproject.toml` | `version = "0.3.0"`. Adoptar release-please en S10. |
| 5.9 | Tests faltantes de `web_search` | `tests/tools/web_search.py` (nuevo) | ≥8 tests: query vacía, `max_results` inválido, budget agotado, budget=0 ilimitado, Tavily OK, Tavily falla→DDG, ambos fallan, no doble incremento. |
| 5.10 | Smoke test de construcción del grafo | `tests/agent/factory.py` (nuevo) | Invocar `create_spark_agent()` de verdad y asertar nodos/tools/subagentes. Detecta roturas de contrato de middleware. |
| 5.11 | `__init__.py` faltantes | `tests/{agent,models,tools,utils}/` | Consistencia con `tests/catalog`, `tests/evals`. Evita colisión de basename. |
| 5.12 | Higiene de deps | `pyproject.toml` | Poner cotas superiores al cluster LangChain: `deepagents>=0.6.12,<0.7`, `langchain>=1.3,<2`, `langgraph>=1.2,<2`. `deepagents` 0.7.0 **elimina** el `runtime=` de `StoreBackend`. |

**Snippet 5.1 — fix verificado:**

```python
# src/agent/middleware.py
from langchain.agents.middleware.types import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime


class MaxTurnsMiddleware(AgentMiddleware):
    """Corta el agente al alcanzar settings.max_turns."""

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        settings = get_settings()
        ai_turns = sum(1 for m in state["messages"] if isinstance(m, AIMessage))
        if ai_turns < settings.max_turns:
            return None
        logger.warning("max_turns alcanzado", extra={"session": _get_session_id()})
        return {
            "messages": [AIMessage(content=TURN_LIMIT_MESSAGE)],
            "jump_to": "end",          # ← contrato real de LangChain 1.x
        }
```

**Test que debe acompañarlo** (el actual da falsa confianza):

```python
async def test_max_turns_actually_stops_the_graph(monkeypatch):
    monkeypatch.setenv("SPARK_MAX_TURNS", "2")
    get_settings.cache_clear()
    agent = create_spark_agent()
    result = await agent.ainvoke(
        {"messages": [HumanMessage("...")]},
        config={"configurable": {"thread_id": "t1"}},
    )
    assert sum(isinstance(m, AIMessage) for m in result["messages"]) <= 3
```

**DoD Sprint 5** — cerrado 2026-08-04 (PR #25, #26, #27 a `dev`)
- [x] 10 bugs B1–B10 cerrados, cada uno con test de regresión.
- [x] `make qa && make test` verde. Coverage ≥ 75% líneas (gate nuevo) — 79%.
- [x] `create_spark_agent()` se invoca en al menos 1 test (`tests/agent/factory.py`).
- [x] Cero mojibake: `rg -n "Ã" src/` devuelve 0.
- [x] `SPARK_MODEL_ID` por defecto está en el allowlist IAM.

Bug adicional no catalogado, encontrado al escribir el smoke test del grafo
(PR #25): `AssessmentOnceMiddleware` solo implementaba el hook síncrono
`wrap_tool_call`. La API real (`ag-ui-langgraph`) invoca el grafo
exclusivamente vía `astream_events` — sin `awrap_tool_call`, **toda** llamada
a herramienta en producción habría fallado con `NotImplementedError`. Cerrado
en el mismo PR que B1.

---


### Sprint 6 — Memoria persistente (28 h) ⭐ núcleo del pedido

**Objetivo**: memoria por sesión y entre sesiones, con paridad funcional con las 4 memory strategies del Harness, y con 3 perfiles de persistencia.

#### 6.A — Capa de persistencia conmutable

**Nuevo módulo `src/persistence/__init__.py`**, `src/persistence/factory.py`:

```python
# src/persistence/factory.py
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from src.config.settings import PersistenceBackend, get_settings


@dataclass(slots=True)
class Persistence:
    checkpointer: BaseCheckpointSaver
    store: BaseStore


@contextlib.asynccontextmanager
async def build_persistence() -> AsyncIterator[Persistence]:
    """Construye checkpointer + store según SPARK_PERSISTENCE_BACKEND.

    Se usa como async context manager desde el lifespan de FastAPI para que
    los pools de conexión se cierren correctamente.
    """
    settings = get_settings()

    if settings.persistence_backend is PersistenceBackend.MEMORY:
        yield Persistence(checkpointer=InMemorySaver(), store=InMemoryStore())
        return

    if settings.persistence_backend is PersistenceBackend.SQLITE:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        async with AsyncSqliteSaver.from_conn_string(settings.sqlite_path) as saver:
            yield Persistence(checkpointer=saver, store=InMemoryStore())
        return

    # PersistenceBackend.POSTGRES
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres.aio import AsyncPostgresStore

    dsn = await resolve_postgres_dsn()          # Secrets Manager, ver 6.A.3
    async with (
        AsyncPostgresSaver.from_conn_string(dsn) as saver,
        AsyncPostgresStore.from_conn_string(dsn) as store,   # sin index= ⇒ sin pgvector
    ):
        await saver.setup()
        await store.setup()
        yield Persistence(checkpointer=saver, store=store)
```

| # | Tarea | Detalle |
|---|---|---|
| 6.A.1 | `PersistenceBackend` StrEnum en `settings.py` | Valores `memory` / `sqlite` / `postgres`. Default `sqlite` en `local`, `postgres` en `agentcore`. |
| 6.A.2 | Settings nuevos | `persistence_backend`, `sqlite_path: Path = Path(".spark-match/checkpoints.sqlite")`, `postgres_secret_arn: str \| None`, `postgres_dsn: SecretStr \| None` (override local). |
| 6.A.3 | `src/persistence/secrets.py` | Resuelve el DSN: SSM `/spark-match/agent/db-secret-arn` → Secrets Manager → JSON `{host,port,database,username,password}` → DSN. Cachear (5 min) igual que hace el backend TS. |
| 6.A.4 | Cablear en el lifespan | `src/api/app.py`: el `build_persistence()` envuelve la creación del grafo. |

#### 6.B — Backend de ficheros compuesto (memoria auto-gestionada por el agente)

```python
# src/agent/backends.py
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from langgraph.runtime import Runtime

MEMORY_ROOT = "/memories/"
SKILLS_ROOT = "/skills/"


def _user_namespace(runtime: Runtime) -> tuple[str, ...]:
    """Aísla los ficheros de memoria por usuario. Wildcards prohibidos."""
    ctx = runtime.context or {}
    user_id = (ctx.get("user_id") if isinstance(ctx, dict) else getattr(ctx, "user_id", None))
    if not user_id:
        raise ValueError("user_id ausente en el runtime context: revisa require_auth")
    return ("spark-match", str(user_id), "files")


def build_backend() -> CompositeBackend:
    return CompositeBackend(
        default=StateBackend(),                      # scratchpad efímero del run
        routes={MEMORY_ROOT: StoreBackend(namespace=_user_namespace)},
    )
```

Con esto, `read_file`/`write_file`/`edit_file` sobre `/memories/...` van al **store persistente por usuario**, y todo lo demás sigue siendo efímero.

#### 6.C — `MemoryMiddleware` (equivalente a AGENTS.md del Harness)

```python
# en factory.py
agent = create_deep_agent(
    ...,
    backend=build_backend(),
    memory=["/memories/AGENTS.md"],   # → MemoryMiddleware lo inyecta en el system prompt
    checkpointer=persistence.checkpointer,
    store=persistence.store,
)
```

Hay que **sembrar** `/memories/AGENTS.md` la primera vez que un usuario entra (middleware `before_agent`), con una plantilla en `src/prompts/user_memory_seed.md`.

#### 6.D — Extracción estructurada del `StudentProfile` (≈ SEMANTIC)

Resucitar `src/memory/profile_manager.py` (hoy código muerto) usando `create_memory_store_manager`, no `create_memory_manager`:

```python
# src/memory/profile_manager.py
from langmem import ReflectionExecutor, create_memory_store_manager

from src.config.settings import get_settings
from src.models.profile import StudentProfile

PROFILE_NAMESPACE = ("spark-match", "{user_id}", "profile")


def build_profile_manager(store):
    settings = get_settings()
    return create_memory_store_manager(
        settings.fast_model_string,          # Haiku: barato, tarea estructurada
        schemas=[StudentProfile],
        instructions=EXTRACTION_INSTRUCTIONS,
        namespace=PROFILE_NAMESPACE,         # {user_id} ← config["configurable"]["user_id"]
        enable_inserts=False,                # un único perfil por usuario
        store=store,
    )


def build_reflection_executor(store):
    """Ejecuta la extracción en background para no penalizar la latencia del turno."""
    return ReflectionExecutor(build_profile_manager(store), store=store)
```

Middleware que lo dispara:

```python
# src/agent/memory_middleware.py
class ProfilePersistMiddleware(AgentMiddleware):
    """after_agent: encola extracción diferida del StudentProfile."""

    def __init__(self, executor):
        self._executor = executor

    def after_agent(self, state, runtime):
        user_id = _user_id_from(runtime)
        self._executor.submit(
            {"messages": state["messages"]},
            config={"configurable": {"user_id": user_id}},
            after_seconds=settings.reflection_delay_seconds,   # p.ej. 30
        )
        return None
```

Y el hidratador que inyecta el perfil ya conocido al inicio del turno:

```python
class ProfileHydrationMiddleware(AgentMiddleware):
    """before_agent: lee el perfil del store y lo mete como SystemMessage."""

    def before_agent(self, state, runtime):
        store = runtime.store
        if store is None:
            return None
        ns = ("spark-match", _user_id_from(runtime), "profile")
        items = store.search(ns, limit=1)
        if not items:
            return None
        profile = items[0].value
        return {"messages": [SystemMessage(content=render_profile_block(profile))]}
```

#### 6.E — Preferencias y episodios (≈ USER_PREFERENCE / EPISODIC)

| Namespace | Contenido | TTL objetivo | Mecanismo |
|---|---|---|---|
| `("spark-match", uid, "profile")` | `StudentProfile` (RIASEC, intereses) | 90 d | langmem store manager |
| `("spark-match", uid, "prefs")` | idioma, formato de respuesta, tono | 365 d | `create_manage_memory_tool` expuesto al agente |
| `("spark-match", uid, "episodes")` | resumen de cada sesión + `thread_id` | 24 h – 30 d | escritura en `after_agent`; limpieza por job |
| `("spark-match", uid, "files")` | `/memories/*` del `StoreBackend` | ∞ | `edit_file` del agente |

Tools de memoria a añadir al agente:

```python
from langmem import create_manage_memory_tool, create_search_memory_tool

manage_prefs = create_manage_memory_tool(
    namespace=("spark-match", "{user_id}", "prefs"),
    actions_permitted=("create", "update"),      # sin delete, evita borrado accidental
)
search_memory = create_search_memory_tool(namespace=("spark-match", "{user_id}", "prefs"))
```

Para el TTL de `episodes`: LangGraph `BaseStore` no expira solo. Implementar `src/memory/janitor.py` con un task de FastAPI (`asyncio` periódico) o un comando `python -m src.memory.janitor` invocable desde un cron/EventBridge.

#### 6.F — Cambios en `factory.py`

`create_spark_agent()` deja de ser sin argumentos:

```python
def create_spark_agent(persistence: Persistence) -> CompiledStateGraph[Any, Any, Any, Any]:
    settings = get_settings()
    backend = build_backend()
    executor = build_reflection_executor(persistence.store)

    return create_deep_agent(
        model=settings.model_string,
        tools=[
            evaluate_riasec_profile, search_careers, calculate_affinity,
            web_search, manage_prefs, search_memory,
        ],
        subagents=[ASSESSMENT_SUBAGENT, MATCHING_SUBAGENT, PLANNING_SUBAGENT],
        system_prompt=SYSTEM_PROMPT,
        name=settings.agent_name,
        backend=backend,
        memory=["/memories/AGENTS.md"],
        context_schema=AgentContext,                     # ver Sprint 7
        checkpointer=persistence.checkpointer,
        store=persistence.store,
        middleware=[
            ProfileHydrationMiddleware(),
            MaxTurnsMiddleware(),
            AssessmentOnceMiddleware(),
            ProfilePersistMiddleware(executor),
        ],
    )
```

#### 6.G — Propagar `thread_id` de verdad

Hoy `src/api/app.py` extrae `input_data.thread_id` **solo para el contador de budget**; nunca llega al grafo. `LangGraphAgent` sí lo inyecta en `config["configurable"]["thread_id"]` (línea ~82), pero sin checkpointer no servía de nada. Con checkpointer ya funciona; **verificar con un test de dos turnos** que el segundo turno ve el primero.

**DoD Sprint 6** — cerrado 2026-08-04 (PRs #31, #32, #33)
- [x] Test: dos requests HTTP consecutivos con el mismo `thread_id` → el agente recuerda el nombre del estudiante del turno 1 (`TestCheckpointerPersistsConversationAcrossInvocations`, PR #32).
- [x] Test: el perfil persiste independientemente del `thread_id` — `ProfileHydrationMiddleware` lee del namespace `("spark-match", user_id, "profile")`, que no incluye `thread_id` (`tests/agent/memory_middleware.py`, PR #33). La extracción real vía langmem no se re-testea (delegada a la librería); se testea que la lectura funciona con cualquier perfil ya presente en el store.
- [ ] ~~Test: dos `user_id` distintos → aislamiento total~~ — **diferido a Sprint 7 explícitamente**. Hoy `user_id` es un placeholder fijo (`src/agent/user_context.DEFAULT_USER_ID`) hasta que el JWT real esté disponible; un test de aislamiento hoy solo probaría que un placeholder es igual a sí mismo, no aislamiento real. El namespacing SÍ existe estructuralmente (partición por `user_id` en todos los namespaces), listo para recibir el valor real sin cambios de forma.
- [x] `SPARK_PERSISTENCE_BACKEND=memory|sqlite` funcionan **sin AWS** (PR #31). `postgres` sigue sin implementar (tarea 6.A.3, DSN vía Secrets Manager) — no bloqueante para el TFP (hard rule #7).
- [x] `src/memory/profile_manager.py` deja de ser código muerto; `build_profile_manager`/`build_reflection_executor` tienen test (`tests/memory/profile_manager.py`, PR #33).
- [x] `langmem` deja de ser dependencia fantasma — usado en `profile_manager.py`, `memory_middleware.py` y las tools `manage_prefs`/`search_memory` en `factory.py`.
- [x] README actualizado: fila de persistencia añadida, claim de `langmem` corregido a "activo desde Sprint 6".

---

### Sprint 7 — Autenticación JWT, roles y aislamiento (24 h) ⭐ núcleo del pedido

**Objetivo**: que el agente reciba y valide el JWT emitido por `03-backend`, derive `user_id`/`role`, y que **toda** la memoria quede particionada por usuario.

#### 7.A — Validación del token

**Nuevo `src/auth/` con `jwt_validator.py`, `dependencies.py`, `context.py`, `secret_loader.py`.**

Dependencia nueva: `pyjwt[crypto]>=2.10` (o `python-jose`). Recomendado **PyJWT** por peso y por soporte directo de `issuer`/`audience`.

```python
# src/auth/jwt_validator.py
import jwt
from jwt import InvalidTokenError

JWT_ISSUER = "spark-match-backend"     # NO cambiar: contrato de 03-backend
JWT_AUDIENCE = "spark-match-api"
JWT_ALGORITHM = "HS256"


def decode_token(token: str, secret: bytes) -> dict[str, Any]:
    """Valida un JWT emitido por spark-match-03-backend.

    La clave son los bytes UTF-8 CRUDOS del SecretString de Secrets Manager.
    No hacer base64-decode ni json.loads: rompería la verificación de firma.
    """
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={"require": ["exp", "iat", "sub"]},
        )
    except InvalidTokenError as exc:
        raise AuthError("Invalid or expired token") from exc
```

```python
# src/auth/secret_loader.py
import boto3
from cachetools import TTLCache          # o implementación propia

SSM_JWT_ARN_PARAM = "/spark-match/secret/jwt-arn"   # mismo path que usa 03-backend

async def load_jwt_secret() -> bytes:
    """SSM → ARN → Secrets Manager → bytes UTF-8 crudos. Cache 5 min."""
    if cached := _CACHE.get("jwt"):
        return cached
    if override := get_settings().jwt_secret:        # override local/dev
        secret = override.get_secret_value().encode("utf-8")
    else:
        ssm = boto3.client("ssm", region_name=get_settings().aws_region)
        arn = ssm.get_parameter(Name=SSM_JWT_ARN_PARAM, WithDecryption=True)["Parameter"]["Value"]
        sm = boto3.client("secretsmanager", region_name=get_settings().aws_region)
        secret = sm.get_secret_value(SecretId=arn)["SecretString"].encode("utf-8")
    _CACHE["jwt"] = secret
    return secret
```

```python
# src/auth/context.py
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class AuthContext:
    user_id: str          # claim `sub`
    email: str            # claim `email`, "" si ausente
    role: str             # claim `role`, "" si ausente

@dataclass(frozen=True, slots=True)
class AgentContext:
    """context_schema del grafo. Llega a runtime.context."""
    user_id: str
    role: str
    email: str = ""
    thread_id: str = ""
```

```python
# src/auth/dependencies.py
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


async def require_auth(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthContext:
    # 1) Contexto del Lambda Authorizer de API Gateway (si estamos detrás de él)
    if lambda_ctx := _authorizer_context(request):
        return AuthContext(**lambda_ctx)

    # 2) Bearer directo
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid authentication")

    claims = decode_token(creds.credentials, await load_jwt_secret())
    return AuthContext(
        user_id=claims["sub"],
        email=claims.get("email") if isinstance(claims.get("email"), str) else "",
        role=claims.get("role") if isinstance(claims.get("role"), str) else "",
    )
```

> Nota: el backend TS **confía ciegamente** en `requestContext.authorizer.lambda` sin re-verificar la firma. Replicamos esa ruta solo si el agente se despliega detrás del mismo API Gateway; en despliegue directo (ECS + ALB) la ruta 2 es la única.

#### 7.B — Guard de propiedad de `thread_id`

Hoy `thread_id` es un string libre del cliente → cualquiera puede leer la conversación de otro. Hay que ligar thread ↔ usuario:

```python
# src/auth/thread_guard.py
THREAD_OWNER_NS = ("spark-match", "_threads")

async def assert_thread_ownership(store: BaseStore, thread_id: str, user_id: str) -> None:
    item = await store.aget(THREAD_OWNER_NS, thread_id)
    if item is None:
        await store.aput(THREAD_OWNER_NS, thread_id, {"user_id": user_id, "created_at": _now()})
        return
    if item.value.get("user_id") != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Thread does not belong to the caller")
```

Alternativa más barata y sin lookup: **derivar** el `thread_id` efectivo como `sha256(f"{user_id}:{client_thread_id}")`. Recomendado hacer **ambos** (derivación + registro), porque la derivación no permite auditar.

#### 7.C — Inyectar el contexto en el grafo

```python
# src/api/app.py  (endpoint /ag-ui)
@app.post(AG_UI_PATH)
async def ag_ui_endpoint(
    input_data: RunAgentInput,
    request: Request,
    auth: AuthContext = Depends(require_auth),
) -> StreamingResponse:
    store = request.app.state.persistence.store
    thread_id = derive_thread_id(auth.user_id, input_data.thread_id)
    await assert_thread_ownership(store, thread_id, auth.user_id)
    input_data.thread_id = thread_id

    set_active_session(thread_id)
    reset_session_budget(thread_id)

    request_agent = request.app.state.langgraph_agent.clone()
    # clone() copia dict(self.config); mutamos la copia para este request
    request_agent.config = {
        "configurable": {
            "thread_id": thread_id,
            "user_id": auth.user_id,      # ← langmem NamespaceTemplate {user_id}
            "role": auth.role,            # ← autorización en tools/middleware
            "email": auth.email,
        }
    }

    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def event_generator():
        async for event in request_agent.run(input_data):
            yield encoder.encode(event)

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())
```

**Por qué funciona**: `ag_ui_langgraph` v0.0.42 hace `base_context.update(config['configurable'])` y lo pasa como `context=` al grafo (líneas ~1601-1606). Por tanto `runtime.context.user_id` estará disponible en todos los middlewares, tools y en el `NamespaceFactory` del `StoreBackend`.

#### 7.D — Autorización por rol

**Realidad**: hoy `role` solo puede valer `"admin"`. Diseñar preparado para `docente`/`graduado` sin bloquearse:

```python
# src/auth/roles.py
class Role(StrEnum):
    ADMIN = "admin"
    DOCENTE = "docente"       # planificado en 03-backend migrations/003
    GRADUADO = "graduado"     # planificado
    STUDENT = "student"       # NO existe aún en el backend

# Fallback explícito y documentado mientras el backend no emita roles reales
DEFAULT_ROLE = Role.STUDENT

CAPABILITIES: dict[Role, frozenset[str]] = {
    Role.STUDENT:  frozenset({"assessment", "matching", "planning", "web_search"}),
    Role.DOCENTE:  frozenset({"assessment", "matching", "planning", "web_search", "view_cohort"}),
    Role.GRADUADO: frozenset({"matching", "planning", "web_search"}),
    Role.ADMIN:    frozenset({"*"}),
}
```

Aplicarlo con un `wrap_tool_call` que rechace tools fuera de capability, y con `interrupt_on` para acciones sensibles si se decide añadir human-in-the-loop.

#### 7.E — Endurecimiento del API

| # | Tarea | Detalle |
|---|---|---|
| 7.E.1 | CORS | Prohibir `["*"]` cuando `allow_credentials=True`. Validador Pydantic que falle al arrancar. |
| 7.E.2 | Cabeceras de seguridad | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` (paridad con el backend TS). |
| 7.E.3 | Rate limiting | `slowapi` o middleware propio: 5 req/min por `user_id` en `/ag-ui`. El backend usa 5.0/10 en login. |
| 7.E.4 | Budget por usuario, no por proceso | `src/budget.py` usa `dict` + `ContextVar` en memoria → se rompe con `--workers > 1`. Mover el contador al `store`, ns `("spark-match", uid, "budget")`. |
| 7.E.5 | Tests del API | `TestClient`: 401 sin token, 401 token expirado, 401 firma mala, 403 thread ajeno, 200 feliz, y que `/health` siga público. |

**DoD Sprint 7** — núcleo (7.A–7.D) cerrado 2026-08-04; 7.E cerrado 2026-08-04
- [x] `POST /ag-ui` devuelve 401 sin `Authorization` (`tests/api/app.py::TestAgUiRequiresAuth`).
- [x] Un JWT firmado con `iss/aud` correctos y la clave del backend valida OK (test con clave sintética, `tests/auth/jwt_validator.py` + `tests/api/app.py::TestAgUiHappyPath`).
- [x] `user_a` no puede leer el `thread_id` de `user_b` (403) — probado a nivel unitario en `tests/auth/thread_guard.py::test_different_owner_is_rejected`. No se reproduce en el stack HTTP completo porque `derive_thread_id` (7.B) ya evita la colisión por construcción: dos usuarios nunca aterrizan en el mismo `thread_id` derivado, así que un 403 real requeriría forzar deliberadamente esa colisión.
- [x] `runtime.context.user_id` está disponible dentro de una tool (test) — `tests/agent/memory_middleware.py` (Sprint 6) ya prueba que los middlewares leen `runtime.context.user_id`; `tests/api/app.py::test_thread_owner_is_registered_under_the_real_user_id` prueba que ese valor es el `user_id` real del JWT end-to-end, no el placeholder `DEFAULT_USER_ID`.
- [x] Namespaces del store contienen `user_id` real, verificado inspeccionando el store (`tests/api/app.py`).
- [x] `src/api/app.py` pasa de 0% a ≥80% de cobertura (99% con `tests/api/app.py`).
- [x] Documentado en `docs/auth.md` el contrato exacto y la limitación de roles.
- [x] 7.E.1 CORS validator: `Settings._validate_cors_origins` rechaza `"*"` y orígenes sin esquema al arrancar (`tests/config/settings.py::TestCorsOriginsValidation`).
- [x] 7.E.2 Cabeceras de seguridad: `SecurityHeadersMiddleware` añade `X-Content-Type-Options`/`X-Frame-Options`/`Referrer-Policy` a toda respuesta, incluidos errores (`tests/api/app.py::TestSecurityHeaders`).
- [x] 7.E.3 Rate limiting: `slowapi`, 5 req/min por `user_id` (fallback IP) en `/ag-ui`, configurable vía `SPARK_RATE_LIMIT_PER_MINUTE` (`tests/api/app.py::TestRateLimit`).
- [x] 7.E.4 Budget por usuario en el store: `src/auth/budget.py::check_and_increment_daily_budget`, namespace `("spark-match", user_id, "budget")`, `SPARK_BUDGET_MAX_REQUESTS_PER_USER_PER_DAY` (`tests/auth/budget.py`). Distinto y complementario al cupo de `web_search` por turno (`src/budget.py`), que sigue en proceso hasta la migración async de Sprint 8 (tarea 8.1) — documentado explícitamente en `docs/auth.md` §7/§8.4.

---

### Sprint 8 — Tools, web_search, MCP y router de intención (24 h)

| # | Tarea | Archivo | Detalle |
|---|---|---|---|
| 8.1 | `web_search` asíncrono | `src/tools/web_search/handler.py` | Migrar a `AsyncTavilyClient` (existe en 0.7.26). DDG sigue síncrono → envolver en `asyncio.to_thread`. Elimina el bloqueo del event loop (B7). |
| 8.2 | Errores tipados en web_search | ídem | Distinguir 401 (API key), 429 (rate limit), timeout y red. Hoy un `except Exception` los iguala. Solo hacer fallback a DDG en 429/timeout/red, **no** en 401. |
| 8.3 | Activar skills | `src/agent/factory.py` | `skills=["/skills/"]` + montar `skills/` en el backend. En local con `FilesystemBackend` root=repo; en prod, sincronizar a `StoreBackend` (equivalente a S3 del Harness). Hoy `skills/vocational_advisor/SKILL.md` es peso muerto. |
| 8.4 | **Intent router Haiku/Sonnet** | `src/agent/router_middleware.py` (nuevo) | Portar el patrón V8 del POC v2: **−26% latencia, −44% coste** con 38% de cobertura Haiku. |
| 8.5 | MCP | `src/mcp/` (nuevo) + `.mcp.json` | `langchain-mcp-adapters`: exponer las 4 tools como servidor MCP y/o consumir tools externas. Cierra §5.4 de `IMPROVEMENTS.md`, pendiente desde Sprint 1. |
| 8.6 | `maxTokens` configurable | `settings.py` | Lección 9 del POC v2: `max_tokens=2048` reduce latencia en turnos de generación de plan. |
| 8.7 | Catálogo de carreras | `data/careers/` | Subir de 10 a ≥20 carreras. Es contenido, no código: bajo coste, alto impacto en los evals. |

**Snippet 8.4 — router:**

```python
# src/agent/router_middleware.py
_FAST_INTENTS = frozenset({"greeting", "chitchat", "assessment_answer", "clarification"})


class IntentRouterMiddleware(AgentMiddleware):
    """Enruta turnos simples a Haiku y turnos complejos a Sonnet."""

    def __init__(self, fast_model: BaseChatModel, strong_model: BaseChatModel) -> None:
        self._fast = fast_model
        self._strong = strong_model

    def wrap_model_call(self, request, handler):
        intent = classify_intent(request.messages)     # heurístico, sin LLM extra
        request.model = self._fast if intent in _FAST_INTENTS else self._strong
        _emit_metric("intent_route", intent, model=request.model.model_id)
        return handler(request)
```

Clasificar con heurísticas (longitud, presencia de tool_calls previas, keywords) **antes** de considerar un LLM clasificador: el POC v2 alcanzó 38% de cobertura Haiku sin coste adicional de clasificación.

**DoD Sprint 8**
- [x] `web_search` no bloquea el event loop (test con `asyncio` concurrente): `AsyncTavilyClient` + `asyncio.to_thread` para DDG (`tests/tools/web_search.py::TestWebSearchHandlerDoesNotBlockEventLoop`).
- [x] Fallback a DDG **no** se dispara con 401 de Tavily: `InvalidAPIKeyError`/`MissingAPIKeyError` retornan error de inmediato; 429/timeout/red siguen cayendo a DDG (`tests/tools/web_search.py::TestWebSearchHandlerTypedTavilyErrors`).
- [x] `SkillsMiddleware` está en el stack (assert sobre `agent.nodes` o sobre el system prompt renderizado): nodo presente y contenido real del skill confirmado en el system message (`tests/agent/factory.py::TestAgentGraphStructure::test_skills_middleware_is_wired_into_the_graph`, `TestSkillsAreLoadedIntoTheSystemPrompt`).
- [x] Router activo con métrica `intent_route` emitida; ≥30% de turnos por Haiku en el dataset de evals: heurística en `src/agent/intent.py` (longitud + keywords narrativos + saludo/chitchat), `IntentRouterMiddleware` en `src/agent/router_middleware.py` loguea `intent_route intent=... model=...`. Cobertura real medida contra `evals/dataset.jsonl` (no un corpus sintético aparte): 31.6% (6/19 turnos) — `tests/agent/intent.py::TestFastIntentCoverageOnEvalDataset`. Enrutamiento real (no solo presencia de nodo, que no aplica a middleware `wrap_model_call`-only) probado en `tests/agent/factory.py::TestIntentRouterSelectsTheModelPerTurn`.
- [x] `.mcp.json` presente y documentado: servidor MCP en `src/mcp/server.py` (`MCPServer` del SDK oficial `mcp`, no `langchain-mcp-adapters` — esa librería es cliente, no servidor; ver `docs/mcp.md` §2 para la corrección). Registra los 4 handlers puros como MCP tools (mismo delegador fino que `src/tools/*/tool.py` usa para LangChain). Alcance: solo exposición, no consumo de servidores externos (decisión confirmada). Documentado en `docs/mcp.md`. Tests: `tests/mcp/server.py` (9 casos, vía `list_tools()`/`call_tool()` reales).
- [x] ≥20 carreras en `data/careers/`: 10 nuevas (`law`, `nursing`, `accounting`, `journalism`, `biology`, `music`, `agronomy`, `tourism`, `physics`, `veterinary`) sumadas a las 10 existentes — 20 total, todas con `id`/`riasec_profile` únicos. Contenido puro, sin cambios de código (`data/careers/README.md`). Guarda de regresión: `tests/tools/catalog.py::TestCareerCatalogSize`.

---

### Sprint 9 — Guardrails y evaluación (20 h)

**Objetivo**: replicar el *Policy engine* y los *Evaluators* del Harness.

#### 9.A — Guardrails (≈ Policy engine)

| # | Guardrail | Implementación |
|---|---|---|
| 9.A.1 | **Prompt injection / jailbreak** | `GuardrailsMiddleware.wrap_model_call`: heurísticas + lista de patrones sobre el último `HumanMessage`. Si dispara → respuesta canónica y `jump_to="end"`. |
| 9.A.2 | **Redacción de PII** | Regex de email/teléfono/DNI sobre lo que se persiste en el store. **Nunca** guardar credenciales (el system prompt de `MemoryMiddleware` ya lo exige). |
| 9.A.3 | **Filtro de contenido** | Opción A (portable): clasificador Haiku sobre input/output. Opción B (AWS): **Bedrock Guardrails** vía `langchain-aws` — requiere `bedrock:ApplyGuardrail` en IAM (no está hoy, ver §6.2). |
| 9.A.4 | **Scope guard** | El agente debe rechazar temas fuera de orientación vocacional. Ya hay 2 casos en `evals/dataset.jsonl` (`off_topic_chitchat`, `out_of_scope_finance`) — convertirlos en assertions duras. |
| 9.A.5 | **LANGUAGE RULE explícita** | Lección 5 del POC v2: el skill debe fijar el idioma para que no lo pise el system prompt. El POC midió **+46% en language match** por esto. |

#### 9.B — Evals

| # | Tarea | Detalle |
|---|---|---|
| 9.B.1 | Dataset ≥30 casos | Hoy 10. Añadir: memoria cross-session (2 turnos, 2 threads), auth negativa, guardrails, budget agotado, perfil ambiguo, código RIASEC empatado. |
| 9.B.2 | Judge multi-dimensión | Hoy es binario (`score: 1.0\|0.0`). Portar el rúbrico del POC v2: `riasec_accuracy` 0.4, `career_relevance` 0.3, `tone` 0.2, `safety` 0.1, `passingScore` 0.7. Modelo: **Haiku 4.5** (lección 4: 10× más barato, misma calidad). |
| 9.B.3 | Arreglar mock mode | Ya cubierto en 5.7; aquí se valida que el mock **falla** cuando se rompe un handler a propósito. |
| 9.B.4 | Métricas de referencia | Registrar P50/P90/coste/turno para comparar contra POC v2 (P50 8.5s, P90 25.5s, $0.0043/turno, helpfulness 4.42/5). Va en `docs/benchmarks.md`. |
| 9.B.5 | `RubricMiddleware` | `deepagents` expone `RubricMiddleware` — evaluar si sustituye parte del judge propio. |

**DoD Sprint 9**
- [x] ≥30 casos en `evals/dataset.jsonl`; ≥5 de memoria y ≥4 de guardrails. *(PR #48: 30 casos totales, 5 memoria, 5 guardrails)*
- [x] Judge multi-dimensión con score ponderado y umbral 0.7. *(PR #49: rubric POC v2 `riasec_accuracy 0.4, career_relevance 0.3, tone 0.2, safety 0.1`, passingScore=0.7, modelo Haiku 4.5 allowlist IAM)*
- [x] `--mode mock` en CI **detecta** una regresión inyectada a propósito (test del test). *(PR #50: 3 tests con monkeypatch sobre los handlers reales; tambien cerro un bug real: la heuristica de matching no verificaba `expected_careers_count`)*
- [x] `docs/benchmarks.md` con la comparativa Deep Agents vs POC v1 vs POC v2. *(PR #51: cifras POC verbatim, Deep Agent marcado "Pendiente -- Sprint 11" honestamente)*
- [x] Guardrails con tests: 5 prompts de inyección bloqueados, 0 falsos positivos en los 30 casos legítimos. *(ampliado por PR #48: 25 casos legitimos no son flaggeados por `tests/agent/guardrails.py::test_no_eval_dataset_user_message_triggers_the_guardrail`)*

> **Sprint 9 cerrado 2026-08-04.** 10 PRs individuales (#43, #44, #45, #46, #47, #48, #49, #50, #51, #52), 352 tests pytest passing, 30/30 evals `--mode mock` passing, gate completo verde. Validacion empirica del RubricMiddleware (subir JudgeScore via self-evaluation in-loop) queda para Sprint 11 -- ver `docs/rubric-middleware-evaluation.md` SS6.

---

### Sprint 10 — Contenedor, CI/CD e infraestructura (24 h)

#### 10.A — Contenedor

`.dockerignore` ya existe (122 líneas, bien escrito) y describe exactamente el build esperado. **Falta el `Dockerfile`.**

```dockerfile
# Dockerfile — multi-stage, uv, Python 3.14, non-root
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
COPY skills/ ./skills/
COPY data/ ./data/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.14-slim-bookworm AS runtime
RUN groupadd --gid 1001 spark && useradd --uid 1001 --gid spark --create-home spark
WORKDIR /app
COPY --from=builder --chown=spark:spark /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER spark
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"
CMD ["python", "-m", "src"]
```

> **Puerto 8080, no 8000**: el frontend ya reserva `localhost:8000` para el backend (§2.5). Cambiar `api_port` por defecto y documentarlo.
> `.dockerignore` referencia `DEPLOYMENT.md` y `RESUMEN_PROYECTO.md`, que **no existen** en el checkout — limpiar o crearlos.

#### 10.B — CI del repo

`.github/workflows/ci.yml` actual: pytest → evals mock → ruff check → ruff format → mypy → build. Faltan: coverage gate, security scan, container build, deploy, `workflow_dispatch`.

Migrar a **consumir el catálogo** de `01-devops` (§6.1 crea lo que falta):

```yaml
name: ci
on:
  pull_request: { branches: [main, dev] }
  push:         { branches: [main, dev] }
  workflow_dispatch:
concurrency: { group: ci-${{ github.ref }}, cancel-in-progress: true }

jobs:
  python-ci:
    uses: spark-match/spark-match-01-devops/.github/workflows/reusable-python-ci.yml@main
    with:
      environment-name: ci
      python-version: '3.14'
      commands: 'lint:ruff-format,lint:ruff-check,typecheck:mypy,test:pytest,coverage:report'
      ruff-targets: 'src tests evals'
      mypy-targets: 'src'
      coverage-threshold: '80'

  sonar:
    uses: spark-match/spark-match-01-devops/.github/workflows/reusable-sonar-python.yml@main
    with:
      project-key:    ${{ vars.SONAR_PROJECT_KEY }}
      project-name:   ${{ vars.SONAR_PROJECT_NAME }}
      organization:   ${{ vars.SONAR_ORGANIZATION }}
      sources:        'src'
      tests:          'tests'
      coverage-paths: 'coverage.xml'
      env:            'ci'
      fail-on-quality-gate: 'true'
    secrets:
      SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}

  codeql:
    uses: spark-match/spark-match-01-devops/.github/workflows/reusable-codeql.yml@main
    with: { languages: 'python,actions', queries-pack: 'security-extended', fail-on-alerts: true }

  gitleaks:
    uses: spark-match/spark-match-01-devops/.github/workflows/reusable-gitleaks.yml@main
    with: { environment-name: ci }
    secrets: { GITLEAKS_LICENSE: ${{ secrets.GITLEAKS_LICENSE }} }

  actionlint:
    uses: spark-match/spark-match-01-devops/.github/workflows/reusable-actionlint.yml@main
    with: { environment-name: ci }

  yamllint:
    uses: spark-match/spark-match-01-devops/.github/workflows/reusable-yamllint.yml@main
    with: { environment-name: ci }

  evals:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --all-groups
      - run: uv run python -m evals.runner --mode mock
```

Y `deploy.yml` (workflow_dispatch + push a `dev`):

```yaml
jobs:
  build-and-push:
    uses: spark-match/spark-match-01-devops/.github/workflows/reusable-container-deploy-ecr.yml@main
    with:
      environment-name: dev
      aws-region: us-east-1
      ecr-repository: spark-match-agent-advisor-dev
      dockerfile-path: Dockerfile
      platforms: linux/arm64
      image-tags-input: 'latest,__GITHUB_SHA_SHORT__'
      provenance: true
      sbom: true
    secrets:
      AWS_DEPLOY_ROLE_ARN: ${{ secrets.AWS_BEDROCK_AGENTCORE_DEPLOY_ROLE_ARN }}
```

> El rol `spark-match-bedrock-agentcore-deploy-{env}` **ya confía en este repo** para `refs/heads/dev`, `refs/heads/main` y `environment:{env}`. Solo hay que exponer su ARN como `vars`/`secret` del repo.

#### 10.C — Gobernanza

| # | Tarea | Dónde |
|---|---|---|
| 10.C.1 | Poblar `statusChecks` | `01-devops` → `governance/repository-governance.json`, entrada `spark-match-07-deep-agent` (hoy `[]`). Añadir `python-ci`, `codeql`, `gitleaks`, `sonar-python`. Luego `./scripts/configure-repo-rulesets.sh --apply --repos spark-match-07-deep-agent`. |
| 10.C.2 | commitlint + release-please | Añadir `.commitlintrc.json`, `.github/release-please-config.json`, `.release-please-manifest.json` y los workflows que consumen `reusable-commitlint.yml` / `reusable-release-please.yml`. Resuelve B10. |
| 10.C.3 | CODEOWNERS sin catch-all | Alinear con `docs/GOVERNANCE-STANDARD.md` de `01-devops`. Resolver la contradicción actual (PR template dice `product-owners`, CODEOWNERS dice `ai-devs`). |
| 10.C.4 | Proyecto SonarCloud | **No existe** hoy (verificado: la búsqueda de proyectos `spark-match` devuelve 0). Crearlo con el quality gate "Spark Match Way" (coverage ≥80%, 0 code smells nuevos, 0 bugs, 0 vulns, dup ≤3%). |
| 10.C.5 | Dependabot | `.github/dependabot.yml`: ecosistemas `uv`/`pip` + `github-actions` + `docker`, semanal, `target-branch: dev`, prefijo `ci(deps):`. |

**DoD Sprint 10**
- [ ] `docker build` produce una imagen que arranca y responde `200` en `/health`.
- [ ] Imagen non-root, `linux/arm64`, con healthcheck.
- [ ] CI consume ≥5 reusables del catálogo org.
- [ ] Coverage gate en 80% activo y verde.
- [ ] CodeQL con `languages: python,actions` sin alertas ≥ warning.
- [ ] `statusChecks` poblados en el ruleset de gobernanza.
- [ ] Proyecto SonarCloud creado y con quality gate pasando.

---

### Sprint 11 — Despliegue, observabilidad y cierre TFP (20 h)

| # | Tarea | Detalle |
|---|---|---|
| 11.1 | Desplegar en AWS | ECS Fargate (recomendado) o AgentCore Runtime. Ver decisión D-1 en §8. |
| 11.2 | Observabilidad | LangSmith (ya configurable) + métricas EMF a CloudWatch namespace **`spark-match-agent`** (único permitido por IAM) + X-Ray (política ya adjunta). Métricas: `turn_latency_ms`, `tokens_in/out`, `cost_usd`, `intent_route`, `tool_calls`, `memory_hits`, `guardrail_blocks`. |
| 11.3 | Cliente AG-UI en el frontend | `04-frontend`: reemplazar el mock de `ChatService` por `fetch()` + `ReadableStream` + parser SSE. Añadir `agentUrl` a ambos `environment*.ts`. Poner `useMocks: false`. **Alinear `AuthResponse.token` con `data.accessToken` del backend.** |
| 11.4 | Runbook | `docs/runbook.md`: arranque, rotación del secreto JWT (propagación ≤5 min + cold start), limpieza de memoria por usuario (GDPR/derecho al olvido), rollback. |
| 11.5 | Actualizar README | Está 2 sprints desfasado: árbol de ficheros incorrecto, troubleshooting apunta a `src/prompts/system.py` (borrado), "future work: LangSmith" ya implementado, afirma persistencia langmem y HITL que no existen, MCP marcado "✅ Ready" sin código. |
| 11.6 | Documento de decisión final | `docs/decision/deepagents-vs-harness-final.md`: con paridad ya implementada, cerrar formalmente la recomendación §8.4 del documento de decisión (híbrido: Deep Agents como fuente de verdad, lecciones del POC v2 portadas). |
| 11.7 | Crear `DEPLOYMENT.md` y `RESUMEN_PROYECTO.md` | Referenciados en `.dockerignore` e `IMPROVEMENTS.md` pero ausentes del checkout. |

**DoD Sprint 11**
- [ ] Agente accesible por HTTPS con JWT real del backend.
- [ ] Conversación end-to-end desde el Angular con memoria entre sesiones demostrable.
- [ ] Dashboard/consulta CloudWatch con las 7 métricas.
- [ ] README sin una sola afirmación falsa.
- [ ] Demo local reproducible: `uv sync && cp .env.example .env && uv run python -m src` funciona **sin AWS** con `SPARK_PERSISTENCE_BACKEND=sqlite`.

---

## 6. Solicitudes a otros repositorios

### 6.1 `spark-match-01-devops` — pipelines reutilizables

> **Contexto (actualizado 2026-08-06)**: el 2026-08-02 se borraron `python-ci.yml`, `container-deploy-ecr.yml`, `trivy.yml`, `checkov.yml` (commit `7ea5a88`, PR #203) y `sonar-python.yml` (commit `c007ce6`, PR #202). El 2026-08-04 se restauraron todas menos `checkov` (PR #297 de `01-devops`).
>
> **Estado de las solicitudes: R1, R2, R3 y R6 cerradas; R4 parcialmente; solo R5 sigue abierta.** El texto de abajo se conserva porque documenta lo que se pidió y por qué; la cabecera de cada solicitud dice si sigue viva.
>
> `spark-match-07-deep-agent` fue efectivamente el primer consumidor Python real del catálogo, y por serlo destapó que `reusable-trivy.yml` estaba rota desde su restauración (ver R4).

#### Solicitud R1 — `reusable-python-ci.yml` — CERRADA (existe y este repo la consume)

Recuperar desde el histórico y modernizar:

```bash
git -C spark-match-01-devops show 7ea5a88^:.github/workflows/python-ci.yml > .github/workflows/reusable-python-ci.yml
```

Cambios exigidos respecto a la versión borrada:

| Aspecto | Versión borrada | Requerido |
|---|---|---|
| Nombre de fichero | `python-ci.yml` | **`reusable-python-ci.yml`** (prefijo obligatorio) |
| `python-version` matrix | `["3.12"]` | `["3.14"]` por defecto, configurable |
| `coverage-threshold` | `''` | Soportar `'80'` y fallar por debajo |
| `commands` | CSV | Mantener; añadir `coverage:report` que emita `coverage.xml` |
| Env isolation | — | `INPUTS_*` en `run:` (guard CodeQL de code-injection) |
| `concurrency` | — | **NO añadir** (los reusables del catálogo no lo llevan, salvo excepciones documentadas) |

Firma esperada (inputs): `environment-name` (req), `working-directory` (`.`), `commands`, `dependency-groups` (`dev`), `runs-on` (`ubuntu-latest`), `ruff-targets` (`src tests`), `mypy-targets` (`src`), `pytest-targets` (`tests`), `pytest-args`, `coverage-output` (`coverage.xml`), `coverage-threshold`, `permissions-write` (false), `lock-check` (false), `sync-mode` (`full|runtime-only|lint-only`), `frozen` (false), `setup-uv-version` (`latest`), `cache-suffix`, `timeout-minutes` (20), `fail-fast` (false), `python-version`.

#### Solicitud R2 — `reusable-container-deploy-ecr.yml` — CERRADA (existe y este repo la consume)

Recuperar de `7ea5a88^`. Inputs originales: `environment-name` (req), `aws-region` (`us-east-1`), `ecr-repository` (req), `dockerfile-path` (`Dockerfile`), `context-path` (`.`), `platforms` (`linux/arm64`), `image-tags-input` (`latest,__GITHUB_SHA_SHORT__`), `cache-scope`, `provenance` (true), `sbom` (true), `cosign-sign` (false), `extra-buildx-args`. Secret: `AWS_DEPLOY_ROLE_ARN` (req). Permisos: `id-token: write`, `contents: read`.

Cambio requerido: permitir el ARN también como **input string** (`deploy-role-arn`), igual que hacen `reusable-terraform-plan/apply`, porque GitHub enmascara secrets a `-` cruzando owner y rompe el assume-role.

#### Solicitud R3 — `reusable-sonar-python.yml` — CERRADA (existe y este repo la consume)

Recuperar de `c007ce6^`. Simetría con `reusable-sonar-typescript.yml` pero para `coverage.xml` (formato Cobertura) en vez de LCOV, y `sonar.python.version=3.14`.

#### Solicitud R4 — `reusable-trivy.yml` (🟡 media) — parcialmente cerrada

`reusable-trivy.yml` **ya existe** en el catálogo (se recuperó). Este repo lo consume desde `ci.yml` con `scan-type: fs`, que cubre CVEs de las dependencias Python de `uv.lock` y misconfigs del Dockerfile, con `severity: CRITICAL,HIGH` e `ignore-unfixed: true`.

**Lo que sigue sin cubrirse** es justo lo que pedía la solicitud original: escanear la **imagen** de contenedor. La receta no acepta secrets ni hace login a AWS, así que no puede bajar una imagen de un ECR privado (`scan-type: image` solo sirve para imágenes públicas o ya presentes en el daemon del runner). Sobre la imagen final quedan sin mirar las CVEs del sistema base.

Para cerrarla del todo hace falta pedir upstream que `reusable-trivy.yml` acepte OIDC + login a ECR, y encadenar el escaneo entre el push a ECR y el `roll` a ECS: así una imagen vulnerable no llega a servir tráfico aunque ya esté publicada.

#### Solicitud R5 — gobernanza (🟡 media) — ABIERTA, la unica que queda

En `governance/repository-governance.json`, entrada `spark-match-07-deep-agent`: cambiar `statusChecks: []` por los checks reales una vez existan R1–R3. Ejecutar `./scripts/configure-repo-rulesets.sh --apply --repos spark-match-07-deep-agent`.

#### Solicitud R6 — documentación — CERRADA (corregida en 01-devops#320)

Se pidió porque `README.md` y `CONTRIBUTING.md` de `01-devops` describían una capa de workflows de Python que en ese momento no existía, y `reusable-quality.yml` como "266 bats + 18 pytest tests".

Resuelta por las dos vías a la vez: los workflows se restauraron el 2026-08-04, y la deriva de la documentación se corrigió en `01-devops#320`. Verificado el 2026-08-06: la capa python está documentada y existe, y la cuenta de tests ya no aparece desactualizada.

---

### 6.2 `spark-match-02-infrastructure` — recursos nuevos

> **Contexto**: los roles IAM del agente **ya existen** (`spark-match-agentcore-runtime-{env}`, `spark-match-bedrock-agentcore-deploy-{env}`), pero **cero recursos** de cómputo, datos o registro están provisionados. `live/prod/main.tf` está vacío.

#### Solicitud I1 — ECR repository (🔴 bloqueante para Sprint 10)

Nuevo `modules/ecr/`. Nombre **obligatorio** para casar con el allowlist IAM existente (`repository/spark-match-agent-*-{env}`):

```hcl
# live/dev/main.tf
module "ecr_agent" {
  source                = "../../modules/ecr"
  project_name          = var.project_name
  environment           = var.environment
  repository_name       = "spark-match-agent-advisor-${var.environment}"
  image_tag_mutability  = "IMMUTABLE"
  scan_on_push          = true
  kms_key_arn           = module.kms.kms_key_arn
  lifecycle_keep_last   = 10
}
```

#### Solicitud I2 — Secretos y parámetros SSM (🔴 bloqueante para Sprint 6/7)

| Recurso | Path/Nombre | Contenido | Consumido por |
|---|---|---|---|
| Secrets Manager | `spark-match/agent-tavily-{env}` | API key de Tavily | `SPARK_TAVILY_API_KEY` |
| Secrets Manager | `spark-match/agent-langsmith-{env}` | API key de LangSmith | `SPARK_LANGSMITH_API_KEY` |
| SSM Parameter | `/spark-match/agent/db-secret-arn` | ARN del secreto de Postgres | `src/persistence/secrets.py` |
| SSM Parameter | `/spark-match/agent/ecr-repository-url` | URL del repo ECR | pipeline de deploy |
| SSM Parameter | `/spark-match/agent/service-url` | URL pública del agente | `04-frontend` |

> El path `/spark-match/secret/jwt-arn` **ya lo escribe/consume `03-backend`**. El agente solo lo lee — el permiso `ssm:GetParameter` sobre `/spark-match/*` ya está concedido. **No duplicar.**

#### Solicitud I3 — Base de datos para checkpointer/store (🔴 bloqueante para persistencia en AWS)

Nuevo `modules/database/`. Aurora PostgreSQL Serverless v2, **sin pgvector** (coherente con ADR-008 revocado). Nombre: `spark-match-aurora-{env}-*` (ya en el allowlist IAM).

⚠️ **Restricción crítica**: `langgraph-checkpoint-postgres` usa `psycopg` sobre TCP 5432, **no** RDS Data API. La decisión "Opción A" vigente (`live/dev/terraform.tfvars`) pone las Lambdas fuera de la VPC contando con el Data API. Para el agente hay dos caminos:

- **I3-a (recomendado)**: el agente corre **dentro** de la VPC (ECS Fargate en subnets privadas) con un SG nuevo `spark-match-sg-agent-{env}` (egress 443 → 0.0.0.0/0 para Bedrock/Tavily/LangSmith; ingress desde ALB). Requiere **NAT en dev** (~$32/mes con 1 AZ) o VPC endpoints. Añadir regla `sg_rds` ingress 5432 desde `sg_agent`.
- **I3-b (barato)**: Aurora con `publicly_accessible = true` **solo en dev**, restringido por SG a la IP de salida. Inaceptable en prod.

Mínimo para el TFP: I3-b en dev, I3-a documentado para prod.

#### Solicitud I4 — Cómputo (🔴 bloqueante para Sprint 11)

Nuevo `modules/ecs-service/` (o `modules/agentcore/` si se elige AgentCore Runtime — pero ver §8 D-1: el IAM del control-plane de AgentCore **no está mapeado**, `docs/IAM_ROLES.md` lo marca como riesgo abierto).

Con ECS Fargate:
- Cluster `spark-match-{env}`
- Task definition ARM64, 1 vCPU / 2 GB, `execution_role` = `spark-match-agentcore-runtime-{env}` (**su trust policy ya acepta `ecs-tasks.amazonaws.com`** — no hay que tocarla)
- Service con `desired_count = 1` en dev
- ALB + target group + listener HTTPS (ACM) — **hoy no existe ningún `aws_lb` en el repo**
- Log group `/aws/spark-match/agent/{env}/service` (ya en el allowlist IAM)

#### Solicitud I5 — Ampliar el allowlist de Bedrock (🟡 alta)

Las políticas actuales permiten **solo 2 modelos**. Si se adopta Bedrock Guardrails (Sprint 9.A.3) hace falta:

```json
{ "Sid": "BedrockGuardrails",
  "Effect": "Allow",
  "Action": ["bedrock:ApplyGuardrail"],
  "Resource": ["arn:aws:bedrock:us-east-1:681526276858:guardrail/*"] }
```

Y si el router necesita un tercer modelo, añadirlo explícitamente al allowlist.

#### Solicitud I6 — Observabilidad (🟡 media)

Nuevo `modules/monitoring/` (planificado, no existe). Alarmas mínimas: error rate >5% 5 min, P90 latencia >30 s, coste diario > umbral. **Hoy no hay un solo `aws_cloudwatch_metric_alarm` en todo el repo.**

#### Solicitud I7 — Corregir deriva dev/prod (🟢 baja, pero es un bug)

`modules/oidc-github/policies/dev/spark-match-lambda-runtime.json` usa patrones laxos (`spark-match-${environment}-*`) mientras prod usa los estrictos (`spark-match-backend-*-${environment}`). Los patrones de prod son la convención correcta.

#### Solicitud I8 — Cablear `live/prod` (🟢 baja para el TFP)

`live/prod/main.tf` son 19 líneas de comentarios y `variables.tf` perdió 14 variables. Fuera del scope del TFP, pero debe quedar registrado.

---

## 7. Dependencias a agregar en `pyproject.toml`

```toml
dependencies = [
    # --- existentes, con cota superior (ver 5.12) ---
    "deepagents>=0.6.12,<0.7",
    "langchain-aws>=1.6.1,<2",
    "ag-ui-langgraph>=0.0.42,<0.1",
    "langmem>=0.0.30,<0.1",
    "fastapi>=0.139.0,<1",
    "uvicorn[standard]>=0.50.0,<1",
    "sse-starlette>=3.4.5,<4",
    "pydantic-settings>=2.14.2,<3",
    "httpx>=0.28.0,<1",
    "tavily-python>=0.7.26,<1",
    "duckduckgo-search>=8.1.1,<9",
    "pyyaml>=6.0.3,<7",

    # --- NUEVAS: persistencia (Sprint 6) ---
    "langgraph-checkpoint-sqlite>=2.0",      # perfil sqlite (demo local sin AWS)
    "langgraph-checkpoint-postgres>=2.0",    # perfil postgres (incluye PostgresStore)
    "psycopg[binary,pool]>=3.2",             # driver del anterior

    # --- NUEVAS: auth (Sprint 7) ---
    "pyjwt[crypto]>=2.10",                   # validación HS256
    "boto3>=1.43.40",                        # SSM + Secrets Manager (ya transitivo, explicitar)
    "cachetools>=5.5",                       # TTL cache del secreto JWT

    # --- NUEVAS: hardening (Sprint 7) ---
    "slowapi>=0.1.9",                        # rate limiting

    # --- NUEVAS: MCP (Sprint 8, opcional) ---
    "langchain-mcp-adapters>=0.1",
]

[dependency-groups]
dev = [
    "pytest>=9.1.1",
    "pytest-asyncio>=1.4.0",
    "pytest-cov>=7.0",                       # arregla `make test-cov` (B8)
    "ruff>=0.15.20",
    "mypy>=2.1.0",
    "types-pyyaml>=6.0.12.20260518",
    "types-cachetools>=5.5",
    "testcontainers[postgres]>=4.9",         # tests de integración del perfil postgres
]
```

**Nuevas variables de entorno** (`.env.example`):

```bash
# Persistencia
SPARK_PERSISTENCE_BACKEND=sqlite            # memory | sqlite | postgres
SPARK_SQLITE_PATH=.spark-match/checkpoints.sqlite
# SPARK_POSTGRES_DSN=postgresql://...       # override local; en AWS se resuelve por SSM

# Auth
# SPARK_JWT_SECRET=...                      # override local; en AWS: SSM /spark-match/secret/jwt-arn
SPARK_JWT_ISSUER=spark-match-backend
SPARK_JWT_AUDIENCE=spark-match-api
SPARK_AUTH_ENABLED=true                     # false SOLO para tests locales

# Modelos (allowlist IAM)
SPARK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0
SPARK_FAST_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
SPARK_MAX_TOKENS=2048
SPARK_ENABLE_INTENT_ROUTER=true

# Memoria
SPARK_REFLECTION_DELAY_SECONDS=30
SPARK_EPISODE_TTL_HOURS=24

# API
SPARK_API_PORT=8080                         # 8000 lo usa el backend en dev
```

---

## 8. Riesgos y decisiones abiertas

| ID | Decisión / Riesgo | Opciones | Recomendación |
|---|---|---|---|
| **D-1** | Runtime de despliegue | (a) ECS Fargate (b) AgentCore Runtime (c) solo local | **(a) ECS Fargate.** El rol `spark-match-agentcore-runtime-{env}` ya acepta `ecs-tasks.amazonaws.com`, y el IAM del control-plane de AgentCore **no está mapeado** (riesgo abierto declarado en `docs/IAM_ROLES.md`). Para el TFP, (c) es suficiente como plan B. |
| **D-2** | Conectividad a Postgres | (a) agente en VPC + NAT (b) Aurora público en dev (c) DynamoDB checkpointer | **(b) en dev, (a) documentado para prod.** `langgraph-checkpoint-postgres` no soporta RDS Data API, que es la premisa de la "Opción A" vigente en infra. |
| **D-3** | Roles de usuario | (a) esperar a que `03-backend` añada `docente`/`graduado` (b) el agente asume `student` por defecto (c) claim custom nuevo | **(b)** ahora + abrir issue en `03-backend` para (a). Hoy el `CHECK` constraint solo permite `'admin'`; bloquearse en (a) para el TFP. |
| **D-4** | Búsqueda semántica de memoria | (a) `PostgresStore` con pgvector (b) sin índice vectorial (c) proveedor externo | **(b).** ADR-008 revocó pgvector. Con ≤20 carreras y 1 perfil por usuario, la búsqueda por namespace/key es suficiente. |
| **D-5** | Alineación del contrato de login | El frontend espera `{token}`, el backend devuelve `{data:{accessToken}}` | Corregir en `04-frontend` (Sprint 11.3). Es un bug latente que solo no explota porque `useMocks: true`. |
| **R-1** | `deepagents` 0.7.0 rompe `StoreBackend(runtime=...)` | — | Cota superior `<0.7` (tarea 5.12) + issue de seguimiento. |
| **R-2** | Sin refresh token ni revocación en el backend | Un JWT robado vale 24 h | Documentar en el runbook. Mitigación parcial: re-chequear `active` contra `identity.users` si se llega a tener acceso a la BD. |
| **R-3** | `01-devops` borró el CI de Python hace 1 día | Bloquea Sprint 10 | R1–R4 de §6.1 son **prerrequisito**. Plan B: CI local en este repo hasta que existan. |
| **R-4** | El contador de budget es in-process | Se rompe con `--workers > 1` o réplicas | Tarea 7.E.4: moverlo al store. |
| **R-5** | Deriva de documentación generalizada | README, IMPROVEMENTS, docs de `01-devops` e infra desalineados | Cada sprint incluye actualización documental en su DoD. |
| **R-6** | Coste de la extracción langmem | Un LLM call extra por sesión | `ReflectionExecutor` diferido (30 s) + Haiku. Doc y tablas comparativas listas en `docs/benchmarks.md` (PR #51); medición real pendiente de Sprint 11 live mode. |

---

## 9. Definition of Done del proyecto (criterio de cierre TFP)

- [ ] **Memoria por sesión**: mismo `thread_id` → el agente recuerda el turno anterior. Test automatizado.
- [ ] **Memoria entre sesiones**: mismo `user_id`, distinto `thread_id` → el perfil RIASEC persiste. Test automatizado.
- [ ] **Aislamiento**: `user_a` no accede a nada de `user_b`. Test automatizado.
- [ ] **Auth**: `/ag-ui` rechaza sin JWT válido emitido por `03-backend` (HS256, `iss`/`aud` correctos).
- [ ] **Roles**: el `role` del token llega a `runtime.context` y condiciona capacidades.
- [ ] **Tools**: 4 tools de dominio + 2 de memoria funcionando; `web_search` async con Tavily + fallback.
- [ ] **Skills**: `SkillsMiddleware` activo, `skills/` cargado de verdad.
- [ ] **Guardrails**: inyección de prompt y PII bloqueadas, con tests.
- [ ] **Evals**: ≥30 casos, judge multi-dimensión, gate en CI que detecta regresiones reales.
- [ ] **Coverage** ≥80%, CI verde consumiendo el catálogo de `01-devops`.
- [ ] **Contenedor** non-root que arranca y responde `/health`.
- [ ] **Desplegado** en AWS con JWT real end-to-end desde el Angular.
- [ ] **Observabilidad**: LangSmith + métricas EMF en `spark-match-agent` + X-Ray.
- [ ] **Demo local sin AWS**: `uv sync && uv run python -m src` con `SPARK_PERSISTENCE_BACKEND=sqlite`.
- [ ] **Documentación** sin afirmaciones falsas; `docs/benchmarks.md` comparando contra POC v1 y v2.

---

## 10. Anexos

### 10.1 Comandos de verificación rápida

```powershell
# Confirmar la firma real de create_deep_agent en el venv
.venv\Scripts\python.exe -c "import inspect; from deepagents import create_deep_agent; print(inspect.signature(create_deep_agent))"

# Confirmar el contrato jump_to (bug B1)
.venv\Scripts\python.exe -c "from langchain.agents.middleware.types import AgentState, JumpTo; print(AgentState.__annotations__); print(JumpTo)"

# Listar backends disponibles
.venv\Scripts\python.exe -c "import deepagents.backends as b; print(sorted(n for n in dir(b) if not n.startswith('_')))"

# Comprobar qué falta instalar para persistencia
.venv\Scripts\python.exe -c "import importlib; [print(m, 'OK' if importlib.util.find_spec(m) else 'MISSING') for m in ['langgraph.checkpoint.sqlite','langgraph.checkpoint.postgres','langgraph.store.postgres','psycopg','jwt']]"

# Buscar mojibake (bug B5)
rg -n "Ã" src/ data/ evals/

# Recuperar los workflows borrados de 01-devops
git -C ..\spark-match-01-devops show 7ea5a88^:.github/workflows/python-ci.yml
git -C ..\spark-match-01-devops show 7ea5a88^:.github/workflows/container-deploy-ecr.yml
git -C ..\spark-match-01-devops show c007ce6^:.github/workflows/sonar-python.yml
```

### 10.2 Mapa de namespaces del store

```
("spark-match", "_threads")                     → {thread_id: {user_id, created_at}}   [ownership guard]
("spark-match", <user_id>, "profile")           → StudentProfile                        [≈ SEMANTIC, 90d]
("spark-match", <user_id>, "prefs")             → preferencias declaradas               [≈ USER_PREFERENCE, 365d]
("spark-match", <user_id>, "episodes")          → resúmenes de sesión                   [≈ EPISODIC, 24h-30d]
("spark-match", <user_id>, "files")             → StoreBackend /memories/*              [auto-gestionado por el agente]
("spark-match", <user_id>, "budget")            → contadores de web_search              [reemplaza src/budget.py]
```

### 10.3 Referencias cruzadas

| Documento | Ruta |
|---|---|
| Decisión Runtime vs Harness vs Deep Agents | `../orion/AWS-DEEPAGENT-VS-AWS-RUNTIME-VS-AWS-HARNESS.md` |
| Plan y bondades del Harness (POC v2) | `../orion/AWS-HARNESS-HARNESS-POC.md` |
| Resultados POC v2 (métricas V8: −42% latencia, −57% coste) | `../orion/AWS-HARNESS-POC-V10.md` |
| Prompt de evaluación de arquitectura | `../orion/AGENT-PROMPT-ARCHITECTURE-EVALUATION.md` |
| Contrato de auth del backend | `../spark-match-03-backend/docs/auth-rbac.md`, `shared/src/auth/jwt-helpers.ts` |
| Catálogo de pipelines | `../spark-match-01-devops/README.md`, `docs/VERSIONING.md`, `AGENTS.md` §11 |
| Diseño IAM (roles del agente) | `../spark-match-02-infrastructure/docs/IAM_ROLES.md` |
| Gaps previos (Sprints 1–4) | `./IMPROVEMENTS.md` |

---

**Fin del documento.** Cualquier agente que lo tome debe empezar por el **Sprint 5**, porque B1 (el agente no se detiene) y B6 (modelo fuera del allowlist IAM) invalidan cualquier prueba posterior.
