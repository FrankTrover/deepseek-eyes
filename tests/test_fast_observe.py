"""Phase 3 fast-observe + Phase 4 planner tests."""

from __future__ import annotations

import asyncio

import pytest

from deepseek_eyes.cache import ExactObservationCache, SingleFlight, observation_cache_key
from deepseek_eyes.contracts import ObserveRequest, VisionObservation
from deepseek_eyes.errors import ErrorCode, EyesError
from deepseek_eyes.planner import ViewPlanner, estimate_image_tokens


async def test_estimate_token_examples() -> None:
    # Official MiMo formula: base 85 + 170 per 512px tile, max 12 tiles.
    assert estimate_image_tokens(512, 512) == 85 + 170
    assert estimate_image_tokens(1024, 512) == 85 + 2 * 170
    assert estimate_image_tokens(4096, 4096) == 85 + 12 * 170  # capped


async def test_planner_budget_exceeded() -> None:
    from deepseek_eyes.interfaces import MediaDescriptor

    media = [
        MediaDescriptor(
            ref="src_x",
            raw_digest="r",
            canonical_digest="c",
            mime_type="image/png",
            width=8192,
            height=8192,
            canonical_bytes=b"",
            origin="t",
            created_at=0.0,
            hard_expires_at=0.0,
        )
    ]
    planner = ViewPlanner(max_total_tokens=100)
    with pytest.raises(EyesError) as exc:
        planner.plan(ObserveRequest(sources=["src_x"], question="q"), media)
    assert exc.value.code == ErrorCode.OBSERVATION_BUDGET_EXCEEDED


async def test_cache_key_ignores_ref() -> None:
    from deepseek_eyes.interfaces import MediaDescriptor

    def media(ref: str) -> MediaDescriptor:
        return MediaDescriptor(
            ref=ref,
            raw_digest="r",
            canonical_digest="SAME",
            mime_type="image/png",
            width=64,
            height=48,
            canonical_bytes=b"",
            origin="t",
            created_at=0.0,
            hard_expires_at=0.0,
        )

    req_a = ObserveRequest(sources=["src_aaaa"], question="q")
    req_b = ObserveRequest(sources=["src_bbbb"], question="q")
    key_a = observation_cache_key(
        req_a, "mimo-v2.5", [media("src_aaaa")], prompt_version="v", planner_decision="full"
    )
    key_b = observation_cache_key(
        req_b, "mimo-v2.5", [media("src_bbbb")], prompt_version="v", planner_decision="full"
    )
    assert key_a == key_b  # same pixels, different ref -> same cache key

    req_c = req_a.model_copy(update={"question": "different"})
    key_c = observation_cache_key(
        req_c, "mimo-v2.5", [media("src_aaaa")], prompt_version="v", planner_decision="full"
    )
    assert key_c != key_a  # different question -> different key


async def test_exact_cache_get_put() -> None:
    cache = ExactObservationCache()
    obs = VisionObservation()
    key = "k"
    assert await cache.get(key) is None
    await cache.put(key, obs)
    got = await cache.get(key)
    assert got is not None
    assert got.tainted is True  # cached results remain tainted


async def test_singleflight_coalesces() -> None:
    sf = SingleFlight()
    counter = {"n": 0}

    async def work():
        counter["n"] += 1
        await asyncio.sleep(0.05)
        return VisionObservation()

    results = await asyncio.gather(*(sf.run("k", work) for _ in range(10)))
    assert counter["n"] == 1
    assert len(results) == 10


async def test_singleflight_failure_not_cached() -> None:
    sf = SingleFlight()
    calls = {"n": 0}

    async def failing():
        calls["n"] += 1
        raise EyesError(ErrorCode.PROVIDER_SERVER_ERROR, "boom")

    for _ in range(2):
        with pytest.raises(EyesError):
            await sf.run("k", failing)
    assert calls["n"] == 2  # each fresh call re-runs after the prior failure clears


async def test_singleflight_key_isolation() -> None:
    sf = SingleFlight()
    calls = {"n": 0}

    async def work():
        calls["n"] += 1
        await asyncio.sleep(0.02)
        return VisionObservation()

    await asyncio.gather(sf.run("a", work), sf.run("b", work))
    assert calls["n"] == 2
