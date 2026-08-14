"""Concrete media intake backed by the security pipeline."""

from __future__ import annotations

from .interfaces import CanonicalImage, MediaProvider
from .security import canonicalize, safe_read_path, sha256_hex


class LocalMediaProvider(MediaProvider):
    """Canonicalize raw bytes or guarded file paths into :class:`CanonicalImage`."""

    async def canonicalize(self, raw: bytes, origin: str = "bytes") -> CanonicalImage:
        # CPU-bound work is offloaded to a thread so it never blocks the loop.
        import asyncio

        canonical, width, height, mime = await asyncio.to_thread(canonicalize, raw, origin)
        return CanonicalImage(
            raw_digest=sha256_hex(raw),
            canonical_digest=sha256_hex(canonical),
            mime_type=mime,
            width=width,
            height=height,
            canonical_bytes=canonical,
            origin=origin,
        )

    async def canonicalize_path(self, path: str, origin: str = "path") -> CanonicalImage:
        import asyncio

        raw = await asyncio.to_thread(safe_read_path, path)
        return await self.canonicalize(raw, origin=origin)
