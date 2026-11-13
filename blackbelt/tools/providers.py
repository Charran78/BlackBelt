"""Configure credentials for supported hosted model providers."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from rich.console import Console
from rich.prompt import Prompt

from blackbelt.providers.credentials import (
    ProviderCredentialsError,
    delete_api_key,
    store_api_key,
)

console = Console()
_PROVIDERS = ("groq", "openrouter")


def run(args: Sequence[str] | str | None = None) -> None:
    """Save or remove a provider key through the system credential manager."""
    arguments = args.split() if isinstance(args, str) else list(args or [])
    parser = _argument_parser()
    if not arguments:
        parser.print_help()
        return
    parsed = parser.parse_args(arguments)
    try:
        if parsed.command == "configure":
            api_key = Prompt.ask(
                f"Clave API de {parsed.provider}",
                password=True,
            )
            store_api_key(parsed.provider, api_key)
            console.print(
                f"[green]Clave de {parsed.provider} guardada en el almacén "
                "seguro del sistema.[/]"
            )
        elif parsed.command == "remove":
            delete_api_key(parsed.provider)
            console.print(f"[green]Clave de {parsed.provider} eliminada.[/]")
    except ProviderCredentialsError as exc:
        console.print(f"[red]{exc}[/]")
    except (EOFError, KeyboardInterrupt):
        console.print("[dim]Configuración cancelada.[/]")


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blackbelt run providers",
        description="Gestiona credenciales LLM sin guardarlas en archivos.",
    )
    subparsers = parser.add_subparsers(dest="command")
    for command in ("configure", "remove"):
        subparser = subparsers.add_parser(
            command,
            help=(
                "Guardar una clave API"
                if command == "configure"
                else "Eliminar una clave API"
            ),
        )
        subparser.add_argument("provider", choices=_PROVIDERS)
    return parser
