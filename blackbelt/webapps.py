"""Loopback-only web application host for SOMA and Ghost Writer."""

from __future__ import annotations

import importlib
import logging
import os
import re
import unicodedata
from datetime import date
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send
import yaml


LOGGER = logging.getLogger(__name__)
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_CONTEXT = 32768
_MAX_LOCAL_PREDICT = 4096
_MAX_CLOUD_PREDICT = 16384
_MAX_RESPONSE_TEXT = 100000


class GhostWriterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["local", "cloud"] = "local"
    system_instruction: str = Field(min_length=1, max_length=16000)
    user_query: str = Field(min_length=1, max_length=32000)


class GhostWriterConfig(BaseModel):
    local_model: str
    cloud_model: str


class GhostWriterResponse(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_RESPONSE_TEXT)


class GhostWriterSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Literal["linkedin", "substack"]
    content: str = Field(min_length=1, max_length=_MAX_RESPONSE_TEXT)


class GhostWriterDestination(BaseModel):
    directory: str


class GhostWriterSaveResponse(BaseModel):
    filename: str
    relative_path: str


def create_app(
    default_page: str = "soma",
    *,
    ollama_client_factory: Callable[..., Any] | None = None,
    assets_root: Path | None = None,
    obsidian_root: Path | None = None,
) -> FastAPI:
    if default_page not in {"soma", "ghostwriter"}:
        raise ValueError("La página inicial debe ser 'soma' o 'ghostwriter'.")
    app = FastAPI(
        title="BlackBelt Local Apps",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]"],
    )
    app.add_middleware(
        _RequestSizeLimitMiddleware,
        max_bytes=_MAX_REQUEST_BYTES,
    )
    app.state.ollama_client_factory = ollama_client_factory
    app.state.assets_root = assets_root or _assets_root()
    app.state.obsidian_root = obsidian_root
    app.state.default_page = default_page

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(f"/{app.state.default_page}/", status_code=307)

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/soma", include_in_schema=False)
    @app.get("/soma/", include_in_schema=False)
    async def soma_page() -> FileResponse:
        return _html_response(_asset_path(app.state.assets_root, "soma"))

    @app.get("/soma/manifest.webmanifest", include_in_schema=False)
    async def soma_manifest() -> FileResponse:
        return _webapp_file(
            "soma",
            "manifest.webmanifest",
            "application/manifest+json",
        )

    @app.get("/soma/icons/icon.svg", include_in_schema=False)
    async def soma_icon() -> FileResponse:
        return _webapp_file("soma", "icons/icon.svg", "image/svg+xml")

    @app.get("/ghostwriter", include_in_schema=False)
    @app.get("/ghostwriter/", include_in_schema=False)
    async def ghostwriter_page() -> FileResponse:
        return _html_response(_asset_path(app.state.assets_root, "ghostwriter"))

    @app.get("/ghostwriter/manifest.webmanifest", include_in_schema=False)
    async def ghostwriter_manifest() -> FileResponse:
        return _webapp_file(
            "ghostwriter",
            "manifest.webmanifest",
            "application/manifest+json",
        )

    @app.get("/ghostwriter/icons/icon.svg", include_in_schema=False)
    async def ghostwriter_icon() -> FileResponse:
        return _webapp_file("ghostwriter", "icons/icon.svg", "image/svg+xml")

    @app.get("/soma-sw.js", include_in_schema=False)
    @app.get("/soma/soma-sw.js", include_in_schema=False)
    async def soma_service_worker() -> FileResponse:
        return _webapp_file(
            "root",
            "soma-sw.js",
            "application/javascript",
            no_cache=True,
        )

    @app.get("/ghostwriter/ghostwriter-sw.js", include_in_schema=False)
    async def ghostwriter_service_worker() -> FileResponse:
        return _webapp_file(
            "ghostwriter",
            "ghostwriter-sw.js",
            "application/javascript",
            no_cache=True,
        )

    @app.get(
        "/api/ghostwriter/destination",
        response_model=GhostWriterDestination,
    )
    async def ghostwriter_destination(
        request: Request,
    ) -> GhostWriterDestination:
        _, relative_directory = _resolve_obsidian_destination(
            request.app.state.obsidian_root,
        )
        return GhostWriterDestination(
            directory=relative_directory.as_posix(),
        )

    @app.post(
        "/api/ghostwriter/save",
        response_model=GhostWriterSaveResponse,
        status_code=201,
    )
    async def save_ghostwriter_note(
        payload: GhostWriterSaveRequest,
        request: Request,
    ) -> GhostWriterSaveResponse:
        destination, relative_directory = _resolve_obsidian_destination(
            request.app.state.obsidian_root,
        )
        filename = _create_obsidian_note(
            destination,
            payload.platform,
            payload.content,
        )
        return GhostWriterSaveResponse(
            filename=filename,
            relative_path=f"{relative_directory.as_posix()}/{filename}",
        )

    @app.get(
        "/api/ghostwriter/config",
        response_model=GhostWriterConfig,
    )
    async def ghostwriter_config() -> GhostWriterConfig:
        return GhostWriterConfig(
            local_model=_ollama_model("local"),
            cloud_model=_ollama_model("cloud"),
        )

    @app.post("/api/ghostwriter/generate", response_model=GhostWriterResponse)
    async def generate_ghostwriter_text(
        payload: GhostWriterRequest,
        request: Request,
    ) -> GhostWriterResponse:
        model = _ollama_model(payload.backend)
        is_cloud = payload.backend == "cloud"
        context_setting = (
            "GHOSTWRITER_CLOUD_NUM_CTX"
            if is_cloud
            else "GHOSTWRITER_NUM_CTX"
        )
        predict_setting = (
            "GHOSTWRITER_CLOUD_NUM_PREDICT"
            if is_cloud
            else "GHOSTWRITER_NUM_PREDICT"
        )
        client = _make_ollama_client(
            request.app.state.ollama_client_factory,
        )
        try:
            response = await run_in_threadpool(
                client.chat,
                model=model,
                messages=[
                    {"role": "system", "content": payload.system_instruction},
                    {"role": "user", "content": payload.user_query},
                ],
                options={
                    "num_ctx": _integer_environment(
                        context_setting,
                        32768 if is_cloud else 4096,
                        512,
                        _MAX_CONTEXT,
                    ),
                    "num_predict": _integer_environment(
                        predict_setting,
                        8192 if is_cloud else 2048,
                        64,
                        _MAX_CLOUD_PREDICT
                        if is_cloud
                        else _MAX_LOCAL_PREDICT,
                    ),
                    "temperature": 0.55,
                    "repeat_penalty": 1.18,
                    "repeat_last_n": 128,
                    **_optional_thread_setting(),
                },
                keep_alive=os.getenv("OLLAMA_KEEP_ALIVE", "0"),
            )
        except ImportError as exc:
            LOGGER.error("Ghost Writer requires the Ollama Python package.")
            raise HTTPException(
                status_code=503,
                detail="Falta la dependencia Ollama de BlackBelt.",
            ) from exc
        except Exception as exc:
            if not _is_ollama_error(exc):
                raise
            detail = _ollama_failure_detail(exc, model)
            reason = _safe_ollama_error(exc)
            LOGGER.warning(
                "Ollama %s generation failed (%s): %s",
                payload.backend,
                type(exc).__name__,
                reason,
            )
            raise HTTPException(
                status_code=503,
                detail=detail,
            ) from exc

        text = _response_text(response)
        if not text:
            raise HTTPException(
                status_code=502,
                detail="Ollama devolvió una respuesta vacía.",
            )
        if len(text) > _MAX_RESPONSE_TEXT:
            raise HTTPException(
                status_code=502,
                detail="Ollama devolvió un texto que supera el límite permitido.",
            )
        return GhostWriterResponse(text=text)

    return app


