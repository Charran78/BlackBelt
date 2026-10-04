"""CLI adapter for local-first Gmail triage."""

from __future__ import annotations

import argparse
from dataclasses import replace
from typing import Sequence

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.text import Text

from blackbelt.mail.credentials import (
    EmailCredentialsError,
    load_credentials,
    store_credentials,
)
from blackbelt.mail.imap_client import MailFetchError
from blackbelt.mail.repository import EmailCache, EmailCacheError
from blackbelt.mail.service import EmailTriageService, TriageReport
from blackbelt.mail.settings import EmailSettings, EmailSettingsError


console = Console()


def run(args: Sequence[str] | str | None = None) -> None:
    load_dotenv(override=False)
    arguments = args.split() if isinstance(args, str) else list(args or [])
    parser = _argument_parser()
    if not arguments:
        parser.print_help()
        return
    parsed = parser.parse_args(arguments)
    try:
        if parsed.command == "configure":
            _configure()
        elif parsed.command == "scan":
            _scan(parsed.hours, parsed.limit, parsed.refresh)
        else:
            parser.print_help()
    except KeyboardInterrupt:
        console.print("\n[yellow]Operación interrumpida.[/]")


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blackbelt run email",
        description="Clasificación local de correo Gmail en modo solo lectura.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "configure",
        help="Guardar la dirección y contraseña de aplicación en el keyring.",
    )
    scan_parser = subparsers.add_parser(
        "scan",
        help="Analizar mensajes recientes con Ollama local.",
    )
    scan_parser.add_argument(
        "--hours",
        type=_positive_integer,
        default=None,
        help="Ventana reciente en horas (máximo 2160).",
    )
    scan_parser.add_argument(
        "--limit",
        type=_positive_integer,
        default=None,
        help="Máximo de mensajes (máximo 500).",
    )
    scan_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Volver a clasificar y actualizar la caché local.",
    )
    return parser


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Debe ser un entero.") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("Debe ser mayor que cero.")
    return parsed


def _configure() -> None:
    from rich.prompt import Prompt

    try:
        username = Prompt.ask("Dirección Gmail")
        app_password = Prompt.ask(
            "Contraseña de aplicación de Gmail",
            password=True,
        )
        store_credentials(username, app_password)
    except EmailCredentialsError as exc:
        console.print(f"[red]{exc}[/]")
        return
    except (EOFError, KeyboardInterrupt):
        console.print("[dim]Configuración cancelada.[/]")
        return
    console.print(
        "[green]Credenciales guardadas en el almacén seguro del sistema.[/]"
    )
    console.print(
        "[dim]Usa una contraseña de aplicación de Google, no tu contraseña "
        "habitual.[/]"
    )


def _scan(
    hours: int | None,
    limit: int | None,
    refresh: bool = False,
) -> None:
    try:
        settings = EmailSettings.from_environment()
        if hours is not None:
            if hours > 24 * 90:
                raise EmailSettingsError("--hours no puede superar 2160.")
            settings = replace(settings, hours=hours)
        if limit is not None:
            if limit > 500:
                raise EmailSettingsError("--limit no puede superar 500.")
            settings = replace(settings, message_limit=limit)
        credentials = load_credentials()
        cache = EmailCache(settings.cache_path)
        service = EmailTriageService(settings, cache)
        console.print(
            f"[bold]Analizando Gmail[/] ([cyan]{credentials.username}[/]), "
            f"últimas {settings.hours} horas, máximo "
            f"{settings.message_limit} mensajes."
        )
        report = service.scan(
            credentials.username,
            credentials.app_password,
            refresh=refresh,
        )
    except (
        EmailSettingsError,
        EmailCredentialsError,
        EmailCacheError,
        MailFetchError,
    ) as exc:
        console.print(f"[red]{exc}[/]")
        return
    _render_report(report)


def _render_report(report: TriageReport) -> None:
    if not report.items:
        console.print("[dim]No hay clasificaciones para mostrar.[/]")
    else:
        table = Table(title="Triaje de correo")
        table.add_column("Imp.", justify="right")
        table.add_column("Urgencia")
        table.add_column("Categoría")
        table.add_column("Riesgo")
        table.add_column("Conf.", justify="right")
        table.add_column("Control")
        table.add_column("Asunto")
        table.add_column("Remitente")
        for item in sorted(
            report.items,
            key=lambda entry: (
                entry.classification.importance,
                entry.classification.confidence,
            ),
            reverse=True,
        ):
            classification = item.classification
            table.add_row(
                str(classification.importance),
                classification.urgency,
                classification.category,
                classification.security_risk,
                str(classification.confidence),
                (
                    "REVISAR"
                    if item.requires_review
                    else "AVISO"
                    if item.warnings
                    else ""
                ),
                _safe_cell(item.message.subject, 52),
                _safe_cell(item.message.sender, 36),
            )
        console.print(table)

        for item in report.items:
            classification = item.classification
            if item.requires_review:
                console.print(
                    f"\n[yellow]Revisión manual[/] "
                    f"{_safe_cell(item.message.subject, 70)}: "
                    f"{'; '.join(item.review_reasons)}. "
                    "No se presenta la acción sugerida como fiable."
                )
                continue
            for warning in item.warnings:
                console.print(
                    f"\n[dim]Aviso[/] "
                    f"{_safe_cell(item.message.subject, 70)}: {warning}."
                )
            if (
                classification.requires_action
                or classification.requires_reply
                or classification.security_risk in {"medio", "alto"}
            ):
                console.print(
                    f"\n[bold]Acción sugerida[/] "
                    f"{_safe_cell(item.message.subject, 70)}"
                )
                console.print(
                    Text.from_markup(
                        "[cyan]" + _safe_cell(classification.summary, 140) + "[/]"
                    )
                )
                console.print(
                    Text.from_markup(
                        "[yellow]"
                        + _safe_cell(classification.suggested_action, 100)
                        + "[/]"
                    )
                )

    console.print(
        f"\nMensajes: {len(report.items)} | Caché: {report.cache_hits} | "
        f"Newsletter verificada: {report.newsletter_shortcuts} | "
        f"Errores de clasificación: {len(report.failures)}"
    )
    for warning in report.warnings:
        console.print(f"[yellow]Aviso: {warning}[/]")
    if report.failures:
        for index, failure in enumerate(report.failures[:5], 1):
            console.print(f"[yellow]Clasificación {index}: {failure}[/]")
        if len(report.failures) > 5:
            console.print(
                f"[yellow]Y {len(report.failures) - 5} errores más.[/]"
            )
        console.print(
            "[yellow]Algunos mensajes no se clasificaron; no se han guardado "
            "resultados incompletos.[/]"
        )
    console.print(
        "[dim]La evaluación de phishing es orientativa y no sustituye "
        "la verificación del remitente o los enlaces.[/]"
    )


def _safe_cell(value: str, max_length: int) -> str:
    clipped = value[:max_length]
    if len(value) > max_length:
        clipped += "..."
    return clipped.replace("[", r"\[")
