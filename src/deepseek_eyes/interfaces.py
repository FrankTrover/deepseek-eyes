"""Abstract interfaces the Runtime depends on.

These keep the Runtime decoupled from the concrete MiMo provider and registry
implementations so tests can inject fakes without any network or media I/O.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol

from .contracts import ObserveRequest, VisionObservation


@dataclass(frozen=True)
class CanonicalImage:
    """The output of media intake: validated, EXIF-stripped canonical pixels."""

    raw_digest: str
    canonical_digest: str
    mime_type: str
    width: int
    height: int
    canonical_bytes: bytes
    origin: str


@dataclass(frozen=True)
class MediaDescriptor:
    """Immutable, read-only view of a registered source."""

    ref: str
    raw_digest: str
    canonical_digest: str
    mime_type: str
    width: int
    height: int
    canonical_bytes: bytes
    origin: str
    created_at: float
    hard_expires_at: float


class VisionProvider(Protocol):
    """Anything that turns an observation request into structured evidence."""

    async def observe(
        self, request: ObserveRequest, media: list[MediaDescriptor]
    ) -> VisionObservation: ...


class MediaProvider(ABC):
    """Media intake: canonicalize raw bytes/paths into a :class:`CanonicalImage`."""

    @abstractmethod
    async def canonicalize(self, raw: bytes, origin: str) -> CanonicalImage:
        """Validate, decode, strip EXIF, and produce canonical image bytes."""

    @abstractmethod
    async def canonicalize_path(self, path: str, origin: str) -> CanonicalImage:
        """Same as :meth:`canonicalize` but with path/junction/symlink guards."""


class SourceRegistry(ABC):
    @abstractmethod
    async def register(self, image: CanonicalImage) -> str:
        """Insert a canonical image and return its source ref."""

    @abstractmethod
    async def resolve(self, ref: str) -> MediaDescriptor:
        """Return the immutable descriptor, updating last-access."""

    @abstractmethod
    async def revoke(self, ref: str) -> None:
        """Remove a source."""

    @abstractmethod
    async def pin(self, ref: str) -> None: ...

    @abstractmethod
    async def unpin(self, ref: str) -> None: ...

    @abstractmethod
    async def stats(self) -> dict[str, object]:
        """Expose live count and byte totals for doctor/diagnostics."""


class RegionRegistry(ABC):
    @abstractmethod
    async def add(
        self, source_ref: str, source_digest: str, bbox: tuple[float, float, float, float]
    ) -> str: ...

    @abstractmethod
    async def resolve(self, ref: str) -> tuple[str, str, tuple[float, float, float, float]]:
        """Return ``(source_ref, source_digest, bbox)``."""


class ObservationCache(ABC):
    @abstractmethod
    async def get(self, key: str) -> VisionObservation | None: ...

    @abstractmethod
    async def put(self, key: str, value: VisionObservation) -> None: ...
