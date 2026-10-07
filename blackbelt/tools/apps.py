"""Launch local BlackBelt web applications and manage desktop starts."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psutil
import requests
import uvicorn
from dotenv import load_dotenv
from rich.console import Console

from blackbelt.webapps import create_app

console = Console()
LOOPBACK_HOST = "127.0.0.1"
_APPLICATIONS = ("soma", "ghostwriter", "meetings", "plan")
_CHILD_ARGUMENT = "--blackbelt-webapps-child="
_DESKTOP_ARGUMENT = "--blackbelt-desktop-launch="
_START_TIMEOUT_SECONDS = 20.0
_START_POLL_INTERVAL_SECONDS = 0.25


class _BrowserOpeningServer(uvicorn.Server):
    browser_url: str

    async def startup(self, sockets: Any = None) -> None:
        await super().startup(sockets=sockets)
        if self.started:
            webbrowser.open(self.browser_url)


def run(args: Sequence[str] | str | None = None) -> None:
    arguments = args.split() if isinstance(args, str) else list(args or [])
    parser = argparse.ArgumentParser(
        prog="blackbelt run apps",
        description="Abre y gestiona aplicaciones locales de BlackBelt.",
    )
    parser.add_argument(
        "application",
        nargs="?",
        choices=_APPLICATIONS,
        help="Aplicación web que se abrirá.",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Reutiliza o inicia el servidor en segundo plano.",
    )
    parser.add_argument(
        "--install-shortcut",
        action="store_true",
        help="Crea un acceso directo de escritorio para Ghost Writer.",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Detiene servidores de BlackBelt iniciados en segundo plano.",
    )
    parsed = parser.parse_args(arguments)

    _load_configuration()
    if parsed.stop:
        stopped = _stop_background_servers()
        console.print(
            "[green]Servidores gestionados detenidos.[/]"
            if stopped
            else "[dim]No había servidores gestionados activos.[/]"
        )
        return
    if parsed.application is None:
        parser.error(
            "indica soma, ghostwriter, meetings, plan o usa --stop."
        )
    if parsed.install_shortcut:
        if parsed.application != "ghostwriter":
            parser.error(
                "--install-shortcut solo está disponible para ghostwriter."
            )
        _install_desktop_shortcut()
        console.print(
            "[green]Acceso directo de Ghost Writer creado en el escritorio.[/]"
        )
        return

    page_path = parsed.application
    url = _application_url(page_path)
    port = _port_from_environment(page_path)
    if _blackbelt_server_is_ready(port):
        if _application_is_ready(port, page_path):
            _open_application(page_path, url)
            console.print(
                f"[green]Servidor ya activo; {page_path} abierto en {url}.[/]"
            )
            return
        console.print(
            "[red]Hay un servidor BlackBelt activo que no incluye esta app. "
            "Reinicia la instancia que ocupa el puerto configurado y vuelve "
            "a intentarlo.[/]"
        )
        return
    if parsed.background:
        started = _ensure_background_server(page_path)
        _open_application(page_path, url)
        message = "Servidor iniciado" if started else "Servidor ya activo"
        console.print(f"[green]{message}; {page_path} abierto en {url}.[/]")
        console.print(
            "[dim]Para detener los servidores gestionados: "
            "blackbelt run apps --stop[/]"
        )
        return

    application = create_app(default_page=page_path)
    server = _BrowserOpeningServer(
        uvicorn.Config(
            application,
            host=LOOPBACK_HOST,
            port=_port_from_environment(page_path),
            log_level="info",
            access_log=False,
        )
    )
    server.browser_url = url
    console.print(f"[green]Abriendo {page_path} en {url}[/]")
    console.print("[dim]El servidor se detiene con Ctrl+C.[/]")
    server.run()


def _module_main() -> None:
    arguments = sys.argv[1:]
    if arguments and arguments[0].startswith(_CHILD_ARGUMENT):
        application = arguments[0][len(_CHILD_ARGUMENT):]
        _run_server_child(application)
        return
    if arguments and arguments[0].startswith(_DESKTOP_ARGUMENT):
        application = arguments[0][len(_DESKTOP_ARGUMENT):]
        try:
            _load_configuration()
            _ensure_background_server(application)
            _open_application(application, _application_url(application))
        except (OSError, RuntimeError, ValueError) as exc:
            _show_desktop_error(str(exc))
        return
    run(arguments)


def _load_configuration() -> None:
    load_dotenv()
    personal_config = Path.cwd() / ".env.personalizaciones"
    if personal_config.is_file():
        load_dotenv(personal_config, override=True)


def _application_url(application: str) -> str:
    return (
        f"http://{LOOPBACK_HOST}:{_port_from_environment(application)}/"
        f"{application}/"
    )


def _ensure_background_server(application: str) -> bool:
    if application not in _APPLICATIONS:
        raise ValueError("La aplicación solicitada no está soportada.")
    port = _port_from_environment(application)
    if _blackbelt_server_is_ready(port):
        if _application_is_ready(port, application):
            return False
        raise RuntimeError(
            "Hay un servidor BlackBelt activo en el puerto configurado, pero "
            f"no sirve {application}. Reinicia esa instancia antes de volver "
            "a intentarlo; BlackBelt no detendrá procesos ajenos."
        )

    state_directory = _state_directory()
    state_directory.mkdir(parents=True, exist_ok=True)
    log_path = state_directory / f"{application}.log"
    _rotate_log_if_needed(log_path)
    child_argument = f"{_CHILD_ARGUMENT}{application}"
    command = [
        sys.executable,
        "-m",
        "blackbelt.tools.apps",
        child_argument,
    ]
    creation_flags = 0
    popen_options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        creation_flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        popen_options["creationflags"] = creation_flags
    else:
        popen_options["start_new_session"] = True

    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            **popen_options,
        )

    pid_path = _pid_path(application)
    _write_pid_record(pid_path, process.pid, application)
    deadline = time.monotonic() + _START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _remove_pid_record(pid_path)
            detail = _log_tail(log_path)
            raise RuntimeError(
                "BlackBelt no pudo iniciar el servidor. "
                f"Consulta el registro local: {log_path}"
                + (f"\n{detail}" if detail else "")
            )
        if (
            _blackbelt_server_is_ready(port)
            and _application_is_ready(port, application)
        ):
            return True
        time.sleep(_START_POLL_INTERVAL_SECONDS)

    if (
        _blackbelt_server_is_ready(port)
        and _application_is_ready(port, application)
    ):
        return True
    if process.poll() is None:
        raise RuntimeError(
            "El servidor de BlackBelt no respondió dentro del tiempo esperado. "
            f"Consulta el registro local: {log_path}"
        )
    _remove_pid_record(pid_path)
    raise RuntimeError(
        "El puerto configurado está ocupado o el servidor no pudo arrancar. "
        f"Consulta el registro local: {log_path}"
    )


def _run_server_child(application: str) -> None:
    if application not in _APPLICATIONS:
        raise SystemExit("Aplicación interna no válida.")
    _load_configuration()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(default_page=application),
            host=LOOPBACK_HOST,
            port=_port_from_environment(application),
            log_level="info",
            access_log=False,
        )
    )
    server.run()


def _blackbelt_server_is_ready(port: int) -> bool:
    try:
        response = requests.get(
            f"http://{LOOPBACK_HOST}:{port}/healthz",
            timeout=0.5,
        )
        return (
            response.status_code == 200
            and response.json() == {"status": "ok"}
        )
    except (requests.RequestException, ValueError):
        return False


def _application_is_ready(port: int, application: str) -> bool:
    if application not in _APPLICATIONS:
        return False
    try:
        response = requests.get(
            f"http://{LOOPBACK_HOST}:{port}/{application}/manifest.webmanifest",
            timeout=0.5,
        )
        return response.status_code == 200
    except requests.RequestException:
        return False


def _open_application(application: str, url: str) -> None:
    shortcut = _find_installed_pwa_shortcut(application)
    start_file = getattr(os, "startfile", None)
    if shortcut is not None and start_file is not None:
        try:
            start_file(str(shortcut))
            return
        except OSError:
            pass
    webbrowser.open(url)


def _find_installed_pwa_shortcut(application: str) -> Path | None:
    shortcut_names = {
        "ghostwriter": ("ghost writer",),
        "meetings": ("reuniones", "blackbelt meetings"),
    }
    target_names = shortcut_names.get(application)
    if target_names is None or os.name != "nt":
        return None
    preferred_name = {
        "ghostwriter": "ghost writer local",
        "meetings": "blackbelt reuniones",
    }[application]
    program_directories = [
        Path(os.getenv("APPDATA", Path.home() / "AppData/Roaming"))
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs",
        Path(os.getenv("ProgramData", "C:/ProgramData"))
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs",
    ]
    candidates: list[Path] = []
    for directory in program_directories:
        if not directory.is_dir():
            continue
        try:
            candidates.extend(
                path
                for path in directory.rglob("*.lnk")
                if any(
                    target_name in path.stem.casefold()
                    for target_name in target_names
                )
            )
        except OSError:
            continue
    return min(
        candidates,
        key=lambda path: (
            preferred_name not in path.stem.casefold(),
            str(path).casefold(),
        ),
        default=None,
    )


def _stop_background_servers() -> bool:
    stopped_any = False
    for application in _APPLICATIONS:
        pid_path = _pid_path(application)
        if not pid_path.is_file():
            continue
        try:
            record = json.loads(pid_path.read_text(encoding="utf-8"))
            process_id = int(record["pid"])
            recorded_application = record["application"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            _remove_pid_record(pid_path)
            continue
        if recorded_application != application:
            _remove_pid_record(pid_path)
            continue
        try:
            process = psutil.Process(process_id)
            command_line = process.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            _remove_pid_record(pid_path)
            continue
        if f"{_CHILD_ARGUMENT}{application}" not in command_line:
            _remove_pid_record(pid_path)
            continue
        try:
            process.terminate()
            process.wait(timeout=5)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            _remove_pid_record(pid_path)
            continue
        _remove_pid_record(pid_path)
        stopped_any = True
    return stopped_any


def _state_directory() -> Path:
    if os.name == "nt":
        base_directory = Path(
            os.getenv(
                "LOCALAPPDATA",
                Path.home() / "AppData/Local",
            )
        )
    else:
        base_directory = Path(
            os.getenv(
                "XDG_STATE_HOME",
                Path.home() / ".local" / "state",
            )
        )
    return base_directory / "BlackBelt" / "webapps"


def _pid_path(application: str) -> Path:
    return _state_directory() / f"{application}.pid"


def _write_pid_record(
    pid_path: Path,
    process_id: int,
    application: str,
) -> None:
    temporary_path = pid_path.with_suffix(".pid.tmp")
    temporary_path.write_text(
        json.dumps({"pid": process_id, "application": application}),
        encoding="utf-8",
    )
    temporary_path.replace(pid_path)


def _remove_pid_record(pid_path: Path) -> None:
    try:
        pid_path.unlink(missing_ok=True)
    except OSError:
        pass


def _rotate_log_if_needed(log_path: Path) -> None:
    if not log_path.exists() or log_path.stat().st_size < 1_000_000:
        return
    previous_log = log_path.with_suffix(".log.1")
    previous_log.unlink(missing_ok=True)
    log_path.replace(previous_log)


def _log_tail(log_path: Path) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-12:])


def _install_desktop_shortcut() -> None:
    if os.name != "nt":
        raise RuntimeError(
            "La creación automática del acceso directo requiere Windows."
        )
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise RuntimeError(
            "No se encontró pythonw.exe junto al intérprete actual. "
            "Activa el entorno Python de BlackBelt y vuelve a intentarlo."
        )
    script = f"""
