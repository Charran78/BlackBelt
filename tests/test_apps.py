"""Tests for the local web application launcher."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from blackbelt.tools import apps


def test_launcher_binds_loopback_and_uses_configured_port(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".env.personalizaciones").write_text(
        "GHOSTWRITER_AUTHOR_NAME=Prueba\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    observed: dict[str, Any] = {}
    dotenv_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_load_dotenv(*args: Any, **kwargs: Any) -> bool:
        dotenv_calls.append((args, kwargs))
        return True

    class FakeServer:
        def __init__(self, config: Any) -> None:
            observed["config"] = config

        def run(self) -> None:
            observed["ran"] = True

    monkeypatch.setenv("BLACKBELT_WEBAPPS_PORT", "9123")
    monkeypatch.setattr(
        apps,
        "load_dotenv",
        fake_load_dotenv,
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
    assert len(dotenv_calls) == 2
    assert dotenv_calls[1][0][0].name == ".env.personalizaciones"
    assert dotenv_calls[1][1] == {"override": True}
    assert observed["ran"] is True


@pytest.mark.parametrize("port", ["0", "1023", "65536", "not-a-port"])
def test_launcher_rejects_invalid_port(
    monkeypatch: pytest.MonkeyPatch,
    port: str,
) -> None:
    monkeypatch.setenv("BLACKBELT_WEBAPPS_PORT", port)

    with pytest.raises(ValueError, match="BLACKBELT_WEBAPPS_PORT"):
        apps._port_from_environment()


@pytest.mark.parametrize("port", ["0", "1023", "65536", "not-a-port"])
def test_meetings_launcher_rejects_invalid_specific_port(
    monkeypatch: pytest.MonkeyPatch,
    port: str,
) -> None:
    monkeypatch.setenv("BLACKBELT_MEETINGS_PORT", port)

    with pytest.raises(ValueError, match="BLACKBELT_MEETINGS_PORT"):
        apps._port_from_environment("meetings")


def test_launcher_rejects_unknown_application() -> None:
    with pytest.raises(SystemExit):
        apps.run(["unknown"])


def test_launcher_opens_meeting_assistant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    class FakeServer:
        def __init__(self, config: Any) -> None:
            observed["config"] = config

        def run(self) -> None:
            observed["ran"] = True

    monkeypatch.setattr(apps, "_load_configuration", lambda: None)
    monkeypatch.setattr(apps, "create_app", lambda **kwargs: kwargs)
    monkeypatch.setattr(apps, "_BrowserOpeningServer", FakeServer)
    monkeypatch.setattr(apps, "_blackbelt_server_is_ready", lambda port: False)
    monkeypatch.setenv("BLACKBELT_WEBAPPS_PORT", "9123")

    apps.run(["meetings"])

    assert observed["config"].app == {"default_page": "meetings"}
    assert observed["config"].port == 9123
    assert observed["ran"] is True


def test_launcher_opens_plan_pwa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    class FakeServer:
        def __init__(self, config: Any) -> None:
            observed["config"] = config

        def run(self) -> None:
            observed["ran"] = True

    monkeypatch.setattr(apps, "_load_configuration", lambda: None)
    monkeypatch.setattr(apps, "create_app", lambda **kwargs: kwargs)
    monkeypatch.setattr(apps, "_BrowserOpeningServer", FakeServer)
    monkeypatch.setattr(apps, "_blackbelt_server_is_ready", lambda port: False)
    monkeypatch.setenv("BLACKBELT_WEBAPPS_PORT", "9125")

    apps.run(["plan"])

    assert observed["config"].app == {"default_page": "plan"}
    assert observed["config"].port == 9125
    assert observed["ran"] is True


def test_meetings_uses_its_pwa_port_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BLACKBELT_WEBAPPS_PORT", raising=False)
    monkeypatch.delenv("BLACKBELT_MEETINGS_PORT", raising=False)

    assert apps._port_from_environment("meetings") == 8766
    assert apps._port_from_environment("ghostwriter") == 8765
    assert apps._port_from_environment("plan") == 8765


def test_meetings_port_can_be_overridden_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BLACKBELT_WEBAPPS_PORT", "9123")
    monkeypatch.setenv("BLACKBELT_MEETINGS_PORT", "9124")

    assert apps._port_from_environment("meetings") == 9124
    assert apps._port_from_environment("ghostwriter") == 9123


def test_background_meetings_child_binds_to_pwa_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    class FakeServer:
        def __init__(self, config: Any) -> None:
            observed["config"] = config

        def run(self) -> None:
            observed["ran"] = True

    monkeypatch.setattr(apps, "_load_configuration", lambda: None)
    monkeypatch.setattr(apps, "create_app", lambda **kwargs: kwargs)
    monkeypatch.setattr(apps.uvicorn, "Server", FakeServer)
    monkeypatch.delenv("BLACKBELT_WEBAPPS_PORT", raising=False)
    monkeypatch.delenv("BLACKBELT_MEETINGS_PORT", raising=False)

    apps._run_server_child("meetings")

    assert observed["config"].port == 8766
    assert observed["config"].host == "127.0.0.1"
    assert observed["ran"] is True


def test_background_launcher_reuses_existing_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[str, str]] = []
    monkeypatch.setattr(apps, "_load_configuration", lambda: None)
    monkeypatch.setattr(
        apps,
        "_ensure_background_server",
        lambda application: False,
    )
    monkeypatch.setattr(
        apps,
        "_open_application",
        lambda application, url: opened.append((application, url)),
    )
    monkeypatch.setenv("BLACKBELT_WEBAPPS_PORT", "9123")

    apps.run(["ghostwriter", "--background"])

    assert opened == [
        ("ghostwriter", "http://127.0.0.1:9123/ghostwriter/"),
    ]


def test_background_meetings_reuses_server_on_pwa_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}
    monkeypatch.setattr(apps, "_load_configuration", lambda: None)
    monkeypatch.setattr(
        apps,
        "_blackbelt_server_is_ready",
        lambda port: observed.update(port=port) or True,
    )
    monkeypatch.setattr(
        apps,
        "_application_is_ready",
        lambda port, application: observed.update(
            app_port=port,
            application=application,
        )
        or True,
    )
    monkeypatch.setattr(
        apps,
        "_open_application",
        lambda application, url: observed.update(url=url),
    )
    monkeypatch.delenv("BLACKBELT_WEBAPPS_PORT", raising=False)
    monkeypatch.delenv("BLACKBELT_MEETINGS_PORT", raising=False)

    apps.run(["meetings", "--background"])

    assert observed == {
        "port": 8766,
        "app_port": 8766,
        "application": "meetings",
        "url": "http://127.0.0.1:8766/meetings/",
    }


def test_background_launcher_starts_server_and_records_pid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeProcess:
        pid = 4321

        def poll(self) -> None:
            return None

    readiness = iter((False, True))
    captured: dict[str, Any] = {}
    monkeypatch.setattr(apps, "_state_directory", lambda: tmp_path)
    monkeypatch.setattr(
        apps,
        "_blackbelt_server_is_ready",
        lambda port: next(readiness),
    )
    monkeypatch.setattr(
        apps,
        "_application_is_ready",
        lambda port, application: True,
    )
    monkeypatch.setattr(apps.time, "sleep", lambda seconds: None)

    def fake_popen(
        command: list[str],
        **kwargs: Any,
    ) -> FakeProcess:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(apps.subprocess, "Popen", fake_popen)

    assert apps._ensure_background_server("ghostwriter") is True

    assert f"{apps._CHILD_ARGUMENT}ghostwriter" in captured["command"]
    assert captured["kwargs"]["stdin"] == apps.subprocess.DEVNULL
    assert "stdout" in captured["kwargs"]
    assert "creationflags" in captured["kwargs"] or (
        "start_new_session" in captured["kwargs"]
    )
    assert json.loads(
        (tmp_path / "ghostwriter.pid").read_text(encoding="utf-8")
    ) == {"pid": 4321, "application": "ghostwriter"}


def test_background_start_fails_cleanly_if_child_exits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeProcess:
        pid = 9876

        def poll(self) -> int:
            return 1

    monkeypatch.setattr(apps, "_state_directory", lambda: tmp_path)
    monkeypatch.setattr(apps, "_blackbelt_server_is_ready", lambda port: False)
    monkeypatch.setattr(apps.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(apps, "_log_tail", lambda path: "Port already in use.")

    with pytest.raises(RuntimeError, match="Port already in use"):
        apps._ensure_background_server("ghostwriter")

    assert not (tmp_path / "ghostwriter.pid").exists()


def test_background_launcher_rejects_active_server_missing_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(apps, "_blackbelt_server_is_ready", lambda port: True)
    monkeypatch.setattr(
        apps,
        "_application_is_ready",
        lambda port, application: False,
    )
    monkeypatch.setattr(
        apps.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail(
            "No debe iniciar sobre un puerto ya ocupado."
        ),
    )

    with pytest.raises(RuntimeError, match="no sirve meetings"):
        apps._ensure_background_server("meetings")


def test_stop_terminates_only_blackbelt_managed_processes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "ghostwriter.pid"
    pid_path.write_text(
        json.dumps({"pid": 1234, "application": "ghostwriter"}),
        encoding="utf-8",
    )
    observed: dict[str, Any] = {}

    class FakeProcess:
        def cmdline(self) -> list[str]:
            return [
                "python.exe",
                "-m",
                "blackbelt.tools.apps",
                f"{apps._CHILD_ARGUMENT}ghostwriter",
            ]

        def terminate(self) -> None:
            observed["terminated"] = True

        def wait(self, timeout: int) -> None:
            observed["timeout"] = timeout

    monkeypatch.setattr(apps, "_state_directory", lambda: tmp_path)
    monkeypatch.setattr(apps.psutil, "Process", lambda pid: FakeProcess())

    assert apps._stop_background_servers() is True
    assert observed == {"terminated": True, "timeout": 5}
    assert not pid_path.exists()


def test_stop_does_not_terminate_unrelated_reused_pid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "ghostwriter.pid"
    pid_path.write_text(
        json.dumps({"pid": 1234, "application": "ghostwriter"}),
        encoding="utf-8",
    )

    class FakeProcess:
        def cmdline(self) -> list[str]:
            return ["unrelated.exe"]

        def terminate(self) -> None:
            pytest.fail("No debe detenerse un proceso ajeno.")

    monkeypatch.setattr(apps, "_state_directory", lambda: tmp_path)
    monkeypatch.setattr(apps.psutil, "Process", lambda pid: FakeProcess())

    assert apps._stop_background_servers() is False
    assert not pid_path.exists()


def test_finds_installed_chrome_pwa_shortcut(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    start_menu = (
        tmp_path
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Aplicaciones de Chrome"
    )
    start_menu.mkdir(parents=True)
    shortcut = start_menu / "Ghost Writer Local.lnk"
    shortcut.touch()
    monkeypatch.setattr(apps.os, "name", "nt")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "shared"))

    assert apps._find_installed_pwa_shortcut("ghostwriter") == shortcut


def test_finds_installed_meetings_pwa_shortcut(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    start_menu = (
        tmp_path
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Aplicaciones de Chrome"
    )
    start_menu.mkdir(parents=True)
    shortcut = start_menu / "BlackBelt Reuniones.lnk"
    shortcut.touch()
    monkeypatch.setattr(apps.os, "name", "nt")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "shared"))

    assert apps._find_installed_pwa_shortcut("meetings") == shortcut


def test_launches_installed_pwa_shortcut_before_browser_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shortcut = tmp_path / "Ghost Writer Local.lnk"
    observed: dict[str, Any] = {}
    monkeypatch.setattr(
        apps,
        "_find_installed_pwa_shortcut",
        lambda application: shortcut,
    )
    monkeypatch.setattr(
        apps.os,
        "startfile",
        lambda path: observed.setdefault("shortcut", path),
        raising=False,
    )
    monkeypatch.setattr(
        apps.webbrowser,
        "open",
        lambda url: pytest.fail("Debe abrir la PWA instalada."),
    )

    apps._open_application(
        "ghostwriter",
        "http://127.0.0.1:8765/ghostwriter/",
    )

    assert observed["shortcut"] == str(shortcut)


def test_install_shortcut_uses_current_python_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    python = tmp_path / "python.exe"
    pythonw = tmp_path / "pythonw.exe"
    python.write_text("", encoding="utf-8")
    pythonw.write_text("", encoding="utf-8")
    observed: dict[str, Any] = {}
    monkeypatch.setattr(apps.os, "name", "nt")
    monkeypatch.setattr(apps.sys, "executable", str(python))
    monkeypatch.setattr(
        apps.subprocess,
        "run",
        lambda command, **kwargs: observed.update(
            command=command,
            kwargs=kwargs,
        )
        or type("Result", (), {"returncode": 0, "stderr": ""})(),
    )
    monkeypatch.chdir(tmp_path)

    apps._install_desktop_shortcut()

    assert observed["command"][0] == "powershell.exe"
    assert observed["command"][-2] == "-EncodedCommand"
    assert observed["kwargs"]["check"] is False
