"""Single-user PowerShell sessions hosted through Windows ConPTY."""

from __future__ import annotations

import hmac
import importlib
import logging
import os
import queue
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

LOGGER = logging.getLogger(__name__)
_OUTPUT_QUEUE_SIZE = 256
_OUTPUT_READ_SIZE = 4096
_MAX_INPUT_CHARS = 8192
_MIN_COLUMNS = 40
_MAX_COLUMNS = 240
_MIN_ROWS = 10
_MAX_ROWS = 80
_SESSION_IDLE_SECONDS = 60 * 60
_SESSION_SWEEP_SECONDS = 15
_OUTPUT_TIMEOUT_SECONDS = 0.5


class TerminalError(RuntimeError):
    """A terminal session could not be started or controlled."""


class _PtyProcess(Protocol):
    fileobj: Any

    def close(self, force: bool = False) -> None: ...

    def isalive(self) -> bool: ...

    def read(self, size: int = _OUTPUT_READ_SIZE) -> str: ...

    def setwinsize(self, rows: int, cols: int) -> None: ...

    def write(self, data: str) -> int: ...


@dataclass
class _TerminalSession:
    token: str
    process: _PtyProcess
    output: queue.Queue[str | None] = field(
        default_factory=lambda: queue.Queue(maxsize=_OUTPUT_QUEUE_SIZE)
    )
    state_lock: threading.Lock = field(default_factory=threading.Lock)
    attached: bool = False
    last_activity: float = field(default_factory=time.monotonic)
    reader: threading.Thread | None = None


class TerminalSessionManager:
    """Own one persistent PowerShell process and bound its lifetime."""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root.resolve()
        self._lock = threading.RLock()
        self._session: _TerminalSession | None = None
        self._shutdown = threading.Event()
        self._sweeper = threading.Thread(
            target=self._sweep_idle_session,
            name="blackbelt-terminal-session-sweeper",
            daemon=True,
        )
        self._sweeper.start()

    def start(self, *, cols: int, rows: int) -> tuple[str, bool]:
        _validate_dimensions(cols, rows)
        with self._lock:
            current = self._session
            if current is not None and current.process.isalive():
                current.last_activity = time.monotonic()
                return current.token, False
            self._session = None
            session = _spawn_session(self._project_root, cols=cols, rows=rows)
            self._session = session
            session.reader = threading.Thread(
                target=self._read_output,
                args=(session,),
                name="blackbelt-terminal-output",
                daemon=True,
            )
            session.reader.start()
            return session.token, True

    def attach(self, token: str) -> _TerminalSession:
        with self._lock:
            session = self._authenticated_session(token)
            with session.state_lock:
                if session.attached:
                    raise TerminalError("La sesión ya tiene una terminal conectada.")
                session.attached = True
                session.last_activity = time.monotonic()
            return session

    def detach(self, session: _TerminalSession) -> None:
        with session.state_lock:
            session.attached = False
            session.last_activity = time.monotonic()

    def write(self, session: _TerminalSession, data: str) -> None:
        if not data or len(data) > _MAX_INPUT_CHARS:
            raise ValueError(
                "La entrada de terminal debe tener entre 1 y 8192 caracteres."
            )
        with self._lock:
            if self._session is not session or not session.process.isalive():
                raise TerminalError("La sesión de PowerShell ya no está activa.")
            try:
                session.process.write(data)
            except (OSError, EOFError, ValueError) as exc:
                raise TerminalError(
                    "No se pudo enviar la entrada a PowerShell."
                ) from exc
            session.last_activity = time.monotonic()

    def resize(self, session: _TerminalSession, *, cols: int, rows: int) -> None:
        _validate_dimensions(cols, rows)
        with self._lock:
            if self._session is not session or not session.process.isalive():
                raise TerminalError("La sesión de PowerShell ya no está activa.")
            try:
                session.process.setwinsize(rows, cols)
            except (OSError, ValueError) as exc:
                raise TerminalError(
                    "No se pudo ajustar el tamaño de PowerShell."
                ) from exc
            session.last_activity = time.monotonic()

    def next_output(self, session: _TerminalSession) -> str | None:
        try:
            return session.output.get(timeout=_OUTPUT_TIMEOUT_SECONDS)
        except queue.Empty:
            if not session.process.isalive():
                return None
            return ""

    def close(self, token: str) -> None:
        with self._lock:
            session = self._authenticated_session(token)
            self._close_session(session)

    def close_all(self) -> None:
        self._shutdown.set()
        with self._lock:
            session = self._session
            if session is not None:
                self._close_session(session)
        if self._sweeper.is_alive():
            self._sweeper.join(timeout=2)

    def _authenticated_session(self, token: str) -> _TerminalSession:
        session = self._session
        if (
            session is None
            or not session.process.isalive()
            or not hmac.compare_digest(session.token, token)
        ):
            raise TerminalError("No existe una sesión activa con ese token.")
        return session

    def _read_output(self, session: _TerminalSession) -> None:
        try:
            while session.process.isalive() and not self._shutdown.is_set():
                try:
                    output = session.process.read(_OUTPUT_READ_SIZE)
                except EOFError:
                    break
                if output:
                    self._enqueue_output(session, output)
                    session.last_activity = time.monotonic()
        except (OSError, ValueError) as exc:
            LOGGER.info("PowerShell terminal output ended: %s", exc)
        finally:
            self._enqueue_output(session, None)

    @staticmethod
    def _enqueue_output(
        session: _TerminalSession,
        value: str | None,
    ) -> None:
        while True:
            try:
                session.output.put_nowait(value)
                return
            except queue.Full:
                try:
                    session.output.get_nowait()
                except queue.Empty:
                    return

    def _sweep_idle_session(self) -> None:
        while not self._shutdown.wait(_SESSION_SWEEP_SECONDS):
            with self._lock:
                session = self._session
                if session is None:
                    continue
                if not session.process.isalive():
                    self._close_session(session)
                    continue
                if time.monotonic() - session.last_activity > _SESSION_IDLE_SECONDS:
                    LOGGER.info("Closing an idle launcher PowerShell session")
                    self._close_session(session)

    def _close_session(self, session: _TerminalSession) -> None:
        if self._session is not session:
            return
        self._session = None
        try:
            session.process.close(force=True)
        except (OSError, EOFError, RuntimeError) as exc:
            LOGGER.warning("Could not close the launcher PowerShell session: %s", exc)
        self._enqueue_output(session, None)


