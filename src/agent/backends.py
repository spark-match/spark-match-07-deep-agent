"""Composite filesystem backend for the agent (Sprint 6, task 6.B; Sprint 8, task 8.3).

Routes ``/memories/*`` to a per-user :class:`~deepagents.backends.StoreBackend`
(persistent, long-term), ``/skills/*`` to a :class:`~deepagents.backends.FilesystemBackend`
rooted at the repo's ``skills/`` directory (real files on disk, read by
``SkillsMiddleware`` — see ``create_deep_agent(skills=[...])`` in
``src/agent/factory.py``), while everything else stays on the ephemeral
:class:`~deepagents.backends.StateBackend` (scratchpad for the current run).
This is what makes ``read_file``/``write_file``/``edit_file`` over
``/memories/...`` durable across sessions without touching the rest of the
agent's filesystem semantics.

Security note on the ``/skills/`` route: ``FilesystemBackend``'s own
docstring warns against using it for "web servers or HTTP APIs" since it
grants direct filesystem read/write access (secrets, ``.env``, arbitrary
source files) unless carefully scoped. Two mitigations, both applied here:

1. ``root_dir`` is the ``skills/`` directory itself, **not** the repo root
   — even a traversal bug in the backend can only ever reach files already
   checked into ``skills/`` (versioned Markdown, never secrets), not
   ``.env``, ``.git/``, or application source.
2. ``virtual_mode=True`` adds path-traversal guardrails (blocks ``..``/``~``
   and absolute paths escaping ``root_dir``) on top of that scoping —
   defense in depth, not a replacement for it (the upstream docs are
   explicit that ``virtual_mode`` alone, without a narrow ``root_dir``,
   "does not provide sandboxing or process isolation").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend
from deepagents.backends.protocol import BackendProtocol
from langgraph.runtime import Runtime

from src.agent.user_context import get_user_id

MEMORY_ROOT = "/memories/"
SKILLS_ROOT = "/skills/"

# src/agent/backends.py -> src/agent -> src -> <repo root>.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _REPO_ROOT / "skills"


def _memory_namespace(runtime: Runtime[Any]) -> tuple[str, ...]:
    """Per-user namespace for the memory-files store route.

    No wildcards (forbidden by ``StoreBackend``); the placeholder
    ``user_id`` (Sprint 6, see :mod:`src.agent.user_context`) still yields a
    well-formed, non-wildcard namespace.
    """
    return ("spark-match", get_user_id(runtime), "files")


def build_backend() -> CompositeBackend:
    """Build the agent's composite filesystem backend.

    ``/memories/...`` -> persistent per-user store.
    ``/skills/...``   -> real skill files on disk, scoped to ``skills/``
                          (see the module docstring's security note).
    everything else   -> ephemeral state (cleared at the end of the run).
    """
    routes: dict[str, BackendProtocol] = {
        MEMORY_ROOT: StoreBackend(namespace=_memory_namespace),
        SKILLS_ROOT: FilesystemBackend(root_dir=_SKILLS_DIR, virtual_mode=True),
    }
    return CompositeBackend(default=StateBackend(), routes=routes)


__all__ = ["MEMORY_ROOT", "SKILLS_ROOT", "build_backend"]
