"""Unit tests for the per-user thread index (src/threads/registry.py)."""

import pytest
from langgraph.store.memory import InMemoryStore

from src.auth.thread_guard import THREAD_OWNER_NAMESPACE, derive_thread_id
from src.threads.registry import (
    DEFAULT_TITLE,
    MAX_TITLE_LENGTH,
    TituloInvalido,
    build_title,
    forget_thread,
    limpiar_titulo,
    list_threads,
    record_thread_activity,
    rename_thread,
    thread_index_namespace,
)


@pytest.fixture
def store():
    return InMemoryStore()


class TestBuildTitle:
    def test_uses_the_opening_message(self):
        assert build_title("¿Qué carreras van con matemáticas?") == (
            "¿Qué carreras van con matemáticas?"
        )

    def test_collapses_whitespace(self):
        assert build_title("  hola\n\n  mundo  ") == "hola mundo"

    def test_truncates_long_messages_with_an_ellipsis(self):
        title = build_title("a" * 200)

        assert len(title) == MAX_TITLE_LENGTH
        assert title.endswith("…")

    @pytest.mark.parametrize("seed", [None, "", "   ", "\n\t"])
    def test_falls_back_to_a_default(self, seed):
        assert build_title(seed) == DEFAULT_TITLE


class TestRecordThreadActivity:
    async def test_creates_an_entry_keyed_by_derived_id(self, store):
        await record_thread_activity(store, "u1", "t_abc", "client-1", "hola mundo")

        item = await store.aget(thread_index_namespace("u1"), "t_abc")

        assert item is not None
        assert item.value["client_thread_id"] == "client-1"
        assert item.value["title"] == "hola mundo"

    async def test_keeps_the_client_side_id(self, store):
        """The derived id is one-way, so without this the frontend could
        never reopen a listed conversation."""
        await record_thread_activity(store, "u1", "t_abc", "client-1", "hola")

        [thread] = await list_threads(store, "u1")

        assert thread["thread_id"] == "client-1"

    async def test_refreshes_updated_at_on_later_turns(self, store):
        await record_thread_activity(store, "u1", "t_abc", "client-1", "primera")
        first = (await store.aget(thread_index_namespace("u1"), "t_abc")).value["updated_at"]

        await record_thread_activity(store, "u1", "t_abc", "client-1", "segunda")
        second = (await store.aget(thread_index_namespace("u1"), "t_abc")).value["updated_at"]

        assert second >= first

    async def test_does_not_rewrite_the_title_on_later_turns(self, store):
        """A sidebar label that kept changing as the conversation went on
        would be worse than a slightly stale one."""
        await record_thread_activity(store, "u1", "t_abc", "client-1", "pregunta original")
        await record_thread_activity(store, "u1", "t_abc", "client-1", "algo distinto")

        item = await store.aget(thread_index_namespace("u1"), "t_abc")

        assert item.value["title"] == "pregunta original"

    async def test_backfills_a_default_title_once_text_arrives(self, store):
        await record_thread_activity(store, "u1", "t_abc", "client-1", None)
        await record_thread_activity(store, "u1", "t_abc", "client-1", "ya hay texto")

        item = await store.aget(thread_index_namespace("u1"), "t_abc")

        assert item.value["title"] == "ya hay texto"

    async def test_none_store_is_a_noop(self):
        await record_thread_activity(None, "u1", "t_abc", "client-1", "hola")


