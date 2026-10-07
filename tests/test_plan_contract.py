"""Acceptance tests for the public ``blackbelt run plan`` CLI contract."""

from __future__ import annotations

from argparse import Namespace
from io import StringIO
from pathlib import Path

from click.testing import Result
from rich.console import Console
from typer.testing import CliRunner

from blackbelt import main
from blackbelt.core import registry
from blackbelt.tools import plan as plan_tool

runner = CliRunner()


def _run_plan(*arguments: str) -> Result:
    return runner.invoke(
        main.app,
        ["run", "plan", "--", *arguments],
        color=False,
    )


def test_plan_is_registered_as_a_blackbelt_tool() -> None:
    assert registry.TOOLS["plan"]["module"] == "blackbelt.tools.plan"


def test_plan_help_lists_the_contract_commands() -> None:
    result = _run_plan("--help")

    assert result.exit_code == 0, result.output
    for command in ("prepare", "validate", "approve", "revise", "show", "list"):
        assert command in result.output


def test_prepare_help_exposes_the_agreed_inputs_and_safety_options() -> None:
    result = _run_plan("prepare", "--help")

    assert result.exit_code == 0, result.output
    for option in (
        "--client",
        "--project",
        "--proposal",
        "--meeting",
        "--questions-only",
        "--engine",
        "--template",
        "--dry-run",
        "--audit-reads",
    ):
        assert option in result.output


def test_prepare_without_client_returns_configuration_exit_code() -> None:
    result = _run_plan("prepare")

    assert result.exit_code == 2
    assert "--client" in result.output


def test_validate_missing_draft_returns_validation_exit_code(
    tmp_path: Path,
) -> None:
    missing_draft = tmp_path / "PLAN-001.md"

    result = _run_plan("validate", str(missing_draft))

    assert result.exit_code == 1
    assert "PLAN-001.md" in result.output


def test_approve_requires_explicit_confirmation(tmp_path: Path) -> None:
    draft = tmp_path / "PLAN-001.md"
    draft.write_text("---\nplan: {}\n---\n", encoding="utf-8")

    result = _run_plan("approve", str(draft))

    assert result.exit_code == 3
    assert "--confirm" in result.output


def test_approve_help_exposes_explicit_update_and_dry_run() -> None:
    result = _run_plan("approve", "--help")

    assert result.exit_code == 0, result.output
    assert "--confirm" in result.output
    assert "--update" in result.output
    assert "--dry-run" in result.output


def test_revise_help_exposes_approved_plan_identifier() -> None:
    result = _run_plan("revise", "--help")

    assert result.exit_code == 0, result.output
    assert "plan_id" in result.output


def test_read_commands_offer_opt_in_audit() -> None:
    for command in ("validate", "show", "list"):
        result = _run_plan(command, "--help")

        assert result.exit_code == 0, result.output
        assert "--audit-reads" in result.output


def test_show_renders_readable_summary_and_flags_empty_approved_plan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = {
        "id": "PLAN-001",
        "cliente": "CL001",
        "proyecto": None,
        "estado": "aprobado",
        "objetivo": "Pendiente de definir",
        "alcance": {"incluye": [], "excluye": []},
        "criterios_aceptacion": [],
        "entregables": [],
        "tareas": [],
        "dependencias": [],
        "riesgos": [],
        "restricciones": [],
        "supuestos": [],
        "preguntas": [],
        "fuentes": [],
    }
    approved_path = tmp_path / "PLAN-001.md"

    class StubService:
        vault = tmp_path

        @staticmethod
        def load_approved(_plan_id: str) -> tuple[Path, dict]:
            return approved_path, plan

    output = StringIO()
    monkeypatch.setattr(plan_tool, "console", Console(file=output, width=120))

    plan_tool._show(
        StubService(),
        Namespace(
            plan="PLAN-001",
            audit_reads=False,
            estado=False,
            tareas=False,
            riesgos=False,
            preguntas=False,
        ),
    )

    rendered = output.getvalue()
    assert "Objetivo" in rendered
    assert "Borrador incompleto" in rendered
    assert "no contiene objetivo definido" in rendered
