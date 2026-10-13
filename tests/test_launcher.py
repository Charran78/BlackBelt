"""Tests for the isolated BlackBelt launcher service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from blackbelt.launcher_indexing import (
    CompanionIndexJobManager,
    WorkIndexJobManager,
)
from blackbelt.launcher_web import create_launcher_app
from blackbelt.tools import apps

_ORIGIN = "http://127.0.0.1:8767"


def test_launcher_uses_its_dedicated_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BLACKBELT_LAUNCHER_PORT", raising=False)
    monkeypatch.setenv("BLACKBELT_WEBAPPS_PORT", "9123")

    assert apps._port_from_environment("launcher") == 8767
    assert apps._application_url("launcher") == f"{_ORIGIN}/launcher/"

    monkeypatch.setenv("BLACKBELT_LAUNCHER_PORT", "9777")
    assert apps._port_from_environment("launcher") == 9777


def test_launcher_serves_pwa_assets_and_security_headers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BLACKBELT_LAUNCHER_PORT", "8767")
    app = create_launcher_app(project_root=tmp_path)

    with TestClient(app, base_url=_ORIGIN) as client:
        page = client.get("/launcher/")
        stylesheet = client.get("/launcher/styles.css")
        service_worker = client.get("/launcher/launcher-sw.js")

    assert page.status_code == 200
    assert "Content-Security-Policy" in page.headers
    assert stylesheet.status_code == 200
    assert "Indexación de Companion" in page.text
    assert "Indexación de Search" in page.text
    assert service_worker.status_code == 200
    assert "blackbelt-launcher-shell-v3" in service_worker.text
    assert "/api/" not in service_worker.text


def test_index_requires_origin_and_explicit_authorization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BLACKBELT_LAUNCHER_PORT", "8767")
    calls: list[str] = []

    def fake_start(manager: CompanionIndexJobManager) -> dict[str, Any]:
        calls.append("started")
        return {"state": "running"}

    monkeypatch.setattr(CompanionIndexJobManager, "start", fake_start)
    app = create_launcher_app(project_root=tmp_path)

    with TestClient(app, base_url=_ORIGIN) as client:
        no_origin = client.post(
            "/api/launcher/index",
            json={"confirmed": True},
        )
        no_consent = client.post(
            "/api/launcher/index",
            headers={"Origin": _ORIGIN},
            json={"confirmed": False},
        )
        authorized = client.post(
            "/api/launcher/index",
            headers={"Origin": _ORIGIN},
            json={"confirmed": True},
        )

    assert no_origin.status_code == 403
    assert no_consent.status_code == 400
    assert authorized.status_code == 200
    assert authorized.json() == {"state": "running"}
    assert calls == ["started"]


def test_work_index_has_separate_status_and_requires_authorization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BLACKBELT_LAUNCHER_PORT", "8767")
    app = create_launcher_app(project_root=tmp_path)

    with TestClient(app, base_url=_ORIGIN) as client:
        status = client.get("/api/launcher/index/work")
        denied = client.post(
            "/api/launcher/index/work",
            headers={"Origin": _ORIGIN},
            json={"confirmed": False},
        )
        no_origin = client.post(
            "/api/launcher/index/work",
            json={"confirmed": True},
        )

    assert status.status_code == 200
    assert status.json()["state"] == "idle"
    assert status.json()["job_kind"] == "work"
    assert status.json()["configuration"]["source_dirs"]
    assert denied.status_code == 400
    assert no_origin.status_code == 403


def test_work_index_start_uses_its_own_job_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BLACKBELT_LAUNCHER_PORT", "8767")
    calls: list[str] = []

    def fake_start(manager: WorkIndexJobManager) -> dict[str, Any]:
        calls.append(manager.job_kind)
        return {"state": "running", "job_kind": manager.job_kind}

    monkeypatch.setattr(WorkIndexJobManager, "start", fake_start)
    app = create_launcher_app(project_root=tmp_path)

    with TestClient(app, base_url=_ORIGIN) as client:
        response = client.post(
            "/api/launcher/index/work",
            headers={"Origin": _ORIGIN},
            json={"confirmed": True},
        )

    assert response.status_code == 200
    assert response.json() == {"state": "running", "job_kind": "work"}
    assert calls == ["work"]


def test_launcher_rejects_untrusted_terminal_origins(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BLACKBELT_LAUNCHER_PORT", "8767")
    app = create_launcher_app(project_root=tmp_path)

    with (
        TestClient(app, base_url=_ORIGIN) as client,
        pytest.raises(WebSocketDenialResponse) as denied,
        client.websocket_connect(
            "/api/launcher/terminal?token=invalid",
            headers={"Origin": "https://attacker.invalid"},
        ),
    ):
        pass

    assert denied.value.status_code == 400


def test_launcher_rejects_terminal_without_a_valid_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BLACKBELT_LAUNCHER_PORT", "8767")
    app = create_launcher_app(project_root=tmp_path)

    with (
        TestClient(app, base_url=_ORIGIN) as client,
        pytest.raises(WebSocketDenialResponse) as denied,
        client.websocket_connect(
            "/api/launcher/terminal?token=invalid",
            headers={"Origin": _ORIGIN},
        ),
    ):
        pass

    assert denied.value.status_code == 400
