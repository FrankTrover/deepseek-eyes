"""Phase 2 provider error mapping + Phase 3 cache/observe integration tests."""

from __future__ import annotations

import pytest

from deepseek_eyes.errors import ErrorCode, EyesError, from_http_status
from deepseek_eyes.provider import MiMoConfig, _opt_int, should_retry
from deepseek_eyes.runtime import Runtime

from .conftest import FakeProvider, png_bytes


@pytest.mark.parametrize(
    "status,expected",
    [
        (400, ErrorCode.PROVIDER_BAD_REQUEST),
        (401, ErrorCode.PROVIDER_AUTH_FAILED),
        (402, ErrorCode.PROVIDER_BALANCE_OR_PLAN_EXHAUSTED),
        (403, ErrorCode.PROVIDER_FORBIDDEN),
        (404, ErrorCode.PROVIDER_BAD_REQUEST),
        (421, ErrorCode.PROVIDER_OVERLOADED),
        (429, ErrorCode.PROVIDER_RATE_LIMIT),
        (500, ErrorCode.PROVIDER_SERVER_ERROR),
        (503, ErrorCode.PROVIDER_SERVER_ERROR),
    ],
)
def test_http_status_map(status: int, expected: ErrorCode) -> None:
    err = from_http_status(status, "x")
    assert err.code == expected


def test_retry_classifier() -> None:
    assert should_retry(EyesError(ErrorCode.PROVIDER_RATE_LIMIT, "r", retryable=True), 0, 3)
    assert not should_retry(EyesError(ErrorCode.PROVIDER_RATE_LIMIT, "r", retryable=True), 3, 3)
    assert not should_retry(EyesError(ErrorCode.PROVIDER_AUTH_FAILED, "a"), 0, 3)
    assert not from_http_status(502, "unknown server error").retryable


def test_token_config_validation() -> None:
    MiMoConfig("https://example.com/v1", "tp-abc123").validate()
    with pytest.raises(EyesError) as exc:
        MiMoConfig("example.com", "tp-abc123").validate()
    assert exc.value.code == ErrorCode.TOKEN_PLAN_CONFIG_INVALID
    with pytest.raises(EyesError) as exc:
        MiMoConfig("https://example.com", "bad-token").validate()
    assert exc.value.code == ErrorCode.TOKEN_PLAN_CONFIG_INVALID


def test_opt_int() -> None:
    assert _opt_int(None) is None
    assert _opt_int("12") == 12


async def test_runtime_exact_cache_zero_provider_calls(fake_provider: FakeProvider) -> None:
    rt = Runtime(provider=fake_provider)
    ref = await rt.register_source(png_bytes())

    req = {"sources": [ref], "question": "what color?"}
    first = await rt.observe(await _mk(req))
    second = await rt.observe(await _mk(req))

    assert first.tainted is True
    assert second.tainted is True
    assert len(fake_provider.calls) == 1  # exact duplicate -> zero extra provider call


async def test_runtime_concurrent_singleflight(fake_provider: FakeProvider) -> None:
    rt = Runtime(provider=fake_provider)
    ref = await rt.register_source(png_bytes())

    import asyncio

    from deepseek_eyes.contracts import ObserveRequest

    async def call() -> object:
        return await rt.observe(ObserveRequest(sources=[ref], question="q"))

    results = await asyncio.gather(*(call() for _ in range(10)))
    assert len(results) == 10
    # single-flight ensures one provider execution for concurrent identical calls
    # (plus zero extra from the exact cache afterwards).
    assert len(fake_provider.calls) == 1


async def test_runtime_retries_only_safe_provider_failures(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from deepseek_eyes.contracts import ObserveRequest, VisionObservation

    class Provider:
        model = "mimo-v2.5"

        def __init__(self, error: EyesError) -> None:
            self.error = error
            self.calls = 0

        async def observe(self, request, media):
            self.calls += 1
            if self.calls == 1:
                raise self.error
            return VisionObservation()

    sleep = AsyncMock()
    monkeypatch.setattr("deepseek_eyes.runtime.asyncio.sleep", sleep)

    retryable = Provider(
        EyesError(ErrorCode.PROVIDER_CONNECT_TIMEOUT, "connect", retryable=True)
    )
    rt = Runtime(provider=retryable)
    ref = await rt.register_source(png_bytes())
    await rt.observe(ObserveRequest(sources=[ref], question="q"))
    assert retryable.calls == 2
    sleep.assert_awaited_once_with(1)

    ambiguous = Provider(
        EyesError(
            ErrorCode.PROVIDER_TIMEOUT_AMBIGUOUS,
            "read",
            possible_duplicate_billing=True,
        )
    )
    rt = Runtime(provider=ambiguous)
    ref = await rt.register_source(png_bytes())
    with pytest.raises(EyesError) as exc:
        await rt.observe(ObserveRequest(sources=[ref], question="q"))
    assert exc.value.possible_duplicate_billing is True
    assert ambiguous.calls == 1


async def test_runtime_contract_mismatch(fake_provider: FakeProvider) -> None:
    rt = Runtime(provider=fake_provider)
    from deepseek_eyes.contracts import ObserveRequest

    with pytest.raises(EyesError) as exc:
        await rt.observe(ObserveRequest(sources=["src_x"], question="q", contract_version="v0"))
    assert exc.value.code == ErrorCode.CONTRACT_VERSION_MISMATCH


async def test_runtime_capture_region_cancelled(fake_provider: FakeProvider, monkeypatch) -> None:
    """Capture is wired to the backend; a cancelled selection surfaces as an error."""
    from deepseek_eyes.capture import CaptureBackend
    from deepseek_eyes.errors import ErrorCode, EyesError

    def cancel():
        raise EyesError(ErrorCode.CAPTURE_CANCELLED, "user cancelled")

    monkeypatch.setattr(
        "deepseek_eyes.runtime.CaptureBackend",
        lambda **kw: CaptureBackend(region_selector=cancel, **kw),
    )
    rt = Runtime(provider=fake_provider)

    async def fake_register(raw: bytes, origin: str) -> str:
        return "src_captured"

    monkeypatch.setattr(rt, "register_source", fake_register)
    with pytest.raises(EyesError) as exc:
        await rt.capture("region")
    assert exc.value.code == ErrorCode.CAPTURE_CANCELLED


async def test_capabilities_never_report_action_guard(fake_provider: FakeProvider) -> None:
    """host_action_guard must stay False — no Action Guard adapter exists.

    Regression: an earlier revision let a config flag flip it to True, which
    SECURITY_GUARANTEES_AND_HOST_ASSUMPTIONS.md §4-5 forbids.
    """
    rt = Runtime(provider=fake_provider)
    caps = rt.capabilities()
    assert caps.host_action_guard is False


async def _mk(d: dict):
    from deepseek_eyes.contracts import ObserveRequest

    return ObserveRequest(**d)
