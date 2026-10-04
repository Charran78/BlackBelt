"""Tests for the local web application launcher."""

from __future__ import annotations

from typing import Any

import pytest

from blackbelt.tools import apps


def test_launcher_binds_loopback_and_uses_configured_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    class FakeServer:
        def __init__(self, config: Any) -> None:
            observed["config"] = config

        def run(self) -> None:
            observed["ran"] = True

    monkeypatch.setenv("BLACKBELT_WEBAPPS_PORT", "9123")
    monkeypatch.setattr(
        apps,
        "load_dotenv",
        lambda: observed.setdefault("dotenv_loaded", True),
    )
    monkeypatch.setattr(apps, "create_app", lambda **kwargs: kwargs)
    monkeypatch.setattr(apps, "_BrowserOpeningServer", FakeServer)

    apps.run(["ghostwriter"])

    config = observed["config"]
    assert config.host == "127.0.0.1"
    assert config.port == 9123
    assert config.app == {
        "default_page": "ghostwriter",
    }
    assert observed["dotenv_loaded"] is True
    assert observed["ran"] is True


@pytest.mark.parametrize("port", ["0", "1023", "65536", "not-a-port"])
def test_launcher_rejects_invalid_port(
    monkeypatch: pytest.MonkeyPatch,
    port: str,
) -> None:
    monkeypatch.setenv("BLACKBELT_WEBAPPS_PORT", port)

    with pytest.raises(ValueError, match="BLACKBELT_WEBAPPS_PORT"):
        apps._port_from_environment()


def test_launcher_rejects_unknown_application() -> None:
    with pytest.raises(SystemExit):
        apps.run(["unknown"])
