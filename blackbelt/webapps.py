"""Loopback-only web application host for local BlackBelt apps."""

from __future__ import annotations

import importlib
import logging
import os
import re
import threading
import time
import unicodedata
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Literal
from urllib.parse import urlsplit
from xml.etree import ElementTree

import requests
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send
import yaml

from blackbelt.knowledge.meeting_prep import (
    AmbiguousMeetingClientError,
    MeetingClientNotFoundError,
    MeetingClientSummary,
    MeetingDossier,
    MeetingPreparationError,
    MeetingPreparationService,
    client_context_markdown,
)


LOGGER = logging.getLogger(__name__)
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_REDDIT_FEED_BYTES = 2 * 1024 * 1024
_MAX_MEETING_SOURCES = 25
_MAX_MEETING_SOURCE_CHARS = 12000
_MAX_MEETING_DOSSIER_BYTES = 512 * 1024
_MEETING_SAVE_LOCK = threading.Lock()
_REDDIT_FEED_URL = (
    "https://www.reddit.com/user/same-survey-7265/"
    "m/tech_ia_y_salud_personal/.rss"
)
_REDDIT_FEED_CACHE_SECONDS = 300
_REDDIT_FEED_CACHE: dict[str, Any] = {}
_REDDIT_FEED_CACHE_LOCK = threading.Lock()
_MAX_CONTEXT = 32768
_MAX_LOCAL_PREDICT = 4096
_MAX_CLOUD_PREDICT = 16384
_MAX_RESPONSE_TEXT = 100000


class RedditFeedItem(BaseModel):
    id: str
    title: str
    subreddit: str
    author: str
    published: str
    excerpt: str
    link: str


class RedditFeedResponse(BaseModel):
    title: str
    updated: str
    items: list[RedditFeedItem]


class GhostWriterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["local", "cloud"] = "local"
    system_instruction: str = Field(min_length=1, max_length=16000)
    user_query: str = Field(min_length=1, max_length=32000)


class GhostWriterConfig(BaseModel):
    local_model: str
    cloud_model: str
    author_name: str
    author_handle: str
    publication_name: str
    default_tags: str


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


class MeetingClientOption(BaseModel):
    client_id: str
    name: str
    status: str
    source: str


class MeetingNoteSource(BaseModel):
    path: str
    title: str
    content: str


class MeetingPreparationResponse(BaseModel):
    client: MeetingClientOption
    client_context: str
    active_projects: list[str]
    previous_projects: list[str]
    sources: list[MeetingNoteSource]
    warnings: list[str]


class MeetingPreparationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1, max_length=160)


class MeetingSaveResponse(BaseModel):
    filename: str
    relative_path: str


