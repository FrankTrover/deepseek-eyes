"""Source and Region registries.

- ``SourceRegistry`` owns all ``src_*`` refs and the canonical media bytes. It
  enforces live-count and byte caps, TTLs, pinning, and LRU eviction.
- ``RegionRegistry`` owns ``reg_*`` refs bound to a source digest + normalized
  bbox; it stores no duplicate image bytes.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .errors import ErrorCode, EyesError
from .ids import new_region_ref, new_source_ref
from .interfaces import CanonicalImage, MediaDescriptor, RegionRegistry, SourceRegistry
from .limits import (
    HARD_TTL_SECONDS,
    IDLE_TTL_SECONDS,
    MAX_LIVE_SOURCES,
    MAX_REGISTRY_CANONICAL_BYTES,
)


@dataclass
class _SourceRecord:
    ref: str
    raw_digest: str
    canonical_digest: str
    mime_type: str
    width: int
    height: int
    canonical_bytes: bytes
    origin: str
    created_at: float
    last_access_at: float
    hard_expires_at: float
    pin_count: int = 0


class InMemorySourceRegistry(SourceRegistry):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: dict[str, _SourceRecord] = {}
        # Insertion order doubles as LRU order (oldest first).
        self._order: list[str] = []

    def _total_bytes(self) -> int:
        return sum(len(r.canonical_bytes) for r in self._records.values())

    def _expired(self, rec: _SourceRecord, now: float) -> bool:
        # Idle TTL does not evict pinned sources; hard TTL evicts everything.
        if now >= rec.hard_expires_at:
            return True
        if rec.pin_count == 0 and now - rec.last_access_at >= IDLE_TTL_SECONDS:
            return True
        return False

    def _evict_locked(self, now: float) -> None:
        """Evict expired first, then oldest unpinned, while over budget."""
        # Remove hard/idle-expired entries.
        for ref in list(self._records):
            rec = self._records[ref]
            if self._expired(rec, now):
                del self._records[ref]
                self._order.remove(ref)

        # LRU eviction for unpinned records while any cap is exceeded.
        while (
            len(self._records) > MAX_LIVE_SOURCES
            or self._total_bytes() > MAX_REGISTRY_CANONICAL_BYTES
        ):
            victim = next((r for r in self._order if self._records[r].pin_count == 0), None)
            if victim is None:
                break
            del self._records[victim]
            self._order.remove(victim)

    async def register(self, image: CanonicalImage) -> str:
        # canonicalization is done upstream (outside the lock).
        async with self._lock:
            self._evict_locked(time.time())
            if len(self._records) >= MAX_LIVE_SOURCES:
                raise EyesError(ErrorCode.SOURCE_REGISTRY_FULL, "source registry is full")
            if self._total_bytes() + len(image.canonical_bytes) > MAX_REGISTRY_CANONICAL_BYTES:
                raise EyesError(
                    ErrorCode.SOURCE_REGISTRY_FULL, "source registry byte budget exhausted"
                )

            now = time.time()
            ref = new_source_ref()
            rec = _SourceRecord(
                ref=ref,
                raw_digest=image.raw_digest,
                canonical_digest=image.canonical_digest,
                mime_type=image.mime_type,
                width=image.width,
                height=image.height,
                canonical_bytes=image.canonical_bytes,
                origin=image.origin,
                created_at=now,
                last_access_at=now,
                hard_expires_at=now + HARD_TTL_SECONDS,
            )
            self._records[ref] = rec
            self._order.append(ref)
            return ref

    async def resolve(self, ref: str) -> MediaDescriptor:
        async with self._lock:
            rec = self._records.get(ref)
            if rec is None:
                raise EyesError(ErrorCode.SOURCE_NOT_FOUND, f"unknown source ref: {ref}")
            now = time.time()
            if now >= rec.hard_expires_at:
                raise EyesError(ErrorCode.SOURCE_EXPIRED, f"source expired: {ref}")
            if rec.pin_count == 0 and now - rec.last_access_at >= IDLE_TTL_SECONDS:
                raise EyesError(ErrorCode.SOURCE_EXPIRED, f"source idle-expired: {ref}")
            rec.last_access_at = now
            return MediaDescriptor(
                ref=rec.ref,
                raw_digest=rec.raw_digest,
                canonical_digest=rec.canonical_digest,
                mime_type=rec.mime_type,
                width=rec.width,
                height=rec.height,
                canonical_bytes=rec.canonical_bytes,
                origin=rec.origin,
                created_at=rec.created_at,
                hard_expires_at=rec.hard_expires_at,
            )

    async def revoke(self, ref: str) -> None:
        async with self._lock:
            if ref in self._records:
                del self._records[ref]
                self._order.remove(ref)

    async def pin(self, ref: str) -> None:
        async with self._lock:
            rec = self._records.get(ref)
            if rec is None:
                raise EyesError(ErrorCode.SOURCE_NOT_FOUND, f"unknown source ref: {ref}")
            rec.pin_count += 1

    async def unpin(self, ref: str) -> None:
        async with self._lock:
            rec = self._records.get(ref)
            if rec is None or rec.pin_count <= 0:
                return
            rec.pin_count -= 1

    async def stats(self) -> dict[str, object]:
        async with self._lock:
            return {
                "live_sources": len(self._records),
                "canonical_bytes": self._total_bytes(),
            }


@dataclass(frozen=True)
class _RegionRecord:
    ref: str
    source_ref: str
    source_digest: str
    bbox: tuple[float, float, float, float]


class InMemoryRegionRegistry(RegionRegistry):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: dict[str, _RegionRecord] = {}

    async def add(
        self, source_ref: str, source_digest: str, bbox: tuple[float, float, float, float]
    ) -> str:
        async with self._lock:
            ref = new_region_ref()
            self._records[ref] = _RegionRecord(
                ref=ref, source_ref=source_ref, source_digest=source_digest, bbox=bbox
            )
            return ref

    async def resolve(self, ref: str) -> tuple[str, str, tuple[float, float, float, float]]:
        async with self._lock:
            rec = self._records.get(ref)
            if rec is None:
                raise EyesError(ErrorCode.REGION_INVALID, f"unknown region ref: {ref}")
            return rec.source_ref, rec.source_digest, rec.bbox