def _spawn_session(
    project_root: Path,
    *,
    cols: int,
    rows: int,
) -> _TerminalSession:
    if os.name != "nt":
        raise TerminalError(
            "La terminal ConPTY del launcher solo está disponible en Windows."
        )
    try:
        winpty = importlib.import_module("winpty")
        backend = importlib.import_module("winpty.enums").Backend.ConPTY
    except ImportError as exc:
        raise TerminalError(
            "Falta pywinpty; instala las dependencias de BlackBelt en Windows."
        ) from exc

    executable = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if executable is None:
        raise TerminalError("No se encontró PowerShell en este equipo.")

    bootstrap = _powershell_bootstrap(project_root)
    try:
        process = winpty.PtyProcess.spawn(
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NoExit",
                "-Command",
                bootstrap,
            ],
            cwd=str(project_root),
            env=os.environ.copy(),
            dimensions=(rows, cols),
            backend=backend,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise TerminalError(
            f"No se pudo abrir PowerShell mediante ConPTY: {exc}"
        ) from exc

    import secrets

    return _TerminalSession(token=secrets.token_urlsafe(32), process=process)


def _powershell_bootstrap(project_root: Path) -> str:
    root = _powershell_quote(str(project_root))
    activate_script = project_root / ".venv" / "Scripts" / "Activate.ps1"
    commands = [f"Set-Location -LiteralPath '{root}'"]
    if activate_script.is_file():
        activation = _powershell_quote(str(activate_script))
        commands.append(
            f"if (Test-Path -LiteralPath '{activation}') {{ . '{activation}' }}"
        )
    return "; ".join(commands)


def _powershell_quote(value: str) -> str:
    return value.replace("'", "''")


def _validate_dimensions(cols: int, rows: int) -> None:
    if not _MIN_COLUMNS <= cols <= _MAX_COLUMNS:
        raise ValueError("Las columnas deben estar entre 40 y 240.")
    if not _MIN_ROWS <= rows <= _MAX_ROWS:
        raise ValueError("Las filas deben estar entre 10 y 80.")