def create_app(
    default_page: str = "soma",
    *,
    ollama_client_factory: Callable[..., Any] | None = None,
    assets_root: Path | None = None,
    obsidian_root: Path | None = None,
) -> FastAPI:
    if default_page not in {"soma", "ghostwriter", "meetings"}:
        raise ValueError(
            "La página inicial debe ser 'soma', 'ghostwriter' o 'meetings'."
        )
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

    @app.get("/meetings", include_in_schema=False)
    @app.get("/meetings/", include_in_schema=False)
    async def meetings_page() -> FileResponse:
        return _html_response(
            _asset_path(app.state.assets_root, "meetings"),
            no_cache=True,
        )

    @app.get("/meetings/manifest.webmanifest", include_in_schema=False)
    async def meetings_manifest() -> FileResponse:
        return _webapp_file(
            "meetings",
            "manifest.webmanifest",
            "application/manifest+json",
        )

    @app.get("/meetings/icons/icon.svg", include_in_schema=False)
    async def meetings_icon() -> FileResponse:
        return _webapp_file("meetings", "icons/icon.svg", "image/svg+xml")

    @app.get("/meetings/meetings-sw.js", include_in_schema=False)
    async def meetings_service_worker() -> FileResponse:
        return _webapp_file(
            "meetings",
            "meetings-sw.js",
            "application/javascript",
            no_cache=True,
        )

    @app.get(
        "/api/meeting-prep/clients",
        response_model=list[MeetingClientOption],
    )
    async def meeting_prep_clients(
        request: Request,
        response: Response,
    ) -> list[MeetingClientOption]:
        response.headers["Cache-Control"] = "no-store"
        service = _meeting_preparation_service(request)
        try:
            clients = await run_in_threadpool(service.list_clients)
        except MeetingPreparationError as exc:
            LOGGER.warning("Meeting client listing failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="No se pudo leer la carpeta de clientes de Obsidian.",
            ) from exc
        return [_meeting_client_option(client) for client in clients]

    @app.get(
        "/api/meeting-prep/prepare",
        response_model=MeetingPreparationResponse,
    )
    async def meeting_prep(
        request: Request,
        response: Response,
        client_id: str = Query(min_length=1, max_length=160),
    ) -> MeetingPreparationResponse:
        response.headers["Cache-Control"] = "no-store"
        service = _meeting_preparation_service(request)
        try:
            dossier = await run_in_threadpool(service.prepare, client_id)
        except AmbiguousMeetingClientError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc
        except MeetingClientNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            ) from exc
        except MeetingPreparationError as exc:
            LOGGER.warning("Meeting dossier preparation failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="No se pudo preparar el dossier desde la bóveda.",
            ) from exc
        return _meeting_preparation_response(dossier, service)

    @app.post(
        "/api/meeting-prep/save",
        response_model=MeetingSaveResponse,
        status_code=201,
    )
    async def save_meeting_prep(
        payload: MeetingPreparationRequest,
        request: Request,
        response: Response,
    ) -> MeetingSaveResponse:
        response.headers["Cache-Control"] = "no-store"
        service = _meeting_preparation_service(request)
        try:
            dossier = await run_in_threadpool(service.prepare, payload.client_id)
        except AmbiguousMeetingClientError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except MeetingClientNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except MeetingPreparationError as exc:
            LOGGER.warning("Meeting dossier preparation failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="No se pudo preparar el dossier desde la bóveda.",
            ) from exc

        rendered = _meeting_preparation_response(dossier, service)
        markdown = _meeting_dossier_markdown(rendered)
        vault_root = request.app.state.obsidian_root or Path(
            os.getenv("OBSIDIAN_VAULT", str(Path.home() / "Obsidian"))
        ).expanduser()
        filename, relative_path = await run_in_threadpool(
            _save_meeting_dossier,
            vault_root,
            rendered.client.client_id,
            markdown,
        )
        return MeetingSaveResponse(
            filename=filename,
            relative_path=relative_path,
        )

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
        platform: Literal["linkedin", "substack"] = "linkedin",
    ) -> GhostWriterDestination:
        _, relative_directory = _resolve_obsidian_destination(
            request.app.state.obsidian_root,
            platform=platform,
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
            platform=payload.platform,
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
            author_name=os.getenv("GHOSTWRITER_AUTHOR_NAME", "").strip(),
            author_handle=os.getenv("GHOSTWRITER_AUTHOR_HANDLE", "").strip(),
            publication_name=os.getenv(
                "GHOSTWRITER_PUBLICATION_NAME",
                "Bits To The Bone",
            ).strip(),
            default_tags=os.getenv(
                "GHOSTWRITER_DEFAULT_TAGS",
                "#newsletter #tech #sistemas #ia",
            ).strip(),
        )

    @app.get(
        "/api/ghostwriter/reddit-feed",
        response_model=RedditFeedResponse,
    )
    async def ghostwriter_reddit_feed(
        refresh: bool = False,
    ) -> RedditFeedResponse:
        return await run_in_threadpool(_reddit_feed, refresh)

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


def _meeting_preparation_service(request: Request) -> MeetingPreparationService:
    configured_root = request.app.state.obsidian_root or Path(
        os.getenv("OBSIDIAN_VAULT", str(Path.home() / "Obsidian"))
    ).expanduser()
    return MeetingPreparationService(Path(configured_root))


def _meeting_client_option(
    client: MeetingClientSummary,
) -> MeetingClientOption:
    return MeetingClientOption(
        client_id=client.client_id,
        name=client.name,
        status=client.status,
        source=client.source,
    )


def _meeting_preparation_response(
    dossier: MeetingDossier,
    service: MeetingPreparationService,
) -> MeetingPreparationResponse:
    client = dossier.client
    client_summary = service.summarize_client(client)
    warnings = list(dossier.warnings)
    sources = dossier.related_notes
    if len(sources) > _MAX_MEETING_SOURCES:
        warnings.append(
            f"Se muestran {_MAX_MEETING_SOURCES} de {len(sources)} notas relacionadas."
        )
    rendered_sources: list[MeetingNoteSource] = []
    for note in sources[:_MAX_MEETING_SOURCES]:
        content, was_truncated = _truncate_meeting_text(
            client_context_markdown(note)
        )
        if was_truncated:
            warnings.append(
                f"Se truncó el contenido mostrado de "
                f"{service.relative_path(note.path)}."
            )
        rendered_sources.append(
            MeetingNoteSource(
                path=service.relative_path(note.path),
                title=note.path.stem,
                content=content,
            )
        )

    client_context, was_truncated = _truncate_meeting_text(
        client_context_markdown(client)
    )
    if was_truncated:
        warnings.append("Se truncó el contexto de la ficha del cliente.")
    return MeetingPreparationResponse(
        client=_meeting_client_option(client_summary),
        client_context=client_context,
        active_projects=_meeting_metadata_values(
            client.metadata.get("proyectos_activos")
        ),
        previous_projects=_meeting_metadata_values(
            client.metadata.get("proyectos_previos")
        ),
        sources=rendered_sources,
        warnings=warnings,
    )


