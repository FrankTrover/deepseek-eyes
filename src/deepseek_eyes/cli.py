"""Typer CLI — doctor, integrity, config export, and non-visual smoke test.

Out of MVP-A the CLI intentionally does not expose a generic ``observe`` command;
that path belongs to the MCP tools.
"""

from __future__ import annotations

import typer

from . import __version__

app = typer.Typer(name="deepseek-eyes", no_args_is_help=True)


@app.command()
def doctor() -> None:
    """Run non-visual health checks on the local environment."""
    import sys

    typer.echo(f"deepseek-eyes {__version__}")
    typer.echo(f"python {sys.version.split()[0]}")
    typer.echo("doctor: OK (MVP-A core has no external health dependencies)")


@app.command()
def config_export() -> None:
    """Export a redacted view of the current config (never secrets)."""
    import json
    import os

    base_url = os.environ.get("DEEPSEEK_EYES_BASE_URL", "")
    token = os.environ.get("DEEPSEEK_EYES_TOKEN", "")
    redacted = {
        "base_url": base_url,
        "token_configured": bool(token),
        "token_hint": (token[:4] + "…") if token else None,
    }
    typer.echo(json.dumps(redacted, indent=2))


@app.command()
def integrity() -> None:
    """Verify the installed package's contract modules import cleanly."""
    from .contracts import ObserveRequest, VisionObservation  # noqa: F401

    typer.echo("integrity: OK")


@app.command()
def smoke() -> None:
    """Non-visual smoke test of the Runtime wiring with a fake provider."""
    import asyncio

    from .runtime import Runtime

    async def _run() -> None:
        rt = Runtime(provider=_FakeProvider())
        caps = rt.capabilities()
        typer.echo(f"capabilities: {caps.model_dump_json()}")

    asyncio.run(_run())


class _FakeProvider:
    model = "mimo-v2.5"

    async def observe(self, request, media):
        from .contracts import VisionObservation

        return VisionObservation()


@app.command()
def control_center() -> None:
    """Launch the PySide6 Control Center."""
    from .control_center import run

    raise SystemExit(run())


@app.command()
def diagnostics() -> None:
    """Print a redacted diagnostics report (never secrets)."""
    import json

    from .diagnostics import assert_no_secrets, redacted_report

    report = redacted_report()
    assert_no_secrets(report)
    typer.echo(json.dumps(report, indent=2, ensure_ascii=False))


@app.command()
def adapter() -> None:
    """Run the host-adapter bridge (JSON-lines over stdio)."""
    from .host_bridge import serve

    raise SystemExit(serve())


if __name__ == "__main__":
    app()
