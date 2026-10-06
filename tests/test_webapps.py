"""Tests for the loopback web applications and local Ollama endpoint."""

from __future__ import annotations

import asyncio
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blackbelt import webapps
from blackbelt.webapps import create_app


def _asset_root(tmp_path: Path) -> Path:
    soma_dir = tmp_path / "soma"
    ghostwriter_dir = tmp_path / "ghostwriter"
    meetings_dir = tmp_path / "meetings"
    soma_dir.mkdir()
    ghostwriter_dir.mkdir()
    meetings_dir.mkdir()
    (soma_dir / "somaguard.html").write_text(
        "<title>SOMA test</title>",
        encoding="utf-8",
    )
    (ghostwriter_dir / "ghostwriter_ai_studio.html").write_text(
        "<title>Ghost Writer test</title>",
        encoding="utf-8",
    )
    (meetings_dir / "index.html").write_text(
        "<title>Meetings test</title>",
        encoding="utf-8",
    )
    return tmp_path


def test_pages_health_redirect_and_service_worker(tmp_path: Path) -> None:
    client = TestClient(
        create_app(assets_root=_asset_root(tmp_path)),
        follow_redirects=False,
        base_url="http://127.0.0.1",
    )

    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/").headers["location"] == "/soma/"
    assert "SOMA test" in client.get("/soma/").text
    assert "Ghost Writer test" in client.get("/ghostwriter/").text
    assert "Meetings test" in client.get("/meetings/").text

    service_worker = client.get("/soma-sw.js")
    assert service_worker.status_code == 200
    assert "application/javascript" in service_worker.headers["content-type"]
    assert service_worker.headers["cache-control"] == "no-cache"
    assert "requestUrl.origin !== appUrl.origin" in service_worker.text
    assert "!requestUrl.pathname.startsWith(appUrl.pathname)" in service_worker.text
    assert "requestUrl.search" in service_worker.text
    assert client.get("/soma/soma-sw.js").text == service_worker.text

    soma_manifest_response = client.get("/soma/manifest.webmanifest")
    soma_manifest = soma_manifest_response.json()
    assert soma_manifest_response.status_code == 200
    assert soma_manifest["start_url"] == "/soma/"
    assert soma_manifest["scope"] == "/soma/"
    assert soma_manifest["display"] == "standalone"
    assert soma_manifest["prefer_related_applications"] is False
    assert {icon["sizes"] for icon in soma_manifest["icons"]} == {
        "192x192",
        "512x512",
    }
    assert client.get("/soma/icons/icon.svg").status_code == 200

    ghost_manifest_response = client.get(
        "/ghostwriter/manifest.webmanifest"
    )
    ghost_manifest = ghost_manifest_response.json()
    assert ghost_manifest_response.status_code == 200
    assert ghost_manifest["start_url"] == "/ghostwriter/"
    assert ghost_manifest["scope"] == "/ghostwriter/"
    assert ghost_manifest["display"] == "standalone"
    assert ghost_manifest["prefer_related_applications"] is False
    assert {icon["sizes"] for icon in ghost_manifest["icons"]} == {
        "192x192",
        "512x512",
    }
    assert client.get("/ghostwriter/icons/icon.svg").status_code == 200

    meetings_manifest_response = client.get("/meetings/manifest.webmanifest")
    meetings_manifest = meetings_manifest_response.json()
    assert meetings_manifest_response.status_code == 200
    assert meetings_manifest_response.headers["content-type"].startswith(
        "application/manifest+json"
    )
    assert meetings_manifest["start_url"] == "/meetings/"
    assert meetings_manifest["scope"] == "/meetings/"
    assert meetings_manifest["display"] == "standalone"
    assert meetings_manifest["prefer_related_applications"] is False
    assert {icon["sizes"] for icon in meetings_manifest["icons"]} == {
        "192x192",
        "512x512",
    }
    assert client.get("/meetings/icons/icon.svg").status_code == 200
    meetings_worker = client.get("/meetings/meetings-sw.js")
    assert meetings_worker.status_code == 200
    assert meetings_worker.headers["cache-control"] == "no-cache"
    assert 'const CACHE_NAME = "meetings-shell-v6"' in meetings_worker.text
    assert "CACHEABLE_PATHS.has(requestUrl.pathname)" in meetings_worker.text
    assert "/api/meeting-prep" not in meetings_worker.text
    assert client.get("/meetings/meetings-sw.js?version=1").headers[
        "cache-control"
    ] == "no-cache"

    ghost_worker = client.get("/ghostwriter/ghostwriter-sw.js")
    assert ghost_worker.status_code == 200
    assert ghost_worker.headers["cache-control"] == "no-cache"
    assert "APP_ASSETS" in ghost_worker.text
    assert "requestUrl.origin !== appUrl.origin" in ghost_worker.text


