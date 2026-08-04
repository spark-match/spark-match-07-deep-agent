"""Unit tests for PII redaction (Sprint 9, task 9.A.2).

See tests/agent/memory_middleware.py for the ProfilePersistMiddleware
integration test (redact-before-submit) — this file covers the pure
redaction functions and the manage_memory tool-call wrapper in isolation.
"""

from langchain_core.messages import AIMessage, HumanMessage

from src.agent.pii import PIIRedactionMiddleware, redact_messages, redact_pii


class TestRedactPiiEmail:
    def test_redacts_a_simple_email(self):
        result = redact_pii("mi correo es juan.perez@example.com")
        assert "juan.perez@example.com" not in result
        assert "[EMAIL_REDACTED]" in result

    def test_redacts_multiple_emails(self):
        result = redact_pii("contacto: a@x.com o b@y.org")
        assert "a@x.com" not in result
        assert "b@y.org" not in result
        assert result.count("[EMAIL_REDACTED]") == 2

    def test_clean_text_is_unchanged(self):
        text = "Me gusta la programación y resolver problemas lógicos."
        assert redact_pii(text) == text


class TestRedactPiiDni:
    def test_redacts_dni_with_explicit_label(self):
        result = redact_pii("mi DNI es 12345678")
        assert "12345678" not in result
        assert "[DNI_REDACTED]" in result

    def test_redacts_dni_with_colon(self):
        result = redact_pii("DNI: 87654321")
        assert "87654321" not in result
        assert "[DNI_REDACTED]" in result

    def test_redacts_documento_de_identidad_label(self):
        result = redact_pii("mi documento de identidad es 11223344")
        assert "11223344" not in result
        assert "[DNI_REDACTED]" in result

    def test_bare_eight_digit_number_without_context_is_not_redacted(self):
        """A bare 8-digit number is too ambiguous (could be a year range,
        a course code, an eval case id, etc.) to redact unconditionally --
        0 false positives on legitimate content is the design goal, same
        discipline as the injection guardrail in src/agent/guardrails.py.
        """
        result = redact_pii("el curso tiene el código 20261234 este ciclo")
        assert "20261234" in result
        assert "[DNI_REDACTED]" not in result

    def test_does_not_cross_a_sentence_boundary(self):
        """Regression guard: the connector-word gap must not jump from one
        sentence's "DNI" mention to an unrelated number in the next."""
        result = redact_pii("No tengo DNI a la mano. El código del curso es 20261234")
        assert "20261234" in result
        assert "[DNI_REDACTED]" not in result


class TestRedactPiiPhone:
    def test_redacts_peru_mobile_number_bare(self):
        result = redact_pii("llámame al 987654321")
        assert "987654321" not in result
        assert "[PHONE_REDACTED]" in result

    def test_redacts_peru_mobile_with_country_code(self):
        result = redact_pii("mi número es +51 987654321")
        assert "987654321" not in result
        assert "[PHONE_REDACTED]" in result

    def test_redacts_with_explicit_telefono_label(self):
        result = redact_pii("teléfono: 123-456-7890")
        assert "123-456-7890" not in result
        assert "[PHONE_REDACTED]" in result

    def test_redacts_with_whatsapp_label(self):
        result = redact_pii("mi whatsapp es 999888777")
        assert "999888777" not in result
        assert "[PHONE_REDACTED]" in result

    def test_a_plain_date_is_not_redacted_as_a_phone(self):
        """Regression guard: a generic 'digits with separators' phone
        pattern could accidentally eat an ISO date (e.g. 2026-08-04 has
        the same digit-group shape as a phone number). Dates must survive."""
        result = redact_pii("la reunión es el 2026-08-04 por la tarde")
        assert "2026-08-04" in result
        assert "[PHONE_REDACTED]" not in result

    def test_a_riasec_like_short_number_is_not_redacted(self):
        result = redact_pii("mi score es 8-9 en la escala")
        assert "[PHONE_REDACTED]" not in result


