"""Rehidratando una conversacion desde el checkpointer.

El checkpointer ya guarda cada turno — es lo que hace que el agente
recuerde. Lo que faltaba era que el frontend pudiera *leerlo*: al recargar
la pagina el navegador no tiene registro de la conversacion en la que
estaba, y el endpoint AG-UI solo emite turnos nuevos.

No todo lo que hay en el checkpoint es conversacion.
``ProfileHydrationMiddleware`` inyecta un ``SystemMessage`` con el perfil
vocacional extraido en cada turno, y aterriza en el mismo canal
``messages``. Devolver el checkpoint tal cual mandaria ese bloque al
navegador como parte del historial.

## La actividad tambien se rehidrata, y por que no basta con dejar de filtrar

La primera version de este modulo tiraba las llamadas a herramienta
enteras. El resultado: al recargar, los chips de actividad desaparecian y
la respuesta quedaba sin procedencia. El estudiante veia unas cifras y
ninguna pista de si salieron del catalogo del MINEDU, de una busqueda en
internet o de un especialista — que es justo lo que decide cuanto fiarse.

Pero devolver los mensajes de herramienta en crudo tampoco vale, y por el
motivo original: los ``ToolMessage`` llevan resultados de busqueda enteros.
Asi que lo que sale de aqui es un **resumen por llamada**, nunca el
payload:

- El nombre de la herramienta, para que el frontend le ponga su etiqueta.
  La copia en castellano vive en la UI (``tool-labels.ts``) y tenerla
  tambien aqui serian dos sitios donde cambiar el mismo texto.
- Un **asunto**, y solo el que autoriza una lista blanca por herramienta
  (``_ASUNTO_POR_HERRAMIENTA``). Es lo que permite desplegar «6 consultas
  al catalogo» y ver que se busco «ingenieria» y tambien «gestion». Los
  argumentos los escribe el modelo, asi que se eligen uno a uno en vez de
  dejarlos pasar: el ``description`` de ``task``, por ejemplo, es el
  prompt interno del coordinador y no sale nunca — mismo criterio que
  ``src/agent/subagent_events.py``.
- Si fue bien o mal.

La forma es la misma que emite el stream en vivo (``subagent`` con la
clave del especialista, ``toolCallId``), para que el frontend tenga un
solo modelo y no dos caminos que se separen con el tiempo.
"""

from __future__ import annotations

from typing import Any, Protocol

# Nombre con el que deepagents registra la herramienta de delegacion.
_HERRAMIENTA_DE_DELEGACION = "task"

# Que argumento de cada herramienta se puede enseñar. Lista BLANCA: lo que
# no este aqui no sale, aunque la herramienta sea nueva. Un `dict` abierto
# acabaria filtrando el primer argumento sensible que alguien añada.
_ASUNTO_POR_HERRAMIENTA: dict[str, str] = {
    "search_careers": "query",
    "search_programs": "query",
    "web_search": "query",
    "recommend_programs": "riasec_code",
}

# Un asunto es una etiqueta, no un texto. El modelo puede emitir una
# consulta larguisima y esto va dentro de un chip.
_MAX_ASUNTO = 80


class SupportsGetState(Protocol):
    """El trozo de LangGraph compilado que necesita este modulo.

    Leer el checkpointer directamente no funciona: ``aget_tuple`` devuelve
    el ultimo checkpoint, cuyos ``channel_values`` traen solo los canales
    que toco ese paso — en un turno terminado son ``skills_metadata`` y
    ``memory_contents``, sin rastro de ``messages``. Reconstruir el estado
    completo desde los blobs es exactamente lo que ya hace ``aget_state``.
    """

    async def aget_state(self, config: dict[str, Any]) -> Any: ...


# Solo estos llegan al cliente como mensajes.
_CLIENT_VISIBLE_ROLES = {"human": "user", "ai": "assistant"}


