"""El contrato del historial de actividad: que se publica de una llamada.

Vivia repartido entre ``history.py`` y ``src/agent/subagent_carryover.py``, uno
en cada punta. Esta aqui porque los dos lados lo necesitan y **este modulo no
importa nada de dentro del proyecto**: es la unica forma de que el agente y el
historial compartan la definicion sin que se importen el uno al otro. Cuando lo
intentamos, el ciclo lo cerraba ``src/threads/__init__.py`` -- que importa
``history`` -- y el arranque moria con un ``partially initialized module``.

Dos cosas viven aqui:

**El resumen publicable de una llamada.** Lo usan los dos caminos: el historial
resume las llamadas del grafo padre al rehidratar, y ``SubagentStepsMiddleware``
resume las de dentro de un subagente en el momento. Que compartan funcion no es
comodidad -- la lista blanca decide que argumento de que herramienta sale hacia
el navegador, y dos copias de esa decision se separan el dia que alguien añada
una herramienta y se acuerde de un solo sitio.

**Y hay una asimetria que obliga a filtrar al escribir.** El historial lee
mensajes que el checkpointer ya guardo, asi que podria permitirse resumir al
leer. Los pasos del subagente **se escriben** en el checkpoint: si se guardaran
crudos y se filtraran despues, los argumentos sin autorizar estarian ya
persistidos, que es justo lo que la lista blanca existe para impedir.

**Y las claves con las que una delegacion saca cosas de dentro.** El middleware
las escribe, el historial las lee; ver ``src/agent/subagent_carryover.py`` para
el mecanismo.
"""

from __future__ import annotations

from typing import Any

#: Bajo que clave viaja lo recogido dentro de ``additional_kwargs``. Con
#: prefijo propio porque ese diccionario es de todos: los proveedores escriben
#: ahi lo suyo y un nombre generico acabaria pisando o pisado.
CLAVE = "spark"

#: El id del informe emitido durante la delegacion.
INFORME = "reportId"

#: Las llamadas a herramienta que el subagente hizo por dentro.
PASOS = "steps"


def lo_recogido(mensaje: Any) -> dict[str, Any]:
    """Lo que se saco de una delegacion, o ``{}``.

    Tolera cualquier forma porque lee mensajes escritos por builds anteriores a
    que esto existiera, que no llevan nada nuestro.
    """
    extras = getattr(mensaje, "additional_kwargs", None)
    if not isinstance(extras, dict):
        return {}
    nuestro = extras.get(CLAVE)
    return nuestro if isinstance(nuestro, dict) else {}


# Nombre con el que deepagents registra la herramienta de delegacion.
HERRAMIENTA_DE_DELEGACION = "task"

# Que argumento de cada herramienta se puede enseñar. Lista BLANCA: lo que
# no este aqui no sale, aunque la herramienta sea nueva. Un `dict` abierto
# acabaria filtrando el primer argumento sensible que alguien añada.
#
# Los nombres tienen que existir de verdad en la firma de la herramienta, y
# eso no se puede dejar al ojo: este mapa nacio con `search_programs: query`
# y esa herramienta no tiene ningun `query` -- busca por `career`. La entrada
# no fallaba, que es lo peor que podia hacer: `args.get("query")` devolvia
# None y el chip salia sin asunto, indistinguible de una llamada sin nada que
# enseñar. Lo cubre `TestLaListaBlancaApuntaAArgumentosReales`.
ASUNTO_POR_HERRAMIENTA: dict[str, str] = {
    "search_careers": "query",
    "search_programs": "career",
    "web_search": "query",
    "recommend_programs": "riasec_code",
}

# Un asunto es una etiqueta, no un texto. El modelo puede emitir una
# consulta larguisima y esto va dentro de un chip.
_MAX_ASUNTO = 80


def _recorta(valor: Any) -> str | None:
    if not isinstance(valor, str):
        valor = str(valor) if valor is not None else ""
    limpio = " ".join(valor.split())
    if not limpio:
        return None
    if len(limpio) <= _MAX_ASUNTO:
        return limpio
    return limpio[: _MAX_ASUNTO - 1].rstrip() + "…"


def resumen_publicable(tool_call: Any) -> dict[str, Any] | None:
    """Resumen publicable de una llamada a herramienta, o ``None``.

    Lo que sale: el nombre de la herramienta -- para que el frontend le ponga su
    etiqueta, que la copia en castellano vive alli --, un asunto si la lista
    blanca lo autoriza, y hueco para el resultado. Lo que no sale: los
    argumentos que nadie ha aprobado, empezando por el ``description`` de
    ``task``, que es el prompt interno del coordinador.
    """
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

    if nombre == HERRAMIENTA_DE_DELEGACION:
        # Solo la clave del especialista. `description`, el otro argumento,
        # es el prompt que el coordinador le redacta y no es para leer.
        subagente = args.get("subagent_type")
        resumen["subagent"] = str(subagente) if subagente else "desconocido"
        return resumen

    campo = ASUNTO_POR_HERRAMIENTA.get(nombre)
    if campo is not None:
        asunto = _recorta(args.get(campo))
        if asunto is not None:
            resumen["subject"] = asunto

    return resumen


__all__ = [
    "ASUNTO_POR_HERRAMIENTA",
    "CLAVE",
    "HERRAMIENTA_DE_DELEGACION",
    "INFORME",
    "PASOS",
    "lo_recogido",
    "resumen_publicable",
]
