"""Tests for local launcher catalog, status, and Ollama controls."""

from __future__ import annotations

from typing import Any, Self

import pytest

from blackbelt import launcher_service


def test_catalog_contains_only_explicit_apps_and_activatable_tools() -> None:
    catalog = launcher_service.get_catalog()

    assert {app["id"] for app in catalog["applications"]} == {
        "soma",
        "ghostwriter",
        "meetings",
        "plan",
        "companion",
    }
    assert {tool["id"] for tool in catalog["tools"]} >= {
        "email",
        "search",
        "global-search",
        "search-index",
        "windows-mentor",
        "linux-mentor",
        "programming-mentor",
    }
    assert all("command" in tool for tool in catalog["tools"])
    global_search = next(
        tool for tool in catalog["tools"] if tool["id"] == "global-search"
    )
    assert '--scope all "consulta"' in global_search["command"]


def test_launcher_only_accepts_loopback_ollama_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "https://ollama.example:11434")
    assert launcher_service._local_ollama_base_url() is None

    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11435")
    assert launcher_service._local_ollama_base_url() == "http://127.0.0.1:11435"


def test_ollama_status_disables_proxy_and_filters_model_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "models": [
                    {"name": "gemma4:e2b"},
                    {"name": 12},
                    "invalid-entry",
                ],
            }

    class FakeSession:
        trust_env = True

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str, *, timeout: float) -> FakeResponse:
            observed.update(url=url, timeout=timeout, trust_env=self.trust_env)
            return FakeResponse()

    monkeypatch.setattr(
        launcher_service,
        "_local_ollama_base_url",
        lambda: "http://127.0.0.1:11434",
    )
    monkeypatch.setattr(
        launcher_service.requests,
        "Session",
        FakeSession,
    )

    status = launcher_service.get_ollama_status()

    assert status["available"] is True
    assert status["models"] == ["gemma4:e2b"]
    assert observed["trust_env"] is False
    assert observed["url"] == "http://127.0.0.1:11434/api/tags"
    assert observed["timeout"] <= 1


def test_start_ollama_does_not_spawn_a_second_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        launcher_service,
        "get_ollama_status",
        lambda: {
            "available": True,
            "local": True,
            "model_count": 0,
            "models": [],
            "detail": "ya disponible",
        },
    )
    monkeypatch.setattr(
        launcher_service.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("No debe iniciar otro proceso."),
    )

    result = launcher_service.start_ollama()

    assert result["started"] is False
    assert result["detail"] == "Ollama ya estaba disponible."


def test_launch_only_starts_registered_web_applications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "blackbelt.tools.apps._ensure_background_server",
        lambda application_id: application_id == "meetings",
    )
    monkeypatch.setattr(
        "blackbelt.tools.apps._application_url",
        lambda application_id: f"http://127.0.0.1/{application_id}/",
    )

    result = launcher_service.launch_application("meetings")

    assert result == {
        "application_id": "meetings",
        "url": "http://127.0.0.1/meetings/",
        "started": True,
    }
    with pytest.raises(ValueError, match="no está registrada"):
        launcher_service.launch_application("arbitrary-command")
