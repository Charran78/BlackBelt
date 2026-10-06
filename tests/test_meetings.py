"""Tests for the read-only Obsidian meeting preparation service."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from blackbelt.knowledge.meeting_prep import (
    MeetingPreparationError,
    MeetingPreparationService,
)
from blackbelt.tools import meetings


ALLOWED_FOLDERS = (
    "001 - PROYECTOS",
    "002 - TAREAS",
    "005 - CLIENTES",
    "013 - PROPUESTA _PRESUPUESTO",
    "019 - REUNIONES",
)


def _create_vault(root: Path) -> Path:
    for folder in (*ALLOWED_FOLDERS, "006 - CREDENCIALES", ".obsidian"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    return root


def _write_note(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_dossier_only_reads_allowlisted_notes_and_explicit_relations(
    tmp_path: Path,
) -> None:
    vault = _create_vault(tmp_path / "vault")
    client_path = _write_note(
        vault / "005 - CLIENTES" / "CL001 - Ana.md",
        """---
tipo: cliente
nombre: Ana García
estado: prospecto
proyectos_activos:
  - Web de bodas
proyectos_previos:
  - Catálogo anterior
---
# Ficha de Ana

## Resumen
- Necesita una web con reservas.

## Personas de contacto
- correo: ana@example.test
""",
    )
    project_path = _write_note(
        vault / "001 - PROYECTOS" / "Web de bodas.md",
        "# Web de bodas\n\nAlcance y propuesta.",
    )
    task_path = _write_note(
        vault / "002 - TAREAS" / "Tareas cliente.md",
        "---\ncliente: CL001\n---\n# Próximas acciones\n\n- Preparar demo\n",
    )
    meeting_path = _write_note(
        vault / "019 - REUNIONES" / "RE001 - CL001.md",
        '---\ncliente: "[[CL001 - Ana]]"\n---\n# Reunión\n\nRevisar la demo.\n',
    )
    proposal_path = _write_note(
        vault / "013 - PROPUESTA _PRESUPUESTO" / "PR001 - Ana.md",
        """---
tipo: propuesta
cliente: Ana García
proyecto: Web de bodas
estado: borrador
---
# Propuesta - Web de bodas

## Inversión
- Base imponible: 1200
- Total: 1452
""",
    )
    unrelated_path = _write_note(
        vault / "002 - TAREAS" / "Tarea no relacionada.md",
        "# Tarea\n\nAna podría ser un nombre mencionado, no una relación.",
    )
    credential_path = _write_note(
        vault / "006 - CREDENCIALES" / "privado.md",
        "---\ncliente: CL001\n---\nNo debe cargarse.",
    )
    outside_allowlist_path = _write_note(
        vault / "013 - PROPUESTAS ANTIGUAS" / "Propuesta antigua.md",
        "---\ncliente: CL001\n---\nNo debe cargarse.",
    )
    hidden_path = _write_note(
        vault / ".obsidian" / "privado.md",
        "---\ncliente: CL001\n---\nNo debe cargarse.",
    )
    before = {
        path: path.read_bytes()
        for path in (
            client_path,
            project_path,
            task_path,
            meeting_path,
            proposal_path,
            unrelated_path,
            credential_path,
            outside_allowlist_path,
            hidden_path,
        )
    }

    dossier = MeetingPreparationService(vault).prepare("CL001")

    assert dossier.client.path == client_path
    assert {note.path for note in dossier.related_notes} == {
        project_path,
        task_path,
        proposal_path,
        meeting_path,
    }
    assert {
        path: path.read_bytes()
        for path in before
    } == before


def test_client_can_be_resolved_by_exact_name_without_accent(
    tmp_path: Path,
) -> None:
    vault = _create_vault(tmp_path / "vault")
    client_path = _write_note(
        vault / "005 - CLIENTES" / "CL001 - Ana.md",
        "---\nnombre: Ana García\n---\n# Aura Bodas\n",
    )

    dossier = MeetingPreparationService(vault).prepare("ana garcia")

    assert dossier.client.path == client_path


def test_client_catalog_returns_only_display_fields_from_client_notes(
    tmp_path: Path,
) -> None:
    vault = _create_vault(tmp_path / "vault")
    client_path = _write_note(
        vault / "005 - CLIENTES" / "CL001 - Ana.md",
        """---
nombre: Ana García
estado: prospecto
correo: ana@example.test
---
# Aura Bodas
""",
    )
    _write_note(
        vault / "001 - PROYECTOS" / "Private project.md",
        "---\ncliente: CL001\n---\n",
    )
    _write_note(
        vault / "006 - CREDENCIALES" / "private.md",
        "---\nnombre: No mostrar\n---\n",
    )

    clients = MeetingPreparationService(vault).list_clients()

    assert len(clients) == 1
    assert clients[0].client_id == "CL001"
    assert clients[0].name == "Ana García"
    assert clients[0].status == "prospecto"
    assert clients[0].source == "005 - CLIENTES/CL001 - Ana.md"
    assert client_path.exists()


def test_client_resolution_rejects_missing_or_ambiguous_names(
    tmp_path: Path,
) -> None:
    vault = _create_vault(tmp_path / "vault")
    client_root = vault / "005 - CLIENTES"
    _write_note(client_root / "CL001 - Ana.md", "---\nnombre: Ana\n---\n")
    service = MeetingPreparationService(vault)

    with pytest.raises(MeetingPreparationError, match="No se encontró"):
        service.prepare("CL099")

    _write_note(client_root / "CL002 - Ana.md", "---\nnombre: Ana\n---\n")
    with pytest.raises(MeetingPreparationError, match="ambiguo"):
        service.prepare("Ana")


def test_service_requires_client_folder_and_rejects_empty_query(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "005 - CLIENTES").mkdir()
    service = MeetingPreparationService(vault)

    with pytest.raises(MeetingPreparationError, match="Indica un ID"):
        service.prepare("  ")


def test_cli_outputs_sources_and_does_not_print_contact_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = _create_vault(tmp_path / "vault")
    _write_note(
        vault / "005 - CLIENTES" / "CL001 - Ana.md",
        """---
nombre: Ana García
estado: prospecto
proyectos_activos:
  - Web de bodas
---
# Aura Bodas

## Resumen
- Preparar demo.

## Personas de contacto
- correo: ana@example.test
""",
    )
    output = StringIO()
    monkeypatch.setattr(meetings.cfg, "OBSIDIAN_VAULT", vault)
    monkeypatch.setattr(
        meetings,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )

    meetings.run(["prepare", "--client", "CL001"])

    rendered = output.getvalue()
    assert "Preparación de reunión" in rendered
    assert "Web de bodas" in rendered
    assert "ana@example.test" not in rendered
    assert "solo lectura" in rendered
