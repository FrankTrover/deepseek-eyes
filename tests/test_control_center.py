"""Phase 8 — Control Center logic tests (headless; GUI window itself is manual).

Covers everything the GUI delegates to: config backup/rollback, credential
create/delete through the keyring, integration diff/repair, redacted
diagnostics, and corrupted-integration repair.
"""

from __future__ import annotations

import json

import pytest

from deepseek_eyes import diagnostics
from deepseek_eyes.config import (
    EyesConfig,
    backup_config,
    load_config,
    restore_backup,
    save_config,
)
from deepseek_eyes.errors import ErrorCode, EyesError
from deepseek_eyes.integration import (
    apply_integration,
    integration_state,
    load_host_config,
    remove_integration,
    repair_integration,
)


@pytest.fixture()
def fake_keyring(monkeypatch):
    """In-memory keyring so tests never touch the OS credential store."""
    store: dict[str, str] = {}

    class FakeKeyring:
        def get_password(self, service, user):
            return store.get((service, user))

        def set_password(self, service, user, pw):
            store[(service, user)] = pw

        def delete_password(self, service, user):
            store.pop((service, user), None)

    fake = FakeKeyring()
    monkeypatch.setattr("deepseek_eyes.credentials.keyring", fake)
    return store


@pytest.fixture()
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setattr("deepseek_eyes.config.config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(
        "deepseek_eyes.integration._find_host_config", lambda: tmp_path / "opencode.json"
    )


# -- config backup / rollback -------------------------------------------------


def test_config_roundtrip(isolated_config):
    save_config(EyesConfig(fullscreen_capture_allowed=True))
    assert load_config().fullscreen_capture_allowed is True


def test_config_backup_and_restore(isolated_config):
    save_config(EyesConfig(fullscreen_capture_allowed=True))
    backup_config()
    save_config(EyesConfig(fullscreen_capture_allowed=False))
    assert load_config().fullscreen_capture_allowed is False
    restore_backup()
    assert load_config().fullscreen_capture_allowed is True


def test_corrupted_config_raises(isolated_config):
    import deepseek_eyes.config as config_mod

    config_mod.config_path().parent.mkdir(parents=True, exist_ok=True)
    config_mod.config_path().write_text("{ not json", encoding="utf-8")
    with pytest.raises(EyesError) as ei:
        load_config()
    assert ei.value.code == ErrorCode.CONFIG_CORRUPTED


def test_restore_without_backup_raises(isolated_config):
    with pytest.raises(EyesError) as ei:
        restore_backup()
    assert ei.value.code == ErrorCode.CONFIG_NOT_FOUND


# -- credentials --------------------------------------------------------------


def test_credential_save_delete(fake_keyring, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_EYES_BASE_URL", "")
    monkeypatch.delenv("DEEPSEEK_EYES_TOKEN", raising=False)

    from deepseek_eyes.credentials import delete_credential, load_credential, save_credential

    assert load_credential() is None
    save_credential("https://example.com", "tp-secret-1")
    cred = load_credential()
    assert cred.base_url == "https://example.com"
    assert cred.token == "tp-secret-1"
    delete_credential()
    assert load_credential() is None


def test_credential_invalid_token_rejected(fake_keyring):
    from deepseek_eyes.credentials import save_credential

    with pytest.raises(EyesError) as ei:
        save_credential("https://example.com", "not-a-tp-token")
    assert ei.value.code == ErrorCode.TOKEN_PLAN_CONFIG_INVALID


def test_env_credential_wins_over_keyring(fake_keyring, monkeypatch):
    from deepseek_eyes.credentials import load_credential, save_credential

    save_credential("https://keyring.example", "tp-keyring")
    monkeypatch.setenv("DEEPSEEK_EYES_BASE_URL", "https://env.example")
    monkeypatch.setenv("DEEPSEEK_EYES_TOKEN", "tp-env")
    cred = load_credential()
    assert cred.base_url == "https://env.example"


# -- integration --------------------------------------------------------------


def test_apply_remove_integration(isolated_config):
    state = apply_integration()
    assert state.configured is True
    assert integration_state().configured is True

    cfg = load_host_config(state.path)
    entry = cfg["mcp"]["deepseek-eyes"]
    assert entry["type"] == "local"
    assert isinstance(entry["command"], list)
    assert entry["command"][-2:] == ["-m", "deepseek_eyes.mcp_server"]
    # The command must not depend on a bare console script on PATH.
    assert entry["command"][0] != "deepseek-eyes-mcp"
    # No secret ever lands in the host config.
    assert "tp-" not in json.dumps(cfg)

    state = remove_integration()
    assert state.configured is False
    assert integration_state().configured is False


def test_apply_writes_plugin_entry(isolated_config):
    state = apply_integration()
    data = load_host_config(state.path)
    plugins = data["plugin"]
    assert isinstance(plugins, list) and len(plugins) == 1
    entry = plugins[0]
    assert isinstance(entry, list) and len(entry) == 2
    path, options = entry
    assert path.endswith("plugin.ts")
    cmd = options["adapterCommand"]
    assert cmd[-1] == "deepseek_eyes.host_bridge"
    assert cmd[-2] == "-m"
    # The adapter command must not depend on a bare console script on PATH.
    assert cmd[0] != "deepseek-eyes"
    # No secret ever lands in the host config.
    assert "tp-" not in json.dumps(data)

    remove_integration()
    assert "plugin" not in load_host_config(state.path)


def test_apply_plugin_idempotent_and_preserves_others(isolated_config):
    import deepseek_eyes.integration as integ_mod

    p = integ_mod._find_host_config()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"plugin": ["/some/other.mjs"]}), encoding="utf-8")

    apply_integration()
    apply_integration()  # second apply must not duplicate

    data = load_host_config(p)
    plugins = data["plugin"]
    assert len(plugins) == 2  # existing entry + one Eyes entry (no dup)
    assert plugins[0] == "/some/other.mjs"
    assert isinstance(plugins[1], list) and plugins[1][0].endswith("plugin.ts")