def test_meeting_assistant_serves_clients_and_dossier_read_only(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    for folder in (
        "001 - PROYECTOS",
        "002 - TAREAS",
        "005 - CLIENTES",
        "006 - CREDENCIALES",
        "013 - PROPUESTA _PRESUPUESTO",
        "019 - REUNIONES",
    ):
        (vault / folder).mkdir(parents=True, exist_ok=True)
    client_note = vault / "005 - CLIENTES" / "CL001 - Ana.md"
    client_note.write_text(
        """---
nombre: Ana García
estado: prospecto
proyectos_activos:
  - Web de bodas
proyectos_previos:
  - Catálogo anterior
---
# Aura Bodas

## Resumen
- Preparar demo.

## Personas de contacto
### Datos directos
- correo: ana@example.test
""",
        encoding="utf-8",
    )
    meeting_note = vault / "019 - REUNIONES" / "RE001 - CL001.md"
    meeting_note.write_text(
        '---\ncliente: "[[CL001 - Ana]]"\n---\n# Reunión\n\nRevisar demo.\n',
        encoding="utf-8",
    )
    proposal_note = vault / "013 - PROPUESTA _PRESUPUESTO" / "PR001 - Ana.md"
    proposal_note.write_text(
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
- Forma de pago: dos plazos
""",
        encoding="utf-8",
    )
    credential_note = vault / "006 - CREDENCIALES" / "privado.md"
    credential_note.write_text(
        "---\ncliente: CL001\n---\nSECRETO-NO-LEER\n",
        encoding="utf-8",
    )
    original_files = {
        path: path.read_bytes()
        for path in (client_note, meeting_note, proposal_note, credential_note)
    }
    client = TestClient(
        create_app(obsidian_root=vault),
        base_url="http://127.0.0.1",
    )

    client_response = client.get("/api/meeting-prep/clients")
    dossier_response = client.get(
        "/api/meeting-prep/prepare",
        params={"client_id": "CL001"},
    )

    assert client_response.status_code == 200
    assert client_response.headers["cache-control"] == "no-store"
    assert client_response.json() == [
        {
            "client_id": "CL001",
            "name": "Ana García",
            "status": "prospecto",
            "source": "005 - CLIENTES/CL001 - Ana.md",
        },
    ]
    assert dossier_response.status_code == 200
    assert dossier_response.headers["cache-control"] == "no-store"
    dossier = dossier_response.json()
    assert dossier["client"]["client_id"] == "CL001"
    assert dossier["active_projects"] == ["Web de bodas"]
    assert dossier["previous_projects"] == ["Catálogo anterior"]
    assert [source["path"] for source in dossier["sources"]] == [
        "013 - PROPUESTA _PRESUPUESTO/PR001 - Ana.md",
        "019 - REUNIONES/RE001 - CL001.md",
    ]
    proposal_source = dossier["sources"][0]["content"]
    assert "Base imponible: 1200" in proposal_source
    assert "Total: 1452" in proposal_source
    assert "Forma de pago: dos plazos" in proposal_source
    assert "ana@example.test" not in dossier_response.text
    assert "SECRETO-NO-LEER" not in dossier_response.text
    assert str(vault) not in dossier_response.text
    assert {path: path.read_bytes() for path in original_files} == original_files

    missing_client = client.get(
        "/api/meeting-prep/prepare",
        params={"client_id": "CL099"},
    )
    assert missing_client.status_code == 404


def test_meeting_assistant_saves_dossier_with_client_key_and_date(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    for folder in (
        "001 - PROYECTOS",
        "002 - TAREAS",
        "005 - CLIENTES",
        "013 - PROPUESTA _PRESUPUESTO",
        "019 - REUNIONES",
    ):
        (vault / folder).mkdir(parents=True, exist_ok=True)
    (vault / "005 - CLIENTES" / "CL001 - Ana.md").write_text(
        "---\nnombre: Ana García\nestado: prospecto\n---\n"
        "# Aura Bodas\n\n## Resumen\n- Necesita una web.\n",
        encoding="utf-8",
    )
    (vault / "013 - PROPUESTA _PRESUPUESTO" / "PR001 - Ana.md").write_text(
        "---\ntipo: propuesta\ncliente: Ana García\n---\n"
        "# Propuesta\n\n## Inversión\n- Total: 1452\n",
        encoding="utf-8",
    )
    client = TestClient(
        create_app(obsidian_root=vault),
        base_url="http://127.0.0.1",
    )

    response = client.post(
        "/api/meeting-prep/save",
        json={"client_id": "CL001"},
    )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    expected_filename = f"CL001_{date.today().isoformat()}.md"
    assert response.json() == {
        "filename": expected_filename,
        "relative_path": f"020 - DOSSIERES/{expected_filename}",
    }
    saved_note = vault / "020 - DOSSIERES" / expected_filename
    saved_content = saved_note.read_text(encoding="utf-8")
    assert "# Dossier de reunión: Ana García" in saved_content
    assert "Clave de cliente: `CL001`" in saved_content
    assert "[[005 - CLIENTES/CL001 - Ana]]" in saved_content
    assert "Total: 1452" in saved_content
    assert "[[013 - PROPUESTA _PRESUPUESTO/PR001 - Ana]]" in saved_content
    assert str(vault) not in saved_content


def test_meeting_assistant_never_overwrites_same_day_dossier(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    client_directory = vault / "005 - CLIENTES"
    client_directory.mkdir(parents=True)
    (client_directory / "CL001 - Ana.md").write_text(
        "---\nnombre: Ana\n---\n# Contexto\n",
        encoding="utf-8",
    )
    client = TestClient(
        create_app(obsidian_root=vault),
        base_url="http://127.0.0.1",
    )
    content = {
        "client_id": "CL001",
    }

    first = client.post("/api/meeting-prep/save", json=content)
    second = client.post("/api/meeting-prep/save", json=content)

    first_path = vault / first.json()["relative_path"]
    second_path = vault / second.json()["relative_path"]
    assert first.status_code == second.status_code == 201
    assert first_path.name == f"CL001_{date.today().isoformat()}.md"
    assert second_path.name == f"CL001_{date.today().isoformat()}_01.md"
    assert first_path != second_path
    assert first_path.read_text(encoding="utf-8") != ""
    assert second_path.read_text(encoding="utf-8") != ""


def test_meeting_assistant_save_rejects_missing_or_extra_client_data(
    tmp_path: Path,
) -> None:
    (tmp_path / "005 - CLIENTES").mkdir(parents=True)
    client = TestClient(
        create_app(obsidian_root=tmp_path),
        base_url="http://127.0.0.1",
    )

    missing_client = client.post(
        "/api/meeting-prep/save",
        json={"client_id": "CL404"},
    )
    extra_field = client.post(
        "/api/meeting-prep/save",
        json={"client_id": "CL404", "path": "../outside.md"},
    )

    assert missing_client.status_code == 404
    assert extra_field.status_code == 422
    assert not (tmp_path.parent / "outside.md").exists()


def test_meeting_assistant_page_is_local_and_renders_note_text_safely() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    page = (
        repository_root
        / "blackbelt"
        / "data"
        / "webapps"
        / "meetings"
        / "index.html"
    ).read_text(encoding="utf-8")
    client = TestClient(create_app(), base_url="http://127.0.0.1")

    response = client.get("/meetings/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.text == page
    assert "/api/meeting-prep/clients" in page
    assert "/api/meeting-prep/prepare" in page
    assert "textContent" in page
    assert "innerHTML" not in page
    assert "CREDENCIALES" in page
    assert 'rel="manifest" href="/meetings/manifest.webmanifest"' in page
    assert 'id="install-button"' in page
    assert 'id="save-button"' in page
    assert 'id="print-button"' in page
    assert "beforeinstallprompt" in page
    assert "navigator.serviceWorker.register(\"/meetings/meetings-sw.js\")" in page
    assert "display-mode: standalone" in page
    assert "/api/meeting-prep/save" in page
    assert "window.print()" in page
    assert "window.addEventListener(\"beforeprint\"" in page
    assert "window.addEventListener(\"afterprint\"" in page
    assert "@media print" in page
    assert "function renderMarkdownWithTables" in page
    assert 'document.createElement("table")' in page
    assert ".source { overflow: visible; break-inside: auto;" in page
    assert "@page { size: auto; margin: 10mm; }" in page


def test_html_pages_link_static_manifests_and_register_scoped_workers() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    soma_html = (
        repository_root
        / "blackbelt"
        / "data"
        / "webapps"
        / "soma"
        / "somaguard.html"
    ).read_text(encoding="utf-8")
    ghostwriter_html = (
        repository_root
        / "blackbelt"
        / "data"
        / "webapps"
        / "ghostwriter"
        / "ghostwriter_ai_studio.html"
    ).read_text(encoding="utf-8")

    assert 'href="/soma/manifest.webmanifest"' in soma_html
    assert "navigator.serviceWorker.register('/soma/soma-sw.js')" in soma_html
    assert "URL.createObjectURL(manifestBlob)" not in soma_html

    assert 'href="/ghostwriter/manifest.webmanifest"' in ghostwriter_html
    assert (
        "navigator.serviceWorker.register('/ghostwriter/ghostwriter-sw.js')"
        in ghostwriter_html
    )
    assert "btn-save-to-vault" in ghostwriter_html
    assert 'id="tab-reddit"' in ghostwriter_html
    assert 'id="view-reddit"' in ghostwriter_html
    assert "/api/ghostwriter/reddit-feed" in ghostwriter_html
    assert "I:\\Mi unidad\\DriveSyncFiles" not in ghostwriter_html
    assert "obsidian-history-destination" not in ghostwriter_html
    assert re.search(
        r'<select id="select-obsidian-path"[^>]*>\s*'
        r'<option value="">[^<]*</option>\s*</select>\s*'
        r'<div id="obsidian-destination-status"',
        ghostwriter_html,
    )
    assert ghostwriter_html.count("<select") == ghostwriter_html.count("</select>")
    assert 'id="btn-generate-content"' in ghostwriter_html
    assert 'id="view-radar"' in ghostwriter_html
    assert "function formatApiError(detail, status)" in ghostwriter_html
    assert "userQuery.length > 32000" in ghostwriter_html
    assert 'onclick="openPublicationComposer()"' in ghostwriter_html
    assert 'id="btn-publish-platform"' in ghostwriter_html
    assert "Publicar en LinkedIn" in ghostwriter_html
    assert "Publicar en Substack" in ghostwriter_html
    assert "https://www.linkedin.com/sharing/compose" in ghostwriter_html
    assert "https://pedromencias.substack.com/publish/post/" in ghostwriter_html
    assert "function updatePublishButton(platform)" in ghostwriter_html
    assert "navigator.clipboard?.writeText" in ghostwriter_html
    assert 'id="radar-news-grid"' in ghostwriter_html
    assert 'id="standalone-aspect-ratio"' in ghostwriter_html
    assert 'id="select-ai-backend"' in ghostwriter_html
    assert "window.confirm(" in ghostwriter_html
    assert "Ollama Cloud" in ghostwriter_html
    assert "function generateContentFlow()" in ghostwriter_html
    assert "no reutilices ejemplos o frases" in ghostwriter_html.lower()
    assert "El otro día estaba a punto de tirar mi PC por la ventana" not in ghostwriter_html

    client = TestClient(
        create_app(),
        base_url="http://127.0.0.1",
    )
    served_html = client.get("/ghostwriter/").text
    for element_id in (
        "btn-generate-content",
        "view-radar",
        "view-reddit",
        "radar-news-grid",
        "reddit-news-grid",
        "standalone-aspect-ratio",
        "btn-save-to-vault",
    ):
        assert f'id="{element_id}"' in served_html
    assert served_html.count("<select") == served_html.count("</select>")


def test_default_routes_serve_packaged_html_even_when_originals_exist() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    webapps_root = repository_root / "blackbelt" / "data" / "webapps"
    client = TestClient(create_app(), base_url="http://127.0.0.1")

    for route, relative_path in (
        ("/soma/", Path("soma") / "somaguard.html"),
        (
            "/ghostwriter/",
            Path("ghostwriter") / "ghostwriter_ai_studio.html",
        ),
    ):
        response = client.get(route)
        packaged_html = (webapps_root / relative_path).read_text(
            encoding="utf-8",
        )
        assert response.status_code == 200
        assert response.text == packaged_html


def test_ghostwriter_saves_new_notes_inside_configured_vault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GHOSTWRITER_OBSIDIAN_SUBDIR", raising=False)
    destination = tmp_path / "013 - PUBLICACIONES"
    destination.mkdir()
    client = TestClient(
        create_app(obsidian_root=tmp_path),
        base_url="http://127.0.0.1",
    )
    content = "---\ntitle: Prueba\n---\nTexto de prueba.\n"

    destination_response = client.get("/api/ghostwriter/destination")
    response = client.post(
        "/api/ghostwriter/save",
        json={"platform": "linkedin", "content": content},
    )

    assert destination_response.status_code == 200
    assert destination_response.json() == {
        "directory": "013 - PUBLICACIONES",
    }
    assert str(tmp_path) not in destination_response.text
    assert response.status_code == 201
    saved_file = response.json()["filename"]
    assert response.json()["relative_path"] == (
        f"013 - PUBLICACIONES/{saved_file}"
    )
    assert saved_file == f"{date.today().isoformat()}_linkedin_prueba.md"
    assert (destination / saved_file).read_text(encoding="utf-8") == content


def test_ghostwriter_routes_platforms_to_personalized_folders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GHOSTWRITER_OBSIDIAN_SUBDIR", raising=False)
    monkeypatch.setenv(
        "GHOSTWRITER_LINKEDIN_SUBDIR",
        "018 - NEWSLETTERS/POSTS",
    )
    monkeypatch.setenv(
        "GHOSTWRITER_SUBSTACK_SUBDIR",
        "018 - NEWSLETTERS/BITS TO THE BONE",
    )
    linkedin_folder = tmp_path / "018 - NEWSLETTERS" / "POSTS"
    substack_folder = (
        tmp_path / "018 - NEWSLETTERS" / "BITS TO THE BONE"
    )
    linkedin_folder.mkdir(parents=True)
    substack_folder.mkdir(parents=True)
    client = TestClient(
        create_app(obsidian_root=tmp_path),
        base_url="http://127.0.0.1",
    )

    linkedin_destination = client.get(
        "/api/ghostwriter/destination?platform=linkedin",
    )
    substack_destination = client.get(
        "/api/ghostwriter/destination?platform=substack",
    )
    linkedin_save = client.post(
        "/api/ghostwriter/save",
        json={"platform": "linkedin", "content": "Post de prueba"},
    )
    substack_save = client.post(
        "/api/ghostwriter/save",
        json={"platform": "substack", "content": "Newsletter de prueba"},
    )

    assert linkedin_destination.json()["directory"] == (
        "018 - NEWSLETTERS/POSTS"
    )
    assert substack_destination.json()["directory"] == (
        "018 - NEWSLETTERS/BITS TO THE BONE"
    )
    assert linkedin_save.status_code == 201
    assert substack_save.status_code == 201
    assert (linkedin_folder / linkedin_save.json()["filename"]).is_file()
    assert (substack_folder / substack_save.json()["filename"]).is_file()


def test_ghostwriter_save_never_overwrites_existing_notes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GHOSTWRITER_OBSIDIAN_SUBDIR", raising=False)
    destination = tmp_path / "013 - PUBLICACIONES"
    destination.mkdir()
    filename = f"{date.today().isoformat()}_substack_post.md"
    existing_note = destination / filename
    existing_note.write_text("Nota existente\n", encoding="utf-8")
    client = TestClient(
        create_app(obsidian_root=tmp_path),
        base_url="http://127.0.0.1",
    )

    response = client.post(
        "/api/ghostwriter/save",
        json={"platform": "substack", "content": "Nota nueva"},
    )

    assert response.status_code == 201
    assert response.json()["filename"] == (
        f"{date.today().isoformat()}_substack_post_1.md"
    )
    assert existing_note.read_text(encoding="utf-8") == "Nota existente\n"


def test_ghostwriter_uses_a_sanitized_frontmatter_title_for_filename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GHOSTWRITER_OBSIDIAN_SUBDIR", raising=False)
    destination = tmp_path / "013 - PUBLICACIONES"
    destination.mkdir()
    client = TestClient(
        create_app(obsidian_root=tmp_path),
        base_url="http://127.0.0.1",
    )

    response = client.post(
        "/api/ghostwriter/save",
        json={
            "platform": "substack",
            "content": '---\ntitle: "IA: mañana / sin humo"\n---\nTexto',
        },
    )

    assert response.status_code == 201
    assert response.json()["filename"] == (
        f"{date.today().isoformat()}_substack_ia-mañana-sin-humo.md"
    )
    assert len(list(destination.iterdir())) == 1


def test_ghostwriter_save_rejects_invalid_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GHOSTWRITER_OBSIDIAN_SUBDIR", raising=False)
    (tmp_path / "013 - PUBLICACIONES").mkdir()
    client = TestClient(
        create_app(obsidian_root=tmp_path),
        base_url="http://127.0.0.1",
    )

    response = client.post(
        "/api/ghostwriter/save",
        json={"platform": "../outside", "content": "No debe escribirse"},
    )

    assert response.status_code == 422
    assert not (tmp_path.parent / "outside").exists()


@pytest.mark.parametrize(
    "unsafe_subdirectory",
    ["../outside", "C:outside", r"C:\outside"],
)
def test_ghostwriter_save_rejects_traversal_in_configured_subdirectory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_subdirectory: str,
) -> None:
    monkeypatch.setenv(
        "GHOSTWRITER_OBSIDIAN_SUBDIR",
        unsafe_subdirectory,
    )
    client = TestClient(
        create_app(obsidian_root=tmp_path),
        base_url="http://127.0.0.1",
    )

    response = client.get("/api/ghostwriter/destination")

    assert response.status_code == 500
    assert "ruta relativa segura" in response.json()["detail"]


def test_ghostwriter_destination_reports_missing_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GHOSTWRITER_OBSIDIAN_SUBDIR", raising=False)
    client = TestClient(
        create_app(obsidian_root=tmp_path),
        base_url="http://127.0.0.1",
    )

    response = client.get("/api/ghostwriter/destination")

    assert response.status_code == 503
    assert "No se encuentra la bóveda" in response.json()["detail"]


def test_root_redirect_can_default_to_ghostwriter() -> None:
    client = TestClient(
        create_app(default_page="ghostwriter"),
        base_url="http://127.0.0.1",
    )

    assert client.get("/", follow_redirects=False).headers["location"] == (
        "/ghostwriter/"
    )

    meetings_client = TestClient(
        create_app(default_page="meetings"),
        base_url="http://127.0.0.1",
    )
    assert meetings_client.get("/", follow_redirects=False).headers["location"] == (
        "/meetings/"
    )


def test_rejects_untrusted_host() -> None:
    client = TestClient(create_app(), base_url="http://127.0.0.1")

    response = client.get("/healthz", headers={"host": "example.com"})

    assert response.status_code == 400


def test_invalid_default_page_is_rejected() -> None:
    with pytest.raises(ValueError, match="página inicial"):
        create_app(default_page="other")


def test_generation_uses_injected_ollama_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.delenv("GHOSTWRITER_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("GHOSTWRITER_NUM_CTX", raising=False)
    monkeypatch.delenv("GHOSTWRITER_NUM_PREDICT", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_KEEP_ALIVE", raising=False)

    class FakeClient:
        def chat(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"message": {"content": "  Borrador local  "}}

    client = TestClient(
        create_app(ollama_client_factory=FakeClient),
        base_url="http://127.0.0.1",
    )
    response = client.post(
        "/api/ghostwriter/generate",
        json={
            "system_instruction": "Escribe en español.",
            "user_query": "Escribe sobre Ollama.",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"text": "Borrador local"}
    assert captured["model"] == "qwen2.5:0.5b"
    assert captured["messages"] == [
        {"role": "system", "content": "Escribe en español."},
        {"role": "user", "content": "Escribe sobre Ollama."},
    ]
    assert captured["keep_alive"] == "0"
    assert captured["options"]["num_ctx"] == 4096
    assert captured["options"]["num_predict"] == 2048
    assert captured["options"]["repeat_penalty"] == 1.18
    assert captured["options"]["repeat_last_n"] == 128
    assert captured["options"]["temperature"] == 0.55


def test_generation_uses_cloud_only_when_explicitly_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.delenv("GHOSTWRITER_OLLAMA_CLOUD_MODEL", raising=False)
    monkeypatch.delenv("GHOSTWRITER_CLOUD_NUM_CTX", raising=False)
    monkeypatch.delenv("GHOSTWRITER_CLOUD_NUM_PREDICT", raising=False)

    class FakeClient:
        def chat(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"message": {"content": "Borrador Cloud"}}

    client = TestClient(
        create_app(ollama_client_factory=FakeClient),
        base_url="http://127.0.0.1",
    )
    response = client.post(
        "/api/ghostwriter/generate",
        json={
            "backend": "cloud",
            "system_instruction": "Escribe en español.",
            "user_query": "Escribe sobre Ollama.",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"text": "Borrador Cloud"}
    assert captured["model"] == "gpt-oss:120b-cloud"
    assert captured["options"]["num_ctx"] == 32768
    assert captured["options"]["num_predict"] == 8192


def test_cloud_generation_limits_are_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv("GHOSTWRITER_CLOUD_NUM_CTX", "16384")
    monkeypatch.setenv("GHOSTWRITER_CLOUD_NUM_PREDICT", "12000")

    class FakeClient:
        def chat(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"message": {"content": "Borrador Cloud"}}

    client = TestClient(
        create_app(ollama_client_factory=FakeClient),
        base_url="http://127.0.0.1",
    )
    response = client.post(
        "/api/ghostwriter/generate",
        json={
            "backend": "cloud",
            "system_instruction": "Escribe en español.",
            "user_query": "Escribe una newsletter extensa.",
        },
    )

    assert response.status_code == 200
    assert captured["options"]["num_ctx"] == 16384
    assert captured["options"]["num_predict"] == 12000


def test_cloud_response_accepts_longer_drafts() -> None:
    long_draft = "Borrador largo. " * 2000

    class FakeClient:
        def chat(self, **kwargs: Any) -> dict[str, Any]:
            return {"message": {"content": long_draft}}

    client = TestClient(
        create_app(ollama_client_factory=FakeClient),
        base_url="http://127.0.0.1",
    )
    response = client.post(
        "/api/ghostwriter/generate",
        json={
            "backend": "cloud",
            "system_instruction": "Escribe en español.",
            "user_query": "Escribe una newsletter extensa.",
        },
    )

    assert response.status_code == 200
    assert len(response.json()["text"]) > 24000


def test_ghostwriter_config_reports_local_and_cloud_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GHOSTWRITER_OLLAMA_MODEL", "qwen2.5:1.5b")
    monkeypatch.setenv(
        "GHOSTWRITER_OLLAMA_CLOUD_MODEL",
        "gemma4:31b-cloud",
    )
    monkeypatch.setenv("GHOSTWRITER_AUTHOR_NAME", "Autora local")
    monkeypatch.setenv("GHOSTWRITER_AUTHOR_HANDLE", "@autora")
    monkeypatch.setenv("GHOSTWRITER_PUBLICATION_NAME", "Mi newsletter")
    monkeypatch.setenv("GHOSTWRITER_DEFAULT_TAGS", "#tech #ia")
    client = TestClient(create_app(), base_url="http://127.0.0.1")

    response = client.get("/api/ghostwriter/config")

    assert response.status_code == 200
    assert response.json() == {
        "local_model": "qwen2.5:1.5b",
        "cloud_model": "gemma4:31b-cloud",
        "author_name": "Autora local",
        "author_handle": "@autora",
        "publication_name": "Mi newsletter",
        "default_tags": "#tech #ia",
    }


def test_reddit_feed_returns_safe_previews_and_caches_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Feed de prueba</title>
      <updated>2026-10-04T03:00:00+00:00</updated>
      <entry>
        <id>t3_test</id>
        <title>Una noticia &amp; una idea</title>
        <link href="https://www.reddit.com/r/technology/comments/test/"/>
        <author><name>/u/tester</name></author>
        <category term="technology"/>
        <published>2026-10-04T02:00:00+00:00</published>
        <content type="html">&lt;div class="md"&gt;&lt;p&gt;Un resumen &lt;strong&gt;útil&lt;/strong&gt;.&lt;/p&gt;&lt;script&gt;alert(1)&lt;/script&gt;&lt;/div&gt;</content>
      </entry>
      <entry>
        <id>unsafe</id>
        <title>Enlace no permitido</title>
        <link href="https://example.com/outside"/>
      </entry>
      <entry>
        <id>t3_link</id>
        <title>Noticia compartida</title>
        <link href="https://www.reddit.com/r/technology/comments/link/"/>
        <category term="technology"/>
        <content type="html">&lt;table&gt;&lt;tr&gt;&lt;td&gt;submitted by /u/tester to r/technology [link] [comments]&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;</content>
      </entry>
    </feed>""".encode("utf-8")
    requests_seen: list[dict[str, Any]] = []

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: Any,
        ) -> None:
            del exc_type, exc_value, traceback

        def iter_content(self, chunk_size: int) -> list[bytes]:
            del chunk_size
            return [feed_xml]

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        requests_seen.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(webapps.requests, "get", fake_get)
    webapps._REDDIT_FEED_CACHE.clear()
    client = TestClient(create_app(), base_url="http://127.0.0.1")

    response = client.get("/api/ghostwriter/reddit-feed")
    cached_response = client.get("/api/ghostwriter/reddit-feed")
    refreshed_response = client.get(
        "/api/ghostwriter/reddit-feed?refresh=true",
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Feed de prueba"
    assert len(response.json()["items"]) == 2
    item = response.json()["items"][0]
    assert item["title"] == "Una noticia & una idea"
    assert item["subreddit"] == "technology"
    assert item["author"] == "/u/tester"
    assert item["excerpt"] == "Un resumen útil."
    assert "alert" not in item["excerpt"]
    assert response.json()["items"][1]["excerpt"] == (
        "Enlace compartido en r/technology. "
        "Abre la publicación para consultar el artículo."
    )
    assert "submitted by" not in response.json()["items"][1]["excerpt"]
    assert cached_response.json() == response.json()
    assert refreshed_response.status_code == 200
    assert len(requests_seen) == 2
    assert requests_seen[0]["url"] == webapps._REDDIT_FEED_URL
    assert requests_seen[0]["allow_redirects"] is False
    assert "BlackBelt" in requests_seen[0]["headers"]["User-Agent"]


def test_reddit_feed_reports_failure_without_cached_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webapps._REDDIT_FEED_CACHE.clear()

    def failed_get(url: str, **kwargs: Any) -> None:
        del url, kwargs
        raise webapps.requests.Timeout("Reddit timeout")

    monkeypatch.setattr(webapps.requests, "get", failed_get)
    client = TestClient(create_app(), base_url="http://127.0.0.1")

    response = client.get("/api/ghostwriter/reddit-feed")

    assert response.status_code == 503
    assert "feed de Reddit" in response.json()["detail"]


def test_reddit_feed_serves_stale_cache_when_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = webapps.RedditFeedResponse(
        title="Feed en caché",
        updated="2026-10-04T03:00:00+00:00",
        items=[],
    )
    webapps._REDDIT_FEED_CACHE.update(
        response=cached,
        expires_at=0.0,
    )

    def failed_get(url: str, **kwargs: Any) -> None:
        del url, kwargs
        raise webapps.requests.Timeout("Reddit timeout")

    monkeypatch.setattr(webapps.requests, "get", failed_get)
    client = TestClient(create_app(), base_url="http://127.0.0.1")

    response = client.get("/api/ghostwriter/reddit-feed?refresh=true")

    assert response.status_code == 200
    assert response.json()["title"] == "Feed en caché"


def test_cloud_model_cannot_be_used_without_cloud_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "GHOSTWRITER_OLLAMA_MODEL",
        "gpt-oss:120b-cloud",
    )
    client = TestClient(create_app(), base_url="http://127.0.0.1")

    response = client.post(
        "/api/ghostwriter/generate",
        json={
            "system_instruction": "Sistema",
            "user_query": "Consulta",
        },
    )

    assert response.status_code == 500
    assert "Selecciona Ollama Cloud" in response.json()["detail"]


def test_generation_supports_ollama_sdk_response_object() -> None:
    class Message:
        content = "Respuesta del SDK"

    class Response:
        message = Message()

    class FakeClient:
        def chat(self, **kwargs: Any) -> Response:
            return Response()

    client = TestClient(
        create_app(ollama_client_factory=FakeClient),
        base_url="http://127.0.0.1",
    )

    response = client.post(
        "/api/ghostwriter/generate",
        json={"system_instruction": "Sistema", "user_query": "Consulta"},
    )

    assert response.status_code == 200
    assert response.json() == {"text": "Respuesta del SDK"}


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        (
            {
                "system_instruction": "s" * 16001,
                "user_query": "Consulta",
            },
            422,
        ),
        (
            {
                "system_instruction": "Sistema",
                "user_query": "q" * 32001,
            },
            422,
        ),
        (
            {
                "system_instruction": "Sistema",
                "user_query": "Consulta",
                "unexpected": True,
            },
            422,
        ),
    ],
)
def test_generation_validates_input(
    payload: dict[str, Any],
    expected_status: int,
) -> None:
    client = TestClient(create_app(), base_url="http://127.0.0.1")

    response = client.post("/api/ghostwriter/generate", json=payload)

    assert response.status_code == expected_status


