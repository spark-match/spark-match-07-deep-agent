"""Auth module (Sprint 7): JWT validation, request context, roles, thread ownership."""

from src.auth.budget import BUDGET_NAMESPACE_SUFFIX, check_and_increment_daily_budget
from src.auth.context import AgentContext, AuthContext
from src.auth.current_token import get_request_token, reset_request_token, set_request_token
from src.auth.dependencies import require_auth
from src.auth.jwt_validator import JWT_ALGORITHM, JWT_AUDIENCE, JWT_ISSUER, AuthError, decode_token
from src.auth.roles import CAPABILITIES, DEFAULT_ROLE, Role, has_capability, resolve_role
from src.auth.secret_loader import load_jwt_secret
from src.auth.thread_guard import THREAD_OWNER_NAMESPACE, assert_thread_ownership, derive_thread_id

__all__ = [
    "BUDGET_NAMESPACE_SUFFIX",
    "CAPABILITIES",
    "DEFAULT_ROLE",
    "JWT_ALGORITHM",
    "JWT_AUDIENCE",
    "JWT_ISSUER",
    "THREAD_OWNER_NAMESPACE",
    "AgentContext",
    "AuthContext",
    "AuthError",
    "Role",
    "assert_thread_ownership",
    "check_and_increment_daily_budget",
    "decode_token",
    "derive_thread_id",
    "get_request_token",
    "has_capability",
    "load_jwt_secret",
    "require_auth",
    "reset_request_token",
    "resolve_role",
    "set_request_token",
]
