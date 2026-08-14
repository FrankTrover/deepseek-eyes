"""Persistent user configuration for DeepSeek Eyes.

Stored as redacted-safe JSON under the platform config dir. Holds capability
switches (full-screen capture) and nothing secret — credentials live in the OS
keyring (see :mod:`deepseek_eyes.credentials`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from platformdirs import user_config_dir

from .errors import ErrorCode, EyesError

APP_NAME = "deepseek-eyes"


@dataclass(frozen=True)
class EyesConfig:
    """User-settable configuration (all fields optional on disk)."""

    fullscreen_capture_allowed: bool = False
    region_capture_allowed: bool = True
    window_capture_allowed: bool = True
    # Hosts Eyes integrates with (integration section of the Control Center).
    host_auto_resize: bool = True


def config_path() -> Path:
    return Path(user_config_dir(APP_NAME)) / "config.json"


def load_config() -> EyesConfig:
    p = config_path()
    if not p.is_file():
        return EyesConfig()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EyesError(ErrorCode.CONFIG_CORRUPTED, f"config file unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise EyesError(ErrorCode.CONFIG_CORRUPTED, "config root must be a JSON object")
    known = {f.name for f in fields(EyesConfig)}
    clean = {k: v for k, v in data.items() if k in known and isinstance(v, bool)}
    return EyesConfig(**clean)


def save_config(cfg: EyesConfig) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    tmp.replace(p)


def backup_config() -> Path:
    """Copy the current config to ``config.json.backup`` and return its path."""
    p = config_path()
    if not p.is_file():
        raise EyesError(ErrorCode.CONFIG_NOT_FOUND, "no config file to back up")
    backup = p.with_suffix(".json.backup")
    backup.write_bytes(p.read_bytes())
    return backup


def restore_backup() -> EyesConfig:
    """Replace the current config with the last backup, if one exists."""
    backup = config_path().with_suffix(".json.backup")
    if not backup.is_file():
        raise EyesError(ErrorCode.CONFIG_NOT_FOUND, "no config backup exists")
    backup.replace(config_path())
    return load_config()
