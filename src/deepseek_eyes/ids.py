"""Opaque, process-local identifiers for sources and regions.

Only the Runtime registry may mint these. They carry no path, filename, or user
identity, and they become invalid on process exit (nothing else re-issues them).
"""

from __future__ import annotations

import secrets

_SOURCE_PREFIX = "src_"
_REGION_PREFIX = "reg_"


def new_source_ref() -> str:
    """Return ``src_<url-safe random>`` with >= 128 bits of entropy."""
    return f"{_SOURCE_PREFIX}{secrets.token_urlsafe(24)}"


def new_region_ref() -> str:
    """Return ``reg_<url-safe random>`` with >= 128 bits of entropy."""
    return f"{_REGION_PREFIX}{secrets.token_urlsafe(24)}"


def is_source_ref(value: str) -> bool:
    return value.startswith(_SOURCE_PREFIX) and len(value) > len(_SOURCE_PREFIX)


def is_region_ref(value: str) -> bool:
    return value.startswith(_REGION_PREFIX) and len(value) > len(_REGION_PREFIX)
