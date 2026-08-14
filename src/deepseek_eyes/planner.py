"""MiMo image token estimation and the view planner.

The token estimator mirrors the official MiMo tile formula. The planner decides,
per observe request, whether to send the full image or a crop, and emits a stable
``planner_decision`` string that becomes part of the exact cache key.
"""

from __future__ import annotations

import math

from .contracts import ObserveRequest
from .interfaces import MediaDescriptor

# MiMo image token estimation constants (official formula).
TILE_SIZE = 512
BASE_TOKENS = 85
TOKENS_PER_TILE = 170
MAX_TILES = 12  # MiMo hard cap for a single image

# Soft budget: total estimated image tokens across all sources.
MAX_TOTAL_IMAGE_TOKENS = 4096


def estimate_image_tokens(width: int, height: int) -> int:
    """Estimate MiMo image tokens for a ``width x height`` image."""
    if width <= 0 or height <= 0:
        return BASE_TOKENS
    tiles_x = math.ceil(width / TILE_SIZE)
    tiles_y = math.ceil(height / TILE_SIZE)
    tiles = min(tiles_x * tiles_y, MAX_TILES)
    return BASE_TOKENS + tiles * TOKENS_PER_TILE


class PlannerDecision:
    """Immutable planner outcome."""

    def __init__(self, key: str, focus_media: list[MediaDescriptor]) -> None:
        self.key = key
        self.focus_media = focus_media


class ViewPlanner:
    """MVP-A planner: full-frame for every source, with budget enforcement."""

    def __init__(self, max_total_tokens: int = MAX_TOTAL_IMAGE_TOKENS) -> None:
        self._max_total_tokens = max_total_tokens

    def plan(self, request: ObserveRequest, media: list[MediaDescriptor]) -> PlannerDecision:
        total = sum(estimate_image_tokens(m.width, m.height) for m in media)
        if total > self._max_total_tokens:
            # MVP-A does not implement crop splitting yet; surface the budget.
            from .errors import ErrorCode, EyesError

            raise EyesError(
                ErrorCode.OBSERVATION_BUDGET_EXCEEDED,
                f"estimated {total} image tokens exceeds budget {self._max_total_tokens}",
            )
        # Full frame, no crop: decision key records source digests only.
        digests = ",".join(m.canonical_digest for m in media)
        return PlannerDecision(key=f"full:{digests}", focus_media=list(media))
