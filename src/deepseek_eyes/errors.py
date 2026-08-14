"""Unified error taxonomy for DeepSeek Eyes.

Codes mirror ``rule/ERROR_AND_MULTI_IMAGE.md``. Every failure the Runtime returns
to a host is an :class:`EyesError` carrying a stable ``code`` so callers can branch
without string-matching messages.
"""

from __future__ import annotations

import json
from enum import StrEnum


class ErrorCode(StrEnum):
    # Contract
    CONTRACT_VERSION_MISMATCH = "CONTRACT_VERSION_MISMATCH"
    REQUEST_INVALID = "REQUEST_INVALID"
    OUTPUT_SCHEMA_INVALID = "OUTPUT_SCHEMA_INVALID"
    OUTPUT_PROTOCOL_VIOLATION = "OUTPUT_PROTOCOL_VIOLATION"
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"

    # Source
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    SOURCE_EXPIRED = "SOURCE_EXPIRED"
    SOURCE_FORBIDDEN = "SOURCE_FORBIDDEN"
    SOURCE_TOO_LARGE = "SOURCE_TOO_LARGE"
    SOURCE_DECODE_FAILED = "SOURCE_DECODE_FAILED"
    SOURCE_REGISTRY_FULL = "SOURCE_REGISTRY_FULL"
    REGION_INVALID = "REGION_INVALID"
    REGION_STALE = "REGION_STALE"

    # Permission
    CAPTURE_NOT_ALLOWED = "CAPTURE_NOT_ALLOWED"
    CAPTURE_CANCELLED = "CAPTURE_CANCELLED"
    CAPTURE_CONFIRMATION_DENIED = "CAPTURE_CONFIRMATION_DENIED"
    FULLSCREEN_NOT_ENABLED = "FULLSCREEN_NOT_ENABLED"

    # Credential / Provider
    TOKEN_PLAN_CONFIG_INVALID = "TOKEN_PLAN_CONFIG_INVALID"
    CREDENTIAL_MISSING = "CREDENTIAL_MISSING"
    PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    PROVIDER_BALANCE_OR_PLAN_EXHAUSTED = "PROVIDER_BALANCE_OR_PLAN_EXHAUSTED"
    PROVIDER_FORBIDDEN = "PROVIDER_FORBIDDEN"
    PROVIDER_BAD_REQUEST = "PROVIDER_BAD_REQUEST"
    PROVIDER_CONTENT_FILTERED = "PROVIDER_CONTENT_FILTERED"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    PROVIDER_SERVER_ERROR = "PROVIDER_SERVER_ERROR"
    PROVIDER_OVERLOADED = "PROVIDER_OVERLOADED"
    PROVIDER_CONNECT_TIMEOUT = "PROVIDER_CONNECT_TIMEOUT"
    PROVIDER_TIMEOUT_AMBIGUOUS = "PROVIDER_TIMEOUT_AMBIGUOUS"

    # Budget
    LOCAL_RATE_LIMIT = "LOCAL_RATE_LIMIT"
    CONCURRENCY_LIMIT = "CONCURRENCY_LIMIT"
    OBSERVATION_BUDGET_EXCEEDED = "OBSERVATION_BUDGET_EXCEEDED"

    # Config / credential store
    CONFIG_CORRUPTED = "CONFIG_CORRUPTED"
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    KEYRING_UNAVAILABLE = "KEYRING_UNAVAILABLE"
    INTEGRATION_CORRUPTED = "INTEGRATION_CORRUPTED"


# HTTP status -> error code, and whether the failure is safe to retry.
# Retry policy per ERROR_AND_MULTI_IMAGE.md §2.
_PROVIDER_STATUS_MAP: dict[int, tuple[ErrorCode, bool]] = {
    400: (ErrorCode.PROVIDER_BAD_REQUEST, False),
    401: (ErrorCode.PROVIDER_AUTH_FAILED, False),
    402: (ErrorCode.PROVIDER_BALANCE_OR_PLAN_EXHAUSTED, False),
    403: (ErrorCode.PROVIDER_FORBIDDEN, False),
    404: (ErrorCode.PROVIDER_BAD_REQUEST, False),
    421: (ErrorCode.PROVIDER_OVERLOADED, False),
    429: (ErrorCode.PROVIDER_RATE_LIMIT, True),
    500: (ErrorCode.PROVIDER_SERVER_ERROR, True),
    503: (ErrorCode.PROVIDER_SERVER_ERROR, True),
}

# Content-filter style failures map to a distinct code and are never retried.
_CONTENT_FILTER_STATUS_MAP: dict[int, ErrorCode] = {
    451: ErrorCode.PROVIDER_CONTENT_FILTERED,
}


class EyesError(Exception):
    """Structured error returned across the Runtime / MCP boundary."""

    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        *,
        retryable: bool = False,
        possible_duplicate_billing: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = ErrorCode(code) if isinstance(code, str) else code
        self.message = message
        self.retryable = retryable
        self.possible_duplicate_billing = possible_duplicate_billing

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "possible_duplicate_billing": self.possible_duplicate_billing,
        }

    def __str__(self) -> str:
        # Keep the wire representation structured so the MCP ``is_error`` text
        # content still carries the code (see ERROR_AND_MULTI_IMAGE.md §7).
        return json.dumps(self.to_dict(), ensure_ascii=False)


def from_http_status(status: int, message: str) -> EyesError:
    """Map a provider HTTP status to a structured :class:`EyesError`."""
    if status in _PROVIDER_STATUS_MAP:
        code, retryable = _PROVIDER_STATUS_MAP[status]
        return EyesError(code, message, retryable=retryable)
    if status in _CONTENT_FILTER_STATUS_MAP:
        return EyesError(_CONTENT_FILTER_STATUS_MAP[status], message, retryable=False)
    if 400 <= status < 500:
        return EyesError(ErrorCode.PROVIDER_BAD_REQUEST, message, retryable=False)
    return EyesError(ErrorCode.PROVIDER_SERVER_ERROR, message, retryable=False)
