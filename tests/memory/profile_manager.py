"""Tests for the langmem-backed StudentProfile manager (Sprint 6, task 6.D)."""

from langgraph.store.memory import InMemoryStore

from src.memory import PROFILE_NAMESPACE, build_profile_manager, build_reflection_executor


class TestBuildProfileManager:
    def test_builds_without_aws_credentials(self):
        """Construction must not require real model credentials — it only
        needs to succeed at *building* the manager, matching the same
        no-AWS-needed guarantee already proven for create_spark_agent().
        """
        store = InMemoryStore()
        manager = build_profile_manager(store)
        assert manager is not None

    def test_namespace_is_partitioned_by_user_id_placeholder(self):
        assert PROFILE_NAMESPACE == ("spark-match", "{user_id}", "profile")


class TestBuildReflectionExecutor:
    def test_builds_and_exposes_submit(self):
        store = InMemoryStore()
        executor = build_reflection_executor(store)
        try:
            assert hasattr(executor, "submit")
        finally:
            # LocalReflectionExecutor spawns a non-daemon worker thread in
            # __init__; without an explicit shutdown the test process would
            # hang waiting for that thread to finish.
            executor.shutdown(wait=True, cancel_futures=True)