def _assets_root() -> Path:
    return Path(__file__).parent / "data" / "webapps"


def _html_response(path: Path) -> FileResponse:
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="No se encontraron los archivos locales de la aplicación.",
        )
    return FileResponse(path, media_type="text/html; charset=utf-8")


def _webapp_file(
    application: str,
    relative_path: str,
    media_type: str,
    *,
    no_cache: bool = False,
) -> FileResponse:
    asset_root = Path(__file__).parent / "data" / "webapps"
    asset_path = (
        asset_root / relative_path
        if application == "root"
        else asset_root / application / relative_path
    )
    if not asset_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="No se encontró un recurso de la aplicación web.",
        )
    headers = {"Cache-Control": "no-cache"} if no_cache else {}
    return FileResponse(asset_path, media_type=media_type, headers=headers)


def _resolve_obsidian_destination(
    configured_root: Path | None,
) -> tuple[Path, Path]:
    root_path = configured_root or Path(
        os.getenv("OBSIDIAN_VAULT", str(Path.home() / "Obsidian"))
    ).expanduser()
    raw_relative_directory = os.getenv(
        "GHOSTWRITER_OBSIDIAN_SUBDIR",
        "013 - PUBLICACIONES",
    )
    relative_directory = Path(raw_relative_directory)
    windows_relative_directory = PureWindowsPath(raw_relative_directory)
    if (
        relative_directory.is_absolute()
        or bool(relative_directory.drive)
        or bool(relative_directory.root)
        or bool(windows_relative_directory.drive)
        or bool(windows_relative_directory.root)
        or not relative_directory.parts
        or any(part in {".", ".."} for part in relative_directory.parts)
        or any(
            part in {".", ".."}
            for part in windows_relative_directory.parts
        )
    ):
        raise HTTPException(
            status_code=500,
            detail="GHOSTWRITER_OBSIDIAN_SUBDIR debe ser una ruta relativa segura.",
        )

    try:
        resolved_root = root_path.resolve(strict=True)
        destination = (resolved_root / relative_directory).resolve(strict=True)
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "No se encuentra la bóveda o la carpeta de publicaciones. "
                "Comprueba OBSIDIAN_VAULT y GHOSTWRITER_OBSIDIAN_SUBDIR."
            ),
        ) from exc

    try:
        relative_destination = destination.relative_to(resolved_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail="La carpeta de publicaciones debe permanecer dentro de la bóveda.",
        ) from exc
    if not destination.is_dir() or not os.access(destination, os.W_OK):
        raise HTTPException(
            status_code=503,
            detail="La carpeta de publicaciones no está disponible para escritura.",
        )
    return destination, relative_destination