$desktop = [Environment]::GetFolderPath('Desktop')
$linkPath = Join-Path $desktop 'Ghost Writer BlackBelt.lnk'
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath = '{_powershell_quote(str(pythonw.resolve()))}'
$link.Arguments = '-m blackbelt.tools.apps {_DESKTOP_ARGUMENT}ghostwriter'
$link.WorkingDirectory = '{_powershell_quote(str(Path.cwd().resolve()))}'
$link.Description = 'Inicia BlackBelt y abre Ghost Writer como aplicación'
$link.WindowStyle = 7
$programs = @(
  (Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs'),
  (Join-Path $env:ProgramData 'Microsoft\\Windows\\Start Menu\\Programs')
)
$pwa = Get-ChildItem $programs -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue |
  Where-Object {{ $_.BaseName -like '*Ghost*Writer*' }} |
  Select-Object -First 1
if ($pwa) {{
  $installed = $shell.CreateShortcut($pwa.FullName)
  if ($installed.IconLocation) {{ $link.IconLocation = $installed.IconLocation }}
}}
$link.Save()
"""
    encoded_script = base64.b64encode(
        script.encode("utf-16le")
    ).decode("ascii")
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded_script,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode:
        raise RuntimeError(
            "Windows no pudo crear el acceso directo."
            + (f" {result.stderr.strip()}" if result.stderr.strip() else "")
        )


def _powershell_quote(value: str) -> str:
    return value.replace("'", "''")


def _show_desktop_error(message: str) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                message,
                "BlackBelt - Ghost Writer",
                0x10,
            )
            return
        except (AttributeError, OSError):
            pass
    console.print(f"[red]{message}[/]")


def _port_from_environment(application: str | None = None) -> int:
    setting = (
        "BLACKBELT_MEETINGS_PORT"
        if application == "meetings"
        else "BLACKBELT_WEBAPPS_PORT"
    )
    raw_value = os.getenv(setting)
    if raw_value is None and application == "meetings":
        raw_value = os.getenv("BLACKBELT_WEBAPPS_PORT")
        if raw_value is not None:
            setting = "BLACKBELT_WEBAPPS_PORT"
    if raw_value is None:
        raw_value = "8766" if application == "meetings" else "8765"
    try:
        port = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{setting} debe ser un entero.") from exc
    if not 1024 <= port <= 65535:
        raise ValueError(
            f"{setting} debe estar entre 1024 y 65535."
        )
    return port


if __name__ == "__main__":
    _module_main()