def test_empty_ollama_response_returns_bad_gateway() -> None:
    class FakeClient:
        def chat(self, **kwargs: Any) -> dict[str, Any]:
            return {"message": {"content": "  "}}

    client = TestClient(
        create_app(ollama_client_factory=FakeClient),
        base_url="http://127.0.0.1",
    )

    response = client.post(
        "/api/ghostwriter/generate",
        json={"system_instruction": "Sistema", "user_query": "Consulta"},
    )

    assert response.status_code == 502
    assert "vacía" in response.json()["detail"]


def test_ollama_transport_error_returns_service_unavailable() -> None:
    class FakeClient:
        def chat(self, **kwargs: Any) -> None:
            raise TimeoutError("local request timed out")

    client = TestClient(
        create_app(ollama_client_factory=FakeClient),
        base_url="http://127.0.0.1",
    )

    response = client.post(
        "/api/ghostwriter/generate",
        json={"system_instruction": "Sistema", "user_query": "Consulta"},
    )

    assert response.status_code == 503
    assert "Ollama" in response.json()["detail"]


@pytest.mark.parametrize(
    ("error_message", "expected_detail"),
    [
        (
            "llama-server reported out-of-memory: failed to allocate KV cache",
            "Reduce GHOSTWRITER_NUM_CTX",
        ),
        (
            "model 'qwen2.5:3b' not found",
            "ollama pull qwen2.5:3b",
        ),
        (
            "prediction aborted, token repeat limit reached",
            "fuente más breve",
        ),
    ],
)
def test_ollama_response_errors_include_actionable_diagnostics(
    error_message: str,
    expected_detail: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ollama

    monkeypatch.setenv("GHOSTWRITER_OLLAMA_MODEL", "qwen2.5:3b")

    class FakeClient:
        def chat(self, **kwargs: Any) -> None:
            raise ollama.ResponseError(error_message, status_code=500)

    client = TestClient(
        create_app(ollama_client_factory=FakeClient),
        base_url="http://127.0.0.1",
    )
    response = client.post(
        "/api/ghostwriter/generate",
        json={"system_instruction": "Sistema", "user_query": "Consulta"},
    )

    assert response.status_code == 503
    assert expected_detail in response.json()["detail"]


@pytest.mark.parametrize(
    ("error_message", "status_code", "expected_detail"),
    [
        ("unauthorized: please sign in", 401, "ollama signin"),
        ("cloud request rejected", 429, "límite de uso"),
    ],
)
def test_cloud_errors_explain_authentication_and_quota(
    error_message: str,
    status_code: int,
    expected_detail: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ollama

    monkeypatch.setenv(
        "GHOSTWRITER_OLLAMA_CLOUD_MODEL",
        "gpt-oss:120b-cloud",
    )

    class FakeClient:
        def chat(self, **kwargs: Any) -> None:
            raise ollama.ResponseError(error_message, status_code=status_code)

    client = TestClient(
        create_app(ollama_client_factory=FakeClient),
        base_url="http://127.0.0.1",
    )
    response = client.post(
        "/api/ghostwriter/generate",
        json={
            "backend": "cloud",
            "system_instruction": "Sistema",
            "user_query": "Consulta",
        },
    )

    assert response.status_code == 503
    assert expected_detail in response.json()["detail"]


def test_importing_and_health_check_do_not_create_ollama_client() -> None:
    def unexpected_factory() -> Any:
        raise AssertionError("Ollama client must be lazy")

    app = create_app(ollama_client_factory=unexpected_factory)
    client = TestClient(app, base_url="http://127.0.0.1")

    assert client.get("/healthz").status_code == 200


def test_chunked_request_without_content_length_is_limited() -> None:
    app = create_app()
    request_messages = [
        {
            "type": "http.request",
            "body": b"x" * (40 * 1024),
            "more_body": True,
        },
        {
            "type": "http.request",
            "body": b"x" * (30 * 1024),
            "more_body": True,
        },
    ]
    sent_messages: list[dict[str, Any]] = []
    message_index = 0

    async def receive() -> dict[str, Any]:
        nonlocal message_index
        message = request_messages[message_index]
        message_index += 1
        return message

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/ghostwriter/generate",
        "raw_path": b"/api/ghostwriter/generate",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"127.0.0.1"),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8765),
    }

    asyncio.run(app(scope, receive, send))

    status = next(
        message["status"]
        for message in sent_messages
        if message["type"] == "http.response.start"
    )
    body = b"".join(
        message.get("body", b"")
        for message in sent_messages
        if message["type"] == "http.response.body"
    )
    assert status == 413
    assert b"supera el tama" in body
