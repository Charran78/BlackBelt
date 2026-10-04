"""Launch the local SOMA or Ghost Writer web application."""

from __future__ import annotations

import argparse
import os
import webbrowser
from collections.abc import Sequence
from typing import Any

import uvicorn
from dotenv import load_dotenv
from rich.console import Console

from blackbelt.webapps import create_app


console = Console()
LOOPBACK_HOST = "127.0.0.1"


class _BrowserOpeningServer(uvicorn.Server):
    browser_url: str

    async def startup(self, sockets: Any = None) -> None:
        await super().startup(sockets=sockets)
        if self.started:
            webbrowser.open(self.browser_url)


def run(args: Sequence[str] | str | None = None) -> None:
    arguments = args.split() if isinstance(args, str) else list(args or [])
    parser = argparse.ArgumentParser(
        prog="blackbelt run apps",
        description="Abre aplicaciones locales de BlackBelt.",
    )
    parser.add_argument(
        "application",
        choices=("soma", "ghostwriter"),
        help="Aplicación web que se abrirá.",
    )
    parsed = parser.parse_args(arguments)

    load_dotenv()
    port = _port_from_environment()
    page_path = (
        "soma"
        if parsed.application == "soma"
        else "ghostwriter"
    )
    url = f"http://{LOOPBACK_HOST}:{port}/{page_path}/"
    application = create_app(default_page=parsed.application)
    server = _BrowserOpeningServer(
        uvicorn.Config(
            application,
            host=LOOPBACK_HOST,
            port=port,
            log_level="info",
            access_log=False,
        )
    )
    server.browser_url = url

    console.print(f"[green]Abriendo {parsed.application} en {url}[/]")
    console.print("[dim]El servidor se detiene con Ctrl+C.[/]")
    server.run()


def _port_from_environment() -> int:
    raw_value = os.getenv("BLACKBELT_WEBAPPS_PORT", "8765")
    try:
        port = int(raw_value)
    except ValueError as exc:
        raise ValueError("BLACKBELT_WEBAPPS_PORT debe ser un entero.") from exc
    if not 1024 <= port <= 65535:
        raise ValueError(
            "BLACKBELT_WEBAPPS_PORT debe estar entre 1024 y 65535."
        )
    return port