def _metadata_string(value: Any) -> str:
    if isinstance(value, str | int | float):
        return str(value).strip()
    return ""


def _meeting_metadata_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        ]
    return []


def _meeting_dossier_markdown(
    dossier: MeetingPreparationResponse,
) -> str:
    generated_at = datetime.now().astimezone().isoformat(timespec="minutes")
    lines = [
        f"# Dossier de reunión: {dossier.client.name}",
        "",
        f"- Cliente: [[{dossier.client.source.removesuffix('.md')}]]",
        f"- Clave de cliente: `{dossier.client.client_id}`",
        f"- Generado: {generated_at}",
        f"- Estado: {dossier.client.status or 'sin estado'}",
        "",
        "## Proyectos activos",
        "",
    ]
    lines.extend(f"- {project}" for project in dossier.active_projects)
    if not dossier.active_projects:
        lines.append("- Sin proyectos activos registrados.")
    lines.extend(("", "## Proyectos anteriores", ""))
    lines.extend(f"- {project}" for project in dossier.previous_projects)
    if not dossier.previous_projects:
        lines.append("- Sin proyectos anteriores registrados.")
    lines.extend(("", "## Contexto del cliente", "", dossier.client_context, ""))
    if dossier.warnings:
        lines.extend(("## Avisos", ""))
        lines.extend(f"- {warning}" for warning in dossier.warnings)
        lines.append("")
    lines.extend(("## Notas relacionadas", ""))
    if not dossier.sources:
        lines.append("No se encontraron notas relacionadas explícitamente.")
    for source in dossier.sources:
        source_link = source.path.removesuffix(".md")
        lines.extend(
            (
                f"### {source.title}",
                "",
                f"Fuente: [[{source_link}]]",
                "",
                source.content.strip(),
                "",
            )
        )
    lines.extend(
        (
            "---",
            "",
            "Dossier generado localmente por BlackBelt. "
            "Verifica las notas fuente antes de compartir o actuar.",
            "",
        )
    )
    return "\n".join(lines)


def _save_meeting_dossier(
    configured_root: Path,
    client_id: str,
    content: str,
) -> tuple[str, str]:
    encoded_content = content.encode("utf-8")
    if len(encoded_content) > _MAX_MEETING_DOSSIER_BYTES:
        raise HTTPException(
            status_code=413,
            detail="El dossier supera el tamaño máximo permitido para guardarlo.",
        )
    try:
        vault_root = configured_root.expanduser().resolve(strict=True)
        if not vault_root.is_dir():
            raise OSError("La bóveda configurada no es una carpeta.")
        destination = vault_root / "020 - DOSSIERES"
        if destination.is_symlink():
            raise HTTPException(
                status_code=503,
                detail="La carpeta 020 - DOSSIERES no puede ser un enlace simbólico.",
            )
        destination.mkdir(exist_ok=True)
        resolved_destination = destination.resolve(strict=True)
        resolved_destination.relative_to(vault_root)
    except HTTPException:
        raise
    except (OSError, ValueError) as exc:
        LOGGER.warning(
            "Meeting dossier folder is unavailable (%s).",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="No se pudo acceder a 020 - DOSSIERES dentro de la bóveda.",
        ) from exc

    if (
        not resolved_destination.is_dir()
        or not os.access(resolved_destination, os.W_OK)
    ):
        raise HTTPException(
            status_code=503,
            detail="La carpeta 020 - DOSSIERES no está disponible para escritura.",
        )

    client_key = _meeting_filename_component(client_id)
    filename_stem = f"{client_key}_{date.today().isoformat()}"
    with _MEETING_SAVE_LOCK:
        for suffix in range(1000):
            numbered_suffix = f"_{suffix:02d}" if suffix else ""
            filename = f"{filename_stem}{numbered_suffix}.md"
            note_path = resolved_destination / filename
            created = False
            phase = "create"
            try:
                with note_path.open(
                    "x",
                    encoding="utf-8",
                    newline="\n",
                ) as note:
                    created = True
                    phase = "write"
                    note.write(content)
                    if not content.endswith("\n"):
                        note.write("\n")
                    phase = "flush"
                    note.flush()
                    phase = "fsync"
                    os.fsync(note.fileno())
                    phase = "close"
                relative_path = note_path.relative_to(vault_root).as_posix()
                return filename, relative_path
            except FileExistsError:
                continue
            except OSError as exc:
                if created:
                    try:
                        note_path.unlink(missing_ok=True)
                    except OSError as cleanup_error:
                        LOGGER.error(
                            "Could not remove incomplete meeting dossier %s "
                            "(%s: %s).",
                            filename,
                            type(cleanup_error).__name__,
                            cleanup_error,
                        )
                LOGGER.error(
                    "Could not save meeting dossier %s during %s "
                    "(%s, errno=%s, winerror=%s, error=%s).",
                    filename,
                    phase,
                    type(exc).__name__,
                    exc.errno,
                    getattr(exc, "winerror", None),
                    exc,
                )
                error_code = getattr(exc, "winerror", None) or exc.errno
                error_code_label = (
                    str(error_code) if error_code is not None else "desconocido"
                )
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "No se pudo guardar el dossier durante "
                        f"{phase} (código {error_code_label})."
                    ),
                ) from exc
    raise HTTPException(
        status_code=409,
        detail="Se agotaron los nombres disponibles para el dossier de hoy.",
    )


