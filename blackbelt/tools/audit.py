"""Visores de los logs de auditoría de BlackBelt."""

from __future__ import annotations

import argparse
import json
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from blackbelt.core import config as cfg
from blackbelt.core import executor, gatekeeper

console = Console()


def run(args: Sequence[str] | str | None = None) -> None:
    """Muestra el audit del Gatekeeper o el estructurado de planes."""
    raw_args = executor.args_to_str(args)
    parser = argparse.ArgumentParser(
        prog="blackbelt run audit",
        description="Consulta el audit del Gatekeeper o de planes.",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Consulta el registro estructurado del módulo plan.",
    )
    parser.add_argument(
        "limit",
        nargs="?",
        type=_positive_limit,
        default=30,
        help="Número de entradas recientes (por defecto: 30).",
    )
    parsed = parser.parse_args(raw_args.split())
    if parsed.plan:
        _show_plan_audit(cfg.PLAN_AUDIT_FILE, parsed.limit)
    else:
        _show_gatekeeper_audit(parsed.limit)


def _show_gatekeeper_audit(limit: int) -> None:
    lines = gatekeeper.read_log(lines=limit)
    if not lines:
        console.print("[dim]Log de auditoría vacío.[/]")
        console.print("[dim]Se llenará cuando uses acciones sensibles.[/]")
        console.print(f"[dim]Archivo: {gatekeeper.AUDIT_FILE}[/]")
        return

    table = Table(title=f"Últimas {len(lines)} entradas del audit log")
    table.add_column("Timestamp", style="dim")
    table.add_column("Riesgo", style="bold")
    table.add_column("Estado")
    table.add_column("Acción")

    for line in lines:
        parts = [part.strip() for part in line.split("|", 3)]
        if len(parts) != 4:
            continue
        timestamp, risk, status, action = parts
        risk_style = {
            "SAFE": "green",
            "WARN": "yellow",
            "CRITICAL": "red",
        }.get(risk, "white")
        status_style = "green" if status == "ALLOWED" else "red"
        table.add_row(
            Text(timestamp),
            Text(risk, style=risk_style),
            Text(status, style=status_style),
            Text(action),
        )
    console.print(table)


def _show_plan_audit(path: Path, limit: int) -> None:
    events, malformed_count = _read_jsonl(path, limit)
    if not events and malformed_count == 0:
        console.print("[dim]Audit de planes vacío.[/]")
        console.print(f"[dim]Archivo: {path}[/]")
        return

    table = Table(title=f"Últimas {len(events)} entradas del audit de planes")
    table.add_column("Fecha", style="dim")
    table.add_column("Acción")
    table.add_column("Plan")
    table.add_column("Resultado")
    table.add_column("Motor / modelo")
    table.add_column("Fuentes")
    table.add_column("Hashes")
    for event in events:
        sources = event.get("fuentes")
        source_count = len(sources) if isinstance(sources, list) else 0
        provider = event.get("proveedor")
        model = event.get("modelo")
        engine = "/".join(
            value for value in (provider, model) if isinstance(value, str)
        )
        hashes = ", ".join(
            f"{label}:{str(event[key])[:12]}"
            for label, key in (
                ("req", "hash_solicitud"),
                ("res", "hash_resultado"),
                ("plan", "hash_plan"),
            )
            if isinstance(event.get(key), str)
        )
        table.add_row(
            Text(str(event.get("fecha", "-"))),
            Text(str(event.get("accion", "-"))),
            Text(str(event.get("plan_id") or "-")),
            Text(str(event.get("resultado", "-"))),
            Text(engine or "-"),
            Text(str(source_count)),
            Text(hashes or "-"),
        )
    console.print(table)
    if malformed_count:
        console.print(
            f"[yellow]Se omitieron {malformed_count} entradas JSONL "
            "malformadas entre las últimas líneas consultadas.[/]"
        )
    console.print(f"[dim]Archivo: {path}[/]")


def _read_jsonl(path: Path, limit: int) -> tuple[list[dict[str, Any]], int]:
    if not path.is_file():
        return [], 0
    try:
        with path.open(encoding="utf-8") as stream:
            recent_lines = deque(stream, maxlen=limit)
    except (OSError, UnicodeDecodeError) as exc:
        console.print(
            f"[red]No se pudo leer el audit de planes ({type(exc).__name__}).[/]"
        )
        return [], 0

    events: list[dict[str, Any]] = []
    malformed_count = 0
    for line in recent_lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed_count += 1
            continue
        if not isinstance(event, dict):
            malformed_count += 1
            continue
        events.append(event)
    return events, malformed_count


def _positive_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("debe ser un número entero") from exc
    if limit < 1:
        raise argparse.ArgumentTypeError("debe ser mayor que cero")
    return limit
