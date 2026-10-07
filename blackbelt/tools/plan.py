"""CLI for preparing, validating, and approving traceable project plans."""

from __future__ import annotations

import argparse
import shlex
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from blackbelt.core import config as cfg
from blackbelt.knowledge.plan import (
    ValidationIssue,
    plan_has_actionable_content,
    render_plan_summary,
)
from blackbelt.knowledge.plan_service import (
    ApprovalResult,
    PlanService,
    PlanServiceError,
    PreparedPlan,
)

console = Console()


def run(args: Sequence[str] | str | None = None) -> None:
    """Run a plan workflow command."""
    arguments = shlex.split(args) if isinstance(args, str) else list(args or [])
    parser = _build_parser()
    parsed = parser.parse_args(arguments)
    service = PlanService(Path(cfg.OBSIDIAN_VAULT))

    try:
        if parsed.command == "prepare":
            _prepare(service, parsed)
        elif parsed.command == "validate":
            _validate(service, parsed)
        elif parsed.command == "approve":
            _approve(service, parsed)
        elif parsed.command == "revise":
            _revise(service, parsed)
        elif parsed.command == "show":
            _show(service, parsed)
        elif parsed.command == "list":
            _list(service, parsed)
    except PlanServiceError as exc:
        console.print(Text(str(exc), style="red"))
        raise SystemExit(exc.exit_code) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blackbelt run plan",
        description="Prepara, valida y aprueba planes de proyecto trazables.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser(
        "prepare",
        help="Genera un borrador local a partir de fuentes permitidas.",
    )
    prepare_parser.add_argument("--client", required=True)
    prepare_parser.add_argument("--project")
    prepare_parser.add_argument(
        "--proposal",
        action="append",
        default=[],
        help="ID de propuesta seleccionada; puede repetirse.",
    )
    prepare_parser.add_argument("--meeting", action="append", default=[])
    prepare_parser.add_argument(
        "--dossier",
        action="append",
        default=[],
        help="Nombre base del dossier seleccionado; puede repetirse.",
    )
    prepare_parser.add_argument("--questions-only", action="store_true")
    prepare_parser.add_argument(
        "--engine",
        choices=("local", "cloud"),
        default="local",
    )
    prepare_parser.add_argument("--template", action="append", default=[])
    prepare_parser.add_argument("--dry-run", action="store_true")
    prepare_parser.add_argument("--audit-reads", action="store_true")
    prepare_parser.set_defaults(handler=_prepare)

    validate_parser = subparsers.add_parser(
        "validate",
        help="Valida un borrador sin modificarlo.",
    )
    validate_parser.add_argument("draft")
    validate_parser.add_argument("--audit-reads", action="store_true")
    validate_parser.set_defaults(handler=_validate)

    approve_parser = subparsers.add_parser(
        "approve",
        help="Publica o actualiza un plan tras confirmación explícita.",
    )
    approve_parser.add_argument("draft")
    approve_parser.add_argument("--confirm", action="store_true")
    approve_parser.add_argument("--update", action="store_true")
    approve_parser.add_argument("--dry-run", action="store_true")
    approve_parser.add_argument("--audit-reads", action="store_true")
    approve_parser.set_defaults(handler=_approve)

    revise_parser = subparsers.add_parser(
        "revise",
        help="Crea en la carpeta de revisión una copia editable de un plan aprobado.",
    )
    revise_parser.add_argument("plan_id")
    revise_parser.set_defaults(handler=_revise)

    show_parser = subparsers.add_parser(
        "show",
        help="Muestra un plan aprobado.",
    )
    show_parser.add_argument("--plan", required=True)
    show_parser.add_argument("--estado", action="store_true")
    show_parser.add_argument("--tareas", action="store_true")
    show_parser.add_argument("--riesgos", action="store_true")
    show_parser.add_argument("--preguntas", action="store_true")
    show_parser.add_argument("--audit-reads", action="store_true")
    show_parser.set_defaults(handler=_show)

    list_parser = subparsers.add_parser(
        "list",
        help="Lista planes aprobados.",
    )
    list_parser.add_argument("--cliente")
    list_parser.add_argument(
        "--estado",
        choices=("borrador", "revisado", "aprobado", "archivado"),
    )
    list_parser.add_argument("--audit-reads", action="store_true")
    list_parser.set_defaults(handler=_list)
    return parser


def _prepare(service: PlanService, parsed: argparse.Namespace) -> None:
    if len(parsed.template) > 1:
        raise PlanServiceError(
            "El MVP admite una plantilla principal; la composición queda pospuesta.",
            2,
        )
    result = service.prepare(
        client=parsed.client,
        project=parsed.project,
        proposals=parsed.proposal or None,
        meetings=parsed.meeting or None,
        dossiers=parsed.dossier or None,
        questions_only=parsed.questions_only,
        engine=parsed.engine,
        template_id=parsed.template[0] if parsed.template else None,
        dry_run=parsed.dry_run,
        console=console,
    )
    if parsed.audit_reads:
        service.record_read(
            "plan_prepare_read",
            result="dry_run" if parsed.dry_run else "ok",
        )
    if isinstance(result, str):
        console.print(result, markup=False, highlight=False)
        return
    _render_prepared(result)


def _validate(service: PlanService, parsed: argparse.Namespace) -> None:
    plan, issues = service.validate_draft(parsed.draft)
    if parsed.audit_reads:
        service.record_read(
            "plan_validate",
            plan_id=_plan_id(plan),
            result=_issue_status(issues),
            item_count=len(issues),
        )
    _render_validation(issues)
    if any(issue.level == "error" for issue in issues):
        raise SystemExit(1)