class TestRedactPiiCombined:
    def test_redacts_all_three_categories_in_one_message(self):
        text = "Soy Juan, mi DNI es 12345678, mi correo juan@x.com y celular 987654321"
        result = redact_pii(text)
        assert "12345678" not in result
        assert "juan@x.com" not in result
        assert "987654321" not in result
        assert "Juan" in result  # name is not PII this module targets

    def test_no_eval_dataset_user_message_triggers_a_false_redaction(self):
        """0 false positives on legitimate vocational-guidance conversation,
        driven off the real dataset rather than a hand-picked list, same
        discipline as tests/agent/guardrails.py's equivalent test."""
        from evals.dataset import load_dataset

        cases = load_dataset()
        false_positives = []
        for case in cases:
            for turn in case.turns:
                if turn.role != "user":
                    continue
                redacted = redact_pii(turn.content)
                if redacted != turn.content:
                    false_positives.append((case.id, turn.content, redacted))

        assert not false_positives, f"unexpected redactions: {false_positives}"


class TestRedactMessages:
    def test_redacts_content_across_a_message_list(self):
        messages = [
            HumanMessage(content="mi correo es juan@example.com"),
            AIMessage(content="Entendido, no compartas tu correo."),
        ]
        redacted = redact_messages(messages)

        assert "juan@example.com" not in redacted[0].content
        assert "[EMAIL_REDACTED]" in redacted[0].content
        assert redacted[1].content == "Entendido, no compartas tu correo."

    def test_returns_new_message_objects_not_mutated_originals(self):
        original = HumanMessage(content="contacto: juan@example.com")
        redacted = redact_messages([original])

        assert original.content == "contacto: juan@example.com"  # untouched
        assert "juan@example.com" not in redacted[0].content

    def test_message_without_pii_is_the_same_object(self):
        """No-op path: a clean message should not be needlessly copied."""
        clean = HumanMessage(content="Me gusta la biología")
        redacted = redact_messages([clean])
        assert redacted[0] is clean

    def test_empty_list_returns_empty_list(self):
        assert redact_messages([]) == []


class _FakeToolCallRequest:
    def __init__(self, tool_call):
        self.tool_call = tool_call

    def override(self, *, tool_call):
        return _FakeToolCallRequest(tool_call)


class TestPIIRedactionMiddleware:
    """Wraps the manage_memory tool call (langmem's create_manage_memory_tool
    output — confirmed by introspection to be named "manage_memory", not
    "manage_prefs", which is only this project's Python variable name for
    it in src/agent/factory.py)."""

    def test_ignores_other_tool_names(self):
        mw = PIIRedactionMiddleware()
        request = _FakeToolCallRequest({"name": "search_careers", "args": {"query": "x"}})

        captured = {}

        def handler(req):
            captured["request"] = req
            return "ok"

        result = mw.wrap_tool_call(request, handler)
        assert result == "ok"
        assert captured["request"] is request  # untouched, not rebuilt

    def test_redacts_content_before_calling_the_handler(self):
        mw = PIIRedactionMiddleware()
        request = _FakeToolCallRequest(
            {
                "name": "manage_memory",
                "args": {"content": "el estudiante prefiere español, contacto juan@x.com"},
            }
        )

        captured = {}

        def handler(req):
            captured["content"] = req.tool_call["args"]["content"]
            return "ok"

        mw.wrap_tool_call(request, handler)

        assert "juan@x.com" not in captured["content"]
        assert "[EMAIL_REDACTED]" in captured["content"]

    def test_clean_content_is_not_rebuilt(self):
        mw = PIIRedactionMiddleware()
        request = _FakeToolCallRequest(
            {"name": "manage_memory", "args": {"content": "el estudiante prefiere español"}}
        )

        captured = {}

        def handler(req):
            captured["request"] = req
            return "ok"

        mw.wrap_tool_call(request, handler)
        assert captured["request"] is request

    def test_missing_content_is_a_noop(self):
        mw = PIIRedactionMiddleware()
        request = _FakeToolCallRequest({"name": "manage_memory", "args": {"action": "update"}})

        called = []
        mw.wrap_tool_call(request, lambda req: called.append(req) or "ok")
        assert called == [request]

    async def test_async_hook_also_redacts(self):
        mw = PIIRedactionMiddleware()
        request = _FakeToolCallRequest(
            {"name": "manage_memory", "args": {"content": "mi dni es 12345678"}}
        )

        captured = {}

        async def handler(req):
            captured["content"] = req.tool_call["args"]["content"]
            return "ok"

        await mw.awrap_tool_call(request, handler)

        assert "12345678" not in captured["content"]
        assert "[DNI_REDACTED]" in captured["content"]
