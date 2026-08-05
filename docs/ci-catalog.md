# CI/CD Catalog Consumer

> Estado: Sprint 10.B (caller PR). Convenciones heredadas de
> `spark-match-01-devops/AGENTS.md` (kebab-case, `@main` pin policy,
> reuse-first, IAM allowlist).

Este repositorio consume el **catalogo reutilizable** de
`spark-match-01-devops` en vez de mantener su propio CI/CD. Esto elimina
la duplicacion, mantiene un solo punto de actualizacion de tooling y
asegura consistencia con el resto del org.

## Workflows propios

| Archivo | Proposito |
|---|---|
| `ci.yml` | Lint + typecheck + tests + coverage + evals + sonar + codeql + gitleaks + actionlint + yamllint. Trigger: `push` y `pull_request` a `main`/`dev`, `workflow_dispatch`. |
| `deploy.yml` | Build + push a ECR via OIDC **y rotacion del servicio ECS**. Trigger: `push` a `dev` o `main`, `workflow_dispatch` con input `environment`. Jobs separados por env (`dev`/`production`), cada uno en dos pasos: `deploy-*` (publica la imagen) y `roll-*` (la pone delante del trafico). |
| `commitlint.yml` | Enforce Conventional Commits sobre PRs y pushes. |
| `release-please.yml` | Release automation desde `main`. Genera PR con bump + changelog. |

## Reusables consumidos

