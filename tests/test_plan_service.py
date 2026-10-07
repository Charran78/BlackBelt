"""Service-level contract tests for plan validation and persistence."""

from __future__ import annotations

import hashlib
import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from blackbelt.knowledge import plan_service
from blackbelt.knowledge.plan import (
    PlanFormatError,
    parse_plan_markdown,
    render_plan_markdown,
    validate_plan,
)
from blackbelt.knowledge.plan_service import (
    PlanService,
    PlanServiceError,
    _bounded_task_candidates,
    _merge_proposal_extractions,
    _merge_questions,
    _normalize_proposal_model_collections,
    _normalize_questions,
)
from blackbelt.knowledge.proposal_extractor import ProposalExtraction

_SOURCE_TEXT = "## Alcance\n\nCrear una web de reservas.\n"
_SOURCE_HASH = hashlib.sha256(_SOURCE_TEXT.encode("utf-8")).hexdigest()


def _valid_plan() -> dict[str, Any]:
    return {
        "id": "PLAN-001",
        "cliente": "CL001",
        "proyecto": "PROY001",
        "estado": "borrador",
        "objetivo": "Preparar una web de reservas.",
        "alcance": {"incluye": ["reservas"], "excluye": []},
        "criterios_aceptacion": ["La reserva puede confirmarse."],
        "creado": "2026-10-06",
        "actualizado": "2026-10-06",
        "plantillas_aplicadas": [{"id": "PLT-001", "version": 1}],
        "generacion": {
            "motor": "local",
            "proveedor": "ollama",
            "modelo": "test-model",
            "fecha": "2026-10-06T10:00:00+00:00",
        },
        "entregables": [
            {
                "id": "E-001",
                "titulo": "Flujo de reservas",
                "descripcion": "Formulario de reserva.",
                "criterio_aceptacion": "Una reserva queda registrada.",
                "estado": "pendiente",
            }
        ],
        "tareas": [
            {
                "id": "T-001",
                "titulo": "Definir formulario",
                "descripcion": "Acordar los campos del formulario.",
                "entregable": "E-001",
                "fase": "descubrimiento",
                "estado": "pendiente",
                "estimacion": {
                    "min_h": 2,
                    "prob_h": 4,
                    "max_h": 8,
                    "base": "Estimación inicial.",
                },
                "estimado_real_h": None,
                "responsable": "equipo",
                "criterio_aceptacion": "Los campos están aprobados.",
                "origen": "inferido_ia",
                "fuentes": ["F-001"],
            }
        ],
        "dependencias": [],
        "riesgos": [],
        "restricciones": [],
        "supuestos": [],
        "preguntas": [],
        "fuentes": [
            {
                "id": "F-001",
                "tipo": "documental",
                "nota": "001 - PROYECTOS/PROY001 - Web.md",
                "seccion": "Alcance",
                "char_start": 0,
                "char_end": len(_SOURCE_TEXT),
                "hash_nota": _SOURCE_HASH,
                "capturado": "2026-10-06T10:00:00+00:00",
            }
        ],
    }


def _model_response() -> str:
    return json.dumps(
        {
            "objetivo": "Preparar una web de reservas.",
            "alcance": {
                "incluye": ["Flujo de reservas"],
                "excluye": ["Aplicación móvil"],
            },
            "criterios_aceptacion": ["Una reserva queda registrada."],
            "entregables": [
                {
                    "titulo": "Flujo de reservas",
                    "descripcion": "Formulario de reserva.",
                    "criterio_aceptacion": "Una reserva queda registrada.",
                }
            ],
            "tareas": [
                {
                    "titulo": "Definir formulario",
                    "descripcion": "Acordar los campos.",
                    "entregable_ref": 1,
                    "fase": "descubrimiento",
                    "estimacion": {
                        "min_h": 2,
                        "prob_h": 4,
                        "max_h": 8,
                        "base": "Descomposición inicial.",
                    },
                    "responsable": "equipo",
                    "criterio_aceptacion": "Los campos están aprobados.",
                    "fuentes": ["F-001"],
                    "depende_de": [],
                }
            ],
            "dependencias": [],
            "riesgos": [],
            "restricciones": [],
            "supuestos": [],
            "preguntas": [
                {
                    "texto": "¿Quién valida el contenido?",
                    "bloquea_refs": [1],
                }
            ],
            "narrativa": "Borrador sujeto a revisión humana.",
        }
    )


