"""El JWT del turno en curso, para que una herramienta pueda hablar con el backend.

**Por que un ContextVar y no ``config["configurable"]``.**

La via obvia seria meter el token junto a ``user_id``, ``role`` y ``email``,
que es como viaja el resto del contexto de autenticacion hasta las
herramientas (ver ``src.api.app.ag_ui_endpoint``). Seria mas corto y seria un
error: cuanto entra en ``configurable`` acaba **serializado** en el
checkpoint de la conversacion y en la traza de LangSmith. Un JWT en una traza
es una credencial en un SaaS de terceros con catorce dias de retencion, y
cualquiera con acceso al proyecto de LangSmith podria suplantar al estudiante
hasta que caduque -- veinticuatro horas, porque el backend los firma con
``DEFAULT_JWT_EXPIRES_SECONDS = 86400``.

Un ContextVar vive en memoria del proceso, no lo serializa nadie y muere con
la peticion. Es el mismo mecanismo que ya usa ``src.budget`` para la sesion
activa, asi que tampoco es un patron nuevo en la casa.

**El aislamiento lo da la peticion, no un ``finally``.** Cada peticion HTTP
corre en su propia Task, y una Task nace con una **copia** del contexto: lo
que se fije dentro no lo ve ninguna otra, y muere cuando la peticion termina.
Es exactamente el argumento que ya usa ``src.budget`` para la sesion activa.

Y por eso el endpoint **no** lo restaura al salir. La respuesta de ``/ag-ui``
es un stream: el generador se consume despues de que la funcion del endpoint
haya retornado, asi que un ``reset`` en un ``finally`` del cuerpo borraria el
token justo antes de que el grafo -- y con el, la herramienta que lo necesita
-- llegara a correr. ``reset_request_token`` existe para los tests, que si
comparten contexto entre casos.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_current_token: ContextVar[str] = ContextVar("current_jwt", default="")


def set_request_token(raw_token: str) -> Token[str]:
    """Fija el JWT del turno. Devuelve el testigo para restaurarlo despues."""
    return _current_token.set(raw_token)


def reset_request_token(testigo: Token[str]) -> None:
    """Restaura el valor anterior. Va en un ``finally``, nunca suelto."""
    _current_token.reset(testigo)


def get_request_token() -> str:
    """El JWT del turno, o cadena vacia si no hay ninguno.

    La cadena vacia no es un caso raro: pasa en cualquier invocacion directa
    del grafo (tests, futuras entradas no HTTP) y en el despliegue detras del
    authorizer de API Gateway, donde el token ya lo consumio el authorizer y
    aqui solo llegan los claims. Quien lo necesite tiene que decirlo con un
    mensaje que se entienda, no dar por hecho que hay uno.
    """
    return _current_token.get()


__all__ = ["get_request_token", "reset_request_token", "set_request_token"]
