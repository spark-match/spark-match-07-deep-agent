"""Auth context dataclasses (Sprint 7, task 7.A).

``AuthContext`` is the result of validating an inbound request (JWT or
API Gateway authorizer context). ``AgentContext`` is the ``context_schema``
passed to ``create_deep_agent`` — it is what ``runtime.context`` exposes
inside every middleware and tool once the graph is invoked with
``config["configurable"]`` populated by the AG-UI endpoint (see
``src/api/app.py::ag_ui_endpoint`` and ``ag_ui_langgraph``'s
``base_context.update(config["configurable"])``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Result of validating the caller's credentials for one request."""

    user_id: str
    email: str = ""
    role: str = ""
    #: El JWT tal cual llego, para poder reenviarlo al backend en nombre del
    #: estudiante (ADR-019: el agente registra y cierra el informe). Va vacio
    #: detras del authorizer de API Gateway, que consume el token y solo
    #: reenvia los claims.
    #:
    #: **No se copia a `AgentContext`**, y esa ausencia es la decision: ese
    #: contexto viaja por `config["configurable"]` y acaba serializado en el
    #: checkpoint y en la traza de LangSmith. Ver `src.auth.current_token`.
    token: str = ""


@dataclass(frozen=True, slots=True)
class AgentContext:
    """``context_schema`` for the compiled graph.

    Mirrors ``AuthContext`` plus the derived ``thread_id`` (see
    ``src.auth.thread_guard.derive_thread_id``), so tools/middleware never
    need to reach back into the raw JWT claims.
    """

    user_id: str
    role: str
    email: str = ""
    thread_id: str = ""


__all__ = ["AgentContext", "AuthContext"]