def _write_note(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _create_vault(root: Path) -> Path:
    client = _write_note(
        root / "005 - CLIENTES" / "CL001 - Ana.md",
        """---
nombre: Ana
proyectos_activos:
  - PROY001 - Web
---
# Ana
Cliente de prueba.
""",
    )
    del client
    _write_note(
        root / "001 - PROYECTOS" / "PROY001 - Web.md",
        """---
cliente: CL001
---
# Alcance

Crear una web de reservas.
""",
    )
    _write_note(
        root / "013 - PROPUESTA _PRESUPUESTO" / "PR001 - Web.md",
        """---
cliente: CL001
proyecto: PROY001
---
# Propuesta

Implementar la web de reservas.
""",
    )
    _write_note(
        root / "019 - REUNIONES" / "RE001 - CL001.md",
        """---
cliente: CL001
proyecto: PROY001
---
# Reunión

Revisar el formulario de reservas.
""",
    )
    _write_note(
        root / "001 - PROYECTOS" / "PROY002 - Privado.md",
        """---
cliente: CL001
privado: true
---
# Privado

CONTENIDO-PRIVADO-NO-ENVIAR
""",
    )
    _write_note(
        root / "006 - CREDENCIALES" / "secretos.md",
        "---\ncliente: CL001\n---\nCREDENCIAL-NO-LEER",
    )
    return root


def _service(
    tmp_path: Path,
    model_call=None,
) -> PlanService:
    vault = _create_vault(tmp_path / "vault")
    return PlanService(
        vault=vault,
        drafts_dir=tmp_path / "home" / ".blackbelt" / "plans" / "drafts",
        audit_file=tmp_path / "home" / ".blackbelt" / "audit" / "plan.jsonl",
        cloud_review_file=tmp_path / "cloud-review.yaml",
        model_call=model_call or (lambda _system, _user, _cloud: _model_response()),
    )


def test_proposal_generation_normalizes_malformed_optional_collections() -> None:
    generated, warnings = _normalize_proposal_model_collections(
        {
            "entregables": "un entregable sin estructura",
            "restricciones": "ninguna",
        }
    )

    assert generated["entregables"] == []
    assert generated["restricciones"] == []
    assert len(warnings) == 2
    assert all("en vez de una lista" in warning for warning in warnings)


def test_client_source_catalog_includes_only_explicitly_related_notes(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    sources = service.list_client_sources("CL001")

    assert [option["id"] for option in sources["projects"]] == ["PROY001"]
    assert [option["id"] for option in sources["proposals"]] == ["PR001"]
    assert [option["id"] for option in sources["meetings"]] == ["RE001"]
    assert all(
        "CONTENIDO-PRIVADO" not in option["relative_path"]
        for options in sources.values()
        for option in options
    )


def test_plan_can_combine_multiple_proposals_meetings_and_client_dossiers(
    tmp_path: Path,
) -> None:
    captured_prompts: list[str] = []
    service = _service(
        tmp_path,
        model_call=lambda _system, user, _cloud: (
            captured_prompts.append(user) or _model_response()
        ),
    )
    _write_note(
        service.vault / "013 - PROPUESTA _PRESUPUESTO" / "PR002 - Web.md",
        """---
cliente: CL001
proyecto: PROY001
---
# Resumen
Segunda propuesta de prueba.
""",
    )
    _write_note(
        service.vault / "020 - DOSSIERES" / "CL001_2026-10-07_01.md",
        """---
cliente: CL001
proyecto: PROY001
---
# Seguimiento
Contexto de la reunión de seguimiento.
""",
    )
    _write_note(
        service.vault / "020 - DOSSIERES" / "CL009_2026-10-07_01.md",
        "---\ncliente: CL009\n---\nCONTEXTO-DE-OTRO-CLIENTE",
    )

    catalog = service.list_client_sources("CL001")
    result = service.prepare(
        client="CL001",
        project="PROY001",
        proposals=("PR001", "PR002"),
        meetings=("RE001",),
        dossiers=("CL001_2026-10-07_01",),
    )

    assert [source["id"] for source in catalog["dossiers"]] == [
        "CL001_2026-10-07_01",
    ]
    assert not isinstance(result, str)
    assert "Segunda propuesta de prueba." in captured_prompts[0]
    assert "Contexto de la reunión de seguimiento." in captured_prompts[0]
    assert "CONTEXTO-DE-OTRO-CLIENTE" not in captured_prompts[0]
    assert any(path.startswith("013 - PROPUESTA _PRESUPUESTO/PR001") for path in result.source_paths)
    assert any(path.startswith("013 - PROPUESTA _PRESUPUESTO/PR002") for path in result.source_paths)
    assert "020 - DOSSIERES/CL001_2026-10-07_01.md" in result.source_paths


def test_project_scope_hides_and_rejects_other_client_sources(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_note(
        service.vault / "013 - PROPUESTA _PRESUPUESTO" / "PR002 - Otro.md",
        "---\ncliente: CL001\nproyecto: PROY009\n---\nOtra propuesta.",
    )
    _write_note(
        service.vault / "019 - REUNIONES" / "RE002 - CL001.md",
        "---\ncliente: CL001\nproyecto: PROY009\n---\nOtra reunión.",
    )
    _write_note(
        service.vault / "020 - DOSSIERES" / "CL001_2026-10-07_02.md",
        "---\ncliente: CL001\nproyecto: PROY009\n---\nOtro dossier.",
    )

    scoped_sources = service.list_client_sources("CL001", "PROY001")

    assert [item["id"] for item in scoped_sources["proposals"]] == ["PR001"]
    assert [item["id"] for item in scoped_sources["meetings"]] == ["RE001"]
    assert scoped_sources["dossiers"] == []
    with pytest.raises(PlanServiceError, match="PR002"):
        service.prepare(client="CL001", project="PROY001", proposals=("PR002",))
    with pytest.raises(PlanServiceError, match="reunión relacionada"):
        service.prepare(client="CL001", project="PROY001", meetings=("RE002",))
    with pytest.raises(PlanServiceError, match="dossier único vinculado"):
        service.prepare(
            client="CL001",
            project="PROY001",
            dossiers=("CL001_2026-10-07_02",),
        )


def test_selected_project_defaults_to_its_linked_source_set(
    tmp_path: Path,
) -> None:
    captured_prompts: list[str] = []
    service = _service(
        tmp_path,
        model_call=lambda _system, user, _cloud: (
            captured_prompts.append(user) or _model_response()
        ),
    )
    _write_note(
        service.vault / "020 - DOSSIERES" / "CL001_2026-10-07_01.md",
        "---\ncliente: CL001\nproyecto: PROY001\n---\n"
        "Contexto de la reunión de seguimiento.",
    )

    result = service.prepare(client="CL001", project="PROY001")

    assert not isinstance(result, str)
    plan, _ = parse_plan_markdown(
        result.draft_path.read_text(encoding="utf-8")
    )
    assert plan["proyecto"] == "PROY001"
    assert {
        source["nota"]
        for source in plan["fuentes"]
    } >= {
        "001 - PROYECTOS/PROY001 - Web.md",
        "013 - PROPUESTA _PRESUPUESTO/PR001 - Web.md",
        "019 - REUNIONES/RE001 - CL001.md",
        "020 - DOSSIERES/CL001_2026-10-07_01.md",
    }
    assert "Revisar el formulario de reservas." in captured_prompts[0]
    assert "Contexto de la reunión de seguimiento." in captured_prompts[0]


def test_project_plan_can_override_automatic_linked_sources(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    result = service.prepare(
        client="CL001",
        project="PROY001",
        proposals=(),
        meetings=(),
        dossiers=(),
    )

    assert not isinstance(result, str)
    plan, _ = parse_plan_markdown(
        result.draft_path.read_text(encoding="utf-8")
    )
    assert plan["proyecto"] == "PROY001"
    assert {
        source["nota"] for source in plan["fuentes"]
    } == {
        "005 - CLIENTES/CL001 - Ana.md",
        "001 - PROYECTOS/PROY001 - Web.md",
    }


def test_plan_rejects_duplicate_sources_and_dossiers_from_other_clients(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_note(
        service.vault / "020 - DOSSIERES" / "CL009_2026-10-07_01.md",
        "---\ncliente: CL009\n---\nContenido ajeno.",
    )

    with pytest.raises(PlanServiceError, match="No repitas una propuesta"):
        service.prepare(client="CL001", proposals=("PR001", "PR001"))
    with pytest.raises(PlanServiceError, match="No repitas el mismo ID"):
        service.prepare(client="CL001", meetings=("RE001", "RE001"))
    with pytest.raises(PlanServiceError, match="No repitas el mismo dossier"):
        service.prepare(
            client="CL001",
            dossiers=("CL001_2026-10-07_01", "CL001_2026-10-07_01"),
        )
    with pytest.raises(PlanServiceError, match="dossier único vinculado"):
        service.prepare(client="CL001", dossiers=("CL009_2026-10-07_01",))


def test_proposal_merge_surfaces_conflicting_dates_and_preserves_citations() -> None:
    first = ProposalExtraction(
        deliverables=[
            {
                "titulo": "Lanzamiento",
                "criterio_aceptacion": "Sitio publicado.",
                "fecha_estimada": "2026-11-01",
                "origen_fecha": "externo",
                "fuentes": ["F-001"],
            }
        ],
        phases=[
            {
                "fase": "Desarrollo",
                "actividad": "Implementación",
                "fecha_inicio": "2026-10-01",
                "fecha_fin": "2026-10-20",
                "origen_fecha": "externo",
                "fuentes": ["F-002"],
            }
        ],
    )
    second = ProposalExtraction(
        deliverables=[
            {
                "titulo": "Lanzamiento",
                "criterio_aceptacion": "Sitio publicado.",
                "fecha_estimada": "2026-11-15",
                "origen_fecha": "externo",
                "fuentes": ["F-003"],
            }
        ],
        phases=[
            {
                "fase": "Desarrollo",
                "actividad": "Implementación",
                "fecha_inicio": "2026-10-05",
                "fecha_fin": "2026-10-20",
                "origen_fecha": "externo",
                "fuentes": ["F-004"],
            }
        ],
    )

    merged, warnings = _merge_proposal_extractions([first, second])

    assert merged is not None
    assert len(merged.deliverables) == len(merged.phases) == 1
    assert "fecha_estimada" not in merged.deliverables[0]
    assert merged.deliverables[0]["fuentes"] == ["F-001", "F-003"]
    assert "fecha_inicio" not in merged.phases[0]
    assert merged.phases[0]["fecha_fin"] == "2026-10-20"
    assert merged.phases[0]["fuentes"] == ["F-002", "F-004"]
    assert len(warnings) == 2
    assert any("entregables coincidentes" in warning for warning in warnings)
    assert any("fases coincidentes" in warning for warning in warnings)


def test_plan_validation_detects_changed_selected_dossier(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    dossier_path = _write_note(
        service.vault / "020 - DOSSIERES" / "CL001_2026-10-07_01.md",
        "---\ncliente: CL001\nproyecto: PROY001\n---\n# Seguimiento\nRevisar pagos.",
    )

    result = service.prepare(
        client="CL001",
        dossiers=("CL001_2026-10-07_01",),
    )
    assert not isinstance(result, str)
    dossier_path.write_text(
        dossier_path.read_text(encoding="utf-8") + "\nCambio posterior.\n",
        encoding="utf-8",
    )

    _, issues = service.validate_draft(result.draft_path)

    assert any(
        "fuente cambió desde la preparación" in issue.message
        for issue in issues
    )


def test_prepare_rejects_project_not_related_to_selected_client(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_note(
        service.vault / "001 - PROYECTOS" / "PROY009 - Otro cliente.md",
        "---\ncliente: CL009\n---\nProyecto ajeno.",
    )

    with pytest.raises(PlanServiceError, match="PROY009"):
        service.prepare(client="CL001", project="PROY009")


def test_archived_draft_can_be_restored_without_changing_its_content(
    tmp_path: Path,
) -> None:
    vault = _create_vault(tmp_path / "vault")
    service = PlanService(
        vault=vault,
        audit_file=tmp_path / "audit" / "plan.jsonl",
    )
    draft_path = vault / "022 - PLANES_BORRADOR" / "PLAN-001.md"
    draft_content = render_plan_markdown(_valid_plan(), "Nota para retomar.")
    _write_note(draft_path, draft_content)

    with pytest.raises(PlanServiceError, match="confirmación"):
        service.archive_draft("PLAN-001", confirm=False)

    archived_path = service.archive_draft("PLAN-001", confirm=True)
    archived = service.list_archived_drafts()

    assert archived_path == vault / "023 - PLANES_CANCELADOS" / "PLAN-001.md"
    assert not draft_path.exists()
    assert archived[0]["plan_id"] == "PLAN-001"
    assert archived[0]["relative_path"] == (
        "023 - PLANES_CANCELADOS/PLAN-001.md"
    )

    restored_path = service.restore_archived_draft(
        "PLAN-001",
        confirm=True,
    )

    assert restored_path == draft_path
    assert not archived_path.exists()
    assert draft_path.read_text(encoding="utf-8") == draft_content
    audit_events = [
        json.loads(line)
        for line in service.audit_file.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["accion"] for event in audit_events] == [
        "plan_archive",
        "plan_restore",
    ]


def test_schema_accepts_valid_plan_and_markdown_roundtrips() -> None:
    plan = _valid_plan()
    content = render_plan_markdown(plan, "Resumen narrativo.")

    parsed, narrative = parse_plan_markdown(content)

    assert parsed == plan
    assert "Resumen narrativo." in narrative
    assert "# Plan PLAN-001" in narrative
    assert "## Tareas" in narrative
    assert "## Trazabilidad" in narrative
    assert _SOURCE_HASH not in narrative
    assert validate_plan(parsed) == []


def test_schema_rejects_duplicate_yaml_keys() -> None:
    content = "---\nplan: {}\nplan: {}\n---\n"

    with pytest.raises(PlanFormatError, match="duplicada"):
        parse_plan_markdown(content)


def test_validation_rejects_bad_estimates_missing_sources_and_bad_references() -> None:
    plan = _valid_plan()
    plan["tareas"][0]["estimacion"] = {
        "min_h": 5,
        "prob_h": 4,
        "max_h": 8,
    }
    plan["tareas"][0]["fuentes"] = ["F-999"]
    plan["tareas"][0]["entregable"] = "E-999"

    errors = [issue.message for issue in validate_plan(plan)]

    assert any("0 < min_h" in message for message in errors)
    assert any("no existe" in message for message in errors)


def test_validation_rejects_dependency_cycles() -> None:
    plan = _valid_plan()
    second_task = dict(plan["tareas"][0], id="T-002")
    plan["tareas"].append(second_task)
    plan["dependencias"] = [
        {
            "id": "D-001",
            "desde": "T-001",
            "hacia": "T-002",
            "tipo": "fin_a_inicio",
            "rigidez": "dura",
            "motivo": "Primero configurar.",
        },
        {
            "id": "D-002",
            "desde": "T-002",
            "hacia": "T-001",
            "tipo": "fin_a_inicio",
            "rigidez": "dura",
            "motivo": "Después volver.",
        },
    ]

    assert any(
        "ciclo" in issue.message
        for issue in validate_plan(plan)
    )


def test_validation_rejects_empty_plan_shell() -> None:
    plan = _valid_plan()
    plan["objetivo"] = "Pendiente de definir"
    plan["alcance"] = {"incluye": [], "excluye": []}
    plan["criterios_aceptacion"] = []
    for field in (
        "entregables",
        "tareas",
        "dependencias",
        "riesgos",
        "restricciones",
        "supuestos",
        "preguntas",
    ):
        plan[field] = []

    issues = validate_plan(plan)

    assert any(
        issue.path == "plan"
        and "contenido accionable" in issue.message
        for issue in issues
    )


def test_prepare_generates_local_draft_and_excludes_private_sources(
    tmp_path: Path,
) -> None:
    captured: list[str] = []

    def fake_model(system: str, user: str, cloud: bool) -> str:
        assert cloud is False
        captured.extend((system, user))
        return _model_response()

    service = _service(tmp_path, fake_model)
    result = service.prepare(
        client="CL001",
        project="PROY001",
        proposal="PR001",
        meetings=["RE001"],
    )

    assert not isinstance(result, str)
    assert result.plan_id == "PLAN-001"
    assert result.draft_path.is_relative_to(service.drafts_dir)
    assert not result.draft_path.is_relative_to(service.vault)
    assert len(result.source_paths) == len(set(result.source_paths))
    draft_text = result.draft_path.read_text(encoding="utf-8")
    assert "CONTENIDO-PRIVADO-NO-ENVIAR" not in draft_text
    assert "CREDENCIAL-NO-LEER" not in draft_text
    assert all(
        secret not in "\n".join(captured)
        for secret in ("CONTENIDO-PRIVADO-NO-ENVIAR", "CREDENCIAL-NO-LEER")
    )
    plan, _ = parse_plan_markdown(draft_text)
    assert plan["cliente"] == "CL001"
    assert plan["proyecto"] == "PROY001"
    assert plan["plantillas_aplicadas"] == [{"id": "PLT-001", "version": 1}]
    assert validate_plan(plan) == []
    project_source = next(
        source
        for source in plan["fuentes"]
        if source["nota"] == "001 - PROYECTOS/PROY001 - Web.md"
    )
    project_text = (
        service.vault / project_source["nota"]
    ).read_text(encoding="utf-8")
    assert project_source["seccion"] == "Alcance"
    assert project_source["char_start"] > 0
    assert (
        project_text[
            project_source["char_start"]:project_source["char_end"]
        ].strip()
        == "Crear una web de reservas."
    )


def test_default_draft_flow_uses_review_folder_and_moves_on_approval(
    tmp_path: Path,
) -> None:
    vault = _create_vault(tmp_path / "vault")
    service = PlanService(
        vault=vault,
        audit_file=tmp_path / "audit" / "plan.jsonl",
        cloud_review_file=tmp_path / "cloud-review.yaml",
        model_call=lambda _system, _user, _cloud: _model_response(),
    )

    prepared = service.prepare(
        client="CL001",
        project="PROY001",
        proposal="PR001",
    )

    assert not isinstance(prepared, str)
    review_dir = vault / "022 - PLANES_BORRADOR"
    assert prepared.draft_path.parent == review_dir
    assert prepared.draft_path.exists()

    preview = service.approve(
        prepared.draft_path,
        confirm=True,
        dry_run=True,
    )
    assert isinstance(preview, str)
    assert prepared.draft_path.exists()
    assert not (vault / "021 - PLANES_APROBADOS").exists()

    approved = service.approve(prepared.draft_path, confirm=True)
    repeated = service.approve(prepared.draft_path, confirm=True)

    assert approved.destination.parent == vault / "021 - PLANES_APROBADOS"
    assert approved.destination.is_file()
    assert not prepared.draft_path.exists()
    assert repeated.unchanged is True

    no_op_revision = service.revise(prepared.plan_id)
    unchanged = service.approve(no_op_revision.draft_path, confirm=True)
    assert unchanged.unchanged is True
    assert not no_op_revision.draft_path.exists()

    revision = service.revise(prepared.plan_id)
    plan, narrative = parse_plan_markdown(
        revision.draft_path.read_text(encoding="utf-8")
    )
    plan["objetivo"] = "Objetivo revisado por una persona."
    revision.draft_path.write_text(
        render_plan_markdown(plan, narrative),
        encoding="utf-8",
    )
    preview = service.approve(
        revision.draft_path,
        confirm=True,
        update=True,
        dry_run=True,
    )
    assert isinstance(preview, str)
    assert "Objetivo revisado por una persona." in preview

    updated = service.approve(
        revision.draft_path,
        confirm=True,
        update=True,
    )
    assert updated.updated is True
    assert not revision.draft_path.exists()
    approved_plan, _ = parse_plan_markdown(
        approved.destination.read_text(encoding="utf-8")
    )
    assert approved_plan["objetivo"] == "Objetivo revisado por una persona."


def test_prepare_uses_deterministic_proposal_facts_as_plan_baseline(
    tmp_path: Path,
) -> None:
    prompts: list[str] = []

    def fake_model(system: str, user: str, _cloud: bool) -> str:
        prompts.extend((system, user))
        response = json.loads(_model_response())
        response["restricciones"] = "ninguna"
        return json.dumps(response)

    service = _service(tmp_path, fake_model)
    proposal_path = (
        service.vault
        / "013 - PROPUESTA _PRESUPUESTO"
        / "PR001 - Web.md"
    )
    proposal_path.write_text(
        """---
cliente: CL001
proyecto: PROY001
moneda: EUR
---
# Propuesta
## Resumen
- Necesidad del cliente: Crear una web de ventas.
## Alcance
### Incluye
- Catálogo y pagos.
### No incluye
- Aplicación móvil.
## Entregables
| Entregable | Criterio de aceptacion | Fecha estimada |
| --- | --- | --- |
| Web MVP | Compra completada correctamente | 05/11/2026 |
## Plan de trabajo
| Fase | Actividad | Duracion o fecha |
| --- | --- | --- |
| MVP | Implementar catálogo y pagos | 05/11/2026 |
## Inversion
| Concepto | Cantidad | Precio unitario | Subtotal |
| --- | ---: | ---: | ---: |
| Web MVP | 1 | 350 | 350 |
- Base imponible: 350
- Total: 350
## Supuestos y dependencias
- El cliente facilita el catálogo.
## Riesgos y cambios de alcance
- Retraso al entregar el catálogo.
- Mitigación: validar por hitos.
## Validez y condiciones
- Confirmar proveedor de pagos.
""",
        encoding="utf-8",
    )

    prepared = service.prepare(
        client="CL001",
        project="PROY001",
        proposal="PR001",
    )

    assert not isinstance(prepared, str)
    plan, narrative = parse_plan_markdown(
        prepared.draft_path.read_text(encoding="utf-8")
    )
    assert prepared.warnings == (
        (
            "Se descartaron restricciones sugeridas: el modelo devolvió "
            "str en vez de una lista."
        ),
    )
    assert "Avisos de generación:" in narrative
    assert plan["objetivo"] == "Crear una web de ventas."
    assert plan["alcance"] == {
        "incluye": ["Catálogo y pagos."],
        "excluye": ["Aplicación móvil."],
    }
    assert plan["entregables"][0]["titulo"] == "Web MVP"
    assert plan["entregables"][0]["fecha_estimada"] == "2026-11-05"
    assert plan["entregables"][0]["fuentes"]
    assert plan["fases_propuestas"][0]["fase"] == "MVP"
    assert plan["inversion"]["partidas"][0]["subtotal"] == 350
    assert plan["inversion"]["fuentes"]
    draft_text = prepared.draft_path.read_text(encoding="utf-8")
    assert "## Fases propuestas" in draft_text
    assert "## Inversión propuesta" in draft_text
    assert "350" in draft_text
    assert plan["riesgos"][0]["probabilidad"] == "sin_evaluar"
    assert plan["riesgos"][0]["origen"] == "externo"
    assert plan["supuestos"][0]["origen"] == "externo"
    assert plan["preguntas"][0]["texto"] == "Confirmar proveedor de pagos."
    assert "Hechos extraídos" in prompts[1]
    assert "no derives horas de precios" in prompts[1].lower()
    assert validate_plan(plan) == []


def test_prepare_fills_missing_task_fields_without_inventing_values(
    tmp_path: Path,
) -> None:
    response = json.loads(_model_response())
    response["tareas"][0].pop("fase")
    response["tareas"][0].pop("criterio_aceptacion")
    response["tareas"][0].pop("responsable")
    service = _service(
        tmp_path,
        lambda _system, _user, _cloud: json.dumps(response),
    )

    prepared = service.prepare(
        client="CL001",
        project="PROY001",
        proposal="PR001",
    )

    assert not isinstance(prepared, str)
    plan, _ = parse_plan_markdown(
        prepared.draft_path.read_text(encoding="utf-8")
    )
    task = plan["tareas"][0]
    assert task["fase"] == "Pendiente de asignar"
    assert task["responsable"] == "Pendiente de asignar"
    assert task["criterio_aceptacion"] == plan["entregables"][0][
        "criterio_aceptacion"
    ]
    assert validate_plan(plan) == []


def test_prepare_rejects_tasks_outside_proposal_scope(
    tmp_path: Path,
) -> None:
    response = json.loads(_model_response())
    valid_task = response["tareas"][0]
    valid_task.update(
        {
            "titulo": "Implementar catálogo y pagos",
            "descripcion": "Configurar catálogo y pagos para la tienda web.",
            "criterio_aceptacion": "La compra se completa correctamente.",
        }
    )
    excluded_task = {
        **valid_task,
        "titulo": "Crear aplicación móvil",
        "descripcion": "Desarrollar la aplicación móvil para compras.",
        "criterio_aceptacion": "La aplicación móvil queda publicada.",
    }
    response["tareas"] = [valid_task, excluded_task]
    service = _service(
        tmp_path,
        lambda _system, _user, _cloud: json.dumps(response),
    )
    proposal_path = (
        service.vault
        / "013 - PROPUESTA _PRESUPUESTO"
        / "PR001 - Web.md"
    )
    proposal_path.write_text(
        """---
cliente: CL001
proyecto: PROY001
---
# Propuesta
## Resumen
- Necesidad del cliente: Crear una tienda web.
## Alcance
### Incluye
- Catálogo online y pagos.
### No incluye
- Aplicación móvil.
## Entregables
| Entregable | Criterio de aceptacion | Fecha estimada |
| --- | --- | --- |
| Web MVP | Compra completada correctamente | 05/11/2026 |
## Plan de trabajo
| Fase | Actividad | Duracion o fecha |
| --- | --- | --- |
| MVP | Implementar catálogo y pagos | 05/11/2026 |
## Validez y condiciones
- Condiciones que deben revisarse antes de enviar:
  - Confirmar proveedor de pagos.
""",
        encoding="utf-8",
    )

    prepared = service.prepare(
        client="CL001",
        project="PROY001",
        proposal="PR001",
    )

    assert not isinstance(prepared, str)
    plan, _ = parse_plan_markdown(
        prepared.draft_path.read_text(encoding="utf-8")
    )
    assert [task["titulo"] for task in plan["tareas"]] == [
        "Implementar catálogo y pagos"
    ]
    assert [question["texto"] for question in plan["preguntas"]] == [
        "Confirmar proveedor de pagos."
    ]
    assert validate_plan(plan) == []


def test_prepare_dry_run_does_not_call_model_or_write_draft(tmp_path: Path) -> None:
    def fail_if_called(_system: str, _user: str, _cloud: bool) -> str:
        pytest.fail("dry-run no debe invocar el modelo")

    service = _service(tmp_path, fail_if_called)

    preview = service.prepare(
        client="CL001",
        project="PROY001",
        proposal="PR001",
        dry_run=True,
    )

    assert isinstance(preview, str)
    assert "Solicitud exacta" in preview
    assert "Implementar la web de reservas." in preview
    assert not service.drafts_dir.exists()


def test_questions_only_draft_contains_no_work_breakdown(
    tmp_path: Path,
) -> None:
    captured_prompts: list[str] = []

    def fake_model(system: str, user: str, _cloud: bool) -> str:
        captured_prompts.extend((system, user))
        return json.dumps(
            {
                "preguntas": [
                    "¿Quién aprueba el diseño?",
                    {"texto": "¿Qué contenido falta?"},
                ]
            }
        )

    service = _service(tmp_path, fake_model)

    prepared = service.prepare(
        client="CL001",
        project="PROY001",
        proposal="PR001",
        questions_only=True,
    )

    assert not isinstance(prepared, str)
    plan, _ = parse_plan_markdown(
        prepared.draft_path.read_text(encoding="utf-8")
    )
    assert plan["tareas"] == []
    assert plan["entregables"] == []
    assert plan["dependencias"] == []
    assert plan["preguntas"]
    assert plan["preguntas"][0]["texto"] == "¿Quién aprueba el diseño?"
    assert "no generes tareas" in captured_prompts[1].lower()
    assert "plantilla" not in captured_prompts[1].lower()
    assert validate_plan(plan) == []


def test_questions_only_refuses_to_persist_when_model_returns_no_questions(
    tmp_path: Path,
) -> None:
    service = _service(
        tmp_path,
        lambda _system, _user, _cloud: json.dumps({"preguntas": []}),
    )

    with pytest.raises(PlanServiceError, match="no se creó un borrador vacío"):
        service.prepare(
            client="CL001",
            project="PROY001",
            proposal="PR001",
            questions_only=True,
        )

    assert not service.drafts_dir.exists()


def test_questions_only_uses_explicit_proposal_conditions_without_model_questions(
    tmp_path: Path,
) -> None:
    service = _service(
        tmp_path,
        lambda _system, _user, _cloud: json.dumps({"preguntas": []}),
    )
    proposal_path = (
        service.vault
        / "013 - PROPUESTA _PRESUPUESTO"
        / "PR001 - Web.md"
    )
    proposal_path.write_text(
        """---
cliente: CL001
proyecto: PROY001
---
# Propuesta
## Validez y condiciones
- Valida hasta: 30/11/2026
- Condiciones que deben revisarse antes de enviar:
  - Confirmar el proveedor de hosting.
  - Definir qué incluye el mantenimiento mensual.
""",
        encoding="utf-8",
    )

    prepared = service.prepare(
        client="CL001",
        project="PROY001",
        proposal="PR001",
        questions_only=True,
    )

    assert not isinstance(prepared, str)
    plan, _ = parse_plan_markdown(
        prepared.draft_path.read_text(encoding="utf-8")
    )
    assert [item["texto"] for item in plan["preguntas"]] == [
        "Confirmar el proveedor de hosting.",
        "Definir qué incluye el mantenimiento mensual.",
    ]
    assert plan["tareas"] == []
    assert plan["entregables"] == []
    assert validate_plan(plan) == []


def test_merge_questions_discards_rewording_but_keeps_new_questions() -> None:
    explicit = [
        {"texto": "Unificar nombre del proyecto."},
        {"texto": "Confirmar viabilidad de Bizum."},
        {
            "texto": (
                "Aclarar forma de pago: primer pago, recurrencia y método."
            )
        },
        {
            "texto": (
                "Añadir propiedad intelectual y confidencialidad al acuerdo."
            )
        },
        {"texto": "Confirmar si el IVA aplicable es finalmente el 21%."},
    ]
    generated = [
        {"texto": "¿Qué nombre se acuerda para el proyecto Aura & Sueños?"},
        {"texto": "¿Cómo se comprobará la viabilidad de la pasarela Bizum?"},
        {
            "texto": (
                "¿Cómo asegurar transparencia en la forma de pago mensual?"
            )
        },
        {
            "texto": (
                "¿Quién pagará las mensualidades de 40 euros y qué opciones "
                "hay?"
            )
        },
        {
            "texto": (
                "¿Qué medidas protegen confidencialidad y propiedad "
                "intelectual de la web?"
            )
        },
        {
            "texto": (
                "¿Qué método asegurará la cobertura del IVA en este proyecto?"
            )
        },
        {
            "texto": (
                "¿Qué se especificará para configurar la pasarela de pagos?"
            )
        },
        {
            "texto": (
                "¿Qué se especificará para configurar las redes sociales?"
            )
        },
        {"texto": "¿Qué campaña de email marketing se preparará?"},
        {"texto": "¿Cuál es el color favorito del cliente?"},
        {"texto": "¿Quién será responsable de las actualizaciones del servidor?"},
    ]

    merged = _merge_questions(
        explicit,
        generated,
        known_topics=["actualizaciones del servidor"],
        duplicate_topics=[
            "conexión de pasarela de pagos",
            "conexión de redes sociales",
        ],
        excluded_topics=["Campañas de marketing", "Aplicación móvil"],
    )

    assert [question["texto"] for question in merged] == [
        "Unificar nombre del proyecto.",
        "Confirmar viabilidad de Bizum.",
        "Aclarar forma de pago: primer pago, recurrencia y método.",
        "Añadir propiedad intelectual y confidencialidad al acuerdo.",
        "Confirmar si el IVA aplicable es finalmente el 21%.",
        "¿Quién será responsable de las actualizaciones del servidor?",
    ]


def test_merge_questions_caps_additional_model_questions() -> None:
    generated = [
        {"texto": f"Pregunta genuinamente nueva número {index} sobre logística"}
        for index in range(1, 7)
    ]

    merged = _merge_questions([], generated)

    assert len(merged) == 4


def test_generated_tasks_are_deduplicated_and_capped() -> None:
    tasks = [{"titulo": f"Tarea {index}"} for index in range(1, 15)]
    tasks[1] = {"titulo": "Tarea 1"}
    tasks[2]["depende_de"] = [1, 2, 99]

    bounded = _bounded_task_candidates(tasks)

    assert len(bounded) == 12
    assert [task["titulo"] for task in bounded] == [
        "Tarea 1",
        *[f"Tarea {index}" for index in range(3, 14)],
    ]
    assert bounded[1]["depende_de"] == [1]


def test_validation_rejects_sensitive_source_paths(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    prepared = service.prepare(
        client="CL001",
        project="PROY001",
        proposal="PR001",
    )
    assert not isinstance(prepared, str)
    plan, narrative = parse_plan_markdown(
        prepared.draft_path.read_text(encoding="utf-8")
    )
    plan["fuentes"][0]["nota"] = "006 - CREDENCIALES/secretos.md"
    prepared.draft_path.write_text(
        render_plan_markdown(plan, narrative),
        encoding="utf-8",
    )

    _, issues = service.validate_draft(prepared.draft_path)

    assert any(
        issue.level == "error" and "carpetas permitidas" in issue.message
        for issue in issues
    )


def test_approval_is_idempotent_and_updates_only_explicitly(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    prepared = service.prepare(
        client="CL001",
        project="PROY001",
        proposal="PR001",
    )
    assert not isinstance(prepared, str)

    preview = service.approve(
        prepared.draft_path,
        confirm=True,
        dry_run=True,
    )
    assert isinstance(preview, str)
    assert not (service.vault / "021 - PLANES_APROBADOS").exists()

    first = service.approve(prepared.draft_path, confirm=True)
    second = service.approve(prepared.draft_path, confirm=True)

    assert first.destination == second.destination
    assert first.plan_hash == second.plan_hash
    assert second.unchanged is True
    assert first.destination.exists()
    assert len(list(first.destination.parent.glob("PLAN-*.md"))) == 1
    assert service.audit_file.read_text(encoding="utf-8").count("\n") == 1

    plan, narrative = parse_plan_markdown(
        prepared.draft_path.read_text(encoding="utf-8")
    )
    plan["objetivo"] = "Objetivo revisado por la persona."
    prepared.draft_path.write_text(
        render_plan_markdown(plan, narrative),
        encoding="utf-8",
    )
    with pytest.raises(PlanServiceError, match="--update"):
        service.approve(prepared.draft_path, confirm=True)

    preview = service.approve(
        prepared.draft_path,
        confirm=True,
        update=True,
        dry_run=True,
    )
    assert isinstance(preview, str)
    assert "Objetivo revisado" in preview
    assert "Objetivo revisado" not in first.destination.read_text(encoding="utf-8")

    updated = service.approve(
        prepared.draft_path,
        confirm=True,
        update=True,
    )
    assert updated.updated is True
    assert "Objetivo revisado" in updated.destination.read_text(encoding="utf-8")
    audit_events = [
        json.loads(line)
        for line in service.audit_file.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["accion"] for event in audit_events] == [
        "plan_approve",
        "plan_approve",
    ]


def test_approval_blocks_if_a_source_changed_after_prepare(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    prepared = service.prepare(
        client="CL001",
        project="PROY001",
        proposal="PR001",
    )
    assert not isinstance(prepared, str)
    source = service.vault / "001 - PROYECTOS" / "PROY001 - Web.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nCambio.\n", encoding="utf-8")

    with pytest.raises(PlanServiceError, match="cambió"):
        service.approve(prepared.draft_path, confirm=True)

    assert not (service.vault / "021 - PLANES_APROBADOS").exists()


def test_cloud_is_fail_closed_without_documented_provider_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plan_service.cfg, "PLAN_CLOUD_MODEL", "gpt-oss:120b-cloud")
    service = _service(tmp_path)

    with pytest.raises(PlanServiceError, match="revisión inicial"):
        service.prepare(
            client="CL001",
            project="PROY001",
            proposal="PR001",
            engine="cloud",
        )


def test_cloud_cancel_audits_attempt_without_payload_or_request_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plan_service.cfg, "PLAN_CLOUD_MODEL", "gpt-oss:120b-cloud")
    service = _service(tmp_path)
    service.cloud_review_file.write_text(
        yaml.safe_dump(
            {
                "proveedor": "ollama",
                "modelo": "gpt-oss:120b-cloud",
                "revisado": True,
                "fecha_revision": "2026-10-06",
                "url_terminos": "https://ollama.com/terms",
                "region": "desconocida",
                "retencion": "desconocida",
                "entrenamiento": "verificado",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        plan_service.Confirm,
        "ask",
        lambda *_args, **_kwargs: False,
    )

    with pytest.raises(PlanServiceError, match="cancelada"):
        service.prepare(
            client="CL001",
            project="PROY001",
            proposal="PR001",
            engine="cloud",
            console=plan_service.Console(width=120),
        )

    event = json.loads(service.audit_file.read_text(encoding="utf-8"))
    assert event["payload_enviado"] is False
    assert "hash_solicitud" not in event
    assert "hash_resultado" not in event
    assert "contenido" not in event


def test_cloud_success_previews_request_and_audits_only_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plan_service.cfg, "PLAN_CLOUD_MODEL", "gpt-oss:120b-cloud")
    captured_calls: list[bool] = []

    def fake_model(_system: str, _user: str, cloud: bool) -> str:
        captured_calls.append(cloud)
        return _model_response()

    service = _service(tmp_path, fake_model)
    service.cloud_review_file.write_text(
        yaml.safe_dump(
            {
                "proveedor": "ollama",
                "modelo": "gpt-oss:120b-cloud",
                "revisado": True,
                "fecha_revision": "2026-10-06",
                "url_terminos": "https://ollama.com/terms",
                "region": "desconocida",
                "retencion": "desconocida",
                "entrenamiento": "verificado",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        plan_service.Confirm,
        "ask",
        lambda *_args, **_kwargs: True,
    )
    preview_output = StringIO()

    result = service.prepare(
        client="CL001",
        project="PROY001",
        proposal="PR001",
        engine="cloud",
        console=plan_service.Console(file=preview_output, width=10_000),
    )

    assert not isinstance(result, str)
    assert captured_calls == [True]
    assert "Solicitud exacta" in preview_output.getvalue()
    assert "Implementar la web de reservas." in preview_output.getvalue()
    event = json.loads(service.audit_file.read_text(encoding="utf-8"))
    assert event["resultado"] == "ok"
    assert len(event["hash_solicitud"]) == 64
    assert len(event["hash_resultado"]) == 64
    assert "contenido" not in event
    assert "Implementar la web de reservas." not in json.dumps(event)


def test_ollama_model_call_reads_typed_chat_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        message=SimpleNamespace(content='{"preguntas": []}'),
        done_reason="stop",
    )
    captured_options: dict[str, Any] = {}

    def fake_chat(**kwargs: Any) -> SimpleNamespace:
        captured_options.update(kwargs["options"])
        return response

    monkeypatch.setattr(
        plan_service.ollama,
        "chat",
        fake_chat,
    )

    content = plan_service._ollama_model_call(
        "Instrucciones del sistema.",
        "Solicitud de prueba.",
        cloud=False,
    )

    assert content == '{"preguntas": []}'
    assert captured_options["num_ctx"] == plan_service.cfg.PLAN_NUM_CTX
    assert captured_options["num_predict"] == plan_service.cfg.PLAN_NUM_PREDICT


def test_ollama_model_call_reports_empty_typed_response_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        message=SimpleNamespace(content="  "),
        done_reason="length",
    )
    monkeypatch.setattr(
        plan_service.ollama,
        "chat",
        lambda **_kwargs: response,
    )

    with pytest.raises(PlanServiceError, match="motivo de finalización: length"):
        plan_service._ollama_model_call(
            "Instrucciones del sistema.",
            "Solicitud de prueba.",
            cloud=False,
        )


def test_question_normalization_accepts_strings_and_preserves_objects() -> None:
    questions = _normalize_questions(
        [
            "¿Quién aprueba el diseño?",
            {"texto": "¿Qué contenido falta?", "responsable": "cliente"},
        ]
    )

    assert questions == [
        {"texto": "¿Quién aprueba el diseño?"},
        {"texto": "¿Qué contenido falta?", "responsable": "cliente"},
    ]


def test_question_normalization_rejects_empty_or_unexpected_items() -> None:
    with pytest.raises(PlanServiceError, match="pregunta 1"):
        _normalize_questions(["   "])

    with pytest.raises(PlanServiceError, match="pregunta 1"):
        _normalize_questions([None])