def test_apply_preserves_existing_host_config(isolated_config):
    import deepseek_eyes.integration as integ_mod

    p = integ_mod._find_host_config()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"model": "deepseek", "mcp": {"other": {"type": "stdio"}}}),
        encoding="utf-8",
    )
    apply_integration()
    data = load_host_config(p)
    assert data["model"] == "deepseek"
    assert "other" in data["mcp"]
    assert "deepseek-eyes" in data["mcp"]


def test_corrupted_integration_repair(isolated_config):
    import deepseek_eyes.integration as integ_mod

    p = integ_mod._find_host_config()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ nope", encoding="utf-8")
    with pytest.raises(EyesError):
        load_host_config(p)
    state = repair_integration()
    assert state.configured is True
    assert json.loads(p.read_text(encoding="utf-8"))["mcp"]["deepseek-eyes"]["type"] == "local"


def test_jsonc_host_config_with_comments(isolated_config):
    """OpenCode configs are JSONC; comments and trailing commas must parse."""
    import deepseek_eyes.integration as integ_mod

    p = integ_mod._find_host_config()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "{\n"
        "  // model selection\n"
        '  "model": "deepseek",\n'
        '  "mcp": {\n'
        '    "other": {"type": "stdio" /* keep me */},\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    data = load_host_config(p)
    assert data["model"] == "deepseek"
    assert data["mcp"]["other"]["type"] == "stdio"

    # apply_integration must preserve existing entries through the JSONC round-trip.
    apply_integration()
    data2 = load_host_config(p)
    assert data2["model"] == "deepseek"
    assert "other" in data2["mcp"]
    assert data2["mcp"]["deepseek-eyes"]["type"] == "local"


def test_host_config_with_bom_parses(isolated_config):
    """Real opencode.json often starts with a UTF-8 BOM; must still parse."""
    import deepseek_eyes.integration as integ_mod

    p = integ_mod._find_host_config()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('\ufeff{"model": "deepseek"}', encoding="utf-8")
    data = load_host_config(p)
    assert data["model"] == "deepseek"

    # Round-trip through apply must also work.
    apply_integration()
    data2 = load_host_config(p)
    assert data2["mcp"]["deepseek-eyes"]["type"] == "local"


def test_jsonc_strip_never_touches_strings():
    from deepseek_eyes.integration import _strip_jsonc

    src = '{"a": "http://example.com/x", "b": "/* not a comment */", "c": [1, 2,]}'
    cleaned = _strip_jsonc(src)
    assert "http://example.com/x" in cleaned
    assert "/* not a comment */" in cleaned
    # trailing comma before ] is dropped, so json.loads succeeds.
    assert json.loads(cleaned)["c"] == [1, 2]


# -- redacted diagnostics -----------------------------------------------------


def test_diagnostics_never_leak_secrets(fake_keyring, monkeypatch):
    from deepseek_eyes.credentials import save_credential

    save_credential("https://secret.example", "tp-ultra-secret")
    monkeypatch.setenv("DEEPSEEK_EYES_TOKEN", "tp-env-secret")
    report = diagnostics.redacted_report()
    diagnostics.assert_no_secrets(report)
    text = json.dumps(report)
    assert "ultra" not in text
    assert "env-secret" not in text


def test_diagnostics_reports_capabilities():
    report = diagnostics.redacted_report()
    assert report["version"]
    assert "credential_configured" in report
    assert "integration" in report
