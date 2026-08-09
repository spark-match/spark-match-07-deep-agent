"""Tests del router de perfil.

Montan **solo el router** sobre una app desnuda con un `InMemoryStore`, no la
aplicación completa. La primera versión de estas pruebas usaba el fixture
`client` de `tests/api/app.py`, que entra en el lifespan entero —compila el
grafo de LangGraph y arranca el `ReflectionExecutor` de langmem, cuyo worker es
un hilo *no-daemon*— una vez por test. Ocho tests, ocho agentes construidos y
ocho apagados de executor, para ejercitar dos rutas que solo tocan el store: la
corrida se quedaba colgada y se comía la máquina.

La regla que sale de ahí: el peso del montaje tiene que ser proporcional a lo
que la prueba comprueba. Lo que estas rutas hacen es leer y fusionar un dict en
el store, y eso se prueba con un store y nada más.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from langgraph.store.memory import InMemoryStore
from starlette.testclient import TestClient

from src.api.profile import router as profile_router
from src.auth import AuthContext, require_auth

NAMESPACE = ("spark-match", "u-perfil", "profile")


def _app(store: InMemoryStore | None) -> FastAPI:
    app = FastAPI()
    app.include_router(profile_router)
    app.state.store = store
    return app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def anonimo(store: InMemoryStore):
    """Sin sobreescribir `require_auth`: las peticiones llegan sin credenciales."""
    with TestClient(_app(store)) as client:
        yield client


@pytest.fixture
def cliente(store: InMemoryStore):
    """Autenticado como `u-perfil`.

    Se sobreescribe la dependencia en vez de firmar un JWT de verdad: quién
    valida el token ya lo cubre `tests/auth/`, y aquí lo que se prueba es qué
    hace el router con el store una vez sabe de quién es la petición.
    """
    app = _app(store)
    app.dependency_overrides[require_auth] = lambda: AuthContext(
        user_id="u-perfil", email="e@e.pe", role="student"
    )
    with TestClient(app) as client:
        yield client


class TestAutenticacion:
    def test_ambas_rutas_exigen_credenciales(self, anonimo):
        assert anonimo.get("/profile").status_code == 401
        assert anonimo.put("/profile/preferences", json={}).status_code == 401


class TestLectura:
    def test_sin_perfil_todavia_devuelve_null(self, cliente):
        response = cliente.get("/profile")

        assert response.status_code == 200
        assert response.json()["profile"] is None

    def test_devuelve_lo_que_extrajo_la_conversacion(self, cliente, store):
        store.put(NAMESPACE, "profile", {"riasec_code": "IRC", "name": "Ana"})

        perfil = cliente.get("/profile").json()["profile"]

        assert perfil["riasec_code"] == "IRC"
        assert perfil["name"] == "Ana"


class TestEscrituraDePreferencias:
    def test_guarda_las_cuatro_y_las_devuelve(self, cliente):
        response = cliente.put(
            "/profile/preferences",
            json={
                "preferred_region": "Arequipa",
                "preferred_management": "publica",
                "preferred_institution_type": "universidad",
                "max_annual_budget": 8000,
            },
        )

        assert response.status_code == 200
        assert response.json()["persisted"] is True

        leido = cliente.get("/profile").json()["profile"]
        assert leido["preferred_region"] == "Arequipa"
        assert leido["max_annual_budget"] == 8000

    def test_no_pisa_lo_que_extrajo_la_conversacion(self, cliente, store):
        """El formulario solo es dueño de sus cuatro campos.

        El resto del perfil lo mantiene el extractor y costó una conversación
        entera reunirlo; reemplazar el item completo borraría el código RIASEC
        y los intereses.
        """
        store.put(NAMESPACE, "profile", {"riasec_code": "IRC", "name": "Ana"})

        cliente.put("/profile/preferences", json={"preferred_region": "Cusco"})

        perfil = cliente.get("/profile").json()["profile"]
        assert perfil["riasec_code"] == "IRC"
        assert perfil["name"] == "Ana"
        assert perfil["preferred_region"] == "Cusco"

    def test_reutiliza_la_clave_existente_en_vez_de_crear_otro_perfil(self, cliente, store):
        """Con dos items en el namespace, `search(..., limit=1)` elegiría uno
        cualquiera y el agente leería un perfil distinto al que se editó."""
        store.put(NAMESPACE, "clave-de-langmem", {"riasec_code": "SAE"})

        cliente.put("/profile/preferences", json={"preferred_region": "Puno"})

        items = store.search(NAMESPACE, limit=10)
        assert len(items) == 1
        assert items[0].key == "clave-de-langmem"

    def test_un_campo_ausente_se_guarda_como_desconocido(self, cliente):
        """Semántica PUT: se reemplaza el bloque entero. `None` significa «no lo
        sabemos», que es lo que hace que el filtro no se aplique."""
        cliente.put(
            "/profile/preferences",
            json={"preferred_region": "Ica", "max_annual_budget": 5000},
        )
        cliente.put("/profile/preferences", json={"preferred_region": "Ica"})

        perfil = cliente.get("/profile").json()["profile"]
        assert perfil["preferred_region"] == "Ica"
        assert perfil["max_annual_budget"] is None

    def test_rechaza_un_presupuesto_negativo(self, cliente):
        response = cliente.put("/profile/preferences", json={"max_annual_budget": -100})

        assert response.status_code == 422

    def test_sin_store_lo_dice_en_vez_de_perder_la_escritura(self):
        """Perfil `memory` sin persistencia, o un despliegue mal configurado."""
        app = _app(None)
        app.dependency_overrides[require_auth] = lambda: AuthContext(user_id="u-perfil")

        with TestClient(app) as client:
            cuerpo = client.put("/profile/preferences", json={"preferred_region": "Lima"}).json()

        assert cuerpo["persisted"] is False


class TestAislamientoEntreUsuarios:
    def test_cada_usuario_ve_solo_su_perfil(self, store):
        app = _app(store)
        app.dependency_overrides[require_auth] = lambda: AuthContext(user_id="u-uno")
        with TestClient(app) as uno:
            uno.put("/profile/preferences", json={"preferred_region": "Tacna"})

        app.dependency_overrides[require_auth] = lambda: AuthContext(user_id="u-dos")
        with TestClient(app) as dos:
            ajeno = dos.get("/profile").json()["profile"]

        assert ajeno is None
