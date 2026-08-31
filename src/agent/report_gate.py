"""La puerta del informe, comprobada ANTES de delegar la redaccion.

El backend decide si un estudiante tiene derecho a informe, y lo decide en
``POST /v1/reports`` -- que es la penultima llamada de la generacion, no la
primera. Asi que el orden real era: el coordinador delega, el subagente de
report consulta el ranking, escribe el retrato del perfil y una explicacion por
carrera, llama a ``publish_orientation_report``, y **ahi** el backend contesta
que no. Medido en dev el 2026-08-11 con un estudiante que ya habia gastado sus
tres informes del dia: 44,9 s de redaccion y sus tokens, para acabar en un 429.

Este middleware pregunta primero. Intercepta la llamada a ``task`` que va al
subagente de report y, si la puerta ya diria que no, contesta el mismo `no` sin
que el subagente llegue a arrancar.

**Lo que se comprueba y de donde sale cada cosa.** El reparto no es arbitrario:
es el mismo que ya documenta ``ProfileGateInput`` del backend. Las dos cifras
del perfil viven en el store de aqui y el backend no puede calcularlas; los dos
umbrales y el recuento viven alli y aqui no se pueden saber. Cada lado aporta
lo suyo:

===================  ==========================  =========================
Motivo               Cifra                       Umbral
===================  ==========================  =========================
sin RIASEC           store del agente            no hay, es binario
perfil corto         store del agente            SSM, via el backend
tope diario          backend                     SSM, via el backend
ya generando         backend                     no hay, es binario
===================  ==========================  =========================

**No es una segunda puerta y no debe comportarse como tal.** Falla abierto: si
el backend no contesta, si el token no esta, si la respuesta no trae el bloque
de elegibilidad -- se delega igual y decide ``publish_orientation_report``, que
es como funcionaba antes de existir esto. Un adelanto que se equivoca hacia el
"no" le quitaria a un estudiante un informe al que tiene derecho, y eso es peor
que gastar 45 s.

**Por que va antes que ``SubagentEventsMiddleware`` en la lista.** Porque los
eventos ``spark.subagent.*`` anuncian a la pantalla que hay un especialista
trabajando. Si el rechazo se decidiera por dentro, el estudiante veria aparecer
y desaparecer un indicador de un subagente que nunca corrio. Por fuera no se
emite nada, y de paso la duracion que reporta ese middleware sigue siendo la
del subagente y no incluye esta consulta.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from src.agent.subagent_events import SUBAGENT_TOOL_NAME
from src.agent.subagents.report import REPORT_SUBAGENT
from src.agent.user_context import get_user_id
from src.auth.current_token import get_request_token
from src.backend import reports_client
from src.backend.reports_client import BackendNoConfigurado, ErrorDelBackend
from src.memory.profile_snapshot import PerfilParaLaPuerta, leer_perfil_para_la_puerta

logger = logging.getLogger(__name__)

#: La clave con la que el coordinador pide el subagente de informes.
REPORT_SUBAGENT_NAME = str(REPORT_SUBAGENT["name"])

#: La hora de Peru. Un desfase fijo y no `ZoneInfo("America/Lima")`: Peru no
#: tiene horario de verano desde 1994, asi que el desfase es exacto, y ademas
#: no depende de que la imagen traiga la base de datos de zonas horarias --
#: `ZoneInfo` se cae con `ZoneInfoNotFoundError` en cualquier imagen sin
#: `tzdata`. Sin nombre propio a proposito: se llamara "UTC-05:00", que es lo
#: que es; ponerle "America/Lima" prometeria una regla de husos que no aplica.
_PERU = timezone(timedelta(hours=-5))


@dataclass(frozen=True)
class Rechazo:
    """Un `no` de la puerta, listo para el coordinador."""

    #: Clave estable, solo para el log. No la lee el modelo.
    motivo: str
    #: Lo que ve el coordinador como resultado de su llamada a ``task``.
    mensaje: str


def _subagente_de(request: ToolCallRequest) -> str | None:
    """A que subagente va esta llamada, o ``None`` si no es una delegacion.

    ``args`` puede llegar sin parsear cuando el modelo emite JSON invalido. Ahi
    no se sabe a quien iba, asi que no se toca: deepagents ya responde a eso
    con su propio error.
    """
    if request.tool_call.get("name") != SUBAGENT_TOOL_NAME:
        return None
    args = request.tool_call.get("args")
    if not isinstance(args, dict):
        return None
    destino = args.get("subagent_type")
    return str(destino) if destino else None


def _cuando_se_reabre(retry_after: Any) -> str:
    """La hora de reapertura en hora de Peru, o ``""`` si no se sabe.

    En hora local y no en el ISO en UTC que manda el backend porque esto acaba
    en una frase que lee un estudiante de secundaria en Arequipa. "a las 06:40"
    es accionable; "2026-08-12T11:40:00.000Z" es cinco horas de error esperando
    a que alguien las cometa.
    """
    if not isinstance(retry_after, str) or not retry_after:
        return ""
    try:
        momento = datetime.fromisoformat(retry_after)
    except ValueError:
        logger.warning("El backend mando un retryAfter ilegible: %r", retry_after)
        return ""

    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    local = momento.astimezone(_PERU)

    dias = (local.date() - datetime.now(_PERU).date()).days
    if dias == 0:
        cuando = "hoy"
    elif dias == 1:
        cuando = "mañana"
    else:
        cuando = f"el {local:%d/%m}"
    return f"{cuando} a las {local:%H:%M}"


def _entero(valor: Any) -> int | None:
    """Un entero del backend, o ``None`` si no vino uno.

    `bool` se descarta antes que nada: en Python es subclase de `int`, asi que
    un `generating: true` mal colocado pasaria por un "queda 1".
    """
    if isinstance(valor, bool) or not isinstance(valor, int):
        return None
    return valor


def decidir(perfil: PerfilParaLaPuerta, elegibilidad: dict[str, Any] | None) -> Rechazo | None:
    """El `no` que la puerta daria ahora, o ``None`` si se puede delegar.

    El orden es el de ``request`` en el backend -- RIASEC, completitud, tope --
    y no el que parezca mas util. Si los dos ordenaran distinto, un estudiante
    que reintenta podria recibir dos motivos diferentes para la misma negativa
    y ninguno de los dos seria mentira, que es la peor forma de confundir a
    alguien.

    ``elegibilidad`` a ``None`` es "no se pudo preguntar": entonces solo se
    comprueba lo que el agente sabe solo, y el resto lo decide el backend
    cuando toque.
    """
    if perfil.riasec_code is None:
        return Rechazo(
            "riasec_missing",
            "No he emitido nada. Este estudiante todavia no tiene guardadas las seis "
            "puntuaciones RIASEC, y el informe se construye a partir de ellas. "
            "Delega en el subagente de assessment para que haga el cuestionario "
            "vocacional, y vuelve a intentar el informe cuando este hecho.",
        )

    if elegibilidad is None:
        return None

    minimo = elegibilidad.get("minProfileCompleteness")
    if isinstance(minimo, int | float) and perfil.profile_completeness < float(minimo):
        return Rechazo(
            "profile_incomplete",
            "No he emitido nada. El perfil de este estudiante se queda corto para un "
            "informe que merezca la pena: tiene el cuestionario hecho pero faltan "
            "datos basicos suyos. Preguntale tu -- su edad, en que ano esta, que le "
            "interesa -- y cuando te haya contestado un par de cosas, vuelve a "
            "pedirme el informe. Sus preferencias de region y presupuesto no cuentan "
            "para esto.",
        )

    if elegibilidad.get("generating") is True:
        return Rechazo(
            "already_generating",
            "No he emitido nada porque ya hay un informe suyo generandose ahora "
            "mismo. Dile que espere unos segundos y que lo vera aparecer solo; no me "
            "lo vuelvas a pedir en este turno.",
        )

    restantes = _entero(elegibilidad.get("remaining"))
    if restantes is not None and restantes <= 0:
        tope = _entero(elegibilidad.get("limit"))
        cuantos = f"{tope} informes al dia" if tope else "su tope de informes al dia"
        momento = _cuando_se_reabre(elegibilidad.get("retryAfter"))
        cuando = f" Podra pedir otro {momento}." if momento else ""
        return Rechazo(
            "daily_limit_reached",
            f"No he emitido nada. Este estudiante ha llegado a {cuantos} y no se le "
            f"puede emitir otro todavia.{cuando} Diselo con esas palabras, sin "
            "prometerle que lo intentaras de nuevo, y ofrecele mientras tanto "
            "revisar los informes que ya tiene o seguir conversando.",
        )

    return None


class ReportGateMiddleware(AgentMiddleware):
    """Contesta el `no` de la puerta sin arrancar el subagente de informes.

    Solo implementa el hook async, y es deliberado. El unico camino que llega
    aqui en produccion es ``astream_events`` (``ag-ui-langgraph``), y las dos
    consultas que hace la puerta -- el store y el backend -- son async las dos.
    Fabricar una version sincrona obligaria a levantar un bucle de eventos
    dentro de uno que ya corre, o a duplicar el cliente HTTP. El camino
    sincrono es el de los tests y la invocacion directa, donde no hay ni token
    ni backend que preguntar: ahi la comprobacion no tendria nada que leer, y
    ``AgentMiddleware`` sin ``wrap_tool_call`` deja pasar la llamada tal cual.
    Lo que se pierde es el adelanto, no la puerta.
    """

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        if _subagente_de(request) != REPORT_SUBAGENT_NAME:
            return await handler(request)

        rechazo = await self._mirar(request)
        if rechazo is None:
            return await handler(request)

        logger.info(
            "Informe no delegado: la puerta ya decia que no (%s)",
            rechazo.motivo,
            extra={"motivo": rechazo.motivo},
        )
        # Sin `status="error"`. Para el coordinador no ha fallado nada: la
        # respuesta a "emiteme el informe" es que hoy no toca, y marcarla como
        # error le invita a reintentar la delegacion que acabamos de evitar.
        return ToolMessage(
            content=rechazo.mensaje,
            tool_call_id=str(request.tool_call.get("id") or ""),
        )

    async def _mirar(self, request: ToolCallRequest) -> Rechazo | None:
        """El veredicto de la puerta, o ``None`` para dejar pasar."""
        runtime = request.runtime
        perfil = await leer_perfil_para_la_puerta(
            getattr(runtime, "store", None), get_user_id(runtime)
        )

        # Lo que se decide sin salir de aqui se decide sin salir de aqui. Un
        # estudiante que aun no ha hecho el cuestionario es el caso mas comun
        # de los cuatro, y preguntarle al backend por su tope antes de mandarlo
        # al assessment seria pagar una peticion para tirar la respuesta.
        solo_con_el_perfil = decidir(perfil, None)
        if solo_con_el_perfil is not None:
            return solo_con_el_perfil

        return decidir(perfil, await self._elegibilidad())

    async def _elegibilidad(self) -> dict[str, Any] | None:
        """Lo que el backend dice del tope, o ``None`` si no se pudo preguntar.

        Nunca lanza y nunca bloquea por su cuenta. Todo lo que sale mal aqui --
        sin token, sin URL de backend, el backend caido, un backend anterior a
        que existiera el bloque -- termina en ``None``, y con ``None`` se
        delega. Es la unica direccion segura: quien no pudo preguntar no puede
        negar.
        """
        token = get_request_token()
        if not token:
            return None

        try:
            respuesta = await reports_client.report_eligibility(token)
        except BackendNoConfigurado:
            return None
        except Exception:
            logger.warning("No se pudo consultar el tope de informes", exc_info=True)
            return None

        if isinstance(respuesta, ErrorDelBackend):
            logger.warning("El backend no dijo si cabe otro informe: %s", respuesta)
            return None
        return respuesta


__all__ = ["REPORT_SUBAGENT_NAME", "Rechazo", "ReportGateMiddleware", "decidir"]
