"""Token Plan credential storage (OS keyring only, never on disk in the clear).

The keyring entry mirrors the environment variables the Runtime already accepts:
``DEEPSEEK_EYES_BASE_URL`` and ``DEEPSEEK_EYES_TOKEN``. Env vars win when set
(CI / host integration); otherwise the Control Center stores/reads the keyring.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import keyring

from .errors import ErrorCode, EyesError
from .provider import MiMoConfig

_SERVICE = "deepseek-eyes"
_TOKEN_USER = "token-plan"
_URL_USER = "token-plan-base-url"


@dataclass(frozen=True)
class StoredCredential:
    base_url: str
    token: str

    def to_config(self) -> MiMoConfig:
        return MiMoConfig(base_url=self.base_url, token=self.token)


def env_credential() -> StoredCredential | None:
    base_url = os.environ.get("DEEPSEEK_EYES_BASE_URL", "").strip()
    token = os.environ.get("DEEPSEEK_EYES_TOKEN", "").strip()
    if base_url and token:
        return StoredCredential(base_url=base_url, token=token)
    return None


def load_credential() -> StoredCredential | None:
    """Env credential first, then the keyring."""
    from_env = env_credential()
    if from_env is not None:
        return from_env
    try:
        token = keyring.get_password(_SERVICE, _TOKEN_USER)
        base_url = keyring.get_password(_SERVICE, _URL_USER)
    except Exception as exc:  # pragma: no cover — backend dependent
        raise EyesError(ErrorCode.KEYRING_UNAVAILABLE, f"keyring unavailable: {exc}") from exc
    if not token or not base_url:
        return None
    return StoredCredential(base_url=base_url, token=token)


def save_credential(base_url: str, token: str) -> None:
    """Validate, then persist to the OS keyring. Never writes the token to disk."""
    cfg = MiMoConfig(base_url=base_url, token=token)
    cfg.validate()
    try:
        keyring.set_password(_SERVICE, _TOKEN_USER, token)
        keyring.set_password(_SERVICE, _URL_USER, base_url)
    except Exception as exc:  # pragma: no cover — backend dependent
        raise EyesError(ErrorCode.KEYRING_UNAVAILABLE, f"keyring unavailable: {exc}") from exc


def delete_credential() -> None:
    try:
        keyring.delete_password(_SERVICE, _TOKEN_USER)
        keyring.delete_password(_SERVICE, _URL_USER)
    except keyring.errors.PasswordDeleteError:
        pass
    except Exception as exc:  # pragma: no cover — backend dependent
        raise EyesError(ErrorCode.KEYRING_UNAVAILABLE, f"keyring unavailable: {exc}") from exc


def has_credential() -> bool:
    try:
        return load_credential() is not None
    except EyesError:
        return False