class TestListThreads:
    async def test_orders_by_most_recent_activity(self, store):
        await record_thread_activity(store, "u1", "t_old", "old", "vieja")
        await record_thread_activity(store, "u1", "t_new", "new", "nueva")
        # Touch the older one so it becomes the most recent.
        await record_thread_activity(store, "u1", "t_old", "old", "vieja")

        threads = await list_threads(store, "u1")

        assert [t["thread_id"] for t in threads] == ["old", "new"]

    async def test_does_not_leak_other_users_threads(self, store):
        await record_thread_activity(store, "u1", "t_mine", "mine", "mía")
        await record_thread_activity(store, "u2", "t_theirs", "theirs", "suya")

        threads = await list_threads(store, "u1")

        assert [t["thread_id"] for t in threads] == ["mine"]

    async def test_never_returns_the_derived_id(self, store):
        """It is the checkpointer key; the client has no business with it."""
        await record_thread_activity(store, "u1", "t_abc", "client-1", "hola")

        [thread] = await list_threads(store, "u1")

        assert "derived_thread_id" in thread  # stripped at the HTTP layer
        assert thread["thread_id"] != thread["derived_thread_id"]

    async def test_paginates(self, store):
        for i in range(5):
            await record_thread_activity(store, "u1", f"t_{i}", f"c{i}", f"conv {i}")

        page = await list_threads(store, "u1", limit=2, offset=2)

        assert len(page) == 2

    async def test_skips_entries_without_a_client_id(self, store):
        """Corrupt or pre-index entries are unusable — the frontend could
        not reopen them — so they are dropped rather than shown broken."""
        await store.aput(thread_index_namespace("u1"), "t_broken", {"title": "sin id"})
        await record_thread_activity(store, "u1", "t_ok", "ok", "buena")

        threads = await list_threads(store, "u1")

        assert [t["thread_id"] for t in threads] == ["ok"]

    async def test_empty_for_a_user_with_no_threads(self, store):
        assert await list_threads(store, "nobody") == []

    async def test_none_store_returns_empty(self):
        assert await list_threads(None, "u1") == []


class TestForgetThread:
    async def test_removes_the_index_entry(self, store):
        await record_thread_activity(store, "u1", "t_abc", "client-1", "hola")

        await forget_thread(store, "u1", "t_abc")

        assert await list_threads(store, "u1") == []

    async def test_releases_the_ownership_record(self, store):
        """Leaving it behind would keep the derived id permanently claimed,
        so reusing the same client-side id after a delete would 403 the
        student on their own thread."""
        thread_id = derive_thread_id("u1", "client-1")
        await store.aput(THREAD_OWNER_NAMESPACE, thread_id, {"user_id": "u1"})
        await record_thread_activity(store, "u1", thread_id, "client-1", "hola")

        await forget_thread(store, "u1", thread_id)

        assert await store.aget(THREAD_OWNER_NAMESPACE, thread_id) is None

    async def test_none_store_is_a_noop(self):
        await forget_thread(None, "u1", "t_abc")


class TestLimpiarTitulo:
    """Un renombrado que no vale se le dice a quien lo pidio.

    `build_title` puede caer al generico porque nadie eligio ese titulo; un
    renombrado vacio es una equivocacion de alguien, y tragarsela dejaria al
    estudiante mirando por que no cambio nada.
    """

    def test_colapsa_los_espacios_como_el_titulo_automatico(self):
        assert limpiar_titulo("  mis   carreras  ") == "mis carreras"

    @pytest.mark.parametrize("propuesto", ["", "   ", "\n\t"])
    def test_rechaza_un_titulo_sin_contenido(self, propuesto):
        with pytest.raises(TituloInvalido):
            limpiar_titulo(propuesto)

    def test_rechaza_uno_mas_largo_que_el_tope(self):
        with pytest.raises(TituloInvalido):
            limpiar_titulo("x" * (MAX_TITLE_LENGTH + 1))

    def test_acepta_justo_el_tope(self):
        assert len(limpiar_titulo("x" * MAX_TITLE_LENGTH)) == MAX_TITLE_LENGTH

    def test_mide_despues_de_colapsar_y_no_antes(self):
        # Sesenta espacios y una letra miden uno, no sesenta y uno. Sin
        # colapsar primero, esto se rechazaria por largo.
        assert limpiar_titulo(" " * 60 + "a") == "a"

    def test_quita_los_caracteres_invisibles(self):
        # Ocupan sitio en el tope sin verse, asi que un titulo de dos letras
        # podria llegar a sesenta y llevarse el recorte por delante. Con
        # escapes y no pegados tal cual: un caracter invisible en el codigo
        # fuente de un test sobre caracteres invisibles no se puede leer.
        assert limpiar_titulo("be\u200bcas\x07") == "becas"


