"""Tests for the loopback web applications and local Ollama endpoint."""

from __future__ import annotations

import asyncio
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blackbelt.webapps import create_app


def _asset_root(tmp_path: Path) -> Path:
    soma_dir = tmp_path / "soma"
    ghostwriter_dir = tmp_path / "ghostwriter"
    soma_dir.mkdir()
    ghostwriter_dir.mkdir()
    (soma_dir / "somaguard.html").write_text(
        "<title>SOMA test</title>",
        encoding="utf-8",
    )
    (ghostwriter_dir / "ghostwriter_ai_studio.html").write_text(
        "<title>Ghost Writer test</title>",
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

    ghost_worker = client.get("/ghostwriter/ghostwriter-sw.js")
    assert ghost_worker.status_code == 200
    assert ghost_worker.headers["cache-control"] == "no-cache"
    assert "APP_ASSETS" in ghost_worker.text
    assert "requestUrl.origin !== appUrl.origin" in ghost_worker.text


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
    assert not re.search(r"(?i)[A-Z]:\\(?:Users|Documents and Settings)\\", ghostwriter_html)
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
        "radar-news-grid",
        "standalone-aspect-ratio",
        "btn-save-to-vault",
    ):
        assert f'id="{element_id}"' in served_html
    assert served_html.count("<select") == served_html.count("</select>")


def test_packaged_webapps_are_sanitized_and_soma_schema_matches_renderers() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    webapps_root = repository_root / "blackbelt" / "data" / "webapps"
    soma_html = (webapps_root / "soma" / "somaguard.html").read_text(
        encoding="utf-8",
    )
    ghostwriter_html = (
        webapps_root / "ghostwriter" / "ghostwriter_ai_studio.html"
    ).read_text(encoding="utf-8")
    for html in (soma_html, ghostwriter_html):
        assert not re.search(r"(?i)[A-Z]:\\Users\\", html)
        assert not re.search(r"(?i)/home/[^/\\s]+", html)

    assert '"plan_personal"' in soma_html
    assert '"plan_rutinas"' in soma_html
    assert "plan_nutricional_deficit_calorico" not in soma_html
    assert "function registerPainEpisode()" not in soma_html
    analysis = soma_html.split("function runAiAnalysis() {", 1)[1].split(
        "function openPanicModal()",
        1,
    )[0]
    assert "no interpreta tus datos" in analysis
    assert "recomend" not in analysis.casefold()
    assert re.search(
        r'<input[^>]*id="cfg-author-name"[^>]*value=""',
        ghostwriter_html,
    )

    for application, html_name in (
        ("soma", "somaguard.html"),
        ("ghostwriter", "ghostwriter_ai_studio.html"),
    ):
        client = TestClient(create_app(), base_url="http://127.0.0.1")
        response = client.get(f"/{application}/")
        assert response.status_code == 200
        assert response.text == (
            webapps_root / application / html_name
        ).read_text(encoding="utf-8")


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
    client = TestClient(create_app(), base_url="http://127.0.0.1")

    response = client.get("/api/ghostwriter/config")

    assert response.status_code == 200
    assert response.json() == {
        "local_model": "qwen2.5:1.5b",
        "cloud_model": "gemma4:31b-cloud",
    }


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
