"""Local status and explicit application actions for the BlackBelt launcher."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psutil
import requests

from blackbelt.core import config as cfg

LOGGER = logging.getLogger(__name__)
LOOPBACK_HOST = "127.0.0.1"
_OLLAMA_START_LOCK = threading.Lock()
_OLLAMA_START_TIMEOUT_SECONDS = 12.0
_OLLAMA_POLL_INTERVAL_SECONDS = 0.3
_HTTP_TIMEOUT_SECONDS = 0.6

WEB_APPLICATIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "soma",
        "name": "SOMA",
        "kind": "web",
        "category": "Productividad",
        "description": "Captura local de ideas y notas rápidas.",
        "tags": ["notas", "local"],
    },
    {
        "id": "ghostwriter",
        "name": "Ghost Writer",
        "kind": "web",
        "category": "Escritura",
        "description": "Estudio de escritura con fuentes locales y Cloud opcional.",
        "tags": ["escritura", "rss"],
    },
    {
        "id": "meetings",
        "name": "Reuniones",
        "kind": "web",
        "category": "Clientes",
        "description": "Prepara dossiers locales para reuniones con clientes.",
        "tags": ["clientes", "dossiers"],
    },
    {
        "id": "plan",
        "name": "Plan",
        "kind": "web",
        "category": "Planificación",
        "description": "Prepara y valida borradores de planes de proyecto.",
        "tags": ["proyectos", "planes"],
    },
    {
        "id": "companion",
        "name": "Compañero",
        "kind": "web",
        "category": "Conocimiento",
        "description": "Conversación local con memoria y citas trazables.",
        "tags": ["memoria", "ollama"],
    },
)
CLI_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "id": "email",
        "name": "Triaje de correo",
        "kind": "cli",
        "category": "Productividad",
        "description": (
            "Clasificación local de Gmail en solo lectura; requiere credenciales "
            "guardadas en el keyring."
        ),
        "command": "blackbelt run email scan",
        "tags": ["correo", "solo lectura"],
    },
    {
        "id": "search",
        "name": "Búsqueda híbrida",
        "kind": "cli",
        "category": "Conocimiento",
        "description": ("Busca solo en el índice de trabajo con Qdrant y BM25."),
        "command": 'blackbelt run search query --scope work "consulta"',
        "tags": ["obsidian", "qdrant", "bm25"],
    },
    {
        "id": "global-search",
        "name": "Búsqueda global",
        "kind": "cli",
        "category": "Conocimiento",
        "description": (
            "Consulta por separado el índice de trabajo y la memoria local "
            "de Companion, sin generar respuestas ni usar Cloud."
        ),
        "command": 'blackbelt run search query --scope all "consulta"',
        "tags": ["obsidian", "memoria", "búsqueda federada"],
    },
    {
        "id": "search-index",
        "name": "Indexar trabajo",
        "kind": "cli",
        "category": "Conocimiento",
        "description": (
            "Actualiza el índice laboral con Rich Progress. Puede consumir "
            "CPU y recursos de Ollama; requiere revisar y ejecutar el comando."
        ),
        "command": "blackbelt run search index",
        "tags": ["obsidian", "indexación", "recursos"],
    },
    {
        "id": "windows-mentor",
        "name": "Mentor de Windows",
        "kind": "mentor",
        "category": "Mentor",
        "description": "Explica PowerShell usando la ayuda local del sistema.",
        "command": 'blackbelt run windows explain "Get-ChildItem"',
        "tags": ["windows", "powershell"],
    },
    {
        "id": "linux-mentor",
        "name": "Mentor de Linux",
        "kind": "mentor",
        "category": "Mentor",
        "description": "Explica comandos Linux con Ollama local.",
        "command": 'blackbelt run linux explain "find . -type f"',
        "tags": ["linux", "ollama"],
    },
    {
        "id": "programming-mentor",
        "name": "Mentor de programación",
        "kind": "mentor",
        "category": "Mentor",
        "description": (
            "Abre el chat técnico local para aprender Python, Linux y desarrollo."
        ),
        "command": "blackbelt run chat",
        "tags": ["programación", "python", "ollama"],
    },
)
_WEB_APPLICATION_IDS = frozenset(app["id"] for app in WEB_APPLICATIONS)


def get_catalog() -> dict[str, list[dict[str, Any]]]:
    """Return only reviewed applications and tools declared by BlackBelt."""
    return {
        "applications": [dict(app) for app in WEB_APPLICATIONS],
        "tools": [dict(tool) for tool in CLI_TOOLS],
    }


def get_status(project_root: Path) -> dict[str, Any]:
    """Collect bounded local health signals without scanning user files."""
    ollama = get_ollama_status()
    return {
        "system": _system_status(project_root),
        "ollama": ollama,
        "applications": [_application_status(app["id"]) for app in WEB_APPLICATIONS],
        "vault": {
            "path": str(cfg.OBSIDIAN_VAULT),
            "available": cfg.OBSIDIAN_VAULT.is_dir(),
        },
    }


def launch_application(application_id: str) -> dict[str, Any]:
    """Start a registered web application without opening a browser window."""
    if application_id not in _WEB_APPLICATION_IDS:
        raise ValueError("La aplicación no está registrada para lanzamiento.")

    from blackbelt.tools.apps import (
        _application_url,
        _ensure_background_server,
    )

    started = _ensure_background_server(application_id)
    return {
        "application_id": application_id,
        "url": _application_url(application_id),
        "started": started,
    }


def get_ollama_status() -> dict[str, Any]:
    """Check the configured loopback Ollama endpoint and installed models."""
    base_url = _local_ollama_base_url()
    if base_url is None:
        return {
            "available": False,
            "local": False,
            "model_count": 0,
            "models": [],
            "detail": "OLLAMA_HOST no apunta a un endpoint local válido.",
        }

    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(
                f"{base_url}/api/tags",
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
    except requests.RequestException:
        return {
            "available": False,
            "local": True,
            "model_count": 0,
            "models": [],
            "detail": "Ollama no responde en el endpoint local configurado.",
        }

    try:
        payload = response.json()
    except requests.exceptions.JSONDecodeError:
        return {
            "available": False,
            "local": True,
            "model_count": 0,
            "models": [],
            "detail": "Ollama devolvió una respuesta JSON no válida.",
        }
    raw_models = payload.get("models", []) if isinstance(payload, dict) else []
    models = [
        str(model["name"])
        for model in raw_models
        if isinstance(model, dict) and isinstance(model.get("name"), str)
    ]
    return {
        "available": True,
        "local": True,
        "model_count": len(models),
        "models": models[:100],
        "detail": "Ollama responde en este equipo.",
    }


def start_ollama() -> dict[str, Any]:
    """Start Ollama only after an explicit request from the launcher."""
    with _OLLAMA_START_LOCK:
        current_status = get_ollama_status()
        if current_status["available"]:
            return {
                **current_status,
                "started": False,
                "detail": "Ollama ya estaba disponible.",
            }
        if not current_status["local"]:
            raise RuntimeError(current_status["detail"])

        executable = _ollama_executable()
        if executable is None:
            raise FileNotFoundError(
                "No se encontró Ollama en PATH ni en sus ubicaciones habituales."
            )
        log_path = _launcher_state_directory() / "ollama-serve.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_log_if_needed(log_path)
        creation_flags = 0
        options: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            creation_flags = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            options["creationflags"] = creation_flags
        else:
            options["start_new_session"] = True

        try:
            with log_path.open("ab") as log_file:
                process = subprocess.Popen(
                    [str(executable), "serve"],
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    **options,
                )
        except OSError as exc:
            LOGGER.exception("Could not start the local Ollama service")
            raise RuntimeError(
                f"No se pudo iniciar Ollama: {exc.strerror or exc}"
            ) from exc

        deadline = time.monotonic() + _OLLAMA_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            status = get_ollama_status()
            if status["available"]:
                return {
                    **status,
                    "started": True,
                    "detail": "Ollama se inició y ya responde.",
                }
            if process.poll() is not None:
                break
            time.sleep(_OLLAMA_POLL_INTERVAL_SECONDS)

        detail = _log_tail(log_path)
        if process.poll() is None:
            return {
                **get_ollama_status(),
                "started": True,
                "detail": (
                    "Ollama se inició, pero la API no respondió dentro del "
                    "tiempo esperado." + (f" Registro: {detail}" if detail else "")
                ),
            }
        return {
            **get_ollama_status(),
            "started": False,
            "detail": (
                "El proceso de Ollama terminó antes de responder."
                + (f" Registro: {detail}" if detail else "")
            ),
        }


def _system_status(project_root: Path) -> dict[str, Any]:
    try:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(project_root))
        cpu_percent: float | None = psutil.cpu_percent(interval=0.15)
        cpu_physical_cores = psutil.cpu_count(logical=False)
        cpu_logical_cores = psutil.cpu_count(logical=True)
    except (OSError, psutil.Error) as exc:
        LOGGER.warning("Could not read local resource metrics: %s", exc)
        memory = None
        disk = None
        cpu_percent = None
        cpu_physical_cores = None
        cpu_logical_cores = None
    return {
        "os": platform.platform(),
        "architecture": platform.machine(),
        "processor": platform.processor() or "No identificado",
        "cpu_physical_cores": cpu_physical_cores,
        "cpu_logical_cores": cpu_logical_cores,
        "shell": _shell_name(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "venv_active": sys.prefix != sys.base_prefix,
        "project_root": str(project_root),
        "cpu_percent": cpu_percent,
        "memory_total_bytes": memory.total if memory else None,
        "memory_available_bytes": memory.available if memory else None,
        "memory_percent": memory.percent if memory else None,
        "disk_free_bytes": disk.free if disk else None,
        "disk_percent": disk.percent if disk else None,
    }


def _shell_name() -> str:
    if os.name == "nt":
        if os.getenv("PSModulePath"):
            return "PowerShell"
        if os.getenv("PROMPT") is not None:
            return "Command Prompt"
        return "Windows"
    return Path(os.getenv("SHELL", "desconocido")).name


def _application_status(application_id: str) -> dict[str, Any]:
    from blackbelt.tools.apps import (
        LOOPBACK_HOST,
        _application_is_ready,
        _application_url,
        _port_from_environment,
    )

    port = _port_from_environment(application_id)
    available = _application_is_ready(port, application_id)
    return {
        "id": application_id,
        "available": available,
        "port": port,
        "url": _application_url(application_id) if available else None,
        "host": LOOPBACK_HOST,
    }


def _local_ollama_base_url() -> str | None:
    raw_host = os.getenv("OLLAMA_HOST", "").strip() or "http://127.0.0.1:11434"
    candidate = raw_host if "://" in raw_host else f"http://{raw_host}"
    try:
        parsed = urlsplit(candidate)
        port = parsed.port or 11434
    except ValueError:
        return None
    if (
        parsed.scheme != "http"
        or parsed.hostname is None
        or parsed.hostname.casefold() not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not 1 <= port <= 65535
    ):
        return None
    hostname = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"http://{hostname}:{port}"


def _ollama_executable() -> Path | None:
    executable = shutil.which("ollama")
    if executable:
        return Path(executable)
    if os.name != "nt":
        return None
    candidates = (
        Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path(os.getenv("ProgramFiles", "C:/Program Files")) / "Ollama" / "ollama.exe",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _launcher_state_directory() -> Path:
    if os.name == "nt":
        base_directory = Path(
            os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        )
    else:
        base_directory = Path(
            os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state")
        )
    return base_directory / "BlackBelt" / "launcher"


def _rotate_log_if_needed(log_path: Path) -> None:
    if not log_path.is_file() or log_path.stat().st_size < 1_000_000:
        return
    previous_log = log_path.with_suffix(".log.1")
    previous_log.unlink(missing_ok=True)
    log_path.replace(previous_log)


def _log_tail(log_path: Path) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-8:])
