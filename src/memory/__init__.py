"""Memory module — profile extraction and persistence via langmem (Sprint 6)."""

from src.memory.profile_manager import (
    EXTRACTION_INSTRUCTIONS,
    PROFILE_NAMESPACE,
    build_profile_manager,
    build_reflection_executor,
)

__all__ = [
    "EXTRACTION_INSTRUCTIONS",
    "PROFILE_NAMESPACE",
    "build_profile_manager",
    "build_reflection_executor",
]