def _approve(service: PlanService, parsed: argparse.Namespace) -> None:
    result = service.approve(
        parsed.draft,
        confirm=parsed.confirm,
        update=parsed.update,
        dry_run=parsed.dry_run,
    )
    if parsed.audit_reads and isinstance(result, ApprovalResult):
        service.record_read(
            "plan_approve_read",
            plan_id=result.plan_id,
            result="unchanged" if result.unchanged else "ok",
        )
    if isinstance(result, str):
        console.print(result, markup=False, highlight=False)
        return
    action = "ya estaba aprobado" if result.unchanged else "aprobado"
    if result.updated:
        action = "actualizado"
    console.print(
        Text(
            f"Plan {result.plan_id} {action}: "
            f"{result.destination.relative_to(service.vault).as_posix()}",
            style="green",
        )
    )


def _revise(service: PlanService, parsed: argparse.Namespace) -> None:
    result = service.revise(parsed.plan_id)
    _render_prepared(result)


def _show(service: PlanService, parsed: argparse.Namespace) -> None:
    path, plan = service.load_approved(parsed.plan)
    if parsed.audit_reads:
        service.record_read("plan_show", plan_id=parsed.plan)
    selected = any(
        (
            parsed.estado,
            parsed.tareas,
            parsed.riesgos,
            parsed.preguntas,
        )
    )
    if not selected:
        console.print(Markdown(render_plan_summary(plan)))
        console.print(f"Archivo: {path.relative_to(service.vault).as_posix()}")
        if not plan_has_actionable_content(plan):
            console.print(
                Text(
                    "Aviso: este plan está aprobado, pero no contiene objetivo "
                    "definido, preguntas ni elementos de trabajo.",
                    style="bold yellow",
                )
            )
        return

    console.print(
        Text(
            f"{plan['id']} | Cliente {plan['cliente']} | "
            "Proyecto "
            f"{plan.get('proyecto_nombre') or plan.get('proyecto') or 'sin proyecto'}",
            style="bold cyan",
        )
    )
    console.print(f"Archivo: {path.relative_to(service.vault).as_posix()}")
    console.print(f"Objetivo: {plan['objetivo']}")
    if not plan_has_actionable_content(plan):
        console.print(
            Text(
                "Aviso: este plan está aprobado, pero no contiene objetivo "
                "definido, preguntas ni elementos de trabajo.",
                style="bold yellow",
            )
        )
    if not selected or parsed.estado:
        console.print(f"Estado: {plan['estado']}")
    if not selected or parsed.tareas:
        _render_items("Tareas", plan.get("tareas", []), ("id", "titulo", "estado"))
    if not selected or parsed.riesgos:
        _render_items(
            "Riesgos",
            plan.get("riesgos", []),
            ("id", "descripcion", "probabilidad", "impacto"),
        )
    if not selected or parsed.preguntas:
        _render_items(
            "Preguntas abiertas",
            [
                question
                for question in plan.get("preguntas", [])
                if question.get("estado") == "abierta"
            ],
            ("id", "texto", "responsable"),
        )


def _list(service: PlanService, parsed: argparse.Namespace) -> None:
    plans = service.list_plans(client=parsed.cliente, state=parsed.estado)
    if parsed.audit_reads:
        service.record_read(
            "plan_list",
            result="ok",
            item_count=len(plans),
        )
    if not plans:
        console.print("[dim]No se encontraron planes aprobados.[/]")
        return
    table = Table(title="Planes aprobados")
    for column in ("Plan", "Cliente", "Proyecto", "Estado", "Actualizado"):
        table.add_column(column)
    for record in plans:
        table.add_row(
            str(record["plan_id"]),
            str(record["cliente"]),
            str(record.get("proyecto") or ""),
            str(record["estado"]),
            str(record["actualizado"]),
        )
    console.print(table)


def _render_prepared(prepared: PreparedPlan) -> None:
    console.print(
        Text(
            f"Borrador {prepared.plan_id} creado: {prepared.draft_path}",
            style="green",
        )
    )
    console.print(
        "Fuentes: " + ", ".join(prepared.source_paths),
        markup=False,
    )


def _render_validation(issues: list[ValidationIssue]) -> None:
    if not issues:
        console.print(Text("Plan válido; sin advertencias.", style="green"))
        return
    table = Table(title="Validación del plan")
    table.add_column("Resultado")
    table.add_column("Elemento")
    table.add_column("Detalle")
    for issue in issues:
        style = "red" if issue.level == "error" else "yellow"
        table.add_row(
            Text(issue.level, style=style),
            issue.path,
            issue.message,
        )
    console.print(table)


def _render_items(
    title: str,
    items: list[dict[str, Any]],
    fields: tuple[str, ...],
) -> None:
    console.print(Text(title, style="bold"))
    if not items:
        console.print("(Ninguno)")
        return
    for item in items:
        values = " | ".join(
            f"{field}: {item.get(field, '')}"
            for field in fields
        )
        console.print(values, markup=False)


def _plan_id(plan: dict[str, Any]) -> str | None:
    identifier = plan.get("id")
    return identifier if isinstance(identifier, str) else None


def _issue_status(issues: list[ValidationIssue]) -> str:
    if any(issue.level == "error" for issue in issues):
        return "error"
    if issues:
        return "aviso"
    return "ok"
