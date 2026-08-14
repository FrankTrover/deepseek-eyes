"""Host integration management — OpenCode first.

Manages the ``mcp`` section of ``opencode.json`` so the host launches
``deepseek-eyes-mcp``. No secrets are written into the config: the MCP server
resolves its credential from the environment or the OS keyring itself.

Operations: inspect, apply (with backup), diff, repair (restore backup or
rewrite a corrupted config), and uninstall (restore original).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ErrorCode, EyesError

MCP_SERVER_KEY = "deepseek-eyes"


def _default_mcp_command() -> list[str]:
    """The MCP command OpenCode launches (type ``local`` + command ARRAY).

    The desktop bundle does ``const [cmd, ...args] = entry.command``, so
    ``command`` MUST be an array, and the entry ``type`` is ``local`` (anything
    that is not ``remote`` is spawned via ``StdioClientTransport``). We invoke
    the module with the venv interpreter so the desktop process's PATH is
    irrelevant.
    """
    return [sys.executable, "-m", "deepseek_eyes.mcp_server"]


def _xdg_config_dir() -> Path:
    """OpenCode (Bun runtime) reads its global config from the XDG config dir."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "opencode"
    return Path.home() / ".config" / "opencode"


def _find_host_config() -> Path:
    """Locate opencode.json(c): project-local first, then the XDG global config.

    The desktop app (Electron/Bun) reads ``~/.config/opencode/opencode.json`` —
    NOT ``platformdirs.user_config_dir``, which resolves to the unrelated
    ``AppData\\Local\\opencode\\opencode`` on Windows.
    """
    for name in ("opencode.json", "opencode.jsonc"):
        local = Path.cwd() / name
        if local.is_file():
            return local
    for name in ("opencode.json", "opencode.jsonc"):
        global_ = _xdg_config_dir() / name
        if global_.is_file():
            return global_
    # Nothing exists yet: create in the global config dir (not cwd) so the
    # desktop app actually picks it up.
    return _xdg_config_dir() / "opencode.json"


def _strip_jsonc(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments and trailing commas, string-aware.

    OpenCode config files are JSONC (comments + trailing commas), so a plain
    ``json.loads`` fails on them. This produces clean JSON without touching the
    contents of quoted strings.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    quote = ""
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                in_string = False
            i += 1
            continue

        if ch in ('"', "'"):
            in_string = True
            quote = ch
            out.append(ch)
            i += 1
            continue

        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue

        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue

        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1  # drop trailing comma
                continue

        out.append(ch)
        i += 1
    return "".join(out)


def load_host_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    # utf-8-sig handles the BOM that VS Code / some editors write into
    # opencode.json; a plain utf-8 decode chokes on it.
    text = path.read_text(encoding="utf-8-sig")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # OpenCode configs are JSONC; retry after stripping comments/trailing commas.
        try:
            data = json.loads(_strip_jsonc(text))
        except json.JSONDecodeError as exc:
            raise EyesError(
                ErrorCode.INTEGRATION_CORRUPTED, f"host config unreadable: {exc}"
            ) from exc
    if not isinstance(data, dict):
        raise EyesError(ErrorCode.INTEGRATION_CORRUPTED, "host config root must be an object")
    return data


@dataclass(frozen=True)
class IntegrationState:
    path: Path
    configured: bool
    # None when not configured or corrupted.
    entry: dict | None = None


def integration_state() -> IntegrationState:
    path = _find_host_config()
    try:
        data = load_host_config(path)
    except EyesError:
        return IntegrationState(path=path, configured=False)
    entry = (data.get("mcp") or {}).get(MCP_SERVER_KEY)
    return IntegrationState(
        path=path,
        configured=isinstance(entry, dict),
        entry=entry if isinstance(entry, dict) else None,
    )


def _write_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".eyes-backup"))
    text = json.dumps(data, indent=2, ensure_ascii=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _project_root() -> Path:
    """Project root; this module lives at ``<root>/src/deepseek_eyes/``."""
    return Path(__file__).resolve().parents[2]


def _plugin_path() -> Path:
    return _project_root() / "host" / "opencode" / "plugin.ts"


def _plugin_entry() -> tuple[str, dict[str, object]]:
    """The opencode.json ``plugin`` tuple ``[path, options]`` (V1 format).

    ``adapterCommand`` invokes the bridge module directly with the venv
    interpreter, so it never depends on a console script being on the desktop
    process's PATH.
    """
    return str(_plugin_path()), {
        "adapterCommand": [sys.executable, "-m", "deepseek_eyes.host_bridge"],
    }


def _find_plugin_index(plugins: list[Any]) -> int | None:
    path = str(_plugin_path())
    for i, entry in enumerate(plugins):
        if entry == path:
            return i
        if isinstance(entry, list) and entry and entry[0] == path:
            return i
    return None


def _upsert_plugin(data: dict) -> None:
    """Add or refresh the Eyes plugin tuple in the ``plugin`` array."""
    plugins = data.get("plugin")
    if not isinstance(plugins, list):
        plugins = []
        data["plugin"] = plugins
    path, options = _plugin_entry()
    idx = _find_plugin_index(plugins)
    if idx is None:
        plugins.append([path, options])
    else:
        plugins[idx] = [path, options]


def _remove_plugin(data: dict) -> None:
    plugins = data.get("plugin")
    if not isinstance(plugins, list):
        return
    path = str(_plugin_path())
    data["plugin"] = [
        e for e in plugins if e != path and not (isinstance(e, list) and e and e[0] == path)
    ]
    if not data["plugin"]:
        data.pop("plugin", None)


def apply_integration(command: list[str] | None = None) -> IntegrationState:
    """Add (or update) the Eyes MCP entry and plugin entry, keeping a backup."""
    cmd = command if command is not None else _default_mcp_command()
    path = _find_host_config()
    data = load_host_config(path)
    mcp = data.setdefault("mcp", {})
    mcp[MCP_SERVER_KEY] = {
        "type": "local",
        "command": cmd,
    }
    _upsert_plugin(data)
    _write_config(path, data)
    return IntegrationState(path=path, configured=True, entry=mcp[MCP_SERVER_KEY])


def remove_integration() -> IntegrationState:
    """Remove the Eyes MCP entry and plugin entry, preserving other settings."""
    path = _find_host_config()
    data = load_host_config(path)
    mcp = data.get("mcp")
    if isinstance(mcp, dict):
        mcp.pop(MCP_SERVER_KEY, None)
        if not mcp:
            data.pop("mcp", None)
    _remove_plugin(data)
    _write_config(path, data)
    return IntegrationState(path=path, configured=False)


def repair_integration(command: list[str] | None = None) -> IntegrationState:
    """Rewrite a corrupted host config to a clean Eyes-only config."""
    cmd = command if command is not None else _default_mcp_command()
    path = _find_host_config()
    # Keep whatever the user had, but if it does not parse, start over with the
    # Eyes entry so the host can launch again.
    try:
        data = load_host_config(path)
    except EyesError:
        data = {}
    mcp = data.setdefault("mcp", {})
    mcp[MCP_SERVER_KEY] = {"type": "local", "command": cmd}
    _upsert_plugin(data)
    _write_config(path, data)
    return IntegrationState(path=path, configured=True, entry=mcp[MCP_SERVER_KEY])
