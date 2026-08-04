# Autenticación y autorización (Sprint 7)

> Contrato exacto entre `spark-match-08-deep-agent` y el JWT emitido por
> `spark-match-03-backend`. Ver también `AGENTS.md` §1.1 (hard rules #4, #5, #6)
> y `ROADMAP-2026-08.md` Sprint 7 para el diseño original.

## 1. Contrato del token

El backend TypeScript firma tokens **HS256** con:

| Claim | Valor | Obligatorio |
|---|---|---|
| `iss` | `spark-match-backend` | Sí |
| `aud` | `spark-match-api` | Sí |
| `sub` | `user_id` del usuario | Sí |
| `exp` / `iat` | timestamps estándar | Sí |
| `email` | string, puede faltar | No (default `""`) |
| `role` | string, puede faltar o ser desconocido | No (default: rol menos privilegiado) |

La clave de firma se usa como **bytes UTF-8 crudos** — nunca base64-decode ni
`json.loads` (hard rule #6 en `AGENTS.md`). `src/auth/jwt_validator.py` valida
`iss`/`aud`/expiración/firma y exige `sub`, `iat`, `exp` con
`options={"require": [...]}`.

## 2. Resolución del secreto

`src/auth/secret_loader.py` resuelve el secreto en este orden:

1. **`SPARK_JWT_SECRET`** (override local/dev) — usado tal cual. Permite que
   el evaluador del TFP corra y pruebe auth completamente offline (hard rule
   #7).
2. **SSM → Secrets Manager** (producción): `SPARK_JWT_SECRET_SSM_PARAM`
   (default `/spark-match/secret/jwt-arn`, el mismo path que lee
   `03-backend`) apunta al ARN del secreto real en Secrets Manager.

El resultado se cachea en proceso por `SPARK_JWT_SECRET_CACHE_SECONDS`
(default 300s).

## 3. Dos rutas de validación (`src/auth/dependencies.py::require_auth`)

1. **Lambda Authorizer de API Gateway** — si el agente corre detrás del
   mismo API Gateway que `03-backend`, sus claims ya vienen validadas en
   `request.scope["aws.event"]["requestContext"]["authorizer"]["lambda"]`.
   Se confían ciegamente (igual que hace el backend TS), **sin
   re-verificar la firma**.
2. **Bearer directo** — único camino en despliegue ECS + ALB. Valida el JWT
   contra el secreto resuelto arriba.

Cualquier fallo en ambas rutas → `401`.

## 4. `thread_id`: derivación + registro de propiedad

El `thread_id` que manda el cliente **no es de confianza** (hard rule #5).
`src/api/app.py::ag_ui_endpoint` aplica dos medidas, ambas activas:

1. **Derivación** (`src.auth.thread_guard.derive_thread_id`): el id efectivo
   usado por el checkpointer es `sha256(f"{user_id}:{client_thread_id}")`.
   Dos usuarios distintos nunca colisionan en el mismo id derivado, aunque
   ambos usen el mismo string del lado cliente.
2. **Registro** (`src.auth.thread_guard.assert_thread_ownership`): la primera
   vez que se ve un `thread_id` derivado, se registra su `user_id` dueño en
   el store (`namespace=("spark-match", "_threads")`). Llamadas posteriores
   con un `user_id` distinto para el mismo id derivado devuelven `403`. Esto
   es defensa en profundidad: en el diseño normal nunca debería dispararse
   (la derivación ya previene la colisión), pero es lo único realmente
   auditable — permite responder "¿de quién es este thread?" sin depurar el
   hash.

Con un `store=None` (grafo sin persistencia, la mayoría de tests unitarios)
`assert_thread_ownership` es un no-op: no hay dónde registrar ni qué
historial proteger.

## 5. `runtime.context` y `context_schema`

`create_spark_agent()` pasa `context_schema=AgentContext`
(`src/auth/context.py`) a `create_deep_agent`. El endpoint `/ag-ui` puebla
`config["configurable"]` con `thread_id`/`user_id`/`role`/`email` antes de
invocar el grafo:

```python
request_agent.config = {
    "configurable": {
        "thread_id": thread_id,
        "user_id": auth.user_id,
        "role": auth.role,
        "email": auth.email,
    }
}
```

`ag_ui_langgraph` 0.0.42 hace `base_context.update(config["configurable"])`
internamente, así que **`runtime.context.user_id`/`.role`/`.email` quedan
disponibles en todo middleware y tool** del grafo, y `"{user_id}"` en los
namespaces de langmem (`PROFILE_NAMESPACE`, `PREFS_NAMESPACE`) se sustituye
por el valor real.

### Placeholder heredado del Sprint 6

`src/agent/user_context.py::get_user_id(runtime)` sigue existiendo como
fallback a `DEFAULT_USER_ID = "local-user"` para invocaciones directas del
grafo compilado que **no** pasan por `/ag-ui` (la mayoría de tests
unitarios, o un futuro entry point no-HTTP). Nunca se usa en una request
autenticada real — `require_auth` rechaza toda request sin JWT válido antes
de que el grafo corra.

## 6. Roles y capacidades (`src/auth/roles.py`)

**Realidad actual del backend**: solo emite `role="admin"` en la práctica.
`docente`/`graduado` están planificados en una migración futura de
`03-backend`; `student` no existe todavía como concepto en el backend.

```python
class Role(StrEnum):
    ADMIN = "admin"
    DOCENTE = "docente"     # planificado, no emitido aún
    GRADUADO = "graduado"   # planificado, no emitido aún
    STUDENT = "student"     # no existe en el backend todavía

DEFAULT_ROLE = Role.STUDENT  # fallback explícito, el MENOS privilegiado
```

`resolve_role(raw_role)` mapea el claim `role` del JWT a un `Role` conocido,
cayendo a `DEFAULT_ROLE` si el valor es `None` o desconocido — un rol no
reconocido **nunca** se trata como más privilegiado que el default.

`CAPABILITIES` define qué tools/capacidades tiene cada rol
(`has_capability(role, capability)`), pero **todavía no hay un
`wrap_tool_call` que lo aplique** — el modelo de datos existe y está
testeado, la autorización activa por tool queda para cuando el backend
realmente emita roles distintos de `admin`/nada.

## 7. Limitaciones conocidas / deuda explícita

- **Roles reales no emitidos por el backend todavía**: `CAPABILITIES` está
  diseñado contra el conjunto de roles *planeado*, no el actual. Nada se
  rompe cuando el backend empiece a emitir `docente`/`graduado`; sí hay que
  añadir el `wrap_tool_call` que efectivamente los haga cumplir.
- **CORS / cabeceras de seguridad / rate limiting / budget por usuario en
  el store** (tareas 7.E.1–7.E.4 del roadmap) — **fuera de alcance de este
  PR**, se abordan en un PR de endurecimiento (`7.E`) separado. `src/budget.py`
  sigue siendo un `dict` de proceso (no compartido entre workers) hasta
  entonces.
- **`docente`/`graduado` sin capacidades diferenciadas reales**: hoy
  `CAPABILITIES` ya las distingue de `student`, pero como el backend no las
  emite, esto no se ha probado contra un JWT real con esos roles — solo
  contra claims sintéticos en tests.
