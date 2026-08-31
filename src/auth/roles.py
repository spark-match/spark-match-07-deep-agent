"""Role-based capability model (Sprint 7, task 7.D).

Today the backend (``spark-match-03-backend``) only ever issues
``role="admin"`` in practice; ``docente``/``graduado`` are planned in a
future backend migration and ``student`` doesn't exist as a backend concept
yet. This module is deliberately designed against the *planned* role set so
wiring capability-based tool authorization doesn't need to change when the
backend catches up — only :data:`CAPABILITIES` would gain real distinctions.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Roles recognized by the agent. See module docstring for provenance."""

    ADMIN = "admin"
    DOCENTE = "docente"
    GRADUADO = "graduado"
    STUDENT = "student"


# Explicit, documented fallback while the backend doesn't emit a role claim
# (or emits one the agent doesn't recognize). Using the least-privileged role
# by default, not admin.
DEFAULT_ROLE = Role.STUDENT

# Tool/capability names, not Python identifiers — these are checked against
# whatever a wrap_tool_call authorization hook decides to gate. "*" means
# unrestricted.
CAPABILITIES: dict[Role, frozenset[str]] = {
    Role.STUDENT: frozenset({"assessment", "matching", "planning", "web_search"}),
    Role.DOCENTE: frozenset({"assessment", "matching", "planning", "web_search", "view_cohort"}),
    Role.GRADUADO: frozenset({"matching", "planning", "web_search"}),
    Role.ADMIN: frozenset({"*"}),
}


def resolve_role(raw_role: str | None) -> Role:
    """Map a raw JWT ``role`` claim to a known :class:`Role`.

    Falls back to :data:`DEFAULT_ROLE` for missing or unrecognized values —
    an unrecognized role must never be silently treated as more privileged
    than the least-privileged default.
    """
    if raw_role is None:
        return DEFAULT_ROLE
    try:
        return Role(raw_role)
    except ValueError:
        return DEFAULT_ROLE


def has_capability(role: Role, capability: str) -> bool:
    """Return whether ``role`` is allowed to use ``capability``."""
    granted = CAPABILITIES.get(role, frozenset())
    return "*" in granted or capability in granted


__all__ = ["CAPABILITIES", "DEFAULT_ROLE", "Role", "has_capability", "resolve_role"]
