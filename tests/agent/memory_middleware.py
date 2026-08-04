"""Tests for the Sprint 6 memory middlewares (hydration, persist, seed)."""

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.store.memory import InMemoryStore

from src.agent.memory_middleware import (
    MEMORY_SEED_FILENAME,
    MemorySeedMiddleware,
    ProfileHydrationMiddleware,
    ProfilePersistMiddleware,
)
from src.agent.user_context import DEFAULT_USER_ID
from src.prompts import USER_MEMORY_SEED


class _FakeRuntimeNoStore:
    store = None
    context = None


class _FakeRuntimeWithStore:
    def __init__(self, store):
        self.store = store
        self.context = None


class _FakeExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, payload, *, config, after_seconds):
        self.calls.append({"payload": payload, "config": config, "after_seconds": after_seconds})


class TestProfileHydrationMiddleware:
    def test_no_store_is_a_noop(self):
        mw = ProfileHydrationMiddleware()
        assert mw.before_agent({"messages": []}, _FakeRuntimeNoStore()) is None

    def test_no_profile_yet_is_a_noop(self):
        mw = ProfileHydrationMiddleware()
        store = InMemoryStore()
        result = mw.before_agent({"messages": []}, _FakeRuntimeWithStore(store))
        assert result is None

    def test_existing_profile_is_injected_as_system_message(self):
        store = InMemoryStore()
        store.put(
            ("spark-match", DEFAULT_USER_ID, "profile"), "profile", {"name": "Juan", "realistic": 8}
        )

        mw = ProfileHydrationMiddleware()
        result = mw.before_agent({"messages": []}, _FakeRuntimeWithStore(store))

        assert result is not None
        messages = result["messages"]
        assert len(messages) == 1
        assert isinstance(messages[0], SystemMessage)
        assert "Juan" in messages[0].content
        assert "realistic: 8" in messages[0].content

    async def test_async_hook_reads_the_same_profile(self):
        store = InMemoryStore()
        store.put(("spark-match", DEFAULT_USER_ID, "profile"), "profile", {"name": "Ana"})

        mw = ProfileHydrationMiddleware()
        result = await mw.abefore_agent({"messages": []}, _FakeRuntimeWithStore(store))

        assert result is not None
        assert "Ana" in result["messages"][0].content


class TestProfilePersistMiddleware:
    def test_no_executor_is_a_noop(self):
        mw = ProfilePersistMiddleware(executor=None)
        result = mw.after_agent({"messages": []}, _FakeRuntimeNoStore())
        assert result is None

    def test_submits_the_conversation_to_the_executor(self):
        executor = _FakeExecutor()
        mw = ProfilePersistMiddleware(executor=executor)
        # A real BaseMessage, not a bare string: Sprint 9, task 9.A.2 makes
        # _submit redact PII via src.agent.pii.redact_messages, which reads
        # message.content -- a plain string stand-in (this test's original
        # shape) would raise AttributeError, which is the correct failure
        # mode for production, not something to paper over here.
        state = {"messages": [HumanMessage(content="fake-message")]}

        result = mw.after_agent(state, _FakeRuntimeNoStore())

        assert result is None  # after_agent never mutates state itself
        assert len(executor.calls) == 1
        call = executor.calls[0]
        submitted_messages = call["payload"]["messages"]
        assert len(submitted_messages) == 1
        assert submitted_messages[0].content == "fake-message"
        assert call["config"]["configurable"]["user_id"] == DEFAULT_USER_ID
        assert call["after_seconds"] == 30  # settings.reflection_delay_seconds default

    def test_redacts_pii_before_submitting(self):
        """Sprint 9, task 9.A.2: the executor must never see raw PII."""
        executor = _FakeExecutor()
        mw = ProfilePersistMiddleware(executor=executor)
        state = {
            "messages": [
                HumanMessage(content="mi correo es juan@example.com, llámame al 987654321")
            ]
        }

        mw.after_agent(state, _FakeRuntimeNoStore())

        submitted = executor.calls[0]["payload"]["messages"][0].content
        assert "juan@example.com" not in submitted
        assert "987654321" not in submitted
        assert "[EMAIL_REDACTED]" in submitted
        assert "[PHONE_REDACTED]" in submitted

    async def test_async_hook_also_submits(self):
        executor = _FakeExecutor()
        mw = ProfilePersistMiddleware(executor=executor)

        await mw.aafter_agent({"messages": []}, _FakeRuntimeNoStore())

        assert len(executor.calls) == 1


class TestMemorySeedMiddleware:
    def test_no_store_is_a_noop(self):
        mw = MemorySeedMiddleware()
        assert mw.before_agent({"messages": []}, _FakeRuntimeNoStore()) is None

    def test_seeds_the_file_on_first_contact(self):
        store = InMemoryStore()
        mw = MemorySeedMiddleware()

        mw.before_agent({"messages": []}, _FakeRuntimeWithStore(store))

        item = store.get(("spark-match", DEFAULT_USER_ID, "files"), MEMORY_SEED_FILENAME)
        assert item is not None
        assert item.value["content"] == USER_MEMORY_SEED

    def test_seeding_is_idempotent(self):
        store = InMemoryStore()
        mw = MemorySeedMiddleware()

        mw.before_agent({"messages": []}, _FakeRuntimeWithStore(store))
        # A second call must not raise or overwrite existing content.
        mw.before_agent({"messages": []}, _FakeRuntimeWithStore(store))

        item = store.get(("spark-match", DEFAULT_USER_ID, "files"), MEMORY_SEED_FILENAME)
        assert item.value["content"] == USER_MEMORY_SEED

    async def test_async_hook_also_seeds(self):
        store = InMemoryStore()
        mw = MemorySeedMiddleware()

        await mw.abefore_agent({"messages": []}, _FakeRuntimeWithStore(store))

        item = store.get(("spark-match", DEFAULT_USER_ID, "files"), MEMORY_SEED_FILENAME)
        assert item is not None