def _text_of(content: Any) -> str:
    """Aplana el contenido de un mensaje de LangChain a texto plano.

    Es una cadena en la mayoria de proveedores y una lista de bloques
    tipados en otros (y siempre, en cuanto un turno lleva razonamiento o
    imagenes). Solo sobreviven los bloques de texto.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _recorta(valor: Any) -> str | None:
    if not isinstance(valor, str):
        valor = str(valor) if valor is not None else ""
    limpio = " ".join(valor.split())
    if not limpio:
        return None
    if len(limpio) <= _MAX_ASUNTO:
        return limpio
    return limpio[: _MAX_ASUNTO - 1].rstrip() + "…"


def _actividad_de(tool_call: Any) -> dict[str, Any] | None:
    """Resumen publicable de una llamada a herramienta."""
    if not isinstance(tool_call, dict):
        return None

    nombre = tool_call.get("name")
    if not isinstance(nombre, str) or not nombre:
        return None

    args = tool_call.get("args")
    args = args if isinstance(args, dict) else {}

    resumen: dict[str, Any] = {
        "id": str(tool_call.get("id") or ""),
        "tool": nombre,
        "ok": None,
    }

    if nombre == _HERRAMIENTA_DE_DELEGACION:
        # Solo la clave del especialista. `description`, el otro argumento,
        # es el prompt que el coordinador le redacta y no es para leer.
        subagente = args.get("subagent_type")
        resumen["subagent"] = str(subagente) if subagente else "desconocido"
        return resumen

    campo = _ASUNTO_POR_HERRAMIENTA.get(nombre)
    if campo is not None:
        asunto = _recorta(args.get(campo))
        if asunto is not None:
            resumen["subject"] = asunto

    return resumen


async def load_thread_messages(
    graph: SupportsGetState | None,
    thread_id: str,
) -> list[dict[str, Any]]:
    """Devuelve el historial visible de ``thread_id``, con su actividad.

    Un hilo desconocido o sin usar da una lista vacia en vez de un error:
    para quien reabre una conversacion, «aun no hay mensajes» y «esta
    conversacion nunca existio» son lo mismo, y distinguirlos filtraria si
    un id derivado existe.
    """
    if graph is None:
        return []

    snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    messages = (getattr(snapshot, "values", None) or {}).get("messages") or []

    history: list[dict[str, Any]] = []
    # Actividad acumulada del turno en curso, en orden de llamada.
    pendientes: dict[str, dict[str, Any]] = {}

    for message in messages:
        tipo = getattr(message, "type", "")

        if tipo == "tool":
            # Cierra el estado de su llamada. `status` es "error" cuando la
            # herramienta reviento; cualquier otra cosa cuenta como exito.
            actividad = pendientes.get(str(getattr(message, "tool_call_id", "")))
            if actividad is not None:
                actividad["ok"] = getattr(message, "status", None) != "error"
            continue

        role = _CLIENT_VISIBLE_ROLES.get(tipo)
        if role is None:
            continue

        if role == "user":
            # Empieza un turno nuevo. Lo que quede pendiente es de un turno
            # que se corto a medias -- cerrar la pestaña durante la
            # generacion, por ejemplo -- y un chip suelto sin respuesta
            # debajo parece un fallo de la aplicacion, no un turno
            # interrumpido.
            pendientes = {}
            history.append(
                {
                    "id": getattr(message, "id", None),
                    "role": role,
                    "content": _text_of(message.content),
                }
            )
            continue

        llamadas = getattr(message, "tool_calls", None) or []
        for tool_call in llamadas:
            actividad = _actividad_de(tool_call)
            if actividad is not None:
                pendientes[actividad["id"]] = actividad

        text = _text_of(getattr(message, "content", ""))
        if not text.strip():
            # Un turno del asistente que solo traia llamadas. Real para el
            # grafo, invisible en la conversacion.
            continue

        entrada: dict[str, Any] = {
            "id": getattr(message, "id", None),
            "role": role,
            "content": text,
        }

        # La actividad se cuelga del mensaje que CIERRA el turno, o sea uno
        # con texto y sin llamadas propias. Anthropic emite a veces texto y
        # llamadas en el mismo mensaje ("dejame revisar el catalogo" +
        # `search_careers`): ese texto es preambulo y el turno sigue, asi
        # que colgarle ahi los chips los pondria sobre la frase equivocada.
        if pendientes and not llamadas:
            entrada["activity"] = list(pendientes.values())
            pendientes = {}

        history.append(entrada)

    return history


__all__ = ["load_thread_messages"]
