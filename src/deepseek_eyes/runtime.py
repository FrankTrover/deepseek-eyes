"""Eyes Core Runtime — the single business core.

Every host adapter, MCP server, and diagnostic CLI reuses this object rather than
duplicating provider/cache/media logic. The Runtime owns source registration and
the fast-observe path (exact cache -> single-flight -> provider -> taint).

MVP-A has no capture: ``capture`` is a hard permission denial placeholder.
"""

from __future__ import annotations

import asyncio
from typing import Any

from . import CONTRACT_VERSION, VISION_SCHEMA_VERSION
from .cache import ExactObservationCache, SingleFlight, observation_cache_key
from .capture import CaptureBackend
from .contracts import (
    CapabilitiesResult,
    ObserveRequest,
    VisionObservation,
)
from .errors import ErrorCode, EyesError
from .ids import is_source_ref
from .interfaces import (
    MediaDescriptor,
    MediaProvider,
    ObservationCache,
    SourceRegistry,
    VisionProvider,
)
from .media import LocalMediaProvider
from .planner import ViewPlanner
from .provider import should_retry
from .registry import InMemorySourceRegistry

PROMPT_VERSION = "mvp-a-6-full-reasoning-vision"
MAX_PROVIDER_ATTEMPTS = 2


class Runtime:
    """Async orchestration core for DeepSeek Eyes."""

    def __init__(
        self,
        provider: VisionProvider,
        *,
        media: MediaProvider | None = None,
        sources: SourceRegistry | None = None,
        cache: ObservationCache | None = None,
        planner: ViewPlanner | None = None,
    ) -> None:
        self._provider = provider
        self._media = media or LocalMediaProvider()
        self._sources = sources or InMemorySourceRegistry()
        self._cache = cache or ExactObservationCache()
        self._planner = planner or ViewPlanner()
        self._singleflight = SingleFlight()

    # -- capabilities --------------------------------------------------------

    def capabilities(self) -> CapabilitiesResult:
        # `host_action_guard` is hard-coded False. There is no Host Action
        # Guard adapter implemented yet, and SECURITY_GUARANTEES_AND_HOST_ASSUMPTIONS.md
        # §4-5 forbids reporting true from a config flag alone — only a verified
        # before-hook interceptor may flip this.
        return CapabilitiesResult(
            version="0.1.0",
            host_action_guard=False,
            capture_available=self._capture_deps_present(),
        )

    @staticmethod
    def _capture_deps_present() -> bool:
        try:
            import mss  # noqa: F401
            import PySide6  # noqa: F401  # region overlay + fullscreen confirm
            import win32gui  # noqa: F401

            return True
        except ImportError:
            return False

    # -- media intake --------------------------------------------------------

    async def register_source(self, raw: bytes, origin: str = "bytes") -> str:
        image = await self._media.canonicalize(raw, origin=origin)
        return await self._sources.register(image)

    async def register_source_path(self, path: str, origin: str = "path") -> str:
        image = await self._media.canonicalize_path(path, origin=origin)
        return await self._sources.register(image)

    async def revoke_source(self, ref: str) -> None:
        await self._sources.revoke(ref)

    # -- observe -------------------------------------------------------------

    async def observe(self, request: ObserveRequest) -> VisionObservation:
        if request.contract_version != CONTRACT_VERSION:
            raise EyesError(ErrorCode.CONTRACT_VERSION_MISMATCH, "contract_version mismatch")
        if request.vision_schema_version != VISION_SCHEMA_VERSION:
            raise EyesError(ErrorCode.CONTRACT_VERSION_MISMATCH, "vision_schema_version mismatch")

        # Validate source refs exist and are well-formed.
        for ref in request.sources:
            if not is_source_ref(ref):
                raise EyesError(ErrorCode.REQUEST_INVALID, f"malformed source ref: {ref}")

        media: list[MediaDescriptor] = []
        for ref in request.sources:
            media.append(await self._sources.resolve(ref))

        decision = self._planner.plan(request, media)
        model = getattr(self._provider, "model", "mimo-v2.5")
        key = observation_cache_key(
            request, model, media, prompt_version=PROMPT_VERSION, planner_decision=decision.key
        )

        cached = await self._cache.get(key)
        if cached is not None:
            return cached

        # Pin sources for the duration of the provider call.
        for m in media:
            await self._sources.pin(m.ref)
        try:

            async def _call() -> VisionObservation:
                for attempt in range(1, MAX_PROVIDER_ATTEMPTS + 1):
                    try:
                        return await self._provider.observe(request, decision.focus_media)
                    except EyesError as exc:
                        if not should_retry(exc, attempt, MAX_PROVIDER_ATTEMPTS):
                            raise
                        await asyncio.sleep(1)
                raise AssertionError("unreachable")

            result = await self._singleflight.run(key, _call)
        finally:
            for m in media:
                await self._sources.unpin(m.ref)

        await self._cache.put(key, result)
        return result

    # -- capture -------------------------------------------------------------

    async def capture(self, scope: str) -> str:
        """Capture the screen, register the frame, and return its source ref.

        Every scope requires explicit human interaction: region shows a drag
        overlay, window captures the foreground window, fullscreen shows a
        per-call confirmation dialog and is gated by configuration.
        """
        from .config import load_config

        cfg = load_config()
        backend = CaptureBackend(
            fullscreen_allowed=cfg.fullscreen_capture_allowed,
        )
        frame = await asyncio.to_thread(backend.capture, scope)
        return await self.register_source(frame.png_bytes, origin=f"capture:{scope}")

    # -- diagnostics ---------------------------------------------------------

    async def stats(self) -> dict[str, Any]:
        src = await self._sources.stats()
        cache_stats_fn = getattr(self._cache, "stats", None)
        cache = await cache_stats_fn() if cache_stats_fn is not None else {"entries": 0, "bytes": 0}
        return {"sources": src, "cache": cache}
