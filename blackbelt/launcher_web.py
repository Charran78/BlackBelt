"""Isolated FastAPI application for the local BlackBelt launcher."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from blackbelt.launcher_indexing import (
    CompanionIndexJobManager,
    IndexJobError,
    WorkIndexJobManager,
)
from blackbelt.launcher_service import (
    get_catalog,
    get_status,
    launch_application,
    start_ollama,
)
from blackbelt.launcher_terminal import (
    TerminalError,
    TerminalSessionManager,
    _TerminalSession,
)

LOGGER = logging.getLogger(__name__)
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class TerminalStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cols: int = Field(default=100, ge=40, le=240)
    rows: int = Field(default=28, ge=10, le=80)


class TerminalCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=32, max_length=128)


class IndexStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool


def create_launcher_app(
    *,
    assets_root: Path | None = None,
    project_root: Path | None = None,
) -> FastAPI:
    """Build a small app on its own origin so terminal access stays isolated."""

    resolved_project_root = project_root or _project_root()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.terminal_manager = TerminalSessionManager(
            resolved_project_root
        )
        application.state.index_manager = CompanionIndexJobManager()
        application.state.work_index_manager = WorkIndexJobManager()
        try:
            yield
        finally:
            application.state.index_manager.request_shutdown()
            application.state.work_index_manager.request_shutdown()
            await run_in_threadpool(application.state.terminal_manager.close_all)

    app = FastAPI(
        title="BlackBelt Launcher",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=sorted(_ALLOWED_HOSTS),
    )
    app.state.assets_root = assets_root or _assets_root()
    app.state.project_root = resolved_project_root
    app.state.allowed_origins = _allowed_origins()

    @app.middleware("http")
    async def protect_browser_actions(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method in _STATE_CHANGING_METHODS and request.url.path.startswith(
            "/api/"
        ):
            origin = request.headers.get("origin")
            if origin not in request.app.state.allowed_origins:
                return JSONResponse(
                    {"detail": "Origen local no autorizado."},
                    status_code=403,
                )
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _content_security_policy()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/launcher/", status_code=307)

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/launcher", include_in_schema=False)
    @app.get("/launcher/", include_in_schema=False)
    async def launcher_page() -> FileResponse:
        return _asset_file("index.html", app.state.assets_root)

    @app.get("/launcher/manifest.webmanifest", include_in_schema=False)
    async def launcher_manifest() -> FileResponse:
        return _asset_file(
            "manifest.webmanifest",
            app.state.assets_root,
            media_type="application/manifest+json",
        )

    @app.get("/launcher/styles.css", include_in_schema=False)
    async def launcher_styles() -> FileResponse:
        return _asset_file("styles.css", app.state.assets_root)

    @app.get("/launcher/app.js", include_in_schema=False)
    async def launcher_script() -> FileResponse:
        return _asset_file(
            "app.js",
            app.state.assets_root,
            media_type="application/javascript",
        )

    @app.get("/launcher/launcher-sw.js", include_in_schema=False)
    async def launcher_service_worker() -> FileResponse:
        return _asset_file(
            "launcher-sw.js",
            app.state.assets_root,
            media_type="application/javascript",
            no_cache=True,
        )

    @app.get("/launcher/icons/icon.svg", include_in_schema=False)
    async def launcher_icon() -> FileResponse:
        return _asset_file(
            "icons/icon.svg",
            app.state.assets_root,
            media_type="image/svg+xml",
        )

    @app.get("/launcher/vendor/xterm.js", include_in_schema=False)
    async def terminal_script() -> FileResponse:
        return _asset_file(
            "vendor/xterm.js",
            app.state.assets_root,
            media_type="application/javascript",
        )

    @app.get("/launcher/vendor/addon-fit.js", include_in_schema=False)
    async def terminal_fit_script() -> FileResponse:
        return _asset_file(
            "vendor/addon-fit.js",
            app.state.assets_root,
            media_type="application/javascript",
        )

    @app.get("/launcher/vendor/xterm.css", include_in_schema=False)
    async def terminal_styles() -> FileResponse:
        return _asset_file(
            "vendor/xterm.css",
            app.state.assets_root,
            media_type="text/css",
        )

    @app.get("/api/launcher/catalog")
    async def catalog() -> dict[str, object]:
        return get_catalog()

    @app.get("/api/launcher/status")
    async def status(request: Request) -> dict[str, object]:
        return await run_in_threadpool(get_status, request.app.state.project_root)

    @app.get("/api/launcher/index")
    async def index_status(request: Request) -> dict[str, object]:
        manager: CompanionIndexJobManager = request.app.state.index_manager
        return await run_in_threadpool(manager.status)

    @app.post("/api/launcher/index")
    async def index_start(
        request: Request,
        payload: IndexStartRequest,
    ) -> dict[str, object]:
        if not payload.confirmed:
            raise HTTPException(
                status_code=400,
                detail="La indexación requiere confirmación explícita.",
            )
        manager: CompanionIndexJobManager = request.app.state.index_manager
        try:
            return await run_in_threadpool(manager.start)
        except IndexJobError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/launcher/index/cancel")
    async def index_cancel(request: Request) -> dict[str, object]:
        manager: CompanionIndexJobManager = request.app.state.index_manager
        try:
            return await run_in_threadpool(manager.cancel)
        except IndexJobError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/launcher/index/work")
    async def work_index_status(request: Request) -> dict[str, object]:
        manager: WorkIndexJobManager = request.app.state.work_index_manager
        return await run_in_threadpool(manager.status)

    @app.post("/api/launcher/index/work")
    async def work_index_start(
        request: Request,
        payload: IndexStartRequest,
    ) -> dict[str, object]:
        if not payload.confirmed:
            raise HTTPException(
                status_code=400,
                detail="La indexación requiere confirmación explícita.",
            )
        manager: WorkIndexJobManager = request.app.state.work_index_manager
        try:
            return await run_in_threadpool(manager.start)
        except IndexJobError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/launcher/index/work/cancel")
    async def work_index_cancel(request: Request) -> dict[str, object]:
        manager: WorkIndexJobManager = request.app.state.work_index_manager
        try:
            return await run_in_threadpool(manager.cancel)
        except IndexJobError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/launcher/apps/{application_id}/launch")
    async def launch(application_id: str) -> dict[str, object]:
        try:
            return await run_in_threadpool(launch_application, application_id)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/launcher/ollama/start")
    async def ollama_start() -> dict[str, object]:
        try:
            return await run_in_threadpool(start_ollama)
        except (FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/launcher/terminal/start")
    async def terminal_start(
        request: Request,
        payload: TerminalStartRequest,
    ) -> dict[str, object]:
        manager: TerminalSessionManager = request.app.state.terminal_manager
        try:
            token, started = await run_in_threadpool(
                manager.start,
                cols=payload.cols,
                rows=payload.rows,
            )
        except (TerminalError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"token": token, "started": started}

    @app.post("/api/launcher/terminal/close")
    async def terminal_close(
        request: Request,
        payload: TerminalCloseRequest,
    ) -> dict[str, str]:
        manager: TerminalSessionManager = request.app.state.terminal_manager
        try:
            await run_in_threadpool(manager.close, payload.token)
        except TerminalError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "closed"}

    @app.websocket("/api/launcher/terminal")
    async def terminal(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if origin not in websocket.app.state.allowed_origins:
            await websocket.close(code=4403, reason="Origen local no autorizado.")
            return
        manager: TerminalSessionManager = websocket.app.state.terminal_manager
        token = websocket.query_params.get("token", "")
        try:
            session = await run_in_threadpool(manager.attach, token)
        except TerminalError:
            await websocket.close(code=4401, reason="Sesión no disponible.")
            return

        await websocket.accept()
        output_task = asyncio.create_task(
            _send_terminal_output(websocket, manager, session)
        )
        try:
            await _receive_terminal_input(websocket, manager, session)
        except WebSocketDisconnect:
            pass
        except (TerminalError, TypeError, ValueError) as exc:
            LOGGER.info("Launcher terminal connection ended: %s", exc)
            await websocket.close(code=1008, reason="Entrada no válida.")
        finally:
            output_task.cancel()
            try:
                output_result = await asyncio.gather(
                    output_task,
                    return_exceptions=True,
                )
                output_error = output_result[0]
                if isinstance(output_error, (RuntimeError, WebSocketDisconnect)):
                    LOGGER.debug(
                        "Launcher terminal output task ended: %s",
                        output_error,
                    )
                elif isinstance(output_error, Exception):
                    LOGGER.error(
                        "Launcher terminal output task failed",
                        exc_info=(
                            type(output_error),
                            output_error,
                            output_error.__traceback__,
                        ),
                    )
            finally:
                await run_in_threadpool(manager.detach, session)

    return app


async def _send_terminal_output(
    websocket: WebSocket,
    manager: TerminalSessionManager,
    session: _TerminalSession,
) -> None:
    while True:
        output = await run_in_threadpool(manager.next_output, session)
        if output is None:
            await websocket.close(code=1000, reason="PowerShell terminó.")
            return
        if output:
            await websocket.send_text(output)


async def _receive_terminal_input(
    websocket: WebSocket,
    manager: TerminalSessionManager,
    session: _TerminalSession,
) -> None:
    while True:
        message = await websocket.receive_json()
        if not isinstance(message, dict):
            raise TypeError("El mensaje de terminal debe ser un objeto JSON.")
        action = message.get("type")
        if action == "input":
            data = message.get("data")
            if not isinstance(data, str):
                raise TypeError("La entrada de terminal debe ser texto.")
            await run_in_threadpool(manager.write, session, data)
        elif action == "resize":
            cols = message.get("cols")
            rows = message.get("rows")
            if (
                isinstance(cols, bool)
                or isinstance(rows, bool)
                or not isinstance(cols, int)
                or not isinstance(rows, int)
            ):
                raise ValueError("El tamaño de terminal debe usar enteros.")
            await run_in_threadpool(
                manager.resize,
                session,
                cols=cols,
                rows=rows,
            )
        else:
            raise ValueError("Tipo de mensaje de terminal no reconocido.")


def _assets_root() -> Path:
    return Path(__file__).parent / "data" / "webapps" / "launcher"


def _asset_file(
    relative_path: str,
    assets_root: Path,
    *,
    media_type: str | None = None,
    no_cache: bool = False,
) -> FileResponse:
    path = assets_root / relative_path
    if not path.is_file():
        raise HTTPException(
            status_code=404, detail="Recurso del launcher no encontrado."
        )
    headers = {"Cache-Control": "no-cache"} if no_cache else None
    return FileResponse(path, media_type=media_type, headers=headers)


def _project_root() -> Path:
    package_root = Path(__file__).resolve().parent.parent
    if (package_root / "pyproject.toml").is_file():
        return package_root
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def _allowed_origins() -> frozenset[str]:
    from blackbelt.tools.apps import _port_from_environment

    port = _port_from_environment("launcher")
    return frozenset(
        {
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        }
    )


def _content_security_policy() -> str:
    from blackbelt.tools.apps import _port_from_environment

    port = _port_from_environment("launcher")
    return (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        f"connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port}; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    )
