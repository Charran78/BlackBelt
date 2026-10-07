"""Regression coverage for safe, diff-first project panorama updates."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from blackbelt.knowledge.project_hub import (
    ProjectHubError,
    ProjectHubService,
)
from blackbelt.knowledge.project_links import (
    ProjectSourceLink,
    project_source_paths,
    update_project_source_properties,
)
from blackbelt.webapps import create_app


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _vault(root: Path) -> Path:
    _write(
        root / "005 - CLIENTES" / "CL001 - Desireé.md",
        """---
nombre: Desireé
proyectos_activos:
  - PROY001 - Sitio web
---
# Cliente
""",
    )
    _write(
        root / "001 - PROYECTOS" / "PROY001 - Sitio web.md",
        """---
cliente: CL001
propuestas: []
reuniones: []
dossieres: []
plan:
---
# Notas humanas

No sobrescribir esta decisión.

<!-- BLACKBELT:PANORAMA:START -->
## Panorama consolidado (gestionado por BlackBelt)

Resumen anterior.
<!-- BLACKBELT:PANORAMA:END -->
""",
    )
    _write(
        root / "013 - PROPUESTA _PRESUPUESTO" / "PR001 - Sitio web.md",
        """---
cliente: CL001
proyecto: PROY001
---
# Objetivo
Crear el sitio.
""",
    )
    _write(
        root / "019 - REUNIONES" / "RE001 - CL001.md",
        """---
cliente: CL001
---
# Decisiones
Se acordó revisar accesibilidad.
""",
    )
    _write(
        root / "020 - DOSSIERES" / "CL001_2026-10-07_01.md",
        """---
cliente: CL001
---
# Seguimiento
La clienta pidió una revisión de pagos.
""",
    )
    _write(
        root / "020 - DOSSIERES" / "CL002_2026-10-07_01.md",
        """---
cliente: CL002
---
NO-INCLUIR-CLIENTE-DISTINTO
""",
    )
    _write(
        root / "019 - REUNIONES" / "RE002 - Secreto.md",
        """---
cliente: CL001
privado: true
---
NO-INCLUIR-PRIVADO
""",
    )
    return root


def _model_response() -> str:
    return json.dumps(
        {
            "resumen": {
                "texto": "Resumen verificable.",
                "fuentes": ["PR001"],
            },
            "objetivo": [{"texto": "Crear el sitio.", "fuentes": ["PR001"]}],
            "alcance": [],
            "entregables": [],
            "decisiones": [
                {"texto": "Revisar accesibilidad.", "fuentes": ["RE001"]},
            ],
            "riesgos": [],
            "conflictos": [],
            "preguntas_abiertas": [],
        },
    )


def test_project_hub_lists_only_client_linked_non_private_sources(
    tmp_path: Path,
) -> None:
    service = ProjectHubService(_vault(tmp_path / "vault"), lambda *_: "{}")

    projects = service.list_projects("CL001")
    sources = service.list_sources("CL001", "PROY001")

    assert [project["id"] for project in projects] == ["PROY001"]
    assert {source.id for source in sources} == {
        "PR001",
        "RE001",
        "CL001_2026-10-07_01",
    }
    assert all("CL002" not in source.relative_path for source in sources)
    assert all("Secreto" not in source.title for source in sources)


def test_preview_and_confirm_replace_only_managed_section(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path / "vault")
    service = ProjectHubService(
        vault,
        lambda _system, _user, cloud: _model_response(),
    )

    preview = service.preview("CL001", "PROY001", ("PR001", "RE001"))
    assert "Resumen anterior." in preview.diff
    assert "Resumen verificable." in preview.diff
    assert "[[013 - PROPUESTA _PRESUPUESTO/PR001 - Sitio web.md|" in preview.diff
    assert "No sobrescribir esta decisión." not in preview.diff
    assert preview.source_paths == (
        "013 - PROPUESTA _PRESUPUESTO/PR001 - Sitio web.md",
        "019 - REUNIONES/RE001 - CL001.md",
    )

    relative_path = service.confirm(preview)
    updated = (vault / relative_path).read_text(encoding="utf-8")
    assert "No sobrescribir esta decisión." in updated
    assert "Resumen verificable." in updated
    assert "plan:\n" in updated
    assert (
        'propuestas:\n  - "[[013 - PROPUESTA _PRESUPUESTO/'
        "PR001 - Sitio web.md|PR001 - Sitio web]]\"\n"
    ) in updated
    assert "[[019 - REUNIONES/RE001 - CL001.md|RE001 · RE001 - CL001]]" in updated
    assert updated.count("BLACKBELT:PANORAMA:START") == 1
    assert {
        source.id
        for source in service.list_sources("CL001", "PROY001")
        if source.selected
    } == {"PR001", "RE001"}
    repeated = service.preview("CL001", "PROY001", ("PR001", "RE001"))
    assert repeated.diff == ""


def test_project_source_properties_preserve_other_frontmatter_and_are_readable() -> None:
    content = """---