def _meeting_filename_component(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", errors="ignore").decode("ascii")
    component = re.sub(r"[^A-Za-z0-9_-]+", "-", ascii_value).strip("-_")
    return component[:80] or "cliente"


def _truncate_meeting_text(content: str) -> tuple[str, bool]:
    if len(content) <= _MAX_MEETING_SOURCE_CHARS:
        return content, False
    marker = "\n\n[Contenido truncado para mantener la respuesta ligera.]"
    return content[:_MAX_MEETING_SOURCE_CHARS - len(marker)] + marker, True


class _FeedTextParser(HTMLParser):
    """Extract a plain-text preview from Reddit's HTML Atom content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.all_parts: list[str] = []
        self.preview_parts: list[str] = []
        self._ignored_depth = 0
        self._preview_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag in {"script", "style"}:
            self._ignored_depth += 1
        elif tag in {"p", "li", "blockquote", "h1", "h2", "h3"}:
            self._preview_depth += 1
        if tag in {"br", "div", "p", "li", "tr"} and not self._ignored_depth:
            self.all_parts.append(" ")
            if self._preview_depth:
                self.preview_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag in {"p", "li", "blockquote", "h1", "h2", "h3"}:
            self._preview_depth = max(0, self._preview_depth - 1)
        if tag in {"div", "p", "li", "tr"} and not self._ignored_depth:
            self.all_parts.append(" ")
            if self._preview_depth:
                self.preview_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.all_parts.append(data)
            if self._preview_depth:
                self.preview_parts.append(data)


def _reddit_feed(force_refresh: bool = False) -> RedditFeedResponse:
    now = time.monotonic()
    with _REDDIT_FEED_CACHE_LOCK:
        cached_until = _REDDIT_FEED_CACHE.get("expires_at", 0.0)
        cached_response = _REDDIT_FEED_CACHE.get("response")
        if (
            cached_response is not None
            and now < cached_until
            and not force_refresh
        ):
            return cached_response

        try:
            with requests.get(
                _REDDIT_FEED_URL,
                headers={
                    "Accept": "application/atom+xml, application/rss+xml",
                    "User-Agent": "BlackBelt/0.1 (local Reddit feed reader)",
                },
                timeout=(3.05, 12),
                allow_redirects=False,
                stream=True,
            ) as response:
                response.raise_for_status()
                content_chunks: list[bytes] = []
                content_size = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    content_size += len(chunk)
                    if content_size > _MAX_REDDIT_FEED_BYTES:
                        raise ValueError("El feed supera el tamaño permitido.")
                    content_chunks.append(chunk)
                content = b"".join(content_chunks)
            if b"\x00" in content:
                raise ValueError("El feed debe usar una codificación XML segura.")
            if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                raise ValueError("El feed contiene declaraciones XML no permitidas.")
            parsed_response = _parse_reddit_feed(content)
        except (
            requests.RequestException,
            ElementTree.ParseError,
            ValueError,
        ) as exc:
            if cached_response is not None:
                LOGGER.warning(
                    "Reddit feed refresh failed; serving cached results (%s).",
                    type(exc).__name__,
                )
                return cached_response
            LOGGER.warning(
                "Reddit feed request failed (%s).",
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="No se pudo actualizar el feed de Reddit. Reinténtalo en unos minutos.",
            ) from exc

        _REDDIT_FEED_CACHE.update(
            response=parsed_response,
            expires_at=time.monotonic() + _REDDIT_FEED_CACHE_SECONDS,
        )
        return parsed_response


def _parse_reddit_feed(content: bytes) -> RedditFeedResponse:
    root = ElementTree.fromstring(content)
    title = _xml_child_text(root, "title") or "Reddit"
    updated = _xml_child_text(root, "updated")
    items: list[RedditFeedItem] = []

    for entry in _xml_children(root, "entry")[:40]:
        item_title = _xml_child_text(entry, "title")
        link = next(
            (
                child.attrib.get("href", "").strip()
                for child in _xml_children(entry, "link")
                if child.attrib.get("rel", "alternate") == "alternate"
            ),
            "",
        )
        if not _is_reddit_link(link) or not item_title:
            continue

        category = next(iter(_xml_children(entry, "category")), None)
        author = _xml_child_text(
            next(iter(_xml_children(entry, "author")), None),
            "name",
        )
        content_node = next(
            (
                child
                for child in list(entry)
                if _xml_local_name(child.tag) in {"content", "summary"}
            ),
            None,
        )
        raw_content = (
            "".join(content_node.itertext()) if content_node is not None else ""
        )
        parser = _FeedTextParser()
        parser.feed(raw_content)
        preview_text = (
            parser.preview_parts
            if parser.preview_parts
            else parser.all_parts if "<" not in raw_content else []
        )
        excerpt = re.sub(r"\s+", " ", "".join(preview_text)).strip()
        if not excerpt:
            excerpt = (
                f"Enlace compartido en r/{category.attrib.get('term', 'reddit')}. "
                "Abre la publicación para consultar el artículo."
                if category is not None
                else "Abre la publicación para consultar el contenido."
            )
        published = (
            _xml_child_text(entry, "published")
            or _xml_child_text(entry, "updated")
        )
        item_id = _xml_child_text(entry, "id") or link
        items.append(
            RedditFeedItem(
                id=item_id[:256],
                title=item_title[:500],
                subreddit=(
                    category.attrib.get("term", "reddit")
                    if category is not None
                    else "reddit"
                )[:100],
                author=author[:100],
                published=published[:64],
                excerpt=excerpt[:500],
                link=link,
            )
        )

    return RedditFeedResponse(title=title[:200], updated=updated[:64], items=items)


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_children(
    parent: ElementTree.Element | None,
    name: str,
) -> list[ElementTree.Element]:
    if parent is None:
        return []
    return [
        child for child in list(parent)
        if _xml_local_name(child.tag) == name
    ]


def _xml_child_text(
    parent: ElementTree.Element | None,
    name: str,
) -> str:
    child = next(iter(_xml_children(parent, name)), None)
    return "".join(child.itertext()).strip() if child is not None else ""


def _is_reddit_link(link: str) -> bool:
    parsed = urlsplit(link)
    return (
        parsed.scheme == "https"
        and parsed.hostname in {"reddit.com", "www.reddit.com"}
        and not parsed.username
        and not parsed.password
    )


def _html_response(
    path: Path,
    *,
    no_cache: bool = False,
) -> FileResponse:
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="No se encontraron los archivos locales de la aplicación.",
        )
    headers = {"Cache-Control": "no-cache"} if no_cache else {}
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        headers=headers,
    )


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
    *,
    platform: Literal["linkedin", "substack"] | None = None,
) -> tuple[Path, Path]:
    root_path = configured_root or Path(
        os.getenv("OBSIDIAN_VAULT", str(Path.home() / "Obsidian"))
    ).expanduser()
    platform_setting = (
        f"GHOSTWRITER_{platform.upper()}_SUBDIR"
        if platform
        else ""
    )
    platform_directory = (
        os.getenv(platform_setting, "").strip()
        if platform_setting
        else ""
    )
    directory_setting = (
        platform_setting
        if platform_directory
        else "GHOSTWRITER_OBSIDIAN_SUBDIR"
    )
    raw_relative_directory = platform_directory or os.getenv(
        directory_setting,
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
            detail=f"{directory_setting} debe ser una ruta relativa segura.",
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
    asset_paths = {
        "soma": root / "soma" / "somaguard.html",
        "ghostwriter": root / "ghostwriter" / "ghostwriter_ai_studio.html",
        "meetings": root / "meetings" / "index.html",
    }
    try:
        return asset_paths[application]
    except KeyError as exc:
        raise ValueError("Aplicación web no soportada.") from exc


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
