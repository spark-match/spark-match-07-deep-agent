"""Persistence factory — build_persistence() smoke and behavior tests.

Only exercises the ``memory`` and ``sqlite`` profiles for real: both must
work fully offline (hard rule #7 in AGENTS.md). ``postgres`` necesita un RDS,
asi que aca solo se verifica el cableado -- que lea el override local del DSN,
que construya el DSN correcto desde el JSON de Secrets Manager, y que use el
schema `agent` -- con dobles en lugar de una base real.
"""

from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.memory import InMemoryStore

from src.config import get_settings
from src.persistence.factory import build_persistence
from src.persistence.secrets import _dsn_from_secret, resolve_postgres_dsn

#: Shape que escribe modules/rds-postgres en spark-match-02-infrastructure.
_CREDS = {
    "host": "db.example.com",
    "port": 5432,
    "database": "sparkmatch",
    "username": "identity",
    "password": "shh",
}


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    """Each test gets a fresh Settings cache (env changes invalidate it)."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestMemoryProfile:
    """SPARK_PERSISTENCE_BACKEND=memory — the trivial, fully-offline default."""

    async def test_yields_in_memory_checkpointer_and_store(self, monkeypatch):
        monkeypatch.setenv("SPARK_PERSISTENCE_BACKEND", "memory")
        get_settings.cache_clear()

        async with build_persistence() as persistence:
            assert isinstance(persistence.checkpointer, InMemorySaver)
            assert isinstance(persistence.store, InMemoryStore)


class TestSqliteProfile:
    """SPARK_PERSISTENCE_BACKEND=sqlite — must work without AWS (hard rule #7)."""

    async def test_yields_sqlite_checkpointer_and_creates_the_db_file(self, monkeypatch, tmp_path):
        db_path = tmp_path / "nested" / "checkpoints.sqlite"
        monkeypatch.setenv("SPARK_PERSISTENCE_BACKEND", "sqlite")
        monkeypatch.setenv("SPARK_SQLITE_PATH", str(db_path))
        get_settings.cache_clear()

        async with build_persistence() as persistence:
            assert isinstance(persistence.checkpointer, AsyncSqliteSaver)
            assert isinstance(persistence.store, InMemoryStore)
            assert db_path.exists()

    async def test_two_turns_with_the_same_thread_id_share_checkpoint_history(
        self, monkeypatch, tmp_path
    ):
        """The whole point of Sprint 6: a real round trip through sqlite.

        Regression guard for 6.G — proves the checkpointer genuinely
        persists conversation state keyed by ``thread_id``, not just that
        the object is constructed.
        """
        db_path = tmp_path / "checkpoints.sqlite"
        monkeypatch.setenv("SPARK_PERSISTENCE_BACKEND", "sqlite")
        monkeypatch.setenv("SPARK_SQLITE_PATH", str(db_path))
        get_settings.cache_clear()

        config = {"configurable": {"thread_id": "shared-thread", "checkpoint_ns": ""}}
        checkpoint_1 = {
            "v": 1,
            "id": "1",
            "ts": "2026-08-04T00:00:00+00:00",
            "channel_values": {"turn": 1},
            "channel_versions": {},
            "versions_seen": {},
        }

        async with build_persistence() as persistence:
            await persistence.checkpointer.aput(config, checkpoint_1, {}, {})

        # Re-open a saver against the same file — simulates a second HTTP
        # request hitting a fresh Persistence built by the same lifespan.
        async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
            tuple_ = await saver.aget_tuple(config)
            assert tuple_ is not None
            assert tuple_.checkpoint["channel_values"]["turn"] == 1


class TestPostgresDsnResolution:
    """resolve_postgres_dsn() — override local vs. camino AWS."""

    def test_prefers_the_local_override_and_never_touches_aws(self, monkeypatch):
        monkeypatch.setenv("SPARK_POSTGRES_DSN", "postgresql://u:p@localhost:5432/agent")
        get_settings.cache_clear()

        assert resolve_postgres_dsn() == "postgresql://u:p@localhost:5432/agent"

    def test_builds_the_dsn_from_the_secrets_manager_payload(self, monkeypatch):
        monkeypatch.delenv("SPARK_POSTGRES_DSN", raising=False)
        get_settings.cache_clear()
        monkeypatch.setattr(
            "src.persistence.secrets._fetch_from_aws",
            lambda: _dsn_from_secret(json.dumps(_CREDS)),
        )

        dsn = resolve_postgres_dsn()

        assert dsn.startswith("postgresql://identity:")
        assert "@db.example.com:5432/sparkmatch" in dsn

    def test_requires_tls_because_rds_pg15_rejects_plaintext(self):
        assert "sslmode=require" in _dsn_from_secret(json.dumps(_CREDS))

    def test_pins_the_search_path_to_the_agent_schema(self):
        # Sin esto, las tablas de LangGraph caen en `public`, donde viven las
        # migraciones del backend. Comparten base; no deben compartir namespace.
        assert "options=-csearch_path%3Dagent" in _dsn_from_secret(json.dumps(_CREDS))

    def test_percent_encodes_credentials_with_reserved_characters(self):
        creds = {**_CREDS, "password": "p@ss:w/rd?"}

        dsn = _dsn_from_secret(json.dumps(creds))

        # La contrasena cruda partiria el DSN en el `@` y en el `:`.
        assert "p%40ss%3Aw%2Frd%3F" in dsn
        assert "@db.example.com" in dsn

    def test_rejects_a_payload_missing_keys(self):
        # json.dumps queda fuera del `with`: pytest.raises debe envolver una
        # sola llamada que pueda lanzar (python:S5778).
        payload = json.dumps({"host": "h", "port": 5432})

        with pytest.raises(ValueError, match="faltan claves"):
            _dsn_from_secret(payload)

    def test_rejects_a_non_json_payload(self):
        with pytest.raises(ValueError, match="no es JSON valido"):
            _dsn_from_secret("not-json{")


class TestPostgresDsnFromAws:
    """El camino de produccion: SSM -> Secrets Manager -> DSN."""

    @staticmethod
    def _fake_boto3_client(calls: dict):
        """Devuelve un boto3.client falso que registra lo que se le pidio."""

        class FakeSsm:
            def get_parameter(self, Name, WithDecryption):
                calls["ssm_param"] = Name
                calls["ssm_decrypt"] = WithDecryption
                return {"Parameter": {"Value": "arn:aws:secretsmanager:us-east-1:1:secret:db-x"}}

        class FakeSecretsManager:
            def get_secret_value(self, SecretId):
                calls["secret_id"] = SecretId
                return {"SecretString": json.dumps(_CREDS)}

        def client(service, region_name):
            calls.setdefault("regions", []).append(region_name)
            return FakeSsm() if service == "ssm" else FakeSecretsManager()

        return client

    def test_reads_the_adr_0002_ssm_path_and_then_the_secret(self, monkeypatch):
        monkeypatch.delenv("SPARK_POSTGRES_DSN", raising=False)
        monkeypatch.setenv("SPARK_DB_SECRET_SSM_PARAM", "/spark-match/dev/config/db-secret-arn")
        get_settings.cache_clear()
        calls: dict = {}
        monkeypatch.setattr("boto3.client", self._fake_boto3_client(calls))

        dsn = resolve_postgres_dsn()

        assert calls["ssm_param"] == "/spark-match/dev/config/db-secret-arn"
        # El parametro es un SecureString: sin WithDecryption vuelve el
        # ciphertext en base64 en vez del ARN.
        assert calls["ssm_decrypt"] is True
        # El ARN que sale de SSM es el SecretId que se le pide a Secrets Manager.
        assert calls["secret_id"] == "arn:aws:secretsmanager:us-east-1:1:secret:db-x"
        assert "@db.example.com:5432/sparkmatch" in dsn
        assert "options=-csearch_path%3Dagent" in dsn


class TestPostgresProfileWiring:
    """El perfil postgres, con dobles en lugar de un RDS real."""

    @staticmethod
    def _install_fakes(monkeypatch, executed: list):
        """Reemplaza AsyncPostgresSaver/Store por dobles que registran el DDL."""
        import contextlib

        class FakeCursor:
            async def execute(self, sql):
                executed.append(sql)

        class FakeConn:
            def cursor(self):
                @contextlib.asynccontextmanager
                async def _cm():
                    yield FakeCursor()

                return _cm()

        class FakeSaver:
            def __init__(self):
                self.conn = FakeConn()
                self.setup_called = False

            async def setup(self):
                self.setup_called = True
                executed.append("saver.setup")

            @classmethod
            def from_conn_string(cls, dsn):
                executed.append(f"saver.from_conn_string:{dsn}")

                @contextlib.asynccontextmanager
                async def _cm():
                    yield cls()

                return _cm()

        class FakeStore:
            async def setup(self):
                executed.append("store.setup")

            @classmethod
            def from_conn_string(cls, dsn):
                @contextlib.asynccontextmanager
                async def _cm():
                    yield cls()

                return _cm()

        import langgraph.checkpoint.postgres.aio as saver_mod
        import langgraph.store.postgres.aio as store_mod

        monkeypatch.setattr(saver_mod, "AsyncPostgresSaver", FakeSaver)
        monkeypatch.setattr(store_mod, "AsyncPostgresStore", FakeStore)

    async def test_creates_the_agent_schema_before_running_setup(self, monkeypatch):
        # setup() corre DDL, asi que el schema tiene que existir antes. Si el
        # orden se invierte, las tablas de LangGraph caen en `public`.
        monkeypatch.setenv("SPARK_PERSISTENCE_BACKEND", "postgres")
        monkeypatch.setenv("SPARK_POSTGRES_DSN", "postgresql://u:p@localhost:5432/db")
        get_settings.cache_clear()
        executed: list[str] = []
        self._install_fakes(monkeypatch, executed)

        async with build_persistence() as persistence:
            assert persistence.checkpointer is not None
            assert persistence.store is not None

        create = next(i for i, s in enumerate(executed) if "CREATE SCHEMA" in s)
        setup = executed.index("saver.setup")
        assert create < setup
        assert 'CREATE SCHEMA IF NOT EXISTS "agent"' in executed[create]

    async def test_uses_the_resolved_dsn(self, monkeypatch):
        monkeypatch.setenv("SPARK_PERSISTENCE_BACKEND", "postgres")
        monkeypatch.setenv("SPARK_POSTGRES_DSN", "postgresql://u:p@localhost:5432/db")
        get_settings.cache_clear()
        executed: list[str] = []
        self._install_fakes(monkeypatch, executed)

        async with build_persistence():
            pass

        assert "saver.from_conn_string:postgresql://u:p@localhost:5432/db" in executed

    async def test_runs_setup_on_both_saver_and_store(self, monkeypatch):
        # postgres es el unico perfil donde el store de largo plazo tambien
        # persiste; si no se llama a su setup(), sus tablas no existen.
        monkeypatch.setenv("SPARK_PERSISTENCE_BACKEND", "postgres")
        monkeypatch.setenv("SPARK_POSTGRES_DSN", "postgresql://u:p@localhost:5432/db")
        get_settings.cache_clear()
        executed: list[str] = []
        self._install_fakes(monkeypatch, executed)

        async with build_persistence():
            pass

        assert "saver.setup" in executed
        assert "store.setup" in executed