def _create_obsidian_note(
    destination: Path,
    platform: str,
    content: str,
) -> str:
    filename_stem = (
        f"{date.today().isoformat()}_{platform}_"
        f"{_obsidian_title_slug(content)}"
    )
    for suffix in range(1000):
        numbered_suffix = f"_{suffix}" if suffix else ""
        filename = f"{filename_stem}{numbered_suffix}.md"
        note_path = destination / filename
        created = False
        try:
            with note_path.open("x", encoding="utf-8", newline="\n") as note:
                created = True
                note.write(content)
                if not content.endswith("\n"):
                    note.write("\n")
            return filename
        except FileExistsError:
            continue
        except OSError as exc:
            if created:
                try:
                    note_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    LOGGER.error(
                        "Could not remove an incomplete Ghost Writer note (%s).",
                        type(cleanup_error).__name__,
                    )
            LOGGER.error(
                "Could not save a Ghost Writer note (%s).",
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="No se pudo guardar la nota en la bóveda configurada.",
            ) from exc

    raise HTTPException(
        status_code=409,
        detail="Hay demasiadas notas con el mismo nombre para la fecha actual.",
    )


def _obsidian_title_slug(content: str) -> str:
    if content.startswith("---"):
        metadata_end = content.find("\n---", 3)
        if metadata_end >= 0:
            try:
                metadata = yaml.safe_load(content[3:metadata_end])
            except yaml.YAMLError:
                metadata = None
            title = metadata.get("title") if isinstance(metadata, dict) else None
            if isinstance(title, str):
                normalized = unicodedata.normalize("NFKC", title).strip()
                slug = re.sub(r"[^\w-]+", "-", normalized, flags=re.UNICODE)
                slug = slug.strip("-_").lower()[:80].rstrip("-_")
                if slug:
                    return slug
    return "post"


def _asset_path(root: Path, application: str) -> Path:
    if application == "soma":
        return root / "soma" / "somaguard.html"
    return root / "ghostwriter" / "ghostwriter_ai_studio.html"


def _ollama_model(backend: Literal["local", "cloud"]) -> str:
    if backend == "cloud":
        model = os.getenv(
            "GHOSTWRITER_OLLAMA_CLOUD_MODEL",
            "gpt-oss:120b-cloud",
        ).strip()
        if not model or len(model) > 128 or not _is_cloud_model(model):
            raise HTTPException(
                status_code=500,
                detail=(
                    "GHOSTWRITER_OLLAMA_CLOUD_MODEL debe indicar un modelo "
                    "Ollama Cloud con etiqueta :cloud o -cloud."
                ),
            )
        return model

    model = os.getenv(
        "GHOSTWRITER_OLLAMA_MODEL",
        os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b"),
    ).strip()
    if not model or len(model) > 128:
        raise HTTPException(
            status_code=500,
            detail="GHOSTWRITER_OLLAMA_MODEL no contiene un nombre válido.",
        )
    if _is_cloud_model(model):
        raise HTTPException(
            status_code=500,
            detail=(
                "El modelo Cloud no puede configurarse como modelo local. "
                "Selecciona Ollama Cloud en el Studio."
            ),
        )
    return model


def _is_cloud_model(model: str) -> bool:
    return model.lower().endswith((":cloud", "-cloud"))


def _make_ollama_client(factory: Callable[..., Any] | None) -> Any:
    if factory is not None:
        return factory()
    try:
        ollama = importlib.import_module("ollama")
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Falta la dependencia Ollama de BlackBelt.",
        ) from exc
    timeout = _integer_environment(
        "GHOSTWRITER_TIMEOUT",
        300,
        1,
        3600,
    )
    return ollama.Client(timeout=timeout)


