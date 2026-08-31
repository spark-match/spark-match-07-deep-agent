"""Tests del ContextVar que lleva el JWT del turno hasta la herramienta.

Lo que de verdad se prueba aqui no es que un ContextVar funcione -- eso ya lo
prueba CPython -- sino las dos propiedades de las que depende que un
estudiante no emita un informe con la credencial de otro: que el valor no se
escape del contexto que lo puso, y que empiece vacio.
"""

from __future__ import annotations

import asyncio

from src.auth.current_token import get_request_token, reset_request_token, set_request_token


def test_por_defecto_no_hay_token():
    # La cadena vacia no es un caso raro: es lo que ve cualquier invocacion
    # directa del grafo y el despliegue detras del authorizer de API Gateway.
    assert get_request_token() == ""


def test_se_lee_lo_que_se_puso():
    testigo = set_request_token("jwt-de-prueba")
    try:
        assert get_request_token() == "jwt-de-prueba"
    finally:
        reset_request_token(testigo)


def test_el_reset_deja_el_valor_anterior():
    fuera = set_request_token("el-de-antes")
    try:
        dentro = set_request_token("el-de-ahora")
        reset_request_token(dentro)
        assert get_request_token() == "el-de-antes"
    finally:
        reset_request_token(fuera)


async def test_una_tarea_no_ve_el_token_de_otra():
    """La propiedad de la que depende el resto: una Task copia el contexto.

    Es lo que hace que no haga falta limpiar al terminar cada peticion, y por
    tanto lo que permite que el endpoint no restaure el token antes de que el
    stream de la respuesta llegue a usarlo.
    """

    async def poner(valor: str, visto: list[str]) -> None:
        set_request_token(valor)
        # Cede el control para que la otra tarea corra entremedias: sin esto
        # las dos terminarian en serie y el test pasaria por accidente.
        await asyncio.sleep(0)
        visto.append(get_request_token())

    visto: list[str] = []
    # `gather` envuelve cada corrutina en una Task, y cada Task nace con una
    # copia del contexto. Ese es exactamente el mecanismo que se prueba.
    await asyncio.gather(poner("token-a", visto), poner("token-b", visto))

    assert sorted(visto) == ["token-a", "token-b"]
    # Y el contexto de fuera sigue limpio: ninguna de las dos se filtro.
    assert get_request_token() == ""
