"""Xiaomi MiMo Token Plan provider (OpenAI-compatible).

Constraints enforced here (Phase 2 acceptance):
- persistent client, never rebuilt per request;
- ``mimo-v2.5`` model;
- Responses API ``reasoning.effort=high`` (full MiMo visual reasoning);
- no tools, no web search;
- structured JSON output;
- usage extraction (incl. cached/image/reasoning tokens);
- finish_reason handling and a retry classifier;
- secret redaction (the token is never logged or echoed).

The provider is fully mockable: ``VisionProvider`` is a Protocol, so tests inject
a fake. Live calls require ``tp-`` + Base URL (see ``spikes/SPIKE_RESULT.md``).
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from PIL import Image

from .contracts import (
    Conflict,
    Evidence,
    ObserveRequest,
    Uncertainty,
    Usage,
    VisionObservation,
)
from .errors import ErrorCode, EyesError, from_http_status
from .security import CANONICAL_MIME

PROVIDER_MODEL = "mimo-v2.5"
MIN_VISION_MAX_EDGE = 512
VALID_EVIDENCE_KINDS = {
    "text",
    "ocr",
    "code",
    "diagram",
    "exact_value",
    "inference",
    "conflict",
    "unsupported",
}
MATERIAL_UNCERTAINTY_MARKERS = (
    "not explicitly visible",
    "not visible",
    "not shown",
    "cannot confirm",
    "can't confirm",
    "ambiguous",
    "unclear",
    "无法确认",
    "未明确显示",
    "没有明确显示",
    "看不到",
    "不确定",
    "不明确",
)


def _vision_payload_bytes(media: Any) -> bytes:
    """Upscale tiny UI thumbnails without increasing MiMo image-tile cost."""
    raw = media.canonical_bytes
    width = int(getattr(media, "width", 0) or 0)
    height = int(getattr(media, "height", 0) or 0)
    longest = max(width, height)
    if longest <= 0 or longest >= MIN_VISION_MAX_EDGE:
        return raw

    scale = MIN_VISION_MAX_EDGE / longest
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    with Image.open(io.BytesIO(raw)) as image:
        enlarged = image.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        output = io.BytesIO()
        enlarged.save(output, format="PNG", optimize=False)
    return output.getvalue()


class MiMoConfig:
    """Minimal Token Plan configuration, loaded by the Runtime."""

    def __init__(self, base_url: str, token: str, *, model: str = PROVIDER_MODEL) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.model = model

    def validate(self) -> None:
        if not self.base_url.startswith(("https://", "http://")):
            raise EyesError(ErrorCode.TOKEN_PLAN_CONFIG_INVALID, "base_url must be an http(s) URL")
        if not self.token.startswith("tp-"):
            raise EyesError(ErrorCode.TOKEN_PLAN_CONFIG_INVALID, "token must start with 'tp-'")
        if not self.model:
            raise EyesError(ErrorCode.TOKEN_PLAN_CONFIG_INVALID, "model must not be empty")


class MiMoProvider:
    """OpenAI-compatible async client pinned to the MiMo Token Plan."""

    def __init__(self, config: MiMoConfig, *, timeout: float = 300.0) -> None:
        config.validate()
        self._config = config
        self._client: AsyncOpenAI = self._build_client(timeout)

    def _build_client(self, timeout: float) -> AsyncOpenAI:
        # httpx is configured explicitly: no environment proxy, no trusting env.
        return AsyncOpenAI(
            base_url=self._config.base_url,
            api_key=self._config.token,
            timeout=timeout,
            max_retries=0,  # retries are classified and bounded by the Runtime
            http_client=httpx.AsyncClient(
                trust_env=False,
                timeout=httpx.Timeout(timeout, connect=10.0),
            ),
        )

    @property
    def model(self) -> str:
        return self._config.model

    async def observe(self, request: ObserveRequest, media) -> VisionObservation:
        """Send a vision request and parse/validate the structured result."""
        content: list[dict[str, Any]] = []
        for m in media:
            payload = _vision_payload_bytes(m)
            content.append(
                {
                    "type": "input_image",
                    "image_url": (
                        f"data:{CANONICAL_MIME};base64,"
                        f"{base64.b64encode(payload).decode('ascii')}"
                    ),
                    "detail": "high",
                }
            )
        # Keep the large, invariant contract in ``instructions`` and put the
        # changing question after the images.  Prefix caches can then reuse the
        # contract for every call and the image bytes when the same attachment
        # is queried again, without omitting any visual information.
        content.append({"type": "input_text", "text": self._build_query(request, media)})

        try:
            response = await self._client.responses.create(
                model=self._config.model,
                instructions=self._build_instructions(),
                input=[{"role": "user", "content": content}],  # type: ignore[list-item,misc]
                # MiMo currently maps every non-``none`` effort to its full
                # thinking mode. Keep it explicit so cost controls never
                # trade away visual reasoning quality.
                reasoning={"effort": "high"},
                text={"format": {"type": "json_object"}},
                # This is MiMo-V2.5's documented default, pinned so a provider
                # default change cannot silently reduce observation capacity.
                max_output_tokens=32_768,
                stream=False,
            )
        except APITimeoutError as exc:
            # The OpenAI SDK wraps every httpx timeout in APITimeoutError.
            # Only connect timeout is known to happen before transmission.
            if isinstance(exc.__cause__, httpx.ConnectTimeout):
                raise EyesError(
                    ErrorCode.PROVIDER_CONNECT_TIMEOUT,
                    "provider connect timeout",
                    retryable=True,
                ) from exc
            raise EyesError(
                ErrorCode.PROVIDER_TIMEOUT_AMBIGUOUS,
                "provider read timeout; request may have been processed",
                possible_duplicate_billing=True,
            ) from exc
        except httpx.ConnectTimeout as exc:
            raise EyesError(
                ErrorCode.PROVIDER_CONNECT_TIMEOUT,
                "provider connect timeout",
                retryable=True,
            ) from exc
        except httpx.ConnectError as exc:
            raise EyesError(
                ErrorCode.PROVIDER_CONNECT_TIMEOUT,
                "provider connection failed before request transmission",
                retryable=True,
            ) from exc
        except httpx.ReadTimeout as exc:
            raise EyesError(
                ErrorCode.PROVIDER_TIMEOUT_AMBIGUOUS,
                "provider read timeout; request may have been processed",
                possible_duplicate_billing=True,
            ) from exc
        except APIConnectionError as exc:
            if isinstance(exc.__cause__, httpx.ConnectError):
                raise EyesError(
                    ErrorCode.PROVIDER_CONNECT_TIMEOUT,
                    "provider connection failed before request transmission",
                    retryable=True,
                ) from exc
            raise EyesError(
                ErrorCode.PROVIDER_TIMEOUT_AMBIGUOUS,
                "provider connection dropped; request may have been processed",
                possible_duplicate_billing=True,
            ) from exc
        except APIStatusError as exc:
            raise from_http_status(exc.status_code, f"provider error {exc.status_code}") from exc
        except httpx.HTTPError as exc:
            raise EyesError(
                ErrorCode.PROVIDER_TIMEOUT_AMBIGUOUS,
                f"provider transport failed; request may have been processed: {exc}",
                possible_duplicate_billing=True,
            ) from exc

        finish_reason = getattr(response, "status", None) or "completed"
        incomplete = getattr(response, "incomplete_details", None)
        if finish_reason == "incomplete" and getattr(incomplete, "reason", None) in {
            "max_output_tokens",
            "length",
        }:
            raise EyesError(ErrorCode.OUTPUT_TRUNCATED, "provider output truncated by token limit")
        if finish_reason == "failed":
            raise EyesError(ErrorCode.OUTPUT_PROTOCOL_VIOLATION, "provider response failed")

        raw_text = getattr(response, "output_text", "") or ""
        parsed = self._parse_json(raw_text)

        return self._to_observation(request, parsed, response.usage, finish_reason)

    def _build_prompt(self, request: ObserveRequest, media: Any | None = None) -> str:
        """Return the complete logical prompt for diagnostics and tests."""
        return f"{self._build_instructions()}\n\n{self._build_query(request, media)}"

    @staticmethod
    def _build_instructions() -> str:
        # JSON mode guarantees syntax, while this invariant contract defines
        # the evidence semantics.  Keeping it request-independent is essential
        # for the provider's prefix cache.
        return (
            "You are a grounded visual inspector for a coding agent. Inspect the supplied "
            "pixels before deciding what they represent. Separate direct visible facts from "
            "interpretation and never fill missing detail from memory.\n\n"
            "Respond with a single JSON object matching exactly this shape:\n"
            "{\n"
            '  "evidence": [\n'
            '    {"source_index": 0, "kind": "text", "text": "...", "confidence": 0.9, '
            '"exact": false, "inference": false}\n'
            "  ],\n"
            '  "uncertainty": [{"text": "...", "severity": "low" | "material"}],\n'
            '  "conflicts": [{"text": "...", "sources": [0, 1]}],\n'
            '  "summary": "..."\n'
            "}\n\n"
            "Rules:\n"
            "- First inventory what is literally visible: exact text, layout, colors, shapes, "
            "icon geometry, and overlays/badges. Only then answer the user's question.\n"
            "- OCR every legible string character-for-character. Do not silently correct, "
            "translate, or complete clipped text.\n"
            "- source_index is the 0-based index of the image each evidence item refers to; "
            "every evidence item MUST carry one.\n"
            "- kind is one of: text, ocr, code, diagram, exact_value, inference, conflict, "
            "unsupported.\n"
            '- Set "exact": true only for values reproduced character-for-character.\n'
            '- Set "inference": true for identity, intent, or other interpretation that is not '
            "literally printed in the image, and for cross-image conclusions.\n"
            '- Put any value you cannot read confidently into "uncertainty" with severity '
            '"material".\n'
            "- For software/icon identity, treat shortcut arrows, notification dots, selection "
            "boxes, and other shell badges as overlays rather than part of the underlying logo.\n"
            "- Shared interface chrome is not unique identity evidence. In particular, generic "
            "editor status bars, Agent panels, and model selectors cannot distinguish VS Code "
            "from Cursor or another VS Code fork. If a title, product name, or unique logo is not "
            "literally visible, mark the identity uncertainty as material and do not choose one.\n"
            "- Never identify a product from color alone. State the discriminating geometry or "
            "exact visible text as direct evidence, then put the identity in a separate "
            'kind="inference" evidence item with calibrated confidence.\n'
            "- If two identities remain plausible, do not pick one. Name at most two candidates "
            "in uncertainty and state the missing visual feature that would decide between them.\n"
            "- The summary must not be more certain or more specific than the evidence. It must "
            "directly answer the user question using only the supplied pixels.\n"
            "- Return JSON only: no markdown fences, no commentary."
        )

    @staticmethod
    def _build_query(request: ObserveRequest, media: Any | None = None) -> str:
        dimensions = ", ".join(
            f"image {index}={getattr(item, 'width', '?')}x{getattr(item, 'height', '?')}"
            for index, item in enumerate(media or [])
        )
        return (
            "<USER_QUESTION>\n"
            f"{request.question}\n"
            "</USER_QUESTION>\n"
            f"Mode: {request.mode}\n"
            f"Number of images: {len(request.sources)}\n"
            f"Dimensions: {dimensions or 'not provided'}"
        )

    def _parse_json(self, text: str) -> dict[str, Any]:
        text = text.strip()
        # Strip a markdown code fence if the model wraps JSON.
        if text.startswith("```"):
            text = text.split("```", 2)[1] if text.count("```") >= 2 else text[3:]
            text = text.strip()
            if text.startswith("json"):
                text = text[4:].strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EyesError(
                ErrorCode.OUTPUT_PROTOCOL_VIOLATION, "provider returned malformed JSON"
            ) from exc
        if not isinstance(parsed, dict):
            raise EyesError(ErrorCode.OUTPUT_PROTOCOL_VIOLATION, "provider JSON must be an object")
        return parsed

    def _resolve_source_ref(self, item: dict[str, Any], request: ObserveRequest) -> str:
        """Map an evidence item's ``source_ref``/``source_index`` to a real ref."""
        ref = item.get("source_ref")
        if isinstance(ref, str) and ref in request.sources:
            return ref
        idx = item.get("source_index")
        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(request.sources):
            return request.sources[idx]
        if len(request.sources) == 1:
            return request.sources[0]
        raise ValueError("evidence item has no valid source_ref/source_index")

    def _to_observation(
        self,
        request: ObserveRequest,
        parsed: dict[str, Any],
        usage: Any | None,
        finish_reason: str,
    ) -> VisionObservation:
        try:
            evidence: list[Evidence] = []
            unattributed = False
            normalized_items = 0
            for e in parsed.get("evidence", []):
                if not isinstance(e, dict):
                    continue
                text = e.get("text", "")
                if not isinstance(text, str) or not text.strip():
                    continue
                try:
                    source_ref = self._resolve_source_ref(e, request)
                    inference = e.get("inference") is True
                except ValueError:
                    # Deterministic fallback: bind to the first source and force
                    # the item to be flagged as inference rather than dropping it.
                    source_ref = request.sources[0]
                    inference = True
                    unattributed = True
                kind = e.get("kind", "text")
                if kind not in VALID_EVIDENCE_KINDS:
                    kind = "text"
                    normalized_items += 1
                confidence = _normalize_confidence(e.get("confidence"))
                if confidence is None:
                    confidence = 0.5
                    normalized_items += 1
                evidence.append(
                    Evidence(
                        source_ref=source_ref,
                        kind=kind,
                        text=text.strip(),
                        confidence=confidence,
                        exact=e.get("exact") is True,
                        inference=inference or kind == "inference",
                    )
                )

            uncertainty = [
                Uncertainty(
                    text=u.get("text", "").strip(),
                    severity=(
                        "material"
                        if _describes_material_uncertainty(u.get("text", ""))
                        else (
                            u.get("severity")
                            if u.get("severity") in {"low", "material"}
                            else "material"
                        )
                    ),
                )
                for u in parsed.get("uncertainty", [])
                if isinstance(u, dict)
                and isinstance(u.get("text"), str)
                and u.get("text", "").strip()
            ]
            if unattributed:
                uncertainty.append(
                    Uncertainty(
                        text="Some evidence could not be attributed to a specific image "
                        "source; treat cross-image statements as inference.",
                        severity="low",
                    )
                )
            if normalized_items:
                uncertainty.append(
                    Uncertainty(
                        text="Some provider evidence fields were malformed and were "
                        "conservatively normalized.",
                        severity="low",
                    )
                )

            conflicts: list[Conflict] = []
            for c in parsed.get("conflicts", []):
                if not isinstance(c, dict) or not c.get("text"):
                    continue
                refs: list[str] = []
                for s in c.get("sources", []):
                    if isinstance(s, str) and s in request.sources:
                        refs.append(s)
                    elif (
                        isinstance(s, int)
                        and not isinstance(s, bool)
                        and 0 <= s < len(request.sources)
                    ):
                        refs.append(request.sources[s])
                if len(refs) >= 2:
                    conflicts.append(Conflict(text=c["text"], sources=refs))

            summary = parsed.get("summary")
            summary = summary.strip() if isinstance(summary, str) and summary.strip() else None
            if any(item.severity == "material" for item in uncertainty):
                has_inference = False
                grounded: list[Evidence] = []
                for item in evidence:
                    if item.inference:
                        has_inference = True
                        if item.confidence >= 0.85:
                            item = item.model_copy(update={"confidence": 0.84})
                    grounded.append(item)
                evidence = grounded
                if has_inference:
                    summary = None
                    uncertainty.append(
                        Uncertainty(
                            text="The interpretive conclusion was withheld because its visual "
                            "basis is materially uncertain; use the direct evidence instead.",
                            severity="material",
                        )
                    )
            if not evidence:
                summary = None
                uncertainty.append(
                    Uncertainty(
                        text="No grounded visual evidence was returned; no visual conclusion "
                        "is reliable.",
                        severity="material",
                    )
                )

            obs = VisionObservation(
                evidence=evidence,
                uncertainty=uncertainty,
                conflicts=conflicts,
                summary=summary,
                usage=self._extract_usage(usage),
                model=self._config.model,
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise EyesError(
                ErrorCode.OUTPUT_SCHEMA_INVALID, f"provider output did not match schema: {exc}"
            ) from exc
        return obs

    def _extract_usage(self, usage: Any | None) -> Usage | None:
        if usage is None:
            return None
        u = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
        prompt = int(u.get("prompt_tokens", u.get("input_tokens", 0)) or 0)
        completion = int(u.get("completion_tokens", u.get("output_tokens", 0)) or 0)
        total = int(u.get("total_tokens", prompt + completion) or 0)
        prompt_details = _details(u, "prompt_tokens_details") or _details(
            u, "input_tokens_details"
        )
        completion_details = _details(u, "completion_tokens_details") or _details(
            u, "output_tokens_details"
        )
        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            cached_tokens=_opt_int(prompt_details.get("cached_tokens")),
            image_tokens=_opt_int(prompt_details.get("image_tokens")),
            reasoning_tokens=_opt_int(completion_details.get("reasoning_tokens")),
            total_tokens=total,
        )


def _opt_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _normalize_confidence(value: Any) -> float | None:
    """Accept a probability or an explicit percentage without inventing certainty."""
    if isinstance(value, bool):
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= confidence <= 1.0:
        return confidence
    if 1.0 < confidence <= 100.0:
        return confidence / 100.0
    return None


def _describes_material_uncertainty(text: str) -> bool:
    folded = text.casefold()
    return any(marker in folded for marker in MATERIAL_UNCERTAINTY_MARKERS)


def _details(u: dict[str, Any], section: str) -> dict[str, Any]:
    d = u.get(section)
    return d if isinstance(d, dict) else {}


# Retry classifier — exposed for tests and the Runtime's bounded-retry loop.
def should_retry(err: EyesError, attempt: int, max_attempts: int) -> bool:
    if not err.retryable:
        return False
    return attempt < max_attempts
