"""Tests for the langmem-backed StudentProfile manager (Sprint 6, task 6.D)."""

from langchain_core.language_models import GenericFakeChatModel
from langgraph.store.memory import InMemoryStore

from src.memory import PROFILE_NAMESPACE, build_profile_manager, build_reflection_executor


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