id: PROY001
cliente: "[[CL001 - Desireé]]"
plan: "[[PLAN-001]]"
propuestas: []
reuniones: []
dossieres: []
---
Texto humano que se conserva.
"""

    updated = update_project_source_properties(
        content,
        (
            ProjectSourceLink(
                "propuesta",
                "013 - PROPUESTA _PRESUPUESTO/PR001 - Web.md",
                "PR001 - Web",
            ),
            ProjectSourceLink(
                "dossier",
                "020 - DOSSIERES/CL001_2026-10-07_01.md",
                "CL001_2026-10-07_01",
            ),
        ),
    )

    assert 'cliente: "[[CL001 - Desireé]]"' in updated
    assert 'plan: "[[PLAN-001]]"' in updated
    assert "Texto humano que se conserva." in updated
    assert project_source_paths(updated) == {
        "013 - PROPUESTA _PRESUPUESTO/PR001 - Web.md",
        "020 - DOSSIERES/CL001_2026-10-07_01.md",
    }


@pytest.mark.parametrize("changed_path_kind", ["project", "source"])
def test_confirm_rejects_stale_project_or_source(
    tmp_path: Path,
    changed_path_kind: str,
) -> None:
    vault = _vault(tmp_path / "vault")
    service = ProjectHubService(
        vault,
        lambda *_: _model_response(),
    )
    preview = service.preview("CL001", "PROY001", ("PR001",))
    changed_path = (
        vault / preview.project_path
        if changed_path_kind == "project"
        else vault / preview.source_paths[0]
    )
    changed_path.write_text(
        changed_path.read_text(encoding="utf-8") + "\nCambio concurrente.\n",
        encoding="utf-8",
    )

    with pytest.raises(ProjectHubError, match="cambió tras la previsualización"):
        service.confirm(preview)


def test_generated_citations_are_restricted_to_selected_sources(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path / "vault")
    response = {
        "resumen": {
            "texto": "No verificar",
            "fuentes": ["CL002"],
        },
        "objetivo": [
            {"texto": "Fundado.", "fuentes": ["PR001"]},
            {"texto": "Sin fuente.", "fuentes": ["CL002"]},
        ],
        "alcance": [],
        "entregables": [],
        "decisiones": [],
        "riesgos": [],
        "conflictos": [],
        "preguntas_abiertas": [],
    }
    service = ProjectHubService(
        vault,
        lambda *_: json.dumps(response),
    )

    preview = service.preview("CL001", "PROY001", ("PR001",))

    assert "Fundado. [`PR001`]" in preview.updated_content
    assert "Sin fuente." not in preview.updated_content
    assert "NO-INCLUIR-CLIENTE-DISTINTO" not in preview.updated_content


def test_preview_rejects_sources_not_linked_to_selected_client(
    tmp_path: Path,
) -> None:
    service = ProjectHubService(_vault(tmp_path / "vault"), lambda *_: "{}")

    with pytest.raises(ProjectHubError, match="ya no están vinculadas"):
        service.preview("CL001", "PROY001", ("PR099",))


def test_preview_rejects_missing_or_duplicated_managed_markers(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path / "vault")
    project_path = vault / "001 - PROYECTOS" / "PROY001 - Sitio web.md"
    project_path.write_text(
        "---\ncliente: CL001\n---\n<!-- BLACKBELT:PANORAMA:START -->",
        encoding="utf-8",
    )
    service = ProjectHubService(vault, lambda *_: _model_response())

    with pytest.raises(ProjectHubError, match="marcadores"):
        service.preview("CL001", "PROY001", ("PR001",))


def test_project_panorama_api_requires_confirmation_and_never_caches_content(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path / "vault")
    response_json = _model_response()

    class FakeOllamaClient:
        def chat(self, **_kwargs: object) -> object:
            return SimpleNamespace(
                message=SimpleNamespace(content=response_json),
            )

    app = create_app(
        obsidian_root=vault,
        ollama_client_factory=FakeOllamaClient,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        sources = client.get(
            "/api/plan/projects/PROY001/sources",
            params={"client_id": "CL001"},
        )
        assert sources.status_code == 200
        assert sources.headers["cache-control"] == "no-store"
        assert "CL002" not in sources.text

        preview = client.post(
            "/api/plan/projects/PROY001/panorama/preview",
            json={
                "client_id": "CL001",
                "source_ids": ["PR001", "RE001"],
            },
        )
        assert preview.status_code == 200
        assert preview.headers["cache-control"] == "no-store"
        preview_payload = preview.json()
        assert "Resumen anterior." in preview_payload["diff"]
        assert str(vault) not in preview.text

        unconfirmed = client.post(
            "/api/plan/projects/PROY001/panorama/confirm",
            json={"token": preview_payload["token"], "confirm": False},
        )
        assert unconfirmed.status_code == 400

        confirmed = client.post(
            "/api/plan/projects/PROY001/panorama/confirm",
            json={"token": preview_payload["token"], "confirm": True},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["relative_path"].startswith("001 - PROYECTOS/")
        assert (vault / confirmed.json()["relative_path"]).is_file()
