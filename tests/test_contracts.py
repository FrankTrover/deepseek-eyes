"""Phase 0 contract & security skeleton tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from deepseek_eyes import CONTRACT_VERSION, VISION_SCHEMA_VERSION
from deepseek_eyes.contracts import (
    BBox,
    BBoxFocus,
    CapabilitiesResult,
    ObserveRequest,
    VisionObservation,
)
from deepseek_eyes.errors import ErrorCode, EyesError
from deepseek_eyes.ids import is_region_ref, is_source_ref, new_region_ref, new_source_ref

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        ObserveRequest(sources=["src_x"], question="q", evil_extra=1)


def test_focus_oneof_enforced() -> None:
    # A focus with a kind but no matching payload must fail.
    with pytest.raises(ValidationError):
        ObserveRequest(sources=["src_x"], question="q", focus={"kind": "region"})
    # A valid bbox focus passes.
    req = ObserveRequest(
        sources=["src_x"],
        question="q",
        focus={"kind": "bbox", "bbox": {"x0": 0, "y0": 0, "x1": 0.5, "y1": 0.5}},
    )
    assert isinstance(req.focus, BBoxFocus)


def test_invalid_bbox_rejected() -> None:
    with pytest.raises(ValidationError):
        BBox(x0=0.5, y0=0, x1=0.4, y1=1)  # x0 >= x1
    with pytest.raises(ValidationError):
        BBox(x0=0, y0=0, x1=2.0, y1=1)  # out of range


def test_ids_high_entropy() -> None:
    a = new_source_ref()
    b = new_source_ref()
    r = new_region_ref()
    assert a != b
    assert is_source_ref(a) and is_source_ref(b)
    assert is_region_ref(r)
    assert not is_source_ref(r)
    # 128 bits -> at least 22 base64url chars after the prefix.
    assert len(a) - len("src_") >= 22


def test_trust_boundary_fixed() -> None:
    obs = VisionObservation()
    assert obs.tainted is True
    assert obs.may_authorize_actions is False


def test_all_error_codes_serializable() -> None:
    for code in ErrorCode:
        err = EyesError(code, "msg")
        d = err.to_dict()
        assert d["code"] == code.value
        assert d["message"] == "msg"
        json.dumps(d)  # must be JSON-serializable


def test_compare_requires_two_sources() -> None:
    with pytest.raises(ValidationError):
        ObserveRequest(sources=["src_x"], question="q", mode="compare")


def test_source_count_bounds() -> None:
    with pytest.raises(ValidationError):
        ObserveRequest(sources=[], question="q")
    with pytest.raises(ValidationError):
        ObserveRequest(sources=[f"src_{i}" for i in range(9)], question="q")


@pytest.mark.parametrize(
    "name",
    ["observe_request", "vision_observation", "capabilities", "evidence", "usage", "bbox"],
)
def test_checked_in_schema_matches(name: str) -> None:
    """Pydantic schema must match the checked-in JSON schema (contract test)."""
    from deepseek_eyes import contracts

    model = getattr(
        contracts,
        {
            "observe_request": "ObserveRequest",
            "vision_observation": "VisionObservation",
            "capabilities": "CapabilitiesResult",
            "evidence": "Evidence",
            "usage": "Usage",
            "bbox": "BBox",
        }[name],
    )
    live = model.model_json_schema()
    checked = json.loads((SCHEMAS / f"{name}.json").read_text(encoding="utf-8"))
    assert live == checked, f"schema drift for {name}: regenerate schemas/*.json"


def test_capabilities_flags() -> None:
    caps = CapabilitiesResult(version="0.1.0")
    assert caps.host_action_guard is False
    assert caps.capture_available is False
    assert caps.contract_version == CONTRACT_VERSION
    assert caps.vision_schema_version == VISION_SCHEMA_VERSION
