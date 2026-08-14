"""Redacted diagnostics — never reveals tokens, base64 payloads, or image data.

Used by the CLI ``diagnostics`` command and the Control Center's
Security/Diagnostics tab.
"""

from __future__ import annotations

import json
import platform
import sys
from typing import Any

from . import __version__
from .config import load_config
from .credentials import has_credential
from .integration import integration_state


def redacted_report(runtime_stats: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assemble a diagnostic report with every secret field replaced."""
    report: dict[str, Any] = {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "credential_configured": has_credential(),
        "credential_source": _credential_source(),
        "config": _redacted_config(),
        "integration": _redacted_integration(),
        "runtime": runtime_stats or {},
    }
    return report


def _credential_source() -> str | None:
    import os

    if os.environ.get("DEEPSEEK_EYES_TOKEN"):
        return "env"
    from .credentials import load_credential

    try:
        if load_credential() is not None:
            return "keyring"
    except Exception:
        return "unavailable"
    return None


def _redacted_config() -> dict[str, Any]:
    try:
        return load_config().__dict__
    except Exception as exc:
        return {"error": str(exc)}


def _redacted_integration() -> dict[str, Any]:
    state = integration_state()
    return {
        "path": str(state.path),
        "configured": state.configured,
    }


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)


def assert_no_secrets(report: dict[str, Any]) -> None:
    """Guard: the serialized report must not contain a tp- token or base64 blob."""
    text = json.dumps(report)
    assert "tp-" not in text, "diagnostics leaked a token"
    assert "base64," not in text, "diagnostics leaked an image payload"
