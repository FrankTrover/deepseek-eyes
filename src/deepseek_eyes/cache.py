"""Exact in-memory observation cache + single-flight coordination.

The exact cache is keyed on the full observe identity (contract + schema
version, model, mode, prompt/question, focus, ordered source digests), so the
same pixels under a different ``src_*`` ref still hit the cache. Results remain
tainted (the cached object is returned as-is). There is no disk cache and no
semantic cache.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict

from .contracts import ObserveRequest, VisionObservation
from .interfaces import MediaDescriptor, ObservationCache
from .limits import EXACT_CACHE_MAX_BYTES, EXACT_CACHE_MAX_ENTRIES, EXACT_CACHE_TTL_SECONDS


def observation_cache_key(
    request: ObserveRequest,
    model: str,
    media: list[MediaDescriptor],
    *,
    prompt_version: str,
    planner_decision: str,
) -> str:
    """Build a stable cache key that ignores transient ``src_*`` refs.

    Uses ordered source canonical digests (and crop bboxes) so the same image
    registered under a different ref shares a cache entry.
    """
    focus = request.focus.model_dump(mode="json") if request.focus is not None else None
    payload = {
        "contract_version": request.contract_version,
        "vision_schema_version": request.vision_schema_version,
        "model": model,
        "prompt_version": prompt_version,
        "mode": request.mode,
        "planner_decision": planner_decision,
        "question": request.question,
        "focus": focus,
        "sources": [
            {"digest": m.canonical_digest, "width": m.width, "height": m.height} for m in media
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class ExactObservationCache(ObservationCache):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._entries: OrderedDict[str, tuple[VisionObservation, float]] = OrderedDict()
        self._total_bytes = 0

    def _size_of(self, value: VisionObservation) -> int:
        return len(value.model_dump_json().encode("utf-8"))

    async def get(self, key: str) -> VisionObservation | None:
        async with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            value, expires_at = item
            if time.monotonic() >= expires_at:
                self._entries.pop(key, None)
                self._total_bytes -= self._size_of(value)
                return None
            # Move to most-recently-used end.
            self._entries.move_to_end(key)
            return value

    async def put(self, key: str, value: VisionObservation) -> None:
        size = self._size_of(value)
        if size > EXACT_CACHE_MAX_BYTES:
            return  # a single oversized result is never cached
        async with self._lock:
            if key in self._entries:
                old, _ = self._entries.pop(key)
                self._total_bytes -= self._size_of(old)
            self._entries[key] = (value, time.monotonic() + EXACT_CACHE_TTL_SECONDS)
            self._total_bytes += size
            # Enforce entry and byte caps (oldest first).
            while (
                len(self._entries) > EXACT_CACHE_MAX_ENTRIES
                or self._total_bytes > EXACT_CACHE_MAX_BYTES
            ):
                _, (old, _) = self._entries.popitem(last=False)
                self._total_bytes -= self._size_of(old)

    async def stats(self) -> dict[str, object]:
        async with self._lock:
            return {"entries": len(self._entries), "bytes": self._total_bytes}


class SingleFlight:
    """Coalesce concurrent identical observe requests into one provider call."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Future[VisionObservation]] = {}

    async def run(self, key: str, coro_factory):
        """Run ``coro_factory()`` once per key; followers await the same Future.

        The first caller (the "creator") installs the Future, runs the work, and
        resolves it; every follower awaits that same Future. The entry is always
        removed on completion/failure so a later identical request starts fresh.
        """
        async with self._lock:
            fut = self._inflight.get(key)
            if fut is not None:
                return await fut
            fut = asyncio.get_running_loop().create_future()
            self._inflight[key] = fut

        try:
            try:
                result = await coro_factory()
            except BaseException as exc:
                fut.set_exception(exc)
            else:
                fut.set_result(result)
        finally:
            async with self._lock:
                if self._inflight.get(key) is fut:
                    del self._inflight[key]

        return await fut
