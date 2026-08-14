"""Pydantic contracts — the wire-visible schema for DeepSeek Eyes.

These models are the single source of truth for both the MCP tool input/output
schema and the checked-in JSON schemas under ``schemas/``. All models reject
unknown fields so that a host cannot smuggle extension data through a request.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import CONTRACT_VERSION, VISION_SCHEMA_VERSION

# ---------------------------------------------------------------------------
# Bounding box (normalized)
# ---------------------------------------------------------------------------


class BBox(BaseModel):
    """A normalized bounding box in ``[0, 1]`` image-relative coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x0: float = Field(ge=0.0, le=1.0)
    y0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_area(self) -> BBox:
        if self.x0 >= self.x1 or self.y0 >= self.y1:
            raise ValueError("bbox must have positive area (x0 < x1 and y0 < y1)")
        return self


# ---------------------------------------------------------------------------
# Focus (oneOf: full | region | bbox)
# ---------------------------------------------------------------------------


class FullFocus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["full"] = "full"


class RegionFocus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["region"] = "region"
    region_ref: str = Field(min_length=5)


class BBoxFocus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["bbox"] = "bbox"
    bbox: BBox


Focus = Annotated[
    FullFocus | RegionFocus | BBoxFocus,
    Field(discriminator="kind"),
]

ObserveMode = Literal["describe", "extract", "verify", "compare", "qa"]

# ---------------------------------------------------------------------------
# Observe request
# ---------------------------------------------------------------------------


class ObserveRequest(BaseModel):
    """Input to ``deepseek_eyes_observe``."""

    model_config = ConfigDict(extra="forbid")

    sources: list[str] = Field(min_length=1, max_length=8)
    question: str = Field(min_length=1, max_length=4096)
    mode: ObserveMode = "extract"
    focus: Focus | None = None
    # Stable constitution / contract pins. Hosts must not override these.
    contract_version: str = CONTRACT_VERSION
    vision_schema_version: str = VISION_SCHEMA_VERSION

    @model_validator(mode="after")
    def _validate_mode_sources(self) -> ObserveRequest:
        if self.mode == "compare" and len(self.sources) < 2:
            raise ValueError("compare mode requires at least 2 sources")
        return self


# ---------------------------------------------------------------------------
# Evidence + observation result
# ---------------------------------------------------------------------------

EvidenceKind = Literal[
    "text",
    "ocr",
    "code",
    "diagram",
    "exact_value",
    "inference",
    "conflict",
    "unsupported",
]


class Evidence(BaseModel):
    """A single tainted visual evidence item, bound to a source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_ref: str
    kind: EvidenceKind
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: BBox | None = None
    # True when the field was reproduced char-exact and locally re-verified
    # against the source pixels (e.g. an exact string match).
    exact: bool = False
    # True when the statement interprets visible facts (for example identity or
    # intent) rather than directly reading pixels, including cross-image inference.
    inference: bool = False


class Uncertainty(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    severity: Literal["low", "material"] = "low"


class Conflict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    sources: list[str] = Field(min_length=2)


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    image_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int = Field(ge=0)


class VisionObservation(BaseModel):
    """Structured, tainted observation returned by the Runtime.

    The trust boundary is hard-coded: visual output may never authorize
    privileged actions, and every item is marked as untrusted evidence.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"] = "ok"
    contract_version: str = CONTRACT_VERSION
    vision_schema_version: str = VISION_SCHEMA_VERSION
    evidence: list[Evidence] = Field(default_factory=list)
    uncertainty: list[Uncertainty] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    summary: str | None = None
    usage: Usage | None = None
    model: str | None = None
    # Hard-coded trust boundary (see SECURITY_GUARANTEES_AND_HOST_ASSUMPTIONS.md).
    tainted: Literal[True] = True
    may_authorize_actions: Literal[False] = False


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class CapabilitiesResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = "deepseek_eyes"
    version: str
    contract_version: str = CONTRACT_VERSION
    vision_schema_version: str = VISION_SCHEMA_VERSION
    provider: str = "mimo-v2.5"
    # MVP-A has no Host Action Guard adapter installed yet.
    host_action_guard: bool = False
    capture_available: bool = False
    max_sources: int = 8


# ---------------------------------------------------------------------------
# Capture (out of MVP-A scope — returns permission denial)
# ---------------------------------------------------------------------------


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["region", "window", "fullscreen"] = "region"


class CaptureResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"] = "ok"
    source_ref: str


__all__ = [
    "BBox",
    "BBoxFocus",
    "CapabilitiesResult",
    "CaptureRequest",
    "CaptureResult",
    "Conflict",
    "Evidence",
    "EvidenceKind",
    "Focus",
    "FullFocus",
    "ObserveMode",
    "ObserveRequest",
    "RegionFocus",
    "Uncertainty",
    "Usage",
    "VisionObservation",
]
