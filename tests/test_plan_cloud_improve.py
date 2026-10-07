"""Privacy and review-flow tests for opt-in Cloud plan improvements."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from blackbelt import webapps
from blackbelt.knowledge import plan_service
from blackbelt.knowledge.plan import parse_plan_markdown, render_plan_markdown
from blackbelt.knowledge.plan_service import PlanService, PlanServiceError
from blackbelt.knowledge.provenance import parse_markdown_source

_PROPOSAL_TEXT = (
    "# Plan de trabajo\n\n"
    "Diseñar el flujo de reservas y configurar el proveedor de pagos.\n\n"
    "Contacto: Ana Gómez\n"
    "Email: ana.gomez@example.test\n"
    "Importe: 1.200 EUR\n"
    "API_KEY=synthetic_secret_value\n"
    "GitHub: ghp_abcdefghijklmnopqrstuvwxyz012345\n"
    "-----BEGIN PRIVATE KEY-----\nsynthetic-private-key\n"
    "-----END PRIVATE KEY-----\n"
)


def _create_service(root: Path) -> tuple[PlanService, Path]:
    vault = root / "vault"
    proposal_path = vault / "013 - PROPUESTA _PRESUPUESTO" / "PR001 - Aura Web.md"
    draft_path = vault / "022 - PLANES_BORRADOR" / "PLAN-001.md"
    proposal_path.parent.mkdir(parents=True)
    draft_path.parent.mkdir(parents=True)
    proposal_path.write_text(_PROPOSAL_TEXT, encoding="utf-8")

    source = parse_markdown_source(
        proposal_path.relative_to(vault).as_posix(),
        _PROPOSAL_TEXT,
    ).chunks[0]
    source_hash = hashlib.sha256(_PROPOSAL_TEXT.encode("utf-8")).hexdigest()
    plan: dict[str, Any] = {
        "id": "PLAN-001",
        "cliente": "CL001",
        "proyecto": "PROY001",
        "proyecto_nombre": "Aura Web",
        "estado": "borrador",
        "objetivo": "Preparar la plataforma Aura Web.",
        "alcance": {"incluye": ["Reservas y pagos"], "excluye": []},
        "criterios_aceptacion": ["El flujo de reservas se puede completar."],
        "creado": "2026-10-07",
        "actualizado": "2026-10-07",
        "plantillas_aplicadas": [{"id": "PLT-001", "version": 1}],
        "generacion": {
            "motor": "local",
            "proveedor": "ollama",
            "modelo": "test-local",
            "fecha": "2026-10-07T10:00:00+00:00",
        },
        "entregables": [
            {
                "id": "E-001",
                "titulo": "Flujo de reservas",
                "descripcion": "Configurar el flujo de reservas y pagos.",
                "criterio_aceptacion": "Una reserva se puede completar.",
                "estado": "pendiente",
            }
        ],
        "tareas": [],
        "dependencias": [],
        "riesgos": [],
        "restricciones": [],
        "supuestos": [],
        "preguntas": [],
        "fuentes": [
            {
                "id": "F-001",
                "tipo": "documental",
                "nota": proposal_path.relative_to(vault).as_posix(),
                "seccion": source.section_path,
                "char_start": source.char_start,
                "char_end": source.char_end,
                "hash_nota": source_hash,
                "capturado": "2026-10-07T10:00:00+00:00",
            }
        ],
    }
    draft_path.write_text(
        render_plan_markdown(plan, "Nota humana preservada."),
        encoding="utf-8",
    )

    review_file = root / "plan-cloud-review.yaml"
    review_file.write_text(
        yaml.safe_dump(
            {
                "proveedor": "ollama",
                "modelo": "gpt-oss:120b-cloud",
                "revisado": True,
                "fecha_revision": "2026-10-06",
                "url_terminos": "https://ollama.com/terms",
                "region": "verificada",
                "retencion": "verificada",
                "entrenamiento": "verificado",
            }
        ),
        encoding="utf-8",
    )
    service = PlanService(
        vault=vault,
        drafts_dir=vault / "022 - PLANES_BORRADOR",
        audit_file=root / "audit" / "plan.jsonl",
        cloud_review_file=review_file,
        model_call=lambda _system, _user, _cloud: "{}",
    )
    return service, draft_path


def _cloud_response() -> str:
    return json.dumps(
        {
            "tareas": [
                {
                    "titulo": "Diseñar el flujo de reservas",
                    "descripcion": "Definir los pasos visibles del proceso.",
                    "entregable_ref": "D-001",
                    "fase": "Diseño",
                    "criterio_aceptacion": (
                        "Los pasos del flujo quedan documentados."
                    ),
                    "fuentes": ["S-001"],
                }
            ],
            "restricciones": [
                {
                    "tipo": "acceso",
                    "descripcion": "Se necesitan accesos del proveedor de pagos.",
                    "motivo": "La configuración depende de esos accesos.",
                    "fuentes": ["S-001"],
                }
            ],
        }
    )


def _enable_cloud(
    monkeypatch: pytest.MonkeyPatch,
    service: PlanService,
) -> None:
    monkeypatch.setattr(
        plan_service.cfg,
        "PLAN_CLOUD_MODEL",
        "gpt-oss:120b-cloud",
    )
    monkeypatch.setattr(
        plan_service.cfg,
        "PLAN_CLOUD_REVIEW_FILE",
        service.cloud_review_file,
    )


def test_preview_redacts_private_data_and_contains_only_minimized_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = _create_service(tmp_path)
    _enable_cloud(monkeypatch, service)

    preview = service.preview_cloud_improvement("PLAN-001")
    serialized_request = json.dumps(preview.request, ensure_ascii=False)
    user_payload = json.loads(preview.request["messages"][1]["content"])

    assert preview.gaps == ("E-001",)
    assert user_payload["plan"]["entregables_sin_tareas"][0]["id"] == "D-001"
    assert user_payload["fuentes"][0]["id"] == "S-001"
    assert "PR001" not in serialized_request
    assert "CL001" not in serialized_request
    assert "PROY001" not in serialized_request
    assert "Ana Gómez" not in serialized_request
    assert "ana.gomez@example.test" not in serialized_request
    assert "1.200 EUR" not in serialized_request
    assert "013 - PROPUESTA" not in serialized_request
    assert preview.redaction_counts["campos_personales"] >= 1
    assert preview.redaction_counts["emails"] >= 1
    assert preview.redaction_counts["importes"] >= 1
    assert preview.redaction_counts["credenciales"] >= 1
    assert preview.redaction_counts["claves_reconocibles"] >= 1
    assert preview.redaction_counts["claves_privadas"] >= 1
    assert "synthetic_secret_value" not in serialized_request
    assert "ghp_abcdefghijklmnopqrstuvwxyz012345" not in serialized_request
    assert "synthetic-private-key" not in serialized_request


def test_cloud_suggestions_are_not_written_until_selected_and_keep_estimates_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, draft_path = _create_service(tmp_path)
    _enable_cloud(monkeypatch, service)
    original = draft_path.read_text(encoding="utf-8")
    service._model_call = lambda _system, _user, cloud: (
        _cloud_response() if cloud else "{}"
    )

    preview = service.preview_cloud_improvement("PLAN-001")
    result = service.generate_cloud_improvement(preview)

    assert len(result.tasks) == 1
    assert result.constraints[0]["categoria"] == "candidata"
    assert result.constraints[0]["no_negociable"] is False
    assert draft_path.read_text(encoding="utf-8") == original

    applied = service.apply_cloud_improvement(
        result,
        task_ids=["task-001"],
        constraint_ids=[],
    )
    updated, narrative = parse_plan_markdown(draft_path.read_text(encoding="utf-8"))

    assert applied["relative_path"] == "022 - PLANES_BORRADOR/PLAN-001.md"
    assert len(updated["tareas"]) == 1
    assert updated["tareas"][0]["estimacion"] is None
    assert updated["restricciones"] == []
    assert "Nota humana preservada." in narrative
    assert not any(
        issue.level == "error"
        for issue in service.validate_draft(draft_path)[1]
    )


def test_cloud_refuses_stale_plan_or_source_and_invalid_citations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, draft_path = _create_service(tmp_path)
    _enable_cloud(monkeypatch, service)
    called = False

    def should_not_send(_system: str, _user: str, _cloud: bool) -> str:
        nonlocal called
        called = True
        return _cloud_response()

    service._model_call = should_not_send
    preview = service.preview_cloud_improvement("PLAN-001")
    source = service.vault / "013 - PROPUESTA _PRESUPUESTO" / "PR001 - Aura Web.md"
    source.write_text(_PROPOSAL_TEXT + "\nNuevo acuerdo.\n", encoding="utf-8")

    with pytest.raises(PlanServiceError, match="fuente cambió"):
        service.generate_cloud_improvement(preview)
    assert called is False

    service, draft_path = _create_service(tmp_path / "invalid")
    _enable_cloud(monkeypatch, service)
    before = draft_path.read_text(encoding="utf-8")
    service._model_call = lambda _system, _user, _cloud: json.dumps(
        {
            "tareas": [
                {
                    "titulo": "Tarea sin cita válida",
                    "descripcion": "Contenido.",
                    "entregable_ref": "D-001",
                    "fase": "Diseño",
                    "criterio_aceptacion": "Criterio.",
                    "fuentes": ["S-999"],
                }
            ],
            "restricciones": [],
        }
    )
    with pytest.raises(PlanServiceError, match="citas"):
        service.generate_cloud_improvement(
            service.preview_cloud_improvement("PLAN-001")
        )
    assert draft_path.read_text(encoding="utf-8") == before


def test_cloud_refuses_draft_changed_while_model_is_generating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, draft_path = _create_service(tmp_path)
    _enable_cloud(monkeypatch, service)
    preview = service.preview_cloud_improvement("PLAN-001")

    def edit_draft_during_request(
        _system: str,
        _user: str,
        _cloud: bool,
    ) -> str:
        draft_path.write_text(
            draft_path.read_text(encoding="utf-8") + "\nEdición humana.\n",
            encoding="utf-8",
        )
        return _cloud_response()

    service._model_call = edit_draft_during_request

    with pytest.raises(PlanServiceError, match="borrador cambió"):
        service.generate_cloud_improvement(preview)

    assert "Edición humana." in draft_path.read_text(encoding="utf-8")
    audit_events = [
        json.loads(line)
        for line in service.audit_file.read_text(encoding="utf-8").splitlines()
    ]
    assert audit_events[-1]["resultado"] == "error"
    assert "payload" not in audit_events[-1]
    assert "respuesta" not in audit_events[-1]


def test_cloud_refuses_to_apply_suggestions_to_a_changed_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, draft_path = _create_service(tmp_path)
    _enable_cloud(monkeypatch, service)
    service._model_call = lambda _system, _user, _cloud: _cloud_response()
    result = service.generate_cloud_improvement(
        service.preview_cloud_improvement("PLAN-001")
    )
    draft_path.write_text(
        draft_path.read_text(encoding="utf-8") + "\nEdición humana.\n",
        encoding="utf-8",
    )

    with pytest.raises(PlanServiceError, match="borrador cambió"):
        service.apply_cloud_improvement(
            result,
            task_ids=["task-001"],
            constraint_ids=[],
        )

    assert "Edición humana." in draft_path.read_text(encoding="utf-8")
    current_plan, _ = parse_plan_markdown(
        draft_path.read_text(encoding="utf-8")
    )
    assert current_plan["tareas"] == []


def test_cloud_rejects_extra_fields_and_fake_deliverables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = _create_service(tmp_path)
    _enable_cloud(monkeypatch, service)
    preview = service.preview_cloud_improvement("PLAN-001")
    valid_task = {
        "titulo": "Preparar el flujo",
        "descripcion": "Definir el proceso.",
        "entregable_ref": "D-001",
        "fase": "Diseño",
        "criterio_aceptacion": "El proceso queda documentado.",
        "fuentes": ["S-001"],
    }

    with pytest.raises(PlanServiceError, match="claves inesperadas"):
        service._validate_cloud_improvement_response(
            {"tareas": [], "restricciones": [], "extra": "no permitido"},
            preview,
        )

    fake_deliverable = {**valid_task, "entregable_ref": "D-999"}
    with pytest.raises(PlanServiceError, match="entregable no incluido"):
        service._validate_cloud_improvement_response(
            {"tareas": [fake_deliverable], "restricciones": []},
            preview,
        )

    duplicate = {
        **valid_task,
        "titulo": " preparar   el flujo ",
    }
    result = service._validate_cloud_improvement_response(
        {"tareas": [valid_task, duplicate], "restricciones": []},
        preview,
    )
    assert len(result.tasks) == 1
    assert result.duplicate_count == 1


def test_cloud_pwa_requires_confirmation_then_selective_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, draft_path = _create_service(tmp_path)
    _enable_cloud(monkeypatch, service)
    model_calls: list[bool] = []

    def model_call(_system: str, _user: str, cloud: bool) -> str:
        model_calls.append(cloud)
        return _cloud_response()

    service._model_call = model_call
    monkeypatch.setattr(webapps, "_plan_service", lambda _request: service)
    client = TestClient(
        webapps.create_app(obsidian_root=service.vault),
        base_url="http://127.0.0.1",
    )
    original = draft_path.read_text(encoding="utf-8")

    preview = client.post(
        "/api/plan/drafts/PLAN-001/cloud-improve/preview",
        json={},
    )
    assert preview.status_code == 200, preview.text
    assert preview.headers["cache-control"] == "no-store"
    assert "entregables_sin_tareas" in preview.json()["request"]["messages"][1]["content"]
    assert "ana.gomez@example.test" not in preview.text

    cancelled = client.post(
        "/api/plan/drafts/PLAN-001/cloud-improve/generate",
        json={"token": preview.json()["token"], "confirm": False},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is True
    assert draft_path.read_text(encoding="utf-8") == original
    assert model_calls == []

    preview = client.post(
        "/api/plan/drafts/PLAN-001/cloud-improve/preview",
        json={},
    )
    generated = client.post(
        "/api/plan/drafts/PLAN-001/cloud-improve/generate",
        json={"token": preview.json()["token"], "confirm": True},
    )
    assert generated.status_code == 200, generated.text
    assert len(generated.json()["tasks"]) == 1
    assert len(generated.json()["constraints"]) == 1
    assert model_calls == [True]
    assert draft_path.read_text(encoding="utf-8") == original

    applied = client.post(
        "/api/plan/drafts/PLAN-001/cloud-improve/apply",
        json={
            "token": generated.json()["token"],
            "task_ids": ["task-001"],
            "constraint_ids": [],
        },
    )
    assert applied.status_code == 200, applied.text
    updated, _ = parse_plan_markdown(draft_path.read_text(encoding="utf-8"))
    assert applied.json()["accepted_tasks"] == 1
    assert applied.json()["accepted_constraints"] == 0
    assert len(updated["tareas"]) == 1
    assert updated["restricciones"] == []
    assert updated["tareas"][0]["estimacion"] is None
    replayed = client.post(
        "/api/plan/drafts/PLAN-001/cloud-improve/apply",
        json={
            "token": generated.json()["token"],
            "task_ids": ["task-001"],
            "constraint_ids": [],
        },
    )
    assert replayed.status_code == 409


def test_cloud_api_rejects_replay_and_cancel_during_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = _create_service(tmp_path)
    _enable_cloud(monkeypatch, service)
    started = threading.Event()
    release = threading.Event()

    def blocking_model_call(
        _system: str,
        _user: str,
        _cloud: bool,
    ) -> str:
        started.set()
        if not release.wait(timeout=10):
            raise TimeoutError("El test no liberó la llamada Cloud.")
        return _cloud_response()

    service._model_call = blocking_model_call
    monkeypatch.setattr(webapps, "_plan_service", lambda _request: service)
    client = TestClient(
        webapps.create_app(obsidian_root=service.vault),
        base_url="http://127.0.0.1",
    )
    preview = client.post(
        "/api/plan/drafts/PLAN-001/cloud-improve/preview",
        json={},
    )
    assert preview.status_code == 200, preview.text
    token = preview.json()["token"]
    generate_url = (
        "/api/plan/drafts/PLAN-001/cloud-improve/generate"
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_request = executor.submit(
            client.post,
            generate_url,
            json={"token": token, "confirm": True},
        )
        assert started.wait(timeout=5)
        concurrent_cancel = client.post(
            generate_url,
            json={"token": token, "confirm": False},
        )
        concurrent_replay = client.post(
            generate_url,
            json={"token": token, "confirm": True},
        )
        assert concurrent_cancel.status_code == 409
        assert concurrent_replay.status_code == 409
        release.set()
        generated = first_request.result(timeout=10)

    assert generated.status_code == 200, generated.text
    replayed = client.post(
        generate_url,
        json={"token": token, "confirm": True},
    )
    assert replayed.status_code == 409


def test_cloud_api_requires_current_review_before_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = _create_service(tmp_path)
    service.cloud_review_file = tmp_path / "missing-review.yaml"
    model_calls: list[bool] = []
    service._model_call = lambda _system, _user, cloud: (
        model_calls.append(cloud) or _cloud_response()
    )
    monkeypatch.setattr(webapps, "_plan_service", lambda _request: service)
    client = TestClient(
        webapps.create_app(obsidian_root=service.vault),
        base_url="http://127.0.0.1",
    )

    response = client.post(
        "/api/plan/drafts/PLAN-001/cloud-improve/preview",
        json={},
    )

    assert response.status_code == 503
    assert model_calls == []
