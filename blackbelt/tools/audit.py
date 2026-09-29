"""Visor del log de auditoría del Gatekeeper."""

from rich.console import Console
from rich.table import Table

from blackbelt.core import executor
from blackbelt.core import gatekeeper

console = Console()


def run(args=None):
    """Muestra las últimas entradas del log de auditoría.
    Uso: blackbelt run audit [n]
    """
    args = executor.args_to_str(args)
    parts = args.split()
    n = int(parts[0]) if parts and parts[0].isdigit() else 30

    lines = gatekeeper.read_log(lines=n)

    if not lines:
        console.print("[dim]Log de auditoría vacío.[/]")
        console.print(f"[dim]Se llenará cuando uses acciones sensibles.[/]")
        console.print(f"[dim]Archivo: {gatekeeper.AUDIT_FILE}[/]")
        return

    table = Table(title=f"Últimas {len(lines)} entradas del audit log")
    table.add_column("Timestamp", style="dim")
    table.add_column("Riesgo", style="bold")
    table.add_column("Estado")
    table.add_column("Acción")

    for line in lines:
        parts = [p.strip() for p in line.split("|", 3)]
        if len(parts) != 4:
            continue
        ts, risk, status, action = parts

        risk_style = {
            "SAFE": "green",
            "WARN": "yellow",
            "CRITICAL": "red",
        }.get(risk, "white")

        status_style = "green" if status == "ALLOWED" else "red"

        table.add_row(
            ts,
            f"[{risk_style}]{risk}[/]",
            f"[{status_style}]{status}[/]",
            action,
        )

    console.print(table)