class TestRenameThread:
    async def test_pone_el_titulo_que_escribio_el_estudiante(self, store):
        await record_thread_activity(store, "u1", "t_1", "client-1", "hola que tal")

        renombrada = await rename_thread(store, "u1", "t_1", "Mis opciones en Arequipa")

        assert renombrada is not None
        assert renombrada["title"] == "Mis opciones en Arequipa"

    async def test_devuelve_el_id_del_cliente_y_no_el_derivado(self, store):
        await record_thread_activity(store, "u1", "t_1", "client-1", "hola")

        renombrada = await rename_thread(store, "u1", "t_1", "Otro nombre")

        assert renombrada is not None
        assert renombrada["thread_id"] == "client-1"

    async def test_el_listado_lo_refleja(self, store):
        await record_thread_activity(store, "u1", "t_1", "client-1", "hola")

        await rename_thread(store, "u1", "t_1", "Becas y costos")

        [thread] = await list_threads(store, "u1")
        assert thread["title"] == "Becas y costos"

    async def test_el_siguiente_turno_no_lo_pisa(self, store):
        await record_thread_activity(store, "u1", "t_1", "client-1", "hola")
        await rename_thread(store, "u1", "t_1", "Becas y costos")

        await record_thread_activity(store, "u1", "t_1", "client-1", "otra pregunta")

        [thread] = await list_threads(store, "u1")
        assert thread["title"] == "Becas y costos"

    async def test_renombrar_al_titulo_generico_tambien_se_respeta(self, store):
        """El caso que obliga a la marca, y el que no tiene explicacion
        posible cuando pasa: sin ella, comparar con DEFAULT_TITLE bastaria
        casi siempre y fallaria justo aqui."""
        await record_thread_activity(store, "u1", "t_1", "client-1", title_seed=None)

        await rename_thread(store, "u1", "t_1", DEFAULT_TITLE)
        await record_thread_activity(store, "u1", "t_1", "client-1", "ya hay texto")

        [thread] = await list_threads(store, "u1")
        assert thread["title"] == DEFAULT_TITLE

    async def test_sin_renombrar_el_relleno_sigue_funcionando(self, store):
        # La marca no puede romper el backfill de una entrada que nacio sin
        # texto que leer.
        await record_thread_activity(store, "u1", "t_1", "client-1", title_seed=None)

        await record_thread_activity(store, "u1", "t_1", "client-1", "ya hay texto")

        [thread] = await list_threads(store, "u1")
        assert thread["title"] == "ya hay texto"

    async def test_no_mueve_la_conversacion_en_el_sidebar(self, store):
        # `updated_at` ordena por actividad de la conversacion, y renombrar
        # no es conversar: si lo moviera, ponerle nombre a un hilo viejo lo
        # mandaria por encima de otro en el que se acaba de hablar.
        await record_thread_activity(store, "u1", "t_1", "client-1", "hola")
        [antes] = await list_threads(store, "u1")

        await rename_thread(store, "u1", "t_1", "Otro nombre")

        [despues] = await list_threads(store, "u1")
        assert despues["updated_at"] == antes["updated_at"]

    async def test_un_hilo_que_nunca_tuvo_un_turno_no_existe(self, store):
        assert await rename_thread(store, "u1", "t_desconocido", "algo") is None

    async def test_el_hilo_de_otro_usuario_no_existe_para_este(self, store):
        await record_thread_activity(store, "u1", "t_1", "client-1", "hola")

        assert await rename_thread(store, "u2", "t_1", "algo") is None

    async def test_none_store_is_a_noop(self):
        assert await rename_thread(None, "u1", "t_1", "algo") is None

    async def test_un_titulo_invalido_no_toca_lo_guardado(self, store):
        await record_thread_activity(store, "u1", "t_1", "client-1", "hola")

        with pytest.raises(TituloInvalido):
            await rename_thread(store, "u1", "t_1", "   ")

        [thread] = await list_threads(store, "u1")
        assert thread["title"] == "hola"
