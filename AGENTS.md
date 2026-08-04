# AGENTS.md — Spark Match Deep Agent (`spark-match-08-deep-agent`)

> Working agreement para agentes de IA (OpenCode, Claude Code, Copilot) y humanos que contribuyen a este repositorio.
> **Lectura obligatoria antes de cada PR.** Fuente de verdad local, no duplicada en `docs/`.
> Última revisión: 2026-08-04.

---

## 1. Propósito y estado del repositorio

`spark-match-08-deep-agent` es el **agente conversacional de orientación vocacional** (modelo RIASEC) de la plataforma Spark Match. Construido con `deepagents` (LangGraph) sobre AWS Bedrock, expone un endpoint AG-UI por SSE que consume el frontend Angular (`spark-match-04-frontend`).

Es un **Trabajo de Fin de Programa (TFP)** — UNI, II Programa de Especialización en IA Generativa y MLOps. Esa condición manda dos requisitos no negociables:

1. El evaluador debe poder correr el agente **en local sin cuenta AWS** (`uv sync && uv run python -m src`).
2. La portabilidad (vendor-neutral) es un criterio de diseño, no un accidente. Ver `../orion/AWS-DEEPAGENT-VS-AWS-RUNTIME-VS-AWS-HARNESS.md` §8.1.

### 1.1 Estado real (verificado 2026-08-04)

El plan de finalización vive en **[`ROADMAP-2026-08.md`](ROADMAP-2026-08.md)** (Sprints 5 → 11). Lo que hay que saber antes de tocar código:

| Capacidad | Estado |
|---|---|
| Memoria por sesión y entre sesiones | **Cerrado (Sprint 6).** `checkpointer=`/`store=`/`backend=` cableados en `create_spark_agent()` y en el lifespan de `app.py`. `user_id` real desde el JWT desde Sprint 7 (antes placeholder fijo) — ver fila de Autenticación. |
| Autenticación / autorización | **Núcleo cerrado (Sprint 7, PRs #A.7).** `POST /ag-ui` exige JWT válido (`src/auth/`); `thread_id` derivado + registro de propiedad (403 cruzado); `runtime.context.user_id/role/email` disponibles en todo middleware/tool vía `context_schema=AgentContext`. Pendiente: CORS validator, cabeceras de seguridad, rate limiting y budget-por-usuario en el store (tareas 7.E.1–7.E.4, PR de endurecimiento aparte). Ver `docs/auth.md`. |
| `langmem` | **Activo.** `src/memory/profile_manager.py` usa `create_memory_store_manager` + `ReflectionExecutor`; `src/agent/memory_middleware.py` hidrata/persiste el perfil. |
| `skills/` | Nunca se carga (`skills=` no se pasa a `create_deep_agent`). |
| Guardrail de turnos | **Corregido (Sprint 5, B1).** `MaxTurnsMiddleware` usa `jump_to` (`@hook_config(can_jump_to=["end"])`), no `goto`. |
| Modelo por defecto | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` — dentro del allowlist IAM de `spark-match-02-infrastructure`. |
| Contenedor | No hay `Dockerfile` (solo un `.dockerignore` huérfano). |

> **Regla**: `README.md` e `IMPROVEMENTS.md` están desactualizados y describen features que no existen. La §2 del `ROADMAP-2026-08.md` es la única fuente de verdad verificada. No cites el README como evidencia.

### 1.2 Repos hermanos

| Repo | Rol | Qué te aporta |
|---|---|---|
| `spark-match-01-devops` | Catálogo único de CI/CD reutilizable | Los workflows que este repo consume |
| `spark-match-02-infrastructure` | Terraform (VPC, IAM, KMS, SGs) | Los roles OIDC y el allowlist de modelos Bedrock |
| `spark-match-03-backend` | Serverless TypeScript (identity) | **Emite el JWT que este agente debe validar** |
| `spark-match-04-frontend` | Angular 22 | Consume `/ag-ui`; guarda el JWT en `localStorage` |

---

## 2. Stack

- **Lenguaje**: Python **3.14** (`.python-version`), gestionado con **`uv`**.
- **Agente**: `deepagents` 0.6.12 sobre `langchain` 1.3.11 / `langgraph` 1.2.7.
- **Modelo**: AWS Bedrock vía `langchain-aws` 1.6.1.
- **API**: FastAPI 0.139 + `ag-ui-langgraph` 0.0.42 + `sse-starlette` (SSE).
- **Config**: `pydantic-settings` con prefijo `SPARK_`.
- **Lint/format**: `ruff` 0.15 (`line-length = 100`, `target-version = "py314"`).
- **Tipos**: `mypy` 2.1 en modo **`strict`**.
- **Tests**: `pytest` 9.1 + `pytest-asyncio` (`asyncio_mode = "auto"`).
- **Entorno de desarrollo**: Windows + **PowerShell 5.1** (ver §9.4 para las trampas).

---

## 3. Modelo de ramas — `main` + `dev`

> **Regla operacional dura**: el flujo canónico de cualquier cambio es
> **`branch` → `dev` → `main`**, en ese orden estricto.
> Nunca se commitea directo a `dev` ni a `main`.
> Nunca se mergea un feature branch directamente a `main`.

| Rama | Rol | Protección |
|---|---|---|
| `dev` | Rama de integración. **Todo PR de desarrollo apunta aquí.** | 1 approval + code owner review, squash-only, delete branch on merge |
| `main` | Rama estable. Solo recibe PRs de sync desde `dev`. | Igual + `require_last_push_approval` |

Ruleset activo: `spark-match-default-branch-protection` (gestionado declarativamente desde `spark-match-01-devops/governance/repository-governance.json`, `reviewerTeam: ai-devs`).

### 3.1 Paso 1 — crear la rama de trabajo

```bash
git checkout dev
git pull --ff-only
git checkout -b feat/sprint-6-persistence-layer
git push -u origin feat/sprint-6-persistence-layer
```

**Naming de ramas** (obligatorio, kebab-case):

```
<type>/sprint-<N>-<slug>
```

- `type` ∈ `feat` | `fix` | `chore` | `docs` | `refactor` | `test` | `ci`
- `N` = número de sprint del `ROADMAP-2026-08.md`
- `slug` = descripción corta en kebab-case, en inglés

Ejemplos válidos: `feat/sprint-6-memory-store`, `fix/sprint-5-max-turns-jump-to`, `chore/sprint-10-dockerfile`.
Ejemplos inválidos: `feature/memory`, `fix-bug`, `ahincho/test`, `feat/impl-2-memory`.

### 3.2 Paso 2 — PR a `dev`

```bash
gh pr create \
  --repo spark-match/spark-match-08-deep-agent \
  --base dev \
  --head feat/sprint-6-persistence-layer \
  --title "feat(persistence): add checkpointer and store factory" \
  --body-file .git/pr-body.md
```

> **Nunca uses `--body "..."` en PowerShell 5.1.** El shell tokeniza las comillas con caracteres especiales de forma impredecible y rompe tildes, ñ y saltos de línea. **Siempre `--body-file`.** Escribe el body en un archivo temporal fuera del árbol versionado (`.git/pr-body.md` o `$env:TEMP`).

**Plantilla del body** (además del `pull_request_template.md` del repo):

```markdown
## Resumen
[1-3 frases]

## Sprint
Sprint N — tarea N.X del ROADMAP-2026-08.md

## Cambios
| Archivo | Cambio |
|---|---|
| `src/...` | descripción de 1 línea |

## Testing
- [ ] `make qa` verde
- [ ] `make test` verde, coverage >= umbral
- [ ] Test de regresión añadido para el bug/feature

## Impacto
[Efecto neto: "Coverage 75% -> 81%", "Cierra B1 del roadmap", etc.]
```

### 3.3 Paso 3 — checks y merge

Esperar a que **todos** los checks pasen. Consultar:

```bash
gh pr checks <num> --repo spark-match/spark-match-08-deep-agent --watch
```

Si un check está en rojo, **se arregla el problema raíz**. No se bypasea (ver §12).

```bash
gh pr merge <num> --repo spark-match/spark-match-08-deep-agent \
  --squash --delete-branch
```

Limpieza local después del merge:

```bash
git checkout dev
git pull --ff-only
git branch -D feat/sprint-6-persistence-layer
git fetch --prune
```

### 3.4 Paso 4 — sync `dev` → `main`

Se hace con un PR dedicado, **no** después de cada merge a `dev`.

```bash
gh pr create \
  --repo spark-match/spark-match-08-deep-agent \
  --base main --head dev \
  --title "chore(sync): dev -> main (sprint 6 - persistencia)" \
  --body-file .git/sync-body.md
gh pr merge <num> --repo spark-match/spark-match-08-deep-agent --squash
```

**Cuándo promover** (criterios, en orden de precedencia):

| Trigger | Categoría |
|---|---|
| Sprint cerrado con DoD cumplido y CI verde | Madurez |
| Code freeze planificado / entrega del TFP | Madurez |
| Hotfix crítico operacional | Decisión explícita |

**NO se sincroniza**: después de cada PR a `dev`, sin indicación explícita del owner del sprint, ni con un check rojo o una alerta CodeQL/Dependabot abierta.

> **Regla**: si dudas, NO sincronices.

### 3.5 Paso 5 — verificación post-sync (obligatoria)

```bash
git fetch origin
git diff --stat origin/main origin/dev
# salida esperada: vacía
```

Si el diff NO está vacío, `main` perdió cambios de `dev`. **No avanzar** hasta reconciliar con un PR de sync correctivo.

### 3.6 Por qué `git log` muestra divergencia aunque el contenido sea idéntico

El sync usa squash merge, que crea **un** commit en `main` sin los commits originales de `dev` como ancestros:

```bash
git log origin/main..origin/dev   # 20+ commits — esperado, no es un bug
git log origin/dev..origin/main   # 0..1 commits — el commit de sync
```

**Esto es esperado.** La verificación real de sincronización es `git diff --stat` (§3.5), no `git log`.

### 3.7 Anti-patterns de branching

- Sincronizar `main` ← `dev` con `git merge --no-ff` (deja merge commit de dos padres; usar `--squash`).
- Sincronizar `dev` ← `main` (rompe el flujo canónico; solo en emergencias documentadas).
- Asumir que `git log` divergente significa desactualización (falso, ver §3.6).
- `git push --force` a `dev` o `main`. Force-push a **tu propia rama** para incorporar feedback de review sí es aceptable.
- Ramas de larga duración. Si un sprint necesita varios PRs, se abren PRs incrementales desde la misma rama contra `dev`, no se acumula todo en un PR gigante.

---

## 4. Convención de commits — Conventional Commits 1.0.0

```bash
git commit -m "feat(memory): wire StoreBackend with per-user namespace"
```

### 4.1 Tipos permitidos (10)

`feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `build`, `ci`, `perf`, `revert`.

### 4.2 Scope enum

| Capa | Scopes |
|---|---|
| **Dominio del agente** | `agent`, `middleware`, `subagents`, `prompts`, `skills`, `tools`, `catalog`, `matching`, `assessment`, `web-search` |
| **Plataforma** | `api`, `auth`, `memory`, `persistence`, `config`, `observability`, `mcp` |
| **Calidad** | `evals`, `tests` |
| **Generales** | `ci`, `deps`, `docs`, `governance`, `docker`, `repo` |

El scope es **opcional**. Los commits de sync (`chore(sync): dev -> main ...`) son válidos.

### 4.3 Reglas de subject

- **lowercase**: sin mayúsculas iniciales en el subject.
- **sin punto final**.
- **header ≤ 100 caracteres**, contando el prefijo `<type>(<scope>): `.
- **Todas las líneas del mensaje ≤ 100 caracteres**, incluido el body. El parser de commitlint clasifica el contenido post-header como body o footer de forma oportunista; una línea larga puede disparar `footer-max-line-length` de forma no determinística.

### 4.4 Trampa: `git commit --amend` no actualiza el título del PR

Con squash merge, GitHub usa el **título del PR** como subject del commit final, **no** el mensaje local. Si corriges el subject con `--amend`, actualiza también el PR:

```bash
gh pr edit <num> --repo spark-match/spark-match-08-deep-agent --title "<nuevo subject>"
```

Si el merge ya ocurrió con el subject malo: **fix forward** (un PR nuevo con commits válidos). No reescribir historia en ramas compartidas.

---

## 5. Quality gates

### 5.1 Gates locales (correr antes de pushear)

```bash
make qa       # ruff format --check + ruff check + mypy strict
make test     # pytest
make test-cov # pytest con coverage
```

Equivalentes sin `make`:

```powershell
uv run ruff format --check src/ tests/ evals/
uv run ruff check src/ tests/ evals/
uv run mypy src
uv run pytest --tb=short
uv run python -m evals.runner --mode mock
```

> `make` exige un `.env` presente (hace `$(error ...)` si falta). Copia `.env.example` a `.env` antes de la primera ejecución.

### 5.2 Gates duros (no relajables)

| Gate | Umbral | Dónde se aplica |
|---|---|---|
| `ruff format --check` | 0 diferencias | CI |
| `ruff check` | 0 violaciones | CI |
| `mypy --strict` sobre `src/` | 0 errores | CI |
| `pytest` | 0 fallos | CI |
| Coverage de líneas | **≥ 80 %** (a partir del Sprint 5; hoy no hay gate) | CI + SonarCloud |
| Evals `--mode mock` | 100 % pass | CI |
| CodeQL (`python`, `actions`) | 0 alertas ≥ `warning` | CI |
| `gitleaks` | 0 secretos | CI |

Si algún gate está rojo, el PR **no es mergeable**.

### 5.3 Reglas de testing

- **Todo archivo nuevo lleva tests en el mismo PR.** Añadir código sin tests baja el coverage y bloquea el gate.
- **Todo bug corregido lleva un test de regresión** que falla antes del fix.
- Convención de nombres: `tests/<dominio>/<módulo>.py` (sin prefijo `test_` en el filename; `pytest` lo descubre vía `python_files = ["*.py"]`). Las funciones sí van con `test_*`.
- Los tests que asserten sobre middleware deben verificar **el comportamiento del grafo compilado**, no el dict devuelto por el hook. El bug B1 pasó desapercibido justo por eso.
- `get_settings` está cacheado con `@lru_cache`: tras `monkeypatch.setenv` hay que llamar `get_settings.cache_clear()`.

---

## 6. Convenciones de código Python

- **Código en inglés**: variables, funciones, clases, módulos, docstrings de API pública.
- **Documentación y comentarios en español**: `AGENTS.md`, `README.md`, `docs/`, `ROADMAP-*.md`, mensajes de PR.
- **Cuidar caracteres especiales**: ñ y tildes correctamente codificadas en UTF-8. El repo ya tiene mojibake commiteado (`ArtÃƒÂ­stico` en `src/tools/assessment/handler.py`) que se filtra al usuario; no añadir más. Verificar con `rg -n "Ã" src/`.
- **Sin emojis decorativos** en código, commits ni mensajes de PR.
- **NO añadir comentarios salvo que se pidan explícitamente.** Self-documenting code.
- **Mimic existing patterns** antes de inventar. El repo tiene patrones establecidos que hay que respetar:
  - **Separación handler / tool**: `handler.py` es lógica pura testeable sin LLM y devuelve el sobre `{"status", "data", "errors"}`; `tool.py` es un wrapper `@tool` fino que desenvuelve el sobre para el modelo. Nunca meter lógica de negocio en el `@tool`.
  - **Prompts como `.md`** con frontmatter YAML en `src/prompts/`, cargados por `src/prompts/loader.py`. Nunca hardcodear prompts en Python.
  - **Catálogo como `.md`** con frontmatter YAML en `data/careers/`. Añadir una carrera es contenido, no código.
  - **Settings tipados** en `src/config/settings.py` con prefijo `SPARK_`. Secretos siempre `SecretStr`.
- **No introducir dependencias nuevas** sin justificarlo en el body del PR. Las dependencias planificadas ya están listadas en `ROADMAP-2026-08.md` §7.
- **Cotas superiores en el cluster LangChain**: `deepagents>=0.6.12,<0.7`, `langchain>=1.3,<2`, `langgraph>=1.2,<2`. `deepagents` 0.7.0 elimina `StoreBackend(runtime=...)`.

---

## 7. Convenciones de GitHub Actions

Este repo **consume** el catálogo de `spark-match-01-devops`. Aplica su convención **kebab-case** (§5.1 de su `AGENTS.md`).

- **kebab-case** para: `name:` de workflow/job/step, `id:` de job/step, inputs y outputs.
- **`SNAKE_CASE`** para: secretos (`secrets.SONAR_TOKEN`) y env vars del SO (`env: AWS_REGION`).
- **Templates embebidos**: concatenar con `-`, nunca con espacio. `name: python-ci-${{ inputs.environment-name }}`, no `name: python-ci (${{ inputs.environment-name }})`.
- **Brand mapping**: `SonarCloud` → `sonar-cloud`, `CodeQL` → `codeql`, `Terraform` → `terraform`, `Docker` → `docker`, `Tavily` → `tavily`.
- **Excepciones** (no kebab): URLs, nombres de actions de terceros (`actions/checkout`), eventos de GitHub (`pull_request`, `workflow_dispatch`), nombres de GH Environment (`dev`, `production`).

### 7.1 Pinning

- **Third-party actions**: `@vN` (major flotante) o `@N.N.N` (exacto). **Nunca SHA.** Lo verifica `spark-match-01-devops/tests/bats/no-sha-pinning.bats`.
- **Reusables del catálogo**: `@main` por defecto (es lo que hacen `03-backend` y `02-infrastructure` para las recetas de ecosistema).
  - Excepción: `reusable-commitlint` y `reusable-release-please` se pinean a tag (`@v0.1.18`), siguiendo lo que hace `02-infrastructure`.

> **Contradicción conocida en el upstream**: `spark-match-01-devops/AGENTS.md` §1.3 y `docs/VERSIONING.md` dicen `@main`; §5.2 punto 8 del mismo archivo dice pinear tag. La práctica real del org es la descrita arriba. Si se unifica upstream, actualizar esta sección.

### 7.2 Metodología reuse-first (obligatoria antes de proponer un workflow nuevo)

Heredada de `spark-match-03-backend/AGENTS.md` §12.2. Antes de escribir cualquier pipeline:

1. **Leer las convenciones** (§7 y §7.1 de este archivo).
2. **Inventariar el catálogo** en `spark-match-01-devops/.github/workflows/reusable-*.yml`.
3. **Decidir en este orden**:
   - **(a) Reuso directo**: ¿alguna receta cubre el 100 %? Citar archivo e inputs exactos.
   - **(b) Reuso 80 % + wrapper en el caller**: identificar el gap. Si es de 1 línea, wrapper; si afecta a N callers futuros, extender la receta con un input retrocompatible.
   - **(c) Receta nueva**: solo si (a) y (b) no aplican.
4. **Al proponer creación, documentar**: filename `reusable-<kebab>.yml`, inputs con defaults, bloque `permissions:` mínimo, binding de GH Environment si hay secretos, nivel de riesgo y compatibilidad hacia atrás.

> **Estado actual crítico**: `spark-match-01-devops` **borró el CI de Python el 2026-08-02** (commits `7ea5a88` y `c007ce6`). No existen `reusable-python-ci.yml`, `reusable-container-deploy-ecr.yml`, `reusable-sonar-python.yml` ni `reusable-trivy.yml`. Este repo sería el primer consumidor Python del org. Las solicitudes formales R1–R6 están en `ROADMAP-2026-08.md` §6.1. **No dupliques esos pipelines aquí**: se piden upstream.

### 7.3 Secretos y OIDC

- **Los ARNs de roles IAM se pasan como input string** (desde `${{ vars.* }}`), **no** como secret. GitHub enmascara los secrets a `-` cruzando owner y eso rompe el `assume-role`. Los ARNs son identificadores, no credenciales.
- **`secrets: inherit` no funciona cross-owner.** Forwardear siempre por nombre explícito.
- Secretos org-level ya disponibles (visibility `all`, no hay que bootstrapear): `GITLEAKS_LICENSE`, `SONAR_TOKEN`, `RELEASE_PLEASE_APP_ID`, `RELEASE_PLEASE_APP_PRIVATE_KEY`.
- El rol `spark-match-bedrock-agentcore-deploy-{env}` **ya confía en este repo** por OIDC para `refs/heads/dev`, `refs/heads/main` y `environment:{env}`. Solo falta exponer su ARN como variable del repo.

---

## 8. Reglas duras (no negociables)

1. **Nunca commitear secretos.** Ni API keys de Tavily/LangSmith, ni el secreto JWT, ni credenciales AWS (`AKIA`/`ASIA`). Todo secreto va a Secrets Manager y se referencia por ARN vía SSM. Si uno se filtra, **rotarlo inmediatamente** en AWS: el valor en `git log` queda muerto pero el secreto sigue vivo hasta que se rote.

2. **El contrato de salto del grafo es `jump_to`, no `goto`.** `AgentState` de LangChain 1.x declara `jump_to: Literal["tools","model","end"]`. LangGraph descarta claves desconocidas **en silencio**. Todo middleware que corte el flujo debe usar `@hook_config(can_jump_to=["end"])` y devolver `{"jump_to": "end"}`. Esta es la causa del bug B1.

3. **El `model_id` debe estar en el allowlist IAM.** `spark-match-02-infrastructure` permite **exactamente dos** foundation models:
   ```
   anthropic.claude-sonnet-4-5-20250929-v1:0
   anthropic.claude-haiku-4-5-20251001-v1:0
   ```
   Cualquier otro da `AccessDeniedException` en AWS. Añadir un modelo requiere PR en el repo de infraestructura.

4. **Toda memoria se particiona por `user_id`.** Los namespaces del store llevan siempre el `user_id` del JWT: `("spark-match", <user_id>, "<dominio>")`. Nunca un namespace global para datos de usuario. El mapa completo está en `ROADMAP-2026-08.md` §10.2.

5. **El `thread_id` del cliente no es de confianza.** Llega del frontend sin validar. Hay que derivarlo (`sha256(user_id + client_thread_id)`) y registrar la propiedad thread ↔ usuario. Sin esto, cualquiera lee la conversación de otro.

6. **No romper el contrato JWT de `spark-match-03-backend`.** Es HS256 con `iss=spark-match-backend`, `aud=spark-match-api`, claims `sub`/`email`/`role`. La clave son los **bytes UTF-8 crudos** del `SecretString` — no hacer base64-decode ni `json.loads`. Detalle completo en `ROADMAP-2026-08.md` §2.2.

7. **El perfil de persistencia `memory` y `sqlite` deben funcionar sin AWS.** Es requisito del TFP. Ningún cambio puede hacer que el arranque local exija credenciales de AWS.

8. **kebab-case en `name:` e `id:` de workflows.** Prohibido Title Case, CamelCase, snake_case y paréntesis con espacios.

9. **Conventional Commits obligatorio** (§4). Cuando se añada `.commitlintrc.json` (Sprint 10), el scope-enum de §4.2 y el enforcer local deben mantenerse sincronizados.

10. **No modificar recetas de `spark-match-01-devops`** desde este repo. Ese catálogo lo posee devops. Las necesidades se canalizan como solicitudes (ver `ROADMAP-2026-08.md` §6.1).

---

## 9. Entorno de desarrollo

### 9.1 Setup

```powershell
uv sync --all-groups
Copy-Item .env.example .env
uv run python -m src
```

### 9.2 Variables de entorno

Todas con prefijo `SPARK_`. Ver `.env.example`. Las nuevas del roadmap (persistencia, auth, router) están en `ROADMAP-2026-08.md` §7.

### 9.3 Verificar la API real de las librerías

No asumas firmas. El repo tiene un `.venv` con las versiones exactas; introspecciónalo:

```powershell
.venv\Scripts\python.exe -c "import inspect; from deepagents import create_deep_agent; print(inspect.signature(create_deep_agent))"
.venv\Scripts\python.exe -c "from langchain.agents.middleware.types import AgentState, JumpTo; print(AgentState.__annotations__); print(JumpTo)"
.venv\Scripts\python.exe -c "import deepagents.backends as b; print(sorted(n for n in dir(b) if not n.startswith('_')))"
```

### 9.4 Trampas de PowerShell 5.1

- **`gh pr create --body "..."`**: rompe tildes y saltos de línea. Usar siempre `--body-file`.
- **`gh api` con `?` en la URL**: el shell interpreta el `?`. Asignar a variable primero:
  ```powershell
  $url = "/repos/spark-match/spark-match-08-deep-agent/actions/runs?event=pull_request"
  gh api "$url"
  ```
- **No hay `&&`**: usar `cmd1; if ($?) { cmd2 }`.
- **Salida de consola con mojibake**: la consola de PowerShell 5.1 renderiza mal UTF-8, pero el **archivo puede estar correcto**. Verificar leyendo el archivo con una herramienta que respete UTF-8 antes de "arreglar" nada.
- **`gh pr merge --admin` en cuentas Enterprise Managed User (EMU)**: falla con `GraphQL: Unauthorized`. Workaround REST documentado en `spark-match-01-devops/AGENTS.md` §4.4.

---

## 10. Terminología unificada: solo "Sprint N"

> **Regla dura**: toda referencia a fases, tracks o etapas se hace **exclusivamente** como **"Sprint N"** (N entero, sin prefijos ni sufijos).

Prohibido en código, docs, tasks, commits, nombres de rama, labels y descripciones de PR:

- `Track A`, `Track B`
- `Impl-2`, `Impl-3`, `Fase 1`, `Phase 2`
- `Prod-1`, `preflight`
- `B1`-`B10` **sin contexto de sprint** (los IDs de bug del roadmap son válidos citando el documento: "B1 del `ROADMAP-2026-08.md`")

Derivadas:

- **Ramas**: `feat/sprint-N-*`, `fix/sprint-N-*`, `chore/sprint-N-*`.
- **Headers de doc**: `### Sprint 6`, no `### Fase de memoria`.
- **Labels de PR**: `sprint-6`.

**Razón**: los repos hermanos (`02-infrastructure` sobre todo) sufrieron confusión real por mezclar "Sprint", "Track", "Impl-N" y "preflight". Una sola dimensión de naming elimina la ambigüedad y hace `grep` fiable.

### 10.1 Sprints activos

El backlog canónico es **[`ROADMAP-2026-08.md`](ROADMAP-2026-08.md)** §5. Resumen:

| Sprint | Tema | Estado | Bloquea a |
|---|---|---|---|
| **5** | Correcciones críticas (B1–B10) + deuda técnica | ✅ Cerrado 2026-08-04 | 6, 7 |
| **6** | Memoria persistente: checkpointer + store + langmem | ✅ Cerrado 2026-08-04 | 7, 9 |
| **7** | Auth JWT + roles + aislamiento por usuario | 🟡 Núcleo cerrado 2026-08-04 (7.E pendiente) | 10 |
| **8** | Tools async, skills, MCP, intent router | Pendiente | 9 |
| **9** | Guardrails + evals ampliados | Pendiente | 11 |
| **10** | Contenedor + CI/CD + infraestructura | Pendiente | 11 |
| **11** | Deploy, observabilidad, cierre TFP | Pendiente | — |

> **Sprint 6 cerrado.** Checkpointer + store + composite backend +
> `MemoryMiddleware` (seed de `/memories/AGENTS.md`) + langmem
> (`ProfileHydrationMiddleware`/`ProfilePersistMiddleware`) cableados en
> `create_spark_agent()` y en el lifespan de `app.py` (PRs #31–#33 a `dev`).
> El particionado por `user_id` existe estructuralmente en todos los
> namespaces pero usa un placeholder fijo (`src/agent/user_context.py`)
> hasta que el Sprint 7 provea el real vía JWT — ver AGENTS.md §1.1. Sigue
> el Sprint 7 (auth JWT), que además reemplaza ese placeholder por el
> `user_id` real.
>
> **Sprint 7 — núcleo cerrado (7.A–7.D).** JWT validado (`src/auth/`),
> `thread_id` derivado + registro de propiedad (7.B), `context_schema=AgentContext`
> cablea `runtime.context.user_id/role/email` en todo el grafo (7.C), modelo
> de roles/capacidades (7.D). El `user_id` placeholder del Sprint 6 queda
> reemplazado por el real en toda request autenticada. Pendiente: 7.E
> (CORS validator, cabeceras de seguridad, rate limiting, budget por
> usuario en el store) en un PR de endurecimiento aparte — ver `docs/auth.md`.

`IMPROVEMENTS.md` documenta los Sprints 1–4, ya cerrados. Es histórico: **no lo uses como backlog**.

---

## 11. Prioridad de alertas de seguridad

> **Regla dura**: toda alerta de Dependabot, CodeQL o GitHub Advanced Security tiene **prioridad P0** y es **bloqueante para el merge**.

Workflow obligatorio al encontrar una alerta:

1. **Clasificar**: Dependabot (dependencia), CodeQL (código), GHAS/Code Scanning (SARIF de terceros).
2. **Dependabot**: mergear el PR automático o actualizar a mano.
3. **CodeQL**: arreglar el código. Si es un falso positivo real, suprimir inline con justificación explícita.
4. **Verificar** antes de mergear:
   ```powershell
   $url = "/repos/spark-match/spark-match-08-deep-agent/code-scanning/alerts?state=open"
   gh api "$url"
   ```
5. **Documentar** la acción en el body del PR.

### 11.1 Nunca inventes una justificación

Lección aprendida en `spark-match-02-infrastructure` (PR #65): se dismissaron 3 alertas con una razón conceptualmente incorrecta, hubo que reabrirlas y re-dismissarlas. Reglas derivadas:

- **Nunca** dismisses una alerta cuya regla no entiendes por completo. Lee la documentación de la regla y verifica contra el código.
- Si la alerta es legítima pero el fix está fuera del scope del PR, dismissar con `won't fix` **y** un comentario que indique: por qué es legítima, dónde está trackeado el fix (sprint/tarea del roadmap) y quién es responsable.
- Antes de dismissar, hacer `rg "<símbolo>"` en el repo para confirmar el supuesto.
- El historial de dismissals queda registrado en GitHub para auditoría. Una razón falsa ahí es un problema de governance serio.

**Anti-pattern**: dismissar en masa sin justificación u ocultar alertas para desbloquear un merge.

---

## 12. Política de admin bypass

**Permitido solo si se cumplen las TRES condiciones:**

1. **Todos** los required checks en `SUCCESS`, y
2. No hay reviewer disponible del team `@spark-match/ai-devs` (nocturno, urgencia operativa, sin quórum), y
3. Queda documentado en la descripción del PR y en el commit message con una razón operativa concreta (no "fix urgente" genérico).

**Prohibido cuando:**

- Cualquier check en `FAILURE` — incluidos lint, mypy, coverage y evals.
- Hay alertas CodeQL / Dependabot / GHAS abiertas.
- El coverage baja del umbral.
- "Solo para mergear rápido".

> **Anti-pattern**: usar admin bypass para saltarse checks en rojo. Si un check falla, se arregla el problema raíz. Esto oculta fallas de tooling, cobertura o seguridad y destruye la capacidad de auditar.

---

## 13. Deuda de gobernanza conocida (pendiente de arreglar)

Estado verificado al 2026-08-04. G4, G6 y G8 siguen abiertos y se cierran en el
Sprint 10 (§10.C del roadmap); requieren CI real / pipelines que aún no existen
(ver `../spark-match-01-devops/AGENTS.md` §7.2 sobre las recetas Python borradas
el 2026-08-02). El resto se resolvió sin depender de CI:

| # | Problema | Detalle |
|---|---|---|
| G1 | ~~`CODEOWNERS` usa catch-all `*`~~ | **Resuelto.** `main` migró a paths explícitos el 2026-07-26 (`1b08968` + `6bc0c10`); `dev` recuperó esa versión en el PR que introdujo este archivo. |
| G2 | ~~`pull_request_template.md` contradice `CODEOWNERS`~~ | **Resuelto.** El template decía que solo `@spark-match/product-owners` puede aprobar y que `ai-devs` no puede; se corrigió para reflejar el ruleset real (`reviewerTeam: ai-devs`, `product-owners` co-owner solo en paths de gobernanza). |
| G3 | ~~`CODEOWNERS` referencia paths inexistentes~~ | **Resuelto.** `/decisions/`, `/onboarding/`, `/postmortems/`, `/CONTRIBUTING.md`, `/LICENSE` no existen en el repo; se quitaron las entradas fantasma en vez de crear directorios vacíos. Si se crean en el futuro, añadir su entrada en el mismo PR. |
| G4 | **`statusChecks: []`** | La entrada de este repo en `governance/repository-governance.json` no exige ningún check. Poblarla tras crear los pipelines (R1–R3 del roadmap). |
| G5 | ~~Sin `.commitlintrc.json`~~ | **Resuelto.** Config añadida con el type-enum y scope-enum de §4 de este documento. El enforcer en CI (`reusable-commitlint`) llega con el Sprint 10. |
| G6 | **Sin release-please** | `pyproject.toml` dice `0.3.0` (ya corregido, bug B10 del Sprint 5), `CHANGELOG.md` declara `0.3.0` released. Configurar `release-please` es cosmético sin el CI que lo dispare; se hace junto al Sprint 10. |
| G7 | ~~Sin `dependabot.yml`~~ | **Resuelto.** Ecosistemas `uv` y `github-actions` añadidos. El ecosistema `docker` se añade en el Sprint 10 cuando exista un `Dockerfile` real. |
| G8 | **Sin proyecto SonarCloud** | Verificado de nuevo el 2026-08-04: la búsqueda de proyectos `spark-match` sigue sin devolver este repo. SonarCloud auto-provisiona el proyecto en el primer análisis real vía CI, o requiere alta manual en la UI; ninguna de las dos vías es posible sin el pipeline del Sprint 10. |
| G9 | ~~`main` y `dev` divergieron en contenido~~ | **Resuelto** el 2026-08-04 (ver §13.1 histórico, PR #24). |
| G10 | **`main`/`dev` sin ancestría git real** | Descubierto el 2026-08-04 al intentar sincronizar el cierre del Sprint 5 (PR #29). Ver §13.2. |

Al añadir un path nuevo de primer nivel, **agrega su entrada en `CODEOWNERS` en el mismo PR**.

### 13.1 Divergencia `main` ↔ `dev` (detectada 2026-08-04)

`main` recibió **tres commits directos el 2026-07-26** que nunca pasaron por `dev`, violando el flujo de §3:

| Commit | Contenido | Estado |
|---|---|---|
| `1b08968` | CODEOWNERS a paths explícitos | Portado a `dev`, cierra G1 |
| `6bc0c10` | CODEOWNERS cubre `/.gitignore` | Portado a `dev` |
| `f6dac3d` (PR #19) | "normalize affinity score to 0-1" | **Aplicado sobre un archivo muerto**, ver abajo |

**Problema 1 — el fix de B4 no existe en ninguna rama.**
PR #19 modificó `src/tools/matching.py`, el módulo plano que el Sprint 4 sustituyó por el paquete `src/tools/matching/`. El código vivo es `src/tools/matching/handler.py`, que sigue sin el fix. En `dev` el fichero plano ni siquiera existe. **B4 sigue abierto**: hay que rehacer el fix sobre `handler.py` con su test de regresión (tarea 5.4 del roadmap).

**Problema 2 — `main` arrastra 668 líneas de código muerto.**
El sync PR #18 ("Sprint 4 final") añadió los paquetes nuevos pero **no borró los módulos planos**. Hoy `main` contiene ambos:

```
src/tools/assessment.py     ← muerto        src/tools/assessment/     ← vivo
src/tools/catalog.py        ← muerto        src/tools/catalog/        ← vivo
src/tools/matching.py       ← muerto        src/tools/matching/       ← vivo
src/tools/web_search.py     ← muerto        src/tools/web_search/     ← vivo
src/prompts/system.py       ← muerto
tests/test_{logging,models,tools}.py  ← muertos
```

En Python un módulo y un paquete con el mismo nombre no pueden coexistir sin ambigüedad de import. `dev` tiene el árbol correcto.

**Consecuencia operativa**: el primer sync `dev` → `main` posterior a esta nota borrará esos 8 ficheros. Es el comportamiento deseado. Antes de lanzarlo hay que confirmar que ningún commit exclusivo de `main` se pierda: revisar `git log origin/dev..origin/main` y portar a `dev` lo que sea legítimo.

**Lección**: esta divergencia es exactamente lo que previene §3. Ningún commit entra a `main` si no pasó antes por `dev`.

### 13.2 `main`/`dev` sin ancestría git real (detectado 2026-08-04, resuelto el mismo día)

Al intentar el sync `dev` → `main` de cierre del Sprint 5 (PR #29), el PR quedó
`CONFLICTING` con `add/add` en casi todos los archivos tocados desde el
Sprint 4, pese a que `git diff --stat origin/main origin/dev` mostraba un
diff limpio y puramente aditivo.

**Causa raíz**: todo el historial de syncs de este repo (incluida la
reconciliación de §13.1 vía PR #24) usó squash o `commit-tree` con un solo
padre — ninguno de los dos crea ancestría git real. Git solo encontraba
como ancestro común un commit anterior al refactor del Sprint 4, así que
cualquier archivo creado después de ese punto (prácticamente todo el
proyecto) aparecía como "añadido de forma independiente en ambos lados" en
el merge de 3 vías, sin importar que el contenido fuera idéntico.

**Fix aplicado**: se construyó un commit de merge real (2 padres: el tip
anterior de `main` + el tip de `dev`) con `git commit-tree`, y se hizo push
directo a `main` bajo la "dual-disable dance" documentada en
`spark-match-01-devops/CONTRIBUTING.md` (flip temporal de
`bypass_mode` del ruleset a `always` + `enforce_admins` a `false`, ventana
de ~5 segundos, ambos restaurados de inmediato). Ver el comentario de
cierre del PR #29 y el mensaje del commit `82bf13b` para el detalle
completo.

**Verificación post-fix**:

```powershell
git diff --stat origin/main origin/dev        # vacío
git merge-base origin/main origin/dev          # == tip de origin/dev
git show -s --format="parents=%P" origin/main  # 2 padres reales
```

Los 3 checks confirmaron ancestría real establecida por primera vez entre
`main` y `dev`. Mientras se mantenga la regla de nunca commitear directo a
`main` (§3), los futuros syncs `dev` → `main` —incluso vía squash normal—
deberían computar un merge-base reciente y relevante en vez de saltar al
ancestro antiguo previo al Sprint 4.

**Lección**: un sync recurrente basado en squash o `commit-tree` de un solo
padre nunca establece ancestría real; solo iguala contenido. Si el repo
necesita reconciliaciones repetidas, la ancestría de 2 padres (como en este
fix) es la única solución durable, aunque requiera un bypass documentado
una única vez.

---

## 14. Lo que NO debes hacer

- Commitear directo a `dev` o `main`.
- Abrir un PR de feature contra `main`.
- `git push --force` a `dev` o `main`.
- Sincronizar `dev` ← `main` sin una emergencia documentada.
- Usar `gh pr create --body "..."` en PowerShell 5.1 (usar `--body-file`).
- Mergear con checks en rojo, con o sin `--admin`.
- Añadir código sin tests en el mismo PR.
- Añadir dependencias no listadas en `ROADMAP-2026-08.md` §7 sin justificarlo en el PR.
- Modificar recetas de `spark-match-01-devops` o el Quality Gate de SonarCloud desde este repo.
- Duplicar aquí un pipeline que debería vivir en el catálogo del org.
- Hardcodear prompts en Python (van a `src/prompts/*.md`).
- Meter lógica de negocio dentro de un `@tool` (va en `handler.py`).
- Usar un `model_id` fuera del allowlist IAM.
- Crear namespaces de memoria sin `user_id`.
- Confiar en el `thread_id` que manda el cliente.
- Citar `README.md` o `IMPROVEMENTS.md` como estado actual del repo.
- Commits vagos: `update`, `fix stuff`, `wip`.
- Ramas de larga duración o PRs gigantes que acumulan un sprint entero.

---

## 15. Referencias

| Documento | Para qué |
|---|---|
| [`ROADMAP-2026-08.md`](ROADMAP-2026-08.md) | **Backlog canónico.** Estado verificado, sprints, snippets, solicitudes cross-repo |
| [`IMPROVEMENTS.md`](IMPROVEMENTS.md) | Histórico de los Sprints 1–4 (cerrados). No es backlog |
| [`README.md`](README.md) | Overview del producto. **Desactualizado**, ver §1.1 |
| [`CHANGELOG.md`](CHANGELOG.md) | Keep a Changelog + SemVer |
| `../orion/AWS-DEEPAGENT-VS-AWS-RUNTIME-VS-AWS-HARNESS.md` | Decisión de arquitectura Runtime vs Harness vs Deep Agents |
| `../orion/AWS-HARNESS-HARNESS-POC.md` | Bondades del Harness que estamos replicando |
| `../orion/AWS-HARNESS-POC-V10.md` | Métricas empíricas del POC v2 (referencia de rendimiento) |
| `../spark-match-01-devops/AGENTS.md` | Convenciones de CI/CD del org, kebab-case, pinning, catálogo |
| `../spark-match-02-infrastructure/AGENTS.md` | Convenciones de infra, sync `dev`→`main`, política de alertas |
| `../spark-match-02-infrastructure/docs/IAM_ROLES.md` | Roles OIDC y permisos que este agente puede asumir |
| `../spark-match-03-backend/AGENTS.md` | Metodología reuse-first (§12.2) y gates de calidad |
| `../spark-match-03-backend/docs/auth-rbac.md` | Contrato JWT que este agente debe validar |

---

**Owner**: `@ahincho` · **Reviewers**: `@spark-match/ai-devs` (CODE OWNERS).
**Mantenido por**: opencode + el equipo de Spark Match.
