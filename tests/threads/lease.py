"""Tests for the per-conversation run lease (src/threads/lease.py).

Uses a real ``InMemoryStore`` rather than a stub: the module's whole job
is a read-then-write against the store API, and a fake that agrees with
the code would prove nothing about ``aget``/``aput``/``adelete``.
"""

from datetime import UTC, datetime, timedelta

from langgraph.store.memory import InMemoryStore

from src.threads.lease import (
    RUN_LEASE_NAMESPACE,
    acquire_run_lease,
    active_run,
    release_run_lease,
)

TTL = 300.0


def _expira(store: InMemoryStore, thread_id: str, cuando: datetime) -> None:
    """Reescribe la caducidad de un arrendamiento ya tomado.

    Es la unica forma de probar el vencimiento sin dormir cinco minutos ni
    parchear el reloj: lo que el modulo lee es el campo, no un temporizador.
    """
    item = store.get(RUN_LEASE_NAMESPACE, thread_id)
    assert item is not None
    store.put(RUN_LEASE_NAMESPACE, thread_id, {**item.value, "expires_at": cuando.isoformat()})


class TestUnTurnoALaVez:
    """El caso que motiva el modulo: dos pestañas sobre la misma conversacion."""

    async def test_el_primero_se_lo_lleva(self):
        store = InMemoryStore()

        assert await acquire_run_lease(store, "t_1", "run-a", TTL) is not None

    async def test_al_segundo_se_le_niega(self):
        store = InMemoryStore()
        await acquire_run_lease(store, "t_1", "run-a", TTL)

        assert await acquire_run_lease(store, "t_1", "run-b", TTL) is None

    async def test_dos_conversaciones_no_se_estorban(self):
        store = InMemoryStore()
        await acquire_run_lease(store, "t_1", "run-a", TTL)

        assert await acquire_run_lease(store, "t_2", "run-b", TTL) is not None

    async def test_al_soltar_entra_el_siguiente(self):
        store = InMemoryStore()
        await acquire_run_lease(store, "t_1", "run-a", TTL)

        await release_run_lease(store, "t_1", "run-a")

        assert await acquire_run_lease(store, "t_1", "run-b", TTL) is not None


class TestActiveRun:
    """Lo que contesta `running` en `GET /threads/{id}/messages`."""

    async def test_una_conversacion_quieta_no_esta_corriendo(self):
        assert await active_run(InMemoryStore(), "t_1") is None

    async def test_mientras_corre_se_ve(self):
        store = InMemoryStore()
        await acquire_run_lease(store, "t_1", "run-a", TTL)

        lease = await active_run(store, "t_1")

        assert lease is not None
        assert lease.run_id == "run-a"

    async def test_al_terminar_deja_de_verse(self):
        store = InMemoryStore()
        await acquire_run_lease(store, "t_1", "run-a", TTL)

        await release_run_lease(store, "t_1", "run-a")

        assert await active_run(store, "t_1") is None


class TestVencimiento:
    """Sin caducidad, un proceso que muere a mitad deja el hilo bloqueado."""

    async def test_un_arrendamiento_vencido_no_cuenta(self):
        store = InMemoryStore()
        await acquire_run_lease(store, "t_1", "run-muerto", TTL)
        _expira(store, "t_1", datetime.now(UTC) - timedelta(seconds=1))

        assert await active_run(store, "t_1") is None

    async def test_y_deja_pasar_al_siguiente(self):
        store = InMemoryStore()
        await acquire_run_lease(store, "t_1", "run-muerto", TTL)
        _expira(store, "t_1", datetime.now(UTC) - timedelta(seconds=1))

        assert await acquire_run_lease(store, "t_1", "run-vivo", TTL) is not None

    async def test_soltar_no_se_lleva_por_delante_un_arrendamiento_ajeno(self):
        # El turno que se paso de su TTL reaparece tarde y suelta. Para
        # entonces el hilo es de otro, y llevarselo por delante dejaria dos
        # turnos corriendo -- justo lo que esto existe para impedir.
        store = InMemoryStore()
        await acquire_run_lease(store, "t_1", "run-lento", TTL)
        _expira(store, "t_1", datetime.now(UTC) - timedelta(seconds=1))
        await acquire_run_lease(store, "t_1", "run-nuevo", TTL)

        await release_run_lease(store, "t_1", "run-lento")

        lease = await active_run(store, "t_1")
        assert lease is not None
        assert lease.run_id == "run-nuevo"


class TestEscapes:
    """Los perfiles sin persistencia tienen que seguir funcionando igual."""

    async def test_sin_store_el_turno_pasa(self):
        assert await acquire_run_lease(None, "t_1", "run-a", TTL) is not None

    async def test_sin_store_nada_esta_corriendo(self):
        assert await active_run(None, "t_1") is None

    async def test_sin_store_soltar_no_revienta(self):
        await release_run_lease(None, "t_1", "run-a")

    async def test_un_ttl_de_cero_desactiva_la_serializacion(self):
        store = InMemoryStore()

        assert await acquire_run_lease(store, "t_1", "run-a", 0) is not None
        assert await acquire_run_lease(store, "t_1", "run-b", 0) is not None

    async def test_un_ttl_de_cero_no_escribe_nada(self):
        store = InMemoryStore()

        await acquire_run_lease(store, "t_1", "run-a", 0)

        assert store.get(RUN_LEASE_NAMESPACE, "t_1") is None


class TestRegistrosIlegibles:
    """Un arrendamiento que no se entiende no puede secuestrar el hilo.

    Cerrarle la conversacion a un estudiante porque una fila esta mal
    escrita es peor fallo que dejar pasar el turno: lo segundo, como mucho,
    permite una carrera que ya existia antes de este modulo.
    """

    async def test_una_fecha_ilegible_no_bloquea(self):
        store = InMemoryStore()
        store.put(RUN_LEASE_NAMESPACE, "t_1", {"run_id": "x", "expires_at": "no es una fecha"})

        assert await acquire_run_lease(store, "t_1", "run-a", TTL) is not None

    async def test_un_registro_sin_caducidad_no_bloquea(self):
        store = InMemoryStore()
        store.put(RUN_LEASE_NAMESPACE, "t_1", {"run_id": "x"})

        assert await acquire_run_lease(store, "t_1", "run-a", TTL) is not None

    async def test_un_valor_que_ni_es_un_dict_no_bloquea(self):
        store = InMemoryStore()
        store.put(RUN_LEASE_NAMESPACE, "t_1", {"lo que sea": True})

        assert await acquire_run_lease(store, "t_1", "run-a", TTL) is not None

    async def test_una_fecha_sin_zona_se_lee_como_utc(self):
        # `datetime.now(UTC).isoformat()` siempre trae zona, pero una fila
        # escrita a mano puede no traerla, y compararla con un datetime
        # consciente reventaria con TypeError en vez de decidir.
        store = InMemoryStore()
        futuro = (datetime.now(UTC) + timedelta(seconds=60)).replace(tzinfo=None)
        store.put(RUN_LEASE_NAMESPACE, "t_1", {"run_id": "x", "expires_at": futuro.isoformat()})

        assert await acquire_run_lease(store, "t_1", "run-a", TTL) is None
