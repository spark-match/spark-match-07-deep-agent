"""Tests for the Sprint 6 memory middlewares (hydration, persist, seed)."""

from dataclasses import dataclass, field, replace

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


@dataclass
class _FakeModelRequest:
    """Mirrors the slice of ModelRequest the middleware touches.

    ``override`` returning a new instance is the part that matters: the
    middleware must not mutate the request it was handed.
    """

    runtime: object
    system_message: SystemMessage | None = None
    messages: list = field(default_factory=list)

    def override(self, **overrides):
        return replace(self, **overrides)


def _capturing_handler(seen: list):
    def handler(request):
        seen.append(request)
        return "response"

    return handler


def _capturing_ahandler(seen: list):
    async def handler(request):
        seen.append(request)
        return "response"

    return handler


def _profile_store(**profile):
    store = InMemoryStore()
    store.put(("spark-match", DEFAULT_USER_ID, "profile"), "profile", profile)
    return store


class TestProfileHydrationMiddleware:
    """The profile belongs in the system prompt, not in the message list.

    Appending it as a SystemMessage — the original implementation — worked
    until a student actually had a profile, and then every turn died before
    reaching the model with "Received multiple non-consecutive system
    messages" from langchain_aws's Anthropic adapter. These tests pin the
    profile to `system_message` and, just as importantly, pin that nothing
    is added to `messages`.
    """

    def test_no_store_leaves_the_request_untouched(self):
        seen = []
        request = _FakeModelRequest(runtime=_FakeRuntimeNoStore())

        ProfileHydrationMiddleware().wrap_model_call(request, _capturing_handler(seen))

        assert seen[0] is request

    def test_no_profile_yet_leaves_the_request_untouched(self):
        seen = []
        request = _FakeModelRequest(runtime=_FakeRuntimeWithStore(InMemoryStore()))

        ProfileHydrationMiddleware().wrap_model_call(request, _capturing_handler(seen))

        assert seen[0] is request

    def test_profile_goes_into_the_system_message(self):
        seen = []
        request = _FakeModelRequest(
            runtime=_FakeRuntimeWithStore(_profile_store(name="Juan", realistic=8))
        )

        ProfileHydrationMiddleware().wrap_model_call(request, _capturing_handler(seen))

        assert "Juan" in seen[0].system_message.content
        assert "realistic: 8" in seen[0].system_message.content

    def test_nothing_is_added_to_the_message_list(self):
        """This is the regression. A system message appended here lands
        after the human turn, which the Bedrock adapter rejects outright,
        and gets checkpointed into the persisted history besides."""
        seen = []
        request = _FakeModelRequest(
            runtime=_FakeRuntimeWithStore(_profile_store(name="Juan")),
            messages=[HumanMessage(content="hola")],
        )

        ProfileHydrationMiddleware().wrap_model_call(request, _capturing_handler(seen))

        assert seen[0].messages == [HumanMessage(content="hola")]
        assert not any(isinstance(m, SystemMessage) for m in seen[0].messages)

    def test_appends_to_an_existing_system_message_instead_of_replacing_it(self):
        """The agent's own prompt has to survive; the profile is added to it."""
        seen = []
        request = _FakeModelRequest(
            runtime=_FakeRuntimeWithStore(_profile_store(name="Juan")),
            system_message=SystemMessage(content="Eres el coordinador principal."),
        )

        ProfileHydrationMiddleware().wrap_model_call(request, _capturing_handler(seen))

        content = seen[0].system_message.content
        assert "Eres el coordinador principal." in content
        assert "Juan" in content

    def test_does_not_mutate_the_original_request(self):
        seen = []
        request = _FakeModelRequest(runtime=_FakeRuntimeWithStore(_profile_store(name="Juan")))

        ProfileHydrationMiddleware().wrap_model_call(request, _capturing_handler(seen))

        assert request.system_message is None
        assert seen[0] is not request

    async def test_async_hook_behaves_the_same(self):
        """Production drives the graph exclusively through astream_events,
        so the async path is the one that actually runs."""
        seen = []
        request = _FakeModelRequest(runtime=_FakeRuntimeWithStore(_profile_store(name="Ana")))

        await ProfileHydrationMiddleware().awrap_model_call(request, _capturing_ahandler(seen))

        assert "Ana" in seen[0].system_message.content
        assert seen[0].messages == []


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
