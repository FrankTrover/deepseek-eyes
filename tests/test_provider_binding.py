"""Provider output-binding and prompt-contract regression tests.

These exercise :class:`MiMoProvider` parsing paths directly (no network): the
prompt must carry the output contract, and multi-image evidence must be bound to
the correct source via ``source_index`` / ``source_ref``.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError
from PIL import Image

from deepseek_eyes.contracts import ObserveRequest, VisionObservation
from deepseek_eyes.errors import ErrorCode, EyesError
from deepseek_eyes.provider import (
    MiMoConfig,
    MiMoProvider,
    _normalize_confidence,
    _vision_payload_bytes,
)


def _provider() -> MiMoProvider:
    return MiMoProvider(MiMoConfig("https://example.com/v1", "tp-test-token"))


def _usage() -> dict:
    return {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}


def test_prompt_spells_out_output_contract() -> None:
    provider = _provider()
    request = ObserveRequest(sources=["src_A", "src_B"], question="compare these")
    prompt = provider._build_prompt(request)
    assert "source_index" in prompt
    assert '"evidence"' in prompt
    assert "Number of images: 2" in prompt
    assert "Return JSON only" in prompt
    assert "First inventory what is literally visible" in prompt
    assert "shortcut arrows" in prompt
    assert "Never identify a product from color alone" in prompt
    assert "kind=\"inference\"" in prompt


def test_default_read_timeout_avoids_paid_retry_on_slow_vision() -> None:
    assert _provider()._client.timeout == 300.0


def test_tiny_vision_payload_is_upscaled_to_one_full_tile() -> None:
    source = io.BytesIO()
    Image.new("RGB", (68, 70), "white").save(source, format="PNG")
    media = SimpleNamespace(canonical_bytes=source.getvalue(), width=68, height=70)

    with Image.open(io.BytesIO(_vision_payload_bytes(media))) as enlarged:
        assert max(enlarged.size) == 512
        assert enlarged.size == (497, 512)


async def test_observe_uses_responses_vision_contract() -> None:
    provider = _provider()
    captured: dict = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text=json.dumps(
                {
                    "evidence": [
                        {
                            "source_index": 0,
                            "kind": "text",
                            "text": "OpenAI logo",
                            "confidence": 0.95,
                        }
                    ],
                    "summary": "OpenAI",
                }
            ),
            status="completed",
            incomplete_details=None,
            usage={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
        )

    source = io.BytesIO()
    Image.new("RGB", (68, 70), "white").save(source, format="PNG")
    media = SimpleNamespace(
        # Canonical bytes are always PNG even if a stale descriptor still
        # carries the source container MIME.
        mime_type="image/jpeg",
        canonical_bytes=source.getvalue(),
        width=68,
        height=70,
    )
    provider._client = SimpleNamespace(  # type: ignore[assignment]
        responses=SimpleNamespace(create=create)
    )

    result = await provider.observe(
        ObserveRequest(sources=["src_A"], question="identify the logo"), [media]
    )

    assert "source_index" in captured["instructions"]
    image = captured["input"][0]["content"][0]
    assert image["type"] == "input_image"
    assert image["detail"] == "high"
    assert image["image_url"].startswith("data:image/png;base64,")
    query = captured["input"][0]["content"][1]
    assert query["type"] == "input_text"
    assert "identify the logo" in query["text"]
    assert captured["reasoning"] == {"effort": "high"}
    assert captured["text"] == {"format": {"type": "json_object"}}
    assert captured["max_output_tokens"] == 32_768
    assert result.summary == "OpenAI"
    assert result.usage is not None
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (12, 3)


def test_multi_image_evidence_binds_by_source_index() -> None:
    provider = _provider()
    request = ObserveRequest(sources=["src_A", "src_B"], question="q")
    parsed = {
        "evidence": [
            {"source_index": 0, "kind": "text", "text": "from A", "confidence": 0.9},
            {"source_index": 1, "kind": "text", "text": "from B", "confidence": 0.8},
        ],
        "summary": "s",
    }
    obs = provider._to_observation(request, parsed, _usage(), "stop")
    assert [e.source_ref for e in obs.evidence] == ["src_A", "src_B"]
    assert [e.text for e in obs.evidence] == ["from A", "from B"]


def test_evidence_binds_by_explicit_source_ref() -> None:
    provider = _provider()
    request = ObserveRequest(sources=["src_A", "src_B"], question="q")
    parsed = {
        "evidence": [
            {"source_ref": "src_B", "kind": "text", "text": "from B"},
        ],
    }
    obs = provider._to_observation(request, parsed, _usage(), "stop")
    assert obs.evidence[0].source_ref == "src_B"


def test_unattributed_evidence_falls_back_and_flags_inference() -> None:
    provider = _provider()
    request = ObserveRequest(sources=["src_A", "src_B"], question="q")
    parsed = {
        "evidence": [{"kind": "text", "text": "no source hint"}],
    }
    obs = provider._to_observation(request, parsed, _usage(), "stop")
    # Falls back to the first source and forces inference=True so a cross-image
    # statement is never treated as a direct reading of one image.
    assert obs.evidence[0].source_ref == "src_A"
    assert obs.evidence[0].inference is True
    assert any(u.severity == "low" and "attribute" in u.text for u in obs.uncertainty)


def test_conflicts_bound_to_source_refs() -> None:
    provider = _provider()
    request = ObserveRequest(sources=["src_A", "src_B"], question="q")
    parsed = {
        "conflicts": [{"text": "disagree", "sources": [0, 1]}],
    }
    obs = provider._to_observation(request, parsed, _usage(), "stop")
    assert obs.conflicts[0].sources == ["src_A", "src_B"]


def test_single_source_default_binding() -> None:
    provider = _provider()
    request = ObserveRequest(sources=["src_only"], question="q")
    parsed = {"evidence": [{"kind": "text", "text": "hello"}]}
    obs = provider._to_observation(request, parsed, _usage(), "stop")
    assert obs.evidence[0].source_ref == "src_only"
    assert obs.evidence[0].inference is False


def test_malformed_evidence_is_conservatively_normalized() -> None:
    provider = _provider()
    request = ObserveRequest(sources=["src_A"], question="q")
    parsed = {
        "evidence": [
            "not a dict",
            {"kind": "text", "text": ""},  # empty text
            {"kind": "text", "text": "good", "confidence": "bogus"},  # bad confidence
        ],
    }
    obs = provider._to_observation(request, parsed, _usage(), "stop")
    assert len(obs.evidence) == 1
    assert obs.evidence[0].confidence == 0.5
    assert any("normalized" in item.text for item in obs.uncertainty)


def test_unsupported_summary_without_evidence_is_not_presented_as_fact() -> None:
    provider = _provider()
    request = ObserveRequest(sources=["src_A"], question="identify it")
    obs = provider._to_observation(
        request,
        {"evidence": [], "summary": "Definitely SomeBrand"},
        _usage(),
        "stop",
    )
    assert obs.summary is None
    assert any(item.severity == "material" for item in obs.uncertainty)


def test_ambiguous_identity_is_downgraded_to_direct_evidence() -> None:
    provider = _provider()
    request = ObserveRequest(sources=["src_A"], question="identify the application")
    obs = provider._to_observation(
        request,
        {
            "evidence": [
                {
                    "kind": "ocr",
                    "text": "37.5K/37K",
                    "confidence": 1,
                    "exact": True,
                },
                {
                    "kind": "inference",
                    "text": "The application is Cursor based on the UI layout.",
                    "confidence": 0.9,
                    "inference": True,
                },
            ],
            "uncertainty": [
                {
                    "text": "The application name is not explicitly visible.",
                    "severity": "low",
                }
            ],
            "summary": "The application is Cursor.",
        },
        _usage(),
        "stop",
    )
    assert obs.summary is None
    assert obs.evidence[0].text == "37.5K/37K"
    assert obs.evidence[1].confidence == 0.84
    assert all(item.severity == "material" for item in obs.uncertainty)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0.9, 0.9), ("80", 0.8), (95, 0.95), ("bogus", None), (True, None), (101, None)],
)
def test_confidence_normalization(raw, expected) -> None:
    assert _normalize_confidence(raw) == expected


def test_observation_is_tainted() -> None:
    provider = _provider()
    request = ObserveRequest(sources=["src_A"], question="q")
    obs = provider._to_observation(request, {"evidence": []}, _usage(), "stop")
    assert obs.tainted is True
    assert obs.may_authorize_actions is False
    assert isinstance(obs, VisionObservation)


@pytest.mark.parametrize(
    "cause_type,code,retryable,possible_duplicate",
    [
        (httpx.ConnectTimeout, ErrorCode.PROVIDER_CONNECT_TIMEOUT, True, False),
        (httpx.ReadTimeout, ErrorCode.PROVIDER_TIMEOUT_AMBIGUOUS, False, True),
    ],
)
async def test_sdk_timeout_preserves_connect_vs_read_semantics(
    cause_type, code, retryable: bool, possible_duplicate: bool
) -> None:
    provider = _provider()
    wire_request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    timeout = APITimeoutError(request=wire_request)
    timeout.__cause__ = cause_type("timeout", request=wire_request)

    async def fail(**kwargs):
        raise timeout

    provider._client = SimpleNamespace(  # type: ignore[assignment]
        responses=SimpleNamespace(create=fail)
    )
    media = SimpleNamespace(mime_type="image/png", canonical_bytes=b"png")

    with pytest.raises(EyesError) as exc:
        await provider.observe(ObserveRequest(sources=["src_A"], question="q"), [media])
    assert exc.value.code == code
    assert exc.value.retryable is retryable
    assert exc.value.possible_duplicate_billing is possible_duplicate
