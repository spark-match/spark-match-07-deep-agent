# Servidor MCP (Sprint 8, tarea 8.5)

> Expone las 4 tools del agente (`evaluate_riasec_profile`, `search_careers`,
> `calculate_affinity`, `web_search`) como servidor [Model Context
> Protocol](https://modelcontextprotocol.io/) para que clientes MCP externos
> (Claude Desktop, otros agentes) puedan invocarlas directamente. Ver
> `ROADMAP-2026-08.md` Sprint 8 para el diseño original y `AGENTS.md` §1.1.

## 1. Alcance: solo exposición, no consumo

El roadmap original describía la tarea 8.5 como "exponer las 4 tools como
servidor MCP **y/o** consumir tools externas". Esta implementación cubre
**únicamente la exposición**. Consumir servidores MCP de terceros habría
requerido elegir un servidor externo arbitrario de ejemplo — una decisión de
diseño no especificada en ningún lado — y queda fuera de alcance de este PR.

## 2. Dependencia: `mcp`, no `langchain-mcp-adapters`

El roadmap nombraba `langchain-mcp-adapters` para esta tarea, pero esa
librería es un **cliente**: convierte servidores MCP externos en tools de
LangChain (la dirección "consumir", no "exponer"). No tiene ninguna API de
servidor.

El framework de servidor (`MCPServer`, antes llamado `FastMCP` en la línea
1.x) vive en el SDK oficial **`mcp`** — la misma dependencia base que
`langchain-mcp-adapters` usa internamente para sus propios servidores de
ejemplo. Es la dependencia correcta y mínima para el alcance de "solo
exponer" de este PR.

> **Nota de versión**: `mcp>=2.0` es un requisito real, no cosmético. La
> versión 2.0 renombró `FastMCP` → `MCPServer` y movió el módulo
> (`mcp.server.fastmcp` → `mcp.server.mcpserver`) respecto a la documentación
> pública de `langchain-mcp-adapters` (que todavía muestra ejemplos con la
> API 1.x). Confirmado instalando e introspeccionando el paquete real, no
> asumido de la documentación.

## 3. Arquitectura: un handler, dos adaptadores de protocolo

`src/mcp/server.py` registra las funciones **handler** puras
(`src/tools/*/handler.py`), no los objetos `@tool` de LangChain
(`src/tools/*/tool.py`). `MCPServer.tool()` introspecciona la firma y el
docstring de una función Python plana — exactamente lo mismo que envuelve
`@tool` de LangChain — así que reutilizar el handler mantiene este módulo
como un delegador fino, consistente con la separación handler/tool de
`AGENTS.md` §6: la misma lógica de negocio respalda ambos protocolos (LangChain
y MCP), nunca dos copias que puedan divergir.

```
src/tools/assessment/handler.py  →  evaluate_riasec_profile_handler()
                                          │
                    ┌─────────────────────┴─────────────────────┐
                    │                                           │
        src/tools/assessment/tool.py           src/mcp/server.py
        (@tool de LangChain, usado             (@mcp_server.tool(),
         por create_spark_agent)                servidor MCP standalone)
```

## 4. Manejo de errores

`_unwrap_or_raise()` adapta el sobre `{"status", "data", "errors"}` de los
handlers a la convención de MCP: si `status != "success"`, lanza
`mcp.server.mcpserver.exceptions.ToolError` en vez de devolver un dict de
estado. Esto hace que el framework represente el fallo como un error de
tool-call real ante el cliente MCP, en vez de una llamada "exitosa" cuyo
payload resulta ser una descripción de fallo.

## 5. Ejecutar el servidor

```powershell
uv run python -m src.mcp
```

Arranca el servidor MCP con transporte `stdio` (el estándar para clientes
locales como Claude Desktop). El proceso queda esperando en stdin/stdout —
no imprime nada por sí solo; es el cliente MCP quien inicia la conexión.

## 6. `.mcp.json`

El archivo `.mcp.json` en la raíz del repo sigue la convención estándar de
manifiesto MCP (`mcpServers`) que leen Claude Desktop, Claude Code y otras
herramientas compatibles con MCP:

```jsonc
{
  "mcpServers": {
    "spark-match-agent": {
      "command": "uv",
      "args": ["run", "python", "-m", "src.mcp"],
      "description": "..."
    }
  }
}
```

Debe lanzarse desde la raíz del repositorio para que `uv run` resuelva el
`.venv` del proyecto correctamente (la mayoría de clientes MCP con
configuración de proyecto ya invocan el comando con `cwd` en el directorio
que contiene el `.mcp.json`).

## 7. Tests

`tests/mcp/server.py` ejercita el servidor real vía su propia API
`list_tools()`/`call_tool()` — la misma superficie que usaría un cliente MCP
real — en vez de llamar directamente a las funciones Python registradas, para
también detectar errores de registro (nombre incorrecto, tool perdida, firma
rota). Incluye un test que confirma que la función registrada delega en el
**mismo** handler que usa el wrapper `@tool` de LangChain (una sola
implementación, no dos copias).

## 8. Limitaciones conocidas

- Sin autenticación en el servidor MCP en sí — a diferencia del endpoint
  `/ag-ui` (JWT, Sprint 7), el transporte `stdio` asume un cliente local de
  confianza (el modelo de seguridad estándar de MCP para ese transporte).
- Sin budget/rate-limiting propio — reutiliza `src/budget.py` para
  `web_search` (el mismo guard que usa el agente principal), pero no tiene
  un límite de invocaciones específico del servidor MCP.
- No consume servidores MCP externos (ver §1).
