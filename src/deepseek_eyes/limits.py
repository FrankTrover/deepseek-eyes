"""Frozen resource limits for the Runtime.

Values match ``rule/PHASE_ACCEPTANCE_CRITERIA.md`` (Phase 1 and Phase 3).
"""

from __future__ import annotations

# --- Media intake -----------------------------------------------------------
PER_SOURCE_ENCODED_MAX_BYTES = 50 * 1024 * 1024  # 50 MiB
MAX_DECODED_PIXELS = 60_000_000  # 60M
MAX_LIVE_SOURCES = 32
MAX_REGISTRY_CANONICAL_BYTES = 256 * 1024 * 1024  # 256 MiB

# --- Source lifecycle -------------------------------------------------------
IDLE_TTL_SECONDS = 20 * 60  # 20 min
HARD_TTL_SECONDS = 60 * 60  # 60 min

# --- Exact observation cache --------------------------------------------------
EXACT_CACHE_MAX_ENTRIES = 128
EXACT_CACHE_MAX_BYTES = 64 * 1024 * 1024  # 64 MiB
EXACT_CACHE_TTL_SECONDS = 60 * 60  # 60 min

# --- Preprocess cache ---------------------------------------------------------
PREPROCESS_CACHE_MAX_BYTES = 128 * 1024 * 1024  # 128 MiB

# --- Housekeeping -------------------------------------------------------------
CLEANUP_INTERVAL_SECONDS = 60