Todos los reusables viven en
[`spark-match-01-devops`](https://github.com/spark-match/spark-match-01-devops/tree/main/.github/workflows)
y se pinean a `@main` (ver `docs/VERSIONING.md` en ese repo, linea 5).

| Reusable | Uso en este repo | Inputs custom |
|---|---|---|
| `reusable-python-ci.yml` | Job `python-ci` (lint + typecheck + tests + coverage gate) | `pytest-args='--cov=src --cov-report=xml:coverage.xml --cov-fail-under=80'`, `coverage-threshold='80'` |
| `reusable-sonar-python.yml` | Job `sonar-python` (quality gate sobre `coverage.xml`) | `coverage-paths='coverage.xml'`, `fail-on-quality-gate='true'` |
| `reusable-codeql.yml` | Job `codeql` (Python + GitHub Actions) | `languages='python,actions'`, `fail-on-severity='warning'` |
| `reusable-gitleaks.yml` | Job `gitleaks` (secretos) | `GITLEAKS_LICENSE` desde secret org-level |
| `reusable-actionlint.yml` | Job `actionlint` (workflow lint) | (sin overrides) |
| `reusable-yamllint.yml` | Job `yamllint` (yaml lint) | (sin overrides) |
| `reusable-commitlint.yml` | Job `commitlint` | `config-path='.commitlintrc.json'`, `commit-depth=20`, `help-url` apuntando al AGENTS.md de este repo |
| `reusable-release-please.yml` | Job `release-please` | secrets `RELEASE_PLEASE_APP_ID` y `RELEASE_PLEASE_APP_PRIVATE_KEY` org-level |
| `reusable-container-deploy-ecr.yml` | Jobs `deploy-dev`, `deploy-production`, `deploy-manual` | `platforms='linux/arm64'`, `provenance=true`, `sbom=true`, `deploy-role-arn` desde var repo-level |
| `reusable-ecs-deploy.yml` | Jobs `roll-dev`, `roll-production`, `roll-manual` | `image-uri` desde el output `image-uri` del job de ECR correspondiente (pinneado por digest) |

## Environments de GitHub

| Nombre | Branch policy | Gate |
|---|---|---|
| `dev` | custom (push a `dev` y manual) | sin reviewer |
| `production` | custom (push a `main` y manual) | reviewer humano |

Los environments siguen llamandose **`dev`** y **`production`** sin sufijo,
como el resto del org (AGENTS.md §1.3 de 02-infrastructure y 03-backend).

Las **variables**, en cambio, son **repo-level y con sufijo `_DEV`/`_PROD`**:

| Variable | dev | production |
|---|---|---|
| `ECR_REPOSITORY_*` | `spark-match-agent-advisor-dev` | `spark-match-agent-advisor-prod` |
| `ECS_CLUSTER_*` | `spark-match-dev` | `spark-match-prod` |
| `ECS_SERVICE_*` | `spark-match-agent-dev` | `spark-match-agent-prod` |
| `ECS_CONTAINER_*` | `agent` | `agent` |
| `AWS_BEDROCK_AGENTCORE_DEPLOY_ROLE_ARN_*` | rol dev | rol prod |

> Esto **no** es duplicacion por descuido: una variable con scope de
> environment **no se resuelve** en el bloque `with:` de una llamada a un
> reusable, porque el environment se enlaza dentro del workflow *llamado*,
> no en el caller. Con `vars.ECR_REPOSITORY` env-scoped el input llegaba
> vacio y el reusable moria en `Input validation failed: ecr-repository:
> required` (corregido en #58). El mismo patron lo aplica
> `spark-match-04-frontend`.

## Pin policy

Todos los `uses:` apuntan a `@main`, **nunca** a un tag concreto. La
excepcion son `reusable-commitlint` y `reusable-release-please`, que en
algunos callers se pinean a tag — en este repo seguimos `@main` por
simplicidad (no somos un caller de releases externos).

Justificacion en `docs/VERSIONING.md` (01-devops, linea 5).

## Permisos declarados

Todos los workflows declaran `permissions:` en modo minimo:

```yaml
permissions:
  contents: read
  pull-requests: read   # o write si el job crea PRs
  id-token: write       # solo deploy
  security-events: write  # solo codeql
```

Los reusables no elevan permisos por si mismos; dependen de lo que
declare el caller.

## Variables del repositorio (no env-scoped)

| Variable | Origen | Proposito |
|---|---|---|
| `DEFAULT_PYTHON_VERSION` | org | Version de Python usada por runners |
| `SONAR_ORGANIZATION` | org | Org key de SonarCloud |
| `SONAR_FAIL_ON_QUALITY_GATE` | org | `true` para bloquear merge en quality gate ERROR |
| `SONAR_PROJECT_KEY` | repo | `spark-match-08-deep-agent` |
| `SONAR_PROJECT_NAME` | repo | `Spark Match - Deep Agent` |
| `SONAR_SOURCES` | repo | `src` |
| `SONAR_TESTS` | repo | `tests,evals` |
| `SONAR_EXCLUDE_PATTERNS` | repo | `.venv`, `__pycache__`, `.git`, `skills` |

## Secrets del repositorio (no env-scoped)

| Secret | Origen | Proposito |
|---|---|---|
| `SONAR_TOKEN` | org | Auth de SonarCloud |
| `GITLEAKS_LICENSE` | org | Licencia gitleaks (org-scoped repos) |
| `RELEASE_PLEASE_APP_ID` | org | GitHub App ID para release-please |
| `RELEASE_PLEASE_APP_PRIVATE_KEY` | org | GitHub App private key |

## Variables env-scoped (deploy)

Las variables con scope de environment **no llevan sufijo**. El env de
GitHub provee el scope automaticamente.

| Env | Variable | Valor actual | Pendiente |
|---|---|---|---|
| `dev` | `ECR_REPOSITORY` | `spark-match-agent-advisor-dev` | - |
| `dev` | `AWS_BEDROCK_AGENTCORE_DEPLOY_ROLE_ARN` | placeholder `arn:aws:iam::681526276858:role/spark-match-bedrock-agentcore-deploy-dev-PLACEHOLDER` | **02-infrastructure** debe crear el rol real y actualizar la variable |
| `production` | `ECR_REPOSITORY` | `spark-match-agent-advisor-production` | ECR repo no provisionado |
| `production` | `AWS_BEDROCK_AGENTCORE_DEPLOY_ROLE_ARN` | placeholder | **02-infrastructure** debe crear el rol real |

## IAM allowlist (referencia rapida)

El rol `spark-match-bedrock-agentcore-deploy-{env}` ya confia en este
repo por OIDC para:

- `repo:spark-match/spark-match-08-deep-agent:ref:refs/heads/dev`
- `repo:spark-match/spark-match-08-deep-agent:ref:refs/heads/main`
- `repo:spark-match/spark-match-08-deep-agent:environment:dev`
- `repo:spark-match/spark-match-08-deep-agent:environment:production`

El ARN debe pasarse al workflow como **input string** (no secret) para
evitar el enmascarado cross-owner que rompe `assume-role`. Ver
AGENTS.md §7.3 (este repo, patron del PR #241/#242 de 01-devops).

## Como anadir un nuevo reusable

1. Verificar que el reusable existe en `01-devops/.github/workflows/`.
2. Verificar que cumple AGENTS.md §7 (kebab-case, permisos minimos, no SHA pinning).
3. Anadir el job en `ci.yml` o el workflow correspondiente con `uses:` apuntando a `@main`.
4. Si requiere secret, pasarlo explicitamente (no `secrets: inherit`).
5. Documentar el uso en este archivo (seccion "Reusables consumidos").
6. Probar en PR antes de mergear.

## Como actualizar un reusable

Los reusables evolucionan en `01-devops`. Para tomar cambios:

1. Sync de `main` de `01-devops` ya esta pinneado (`@main` resuelve siempre al HEAD).
2. Si hay breaking change, abrir issue en `01-devops` antes de actualizar.
3. Probar en PR de este repo. Si rompe, fijar temporalmente a SHA conocido y abrir issue upstream.

No se aceptan breaking changes sin coordination.
