"""Tests for the langmem-backed StudentProfile manager (Sprint 6, task 6.D)."""

from langchain_core.language_models import GenericFakeChatModel
from langgraph.store.memory import InMemoryStore

from src.config import get_settings
from src.memory import PROFILE_NAMESPACE, build_profile_manager, build_reflection_executor
from src.memory import profile_manager as modulo


class TestBuildProfileManager:
    def test_builds_without_aws_credentials(self):
        """Construction must not require real model credentials — it only
        needs to succeed at *building* the manager, matching the same
        no-AWS-needed guarantee already proven for create_spark_agent().

        A fake model is injected because ``create_memory_store_manager``
        eagerly resolves its ``model`` argument through
        ``init_chat_model`` in its own ``__init__`` — unlike deepagents'
        ``create_deep_agent``, which only stores the model string and
        resolves it lazily. Without the injection this test would try to
        build a real ``ChatBedrock`` and fail wherever no AWS
        region/credentials are configured (e.g. CI runners).
        """
        store = InMemoryStore()
        manager = build_profile_manager(store, model=GenericFakeChatModel(messages=iter([])))
        assert manager is not None

    def test_namespace_is_partitioned_by_user_id_placeholder(self):
        assert PROFILE_NAMESPACE == ("spark-match", "{user_id}", "profile")


class TestElExtractorLlevaTechoDeSalida:
    """El fallo que dejaba a cualquier estudiante sin informe.

    langmem resuelve un modelo en texto con ``init_chat_model(model)`` a
    secas, y ``ChatBedrock`` entonces aplica su default de 1024 tokens de
    salida. Con la conversacion entera de entrada, la llamada a ``PatchDoc``
    volvia con ``stop_reason=max_tokens`` -- troceada, o sea perdida -- y el
    perfil no se completaba nunca por encima del 0.50 que deja fuera de la
    puerta de D8.
    """

    def test_un_texto_se_resuelve_con_max_tokens(self, monkeypatch):
        visto = {}

        def init_espia(spec, **kwargs):
            visto["spec"] = spec
            visto["kwargs"] = kwargs
            return GenericFakeChatModel(messages=iter([]))

        monkeypatch.setattr(modulo, "init_chat_model", init_espia)
        build_profile_manager(InMemoryStore())

        assert visto["spec"] == get_settings().fast_model_string
        # La asercion que importa: que haya un techo, y que sea el mismo que
        # `src.agent.factory` le pone a los modelos del agente.
        assert visto["kwargs"]["max_tokens"] == get_settings().max_tokens

    def test_el_techo_deja_sitio_de_sobra_al_default_del_proveedor(self):
        """1024 es lo que ponia Bedrock solo. Cualquier valor que no lo supere
        con margen devolveria el fallo, asi que se fija aqui y no en un
        comentario."""
        assert get_settings().max_tokens > 1024

    def test_un_modelo_ya_construido_pasa_intacto(self, monkeypatch):
        """La garantia de "sin AWS en CI": si el test inyecta un falso, nadie
        puede llamar a ``init_chat_model`` por detras."""

        def init_prohibido(*args, **kwargs):
            raise AssertionError("no se debe resolver un modelo ya construido")

        monkeypatch.setattr(modulo, "init_chat_model", init_prohibido)
        falso = GenericFakeChatModel(messages=iter([]))
        assert build_profile_manager(InMemoryStore(), model=falso) is not None


class TestBuildReflectionExecutor:
    def test_builds_and_exposes_submit(self):
        store = InMemoryStore()
        executor = build_reflection_executor(store, model=GenericFakeChatModel(messages=iter([])))
        try:
            assert hasattr(executor, "submit")
        finally:
            # LocalReflectionExecutor spawns a non-daemon worker thread in
            # __init__; without an explicit shutdown the test process would
            # hang waiting for that thread to finish.
            executor.shutdown(wait=True, cancel_futures=True)
