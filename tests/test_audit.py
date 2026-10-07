"""Tests for the Gatekeeper and structured plan audit viewers."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from rich.console import Console

from blackbelt.core import config
from blackbelt.tools import audit


def test_audit_keeps_gatekeeper_view_as_default(
    monkeypatch,
) -> None:
    output = StringIO()
    monkeypatch.setattr(audit, "console", Console(file=output, width=120))
    monkeypatch.setattr(
        audit.gatekeeper,
        "read_log",
        lambda lines: ["2026-10-06 | SAFE | ALLOWED | leer estado"],
    )

    audit.run(["5"])

    assert "leer estado" in output.getvalue()
    assert "audit log" in output.getvalue()


def test_plan_audit_shows_safe_fields_and_respects_entry_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audit_path = tmp_path / "plan.jsonl"
    first_event = {
        "fecha": "2026-10-06T10:00:00+00:00",
        "accion": "plan_generation_cloud",
        "plan_id": "PLAN-001",
        "resultado": "ok",
        "proveedor": "ollama",
        "modelo": "gpt-oss:120b-cloud",
        "fuentes": [{"id": "F-001", "nota": "nota privada.md"}],
        "hash_solicitud": "a" * 64,
        "hash_resultado": "b" * 64,
        "contenido": "TEXTO-PRIVADO-NO-MOSTRAR",
    }
    second_event = {
        "fecha": "2026-10-06T11:00:00+00:00",
        "accion": "plan_approve",
        "plan_id": "PLAN-002",
        "resultado": "ok",
        "hash_plan": "c" * 64,
    }
    audit_path.write_text(
        "\n".join(map(json.dumps, (first_event, second_event))) + "\n",
        encoding="utf-8",
    )
    output = StringIO()
    monkeypatch.setattr(config, "PLAN_AUDIT_FILE", audit_path)
    monkeypatch.setattr(audit, "console", Console(file=output, width=200))

    audit.run(["--plan", "1"])

    rendered = output.getvalue()
    assert "PLAN-002" in rendered
    assert "PLAN-001" not in rendered
    assert "plan_approve" in rendered
    assert "TEXTO-PRIVADO-NO-MOSTRAR" not in rendered
    assert "nota privada.md" not in rendered
    assert "plan:c" + "c" * 10 in rendered


def test_plan_audit_warns_for_malformed_lines_without_echoing_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audit_path = tmp_path / "plan.jsonl"
    audit_path.write_text(
        '{"accion":"plan_approve","plan_id":"PLAN-001"}\n'
        "BROKEN-PRIVATE-AUDIT-LINE\n",
        encoding="utf-8",
    )
    output = StringIO()
    monkeypatch.setattr(config, "PLAN_AUDIT_FILE", audit_path)
    monkeypatch.setattr(audit, "console", Console(file=output, width=120))

    audit.run(["--plan", "2"])

    rendered = output.getvalue()
    assert "PLAN-001" in rendered
    assert "malformadas" in rendered
    assert "BROKEN-PRIVATE-AUDIT-LINE" not in rendered