def _is_ollama_error(error: Exception) -> bool:
    if isinstance(error, (TimeoutError, OSError)):
        return True
    try:
        ollama = importlib.import_module("ollama")
    except ImportError:
        return False
    return isinstance(
        error,
        (ollama.RequestError, ollama.ResponseError, TimeoutError, OSError),
    )


def _safe_ollama_error(error: Exception) -> str:
    message = getattr(error, "error", str(error))
    normalized = re.sub(r"\s+", " ", str(message)).strip()
    return normalized[:400] or type(error).__name__


def _ollama_failure_detail(error: Exception, model: str) -> str:
    message = _safe_ollama_error(error).lower()
    if _is_cloud_model(model) and any(
        marker in message
        for marker in (
            "unauthorized",
            "authentication",
            "sign in",
            "signin",
            "not logged in",
        )
    ):
        return "Inicia sesión en Ollama Cloud desde esta cuenta con: ollama signin"
    if _is_cloud_model(model) and (
        getattr(error, "status_code", None) == 429
        or any(
            marker in message
            for marker in (
                "rate limit",
                "quota exceeded",
                "too many requests",
            )
        )
    ):
        return (
            "Ollama Cloud ha alcanzado su límite de uso. Espera a que se "
            "renueve la cuota o cambia el Studio a modo local."
        )
    if any(
        marker in message
        for marker in (
            "out of memory",
            "failed to allocate",
            "unable to allocate",
            "not enough memory",
        )
    ):
        return (
            f"Ollama no tiene memoria suficiente para cargar {model}. "
            "Reduce GHOSTWRITER_NUM_CTX y OLLAMA_NUM_PARALLEL, o usa un "
            "modelo más pequeño."
        )
    if "not found" in message and "model" in message:
        return (
            f"Ollama no encuentra el modelo {model}. "
            f"Descárgalo con: ollama pull {model}"
        )
    if "prediction aborted" in message and "repeat" in message:
        return (
            "El modelo se ha quedado repitiendo texto y Ollama ha detenido "
            "la generación. Prueba con una fuente más breve o vuelve a "
            "generar; se han reforzado los controles antirrepetición."
        )
    return (
        f"Ollama rechazó la generación con {model}: "
        f"{_safe_ollama_error(error)}"
    )


def _response_text(response: Any) -> str:
    if isinstance(response, dict):
        message = response.get("message")
    else:
        message = getattr(response, "message", None)
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    return content.strip() if isinstance(content, str) else ""


def _integer_environment(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"{name} debe ser un número entero.",
        ) from exc
    if not minimum <= value <= maximum:
        raise HTTPException(
            status_code=500,
            detail=f"{name} debe estar entre {minimum} y {maximum}.",
        )
    return value


def _optional_thread_setting() -> dict[str, int]:
    raw_value = os.getenv("OLLAMA_NUM_THREAD", "").strip()
    if not raw_value:
        return {}
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail="OLLAMA_NUM_THREAD debe ser un entero.",
        ) from exc
    if not 1 <= value <= 256:
        raise HTTPException(
            status_code=500,
            detail="OLLAMA_NUM_THREAD debe estar entre 1 y 256.",
        )
    return {"num_thread": value}


class _RequestSizeLimitMiddleware:
    """Buffer bounded request bodies before application parsing."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or scope["method"] not in {
            "POST",
            "PUT",
            "PATCH",
        }:
            await self.app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length == -1:
            response = JSONResponse(
                status_code=400,
                content={"detail": "Cabecera Content-Length inválida."},
            )
            await response(scope, receive, send)
            return
        if content_length is not None and content_length > self.max_bytes:
            await self._send_too_large(scope, receive, send)
            return

        messages: list[Message] = []
        body_size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            body_size += len(message.get("body", b""))
            if body_size > self.max_bytes:
                await self._send_too_large(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        message_index = 0

        async def replay_body() -> Message:
            nonlocal message_index
            if message_index < len(messages):
                message = messages[message_index]
                message_index += 1
                return message
            return await receive()

        await self.app(scope, replay_body, send)

    async def _send_too_large(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": "La petición supera el tamaño permitido."},
        )
        await response(scope, receive, send)


def _content_length(scope: Scope) -> int | None:
    headers = dict(scope.get("headers", []))
    raw_length = headers.get(b"content-length")
    if raw_length is None:
        return None
    if not raw_length.isdigit():
        return -1
    try:
        return int(raw_length)
    except ValueError:
        return -1


app = create_app()
