"""Read-only CLI for preparing a meeting dossier from the Obsidian vault."""

from __future__ import annotations

import argparse
import shlex
from collections.abc import Sequence
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from blackbelt.core import config as cfg
from blackbelt.knowledge.meeting_prep import (
    MeetingDossier,
    MeetingPreparationError,
    MeetingPreparationService,
    client_context_markdown,
)


console = Console()
_CLIENT_SUMMARY_FIELDS = (
    ("estado", "Estado"),
    ("ultimo_contacto", "Último contacto"),
    ("proximo_contacto", "Próximo contacto"),
    ("proyectos_activos", "Proyectos activos"),
    ("proyectos_previos", "Proyectos previos"),
)


def run(args: Sequence[str] | str | None = None) -> None:
    """Run the meeting preparation CLI without modifying vault files."""
    arguments = shlex.split(args) if isinstance(args, str) else list(args or [])
    parser = argparse.ArgumentParser(
        prog="blackbelt run meetings",
        description="Prepara un dossier de reunión desde notas permitidas.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser(
        "prepare",
        help="Reúne la ficha del cliente y sus notas relacionadas.",
    )
    prepare_parser.add_argument(
        "--client",
        required=True,
        help="ID o nombre exacto de la ficha en 005 - CLIENTES.",
    )
    parsed = parser.parse_args(arguments)

    service = MeetingPreparationService(Path(cfg.OBSIDIAN_VAULT))
    try:
        dossier = service.prepare(parsed.client)
    except MeetingPreparationError as exc:
        console.print(Text(f"No se pudo preparar el dossier: {exc}", style="red"))
        return
    _render_dossier(dossier, service)


def _render_dossier(
    dossier: MeetingDossier,
    service: MeetingPreparationService,
) -> None:
    client = dossier.client
    console.print(
        Panel(
            Text(
                f"Cliente: {client.path.stem}\n"
                f"Fuente: {service.relative_path(client.path)}\n"
                "Modo: solo lectura; sin IA ni escritura",
            ),
            title="Preparación de reunión",
            border_style="cyan",
        )
    )

    summary_lines = [
        f"{label}: {_format_metadata(client.metadata.get(field_name))}"
        for field_name, label in _CLIENT_SUMMARY_FIELDS
        if client.metadata.get(field_name) not in (None, "", [])
    ]
    _print_section("Ficha", "\n".join(summary_lines))
    _print_section("Contexto del cliente", client_context_markdown(client))

    if dossier.related_notes:
        console.print(Text("Notas relacionadas", style="bold cyan"))
        for note in dossier.related_notes:
            console.print(
                Text(f"Fuente: {service.relative_path(note.path)}", style="dim")
            )
            content = client_context_markdown(note)
            console.print(content or "(La nota no tiene contexto textual.)", markup=False)
            console.print()
    else:
        console.print("No se encontraron notas relacionadas por ID o enlace explícito.")

    console.print(
        Text(
            "Revisa las fuentes antes de la reunión; el dossier no infiere "
            "relaciones ni confirma datos.",
            style="dim",
        )
    )
    for warning in dossier.warnings:
        console.print(Text(f"Aviso: {warning}", style="yellow"))


def _print_section(title: str, content: str) -> None:
    console.print(Text(title, style="bold cyan"))
    console.print(content or "(Sin información en la ficha.)", markup=False)
    console.print()


def _format_metadata(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)
