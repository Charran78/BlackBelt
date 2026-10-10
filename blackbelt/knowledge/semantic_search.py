"""Incremental, local-first hybrid search over an Obsidian vault."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx
import ollama
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from blackbelt.knowledge.provenance import (
    SourceChunk,
    SourceDocument,
    SourceProvenance,
    parse_markdown_source,
)

_COLLECTION_NAME = "obsidian_notes"
_INDEX_SCHEMA_VERSION = 3
_MAX_NOTE_BYTES = 2 * 1024 * 1024
_EMBED_BATCH_SIZE = 16
_SEARCH_CANDIDATE_MULTIPLIER = 8
_MAX_SEARCH_CANDIDATES = 100
_RRF_CONSTANT = 60
_PROJECT_FOLDER = "001 - PROYECTOS"
_PROJECT_OVERVIEW_TERMS = frozenset(
    {
        "ahora",
        "actual",
        "actuales",
        "abierto",
        "abiertos",
        "activo",
        "activos",
        "cartera",
        "curso",
        "estado",
        "marcha",
        "portfolio",
        "seguimiento",
        "tengo",
        "ver",
        "lista",
        "listar",
    }
)
_WIKILINK_PATTERN = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
_OBSIDIAN_IMAGE_PATTERN = re.compile(r"!\[\[[^\]]+\]\]")
_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_STOP_WORDS = frozenset(
    [
        "a",
        "al",
        "algo",
        "algunas",
        "algunos",
        "ante",
        "antes",
        "como",
        "con",
        "contra",
        "cual",
        "cuales",
        "cuando",
        "de",
        "del",
        "desde",
        "donde",
        "durante",
        "e",
        "el",
        "ella",
        "ellas",
        "ello",
        "ellos",
        "en",
        "entre",
        "era",
        "eran",
        "es",
        "esa",
        "esas",
        "ese",
        "eso",
        "esos",
        "esta",
        "estaba",
        "estaban",
        "estas",
        "este",
        "esto",
        "estos",
        "fue",
        "fueron",
        "ha",
        "habia",
        "han",
        "hasta",
        "hay",
        "la",
        "las",
        "le",
        "les",
        "lo",
        "los",
        "mas",
        "me",
        "mi",
        "mis",
        "mucho",
        "muy",
        "nos",
        "o",
        "os",
        "otra",
        "otras",
        "otro",
        "otros",
        "para",
        "pero",
        "poco",
        "por",
        "porque",
        "que",
        "quien",
        "quienes",
        "se",
        "sea",
        "sean",
        "ser",
        "sobre",
        "su",
        "sus",
        "te",
        "tiene",
        "tienen",
        "todo",
        "todos",
        "tu",
        "tus",
        "un",
        "una",
        "unas",
        "uno",
        "unos",
        "y",
        "ya",
        "the",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "this",
        "to",
        "was",
        "were",
        "with",
    ]
)
_EXCLUDED_DIRECTORY_NAMES = {
    "000 - plantillas",
    "006 - credenciales",
    "022 - planes_borrador",
}


class SemanticSearchError(RuntimeError):
    """Raised when indexing or searching cannot be completed safely."""


class EmbeddingProvider(Protocol):
    """Local text embedding interface, replaceable in deterministic tests."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one finite, non-empty vector for each input text."""


@dataclass(frozen=True)
class SearchIndexStats:
    """Summary of one idempotent vault indexing pass."""

    scanned: int
    indexed: int
    unchanged: int
    removed: int
    skipped: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class SearchIndexProgress:
    """Progress event emitted while scanning or checkpointing index batches."""

    stage: Literal["scan", "embed", "cooldown", "cleanup", "complete"]
    current: int
    total: int
    detail: str
    indexed: int = 0
    unchanged: int = 0
    chunks_completed: int = 0
    chunks_total: int = 0
    batch_index: int = 0
    batch_count: int = 0


class _IndexPacer:
    """Insert a cooldown between embedding requests after a work interval."""

    def __init__(
        self,
        *,
        work_interval_seconds: float,
        cooldown_seconds: float,
        progress_callback: Callable[[SearchIndexProgress], None] | None,
    ) -> None:
        self._work_interval_seconds = work_interval_seconds
        self._cooldown_seconds = cooldown_seconds
        self._progress_callback = progress_callback
        self._work_started = time.monotonic()
        self._worked_seconds = 0.0

    def after_embedding_batch(
        self,
        *,
        batch_index: int,
        batch_count: int,
        source_path: str,
        chunks_completed: int,
        chunks_total: int,
        indexed: int,
        unchanged: int,
    ) -> None:
        now = time.monotonic()
        self._worked_seconds += now - self._work_started
        self._work_started = now
        if (
            self._work_interval_seconds <= 0
            or self._worked_seconds < self._work_interval_seconds
        ):
            return

        self._worked_seconds = 0.0
        if self._cooldown_seconds <= 0:
            return

        deadline = now + self._cooldown_seconds
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            SemanticSearchService._notify_index_progress(
                self._progress_callback,
                SearchIndexProgress(
                    stage="cooldown",
                    current=max(
                        0,
                        math.ceil(self._cooldown_seconds - remaining),
                    ),
                    total=math.ceil(self._cooldown_seconds),
                    detail=(
                        f"Descanso tras {source_path} · "
                        f"{math.ceil(remaining)} s restantes"
                    ),
                    indexed=indexed,
                    unchanged=unchanged,
                    chunks_completed=chunks_completed,
                    chunks_total=chunks_total,
                    batch_index=batch_index,
                    batch_count=batch_count,
                ),
            )
            if remaining <= 0:
                break
            time.sleep(min(1.0, remaining))
        self._work_started = time.monotonic()


@dataclass(frozen=True)
class SearchHit:
    """A fused search result with auditable Obsidian source metadata."""

    path: str
    title: str
    content: str
    wikilinks: tuple[str, ...]
    score: float
    semantic_rank: int | None
    lexical_rank: int | None
    provenance: SourceProvenance | None = None


@dataclass(frozen=True)
class IndexedSource:
    """Full source text after validating its indexed hash and allowed path."""

    path: str
    title: str
    source_hash: str
    content: str


@dataclass(frozen=True)
class _ProjectInventoryChunk:
    content: str
    section_titles_json: str
    chunk_index: int
    chunk_count: int
    char_start: int
    char_end: int
    chunk_hash: str


@dataclass
class _ProjectInventorySource:
    source_id: str
    folder: str
    title: str
    frontmatter: dict[str, object]
    source_hash: str
    wikilinks: tuple[str, ...]
    chunks: list[_ProjectInventoryChunk]


@dataclass(frozen=True)
class _RankedHit:
    """Validated result returned by one search backend."""

    chunk_id: str
    path: str
    title: str
    content: str
    wikilinks: tuple[str, ...]
    provenance: SourceProvenance


@dataclass(frozen=True)
class _ActiveChunk:
    """Canonical SQLite identity used to validate backend payloads."""

    source_id: str
    path: str
    folder: str
    title: str
    frontmatter_json: str
    wikilinks: tuple[str, ...]
    source_hash: str
    chunk_hash: str
    chunk_index: int
    chunk_count: int
    section_titles: tuple[str, ...]
    char_start: int
    char_end: int


@dataclass
class _SearchCandidate:
    """Mutable accumulator for the reciprocal-rank fusion step."""

    path: str
    title: str
    content: str
    wikilinks: tuple[str, ...]
    content_match_count: int
    section_match_count: int = 0
    provenance: SourceProvenance | None = None
    meaningful_term_count: int = 0
    score: float = 0.0
    semantic_rank: int | None = None
    lexical_rank: int | None = None


class OllamaEmbeddingProvider:
    """Create embeddings through a loopback-only Ollama endpoint."""

    def __init__(
        self,
        model: str,
        *,
        timeout: int = 180,
        keep_alive: str | int = "0",
        num_thread: int | None = None,
    ) -> None:
        self._model = model
        self._keep_alive = keep_alive
        self._options = {"num_thread": num_thread} if num_thread is not None else None
        host = os.getenv("OLLAMA_HOST", "").strip()
        if host and not _is_loopback_ollama_host(host):
            raise SemanticSearchError(
                "La búsqueda solo admite un endpoint Ollama local "
                "(localhost o loopback)."
            )
        self._client = ollama.Client(
            host=host or None,
            timeout=timeout,
            trust_env=False,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed text without sending notes to a remote service."""
        if not texts:
            return []
        try:
            response = self._client.embed(
                model=self._model,
                input=list(texts),
                keep_alive=self._keep_alive,
                options=self._options,
            )
        except (httpx.ConnectError, httpx.TimeoutException):
            time.sleep(0.25)
            try:
                response = self._client.embed(
                    model=self._model,
                    input=list(texts),
                    keep_alive=self._keep_alive,
                    options=self._options,
                )
            except (ollama.ResponseError, httpx.HTTPError) as retry_exc:
                raise self._embedding_error() from retry_exc
        except (ollama.ResponseError, httpx.HTTPError) as exc:
            raise self._embedding_error() from exc

        vectors: list[list[float]] = []
        try:
            for raw_vector in response.embeddings:
                vector = [float(value) for value in raw_vector]
                if not vector or not all(math.isfinite(value) for value in vector):
                    raise ValueError("El embedding está vacío o no es finito.")
                vectors.append(vector)
        except (AttributeError, TypeError, ValueError) as exc:
            raise SemanticSearchError(
                "Ollama devolvió un formato de embedding no válido."
            ) from exc
        if len(vectors) != len(texts):
            raise SemanticSearchError(
                "Ollama devolvió una cantidad de embeddings inesperada."
            )
        return vectors

    def _embedding_error(self) -> SemanticSearchError:
        return SemanticSearchError(
            "No se pudieron generar embeddings con Ollama local. "
            "Comprueba que el servicio está activo y que existe "
            f"el modelo '{self._model}'."
        )


class SemanticSearchService:
    """Maintain a local Qdrant vector index and SQLite FTS5 index."""

    def __init__(
        self,
        vault: Path,
        index_dir: Path,
        *,
        model: str = "nomic-embed-text",
        embedder: EmbeddingProvider | None = None,
        embedding_timeout: int = 180,
        min_cosine_score: float = 0.65,
        include_dirs: Sequence[str] | None = None,
        max_note_bytes: int = _MAX_NOTE_BYTES,
        embedding_keep_alive: str | int = "0",
        embedding_batch_size: int = _EMBED_BATCH_SIZE,
        embedding_num_thread: int | None = None,
        thinking_filter_dirs: Sequence[str] = (),
    ) -> None:
        if not model.strip():
            raise ValueError("El modelo de embeddings no puede estar vacío.")
        if not -1.0 <= min_cosine_score <= 1.0:
            raise ValueError("El umbral de similitud debe estar entre -1 y 1.")
        if max_note_bytes < 1:
            raise ValueError("El límite de tamaño por nota debe ser positivo.")
        if embedding_batch_size < 1:
            raise ValueError("El lote de embeddings debe ser positivo.")
        if embedding_num_thread is not None and embedding_num_thread < 1:
            raise ValueError("El número de hilos debe ser positivo.")
        self._vault = vault.expanduser()
        self._model = model.strip()
        self._min_cosine_score = min_cosine_score
        self._include_dirs = _normalize_include_dirs(include_dirs)
        self._max_note_bytes = max_note_bytes
        self._embedding_batch_size = embedding_batch_size
        self._thinking_filter_dirs = (
            _normalize_include_dirs(thinking_filter_dirs) or ()
            if thinking_filter_dirs
            else ()
        )
        index_identity = (
            f"{_INDEX_SCHEMA_VERSION}\0{self._model}"
            f"\0thinking_filter_dirs={','.join(self._thinking_filter_dirs)}"
        )
        model_key = hashlib.sha256(index_identity.encode("utf-8")).hexdigest()[:12]
        self._model_dir = index_dir.expanduser() / model_key
        self._qdrant_dir = self._model_dir / "qdrant"
        self._database_path = self._model_dir / "lexical.sqlite3"
        self._embedder = embedder or OllamaEmbeddingProvider(
            self._model,
            timeout=embedding_timeout,
            keep_alive=embedding_keep_alive,
            num_thread=embedding_num_thread,
        )

    def index(
        self,
        *,
        batch_size: int = 5,
        work_interval_seconds: float = 60.0,
        cooldown_seconds: float = 60.0,
        progress_callback: Callable[[SearchIndexProgress], None] | None = None,
    ) -> SearchIndexStats:
        """Hash sources, checkpoint each note, and pace embedding requests."""
        if batch_size < 1:
            raise ValueError("El lote de fuentes debe ser positivo.")
        if work_interval_seconds < 0:
            raise ValueError("El intervalo de trabajo no puede ser negativo.")
        if cooldown_seconds < 0:
            raise ValueError("La pausa entre solicitudes no puede ser negativa.")
        try:
            vault_root = self._vault.resolve(strict=True)
        except OSError as exc:
            raise SemanticSearchError(
                f"No se puede acceder a la bóveda configurada: {exc}"
            ) from exc
        if not vault_root.is_dir():
            raise SemanticSearchError(
                "La ruta configurada para Obsidian no es una carpeta."
            )

        try:
            resolved_model_dir = self._model_dir.resolve()
            resolved_model_dir.relative_to(vault_root)
        except ValueError:
            pass
        else:
            raise SemanticSearchError(
                "El índice debe guardarse fuera de la bóveda de Obsidian."
            )
        try:
            vault_root.relative_to(resolved_model_dir)
        except ValueError:
            pass
        else:
            raise SemanticSearchError(
                "El directorio del índice no puede contener la bóveda."
            )

        safe_paths, warnings = self._load_note_paths(vault_root)
        self._model_dir.mkdir(parents=True, exist_ok=True)
        with closing(self._open_database()) as database:
            indexed_sources = dict(
                database.execute("SELECT path, source_hash FROM sources").fetchall()
            )
        scanned_paths: set[str] = set()
        changed_paths: list[tuple[str, Path]] = []
        unchanged = 0
        for current, (relative, path) in enumerate(safe_paths, start=1):
            try:
                if path.stat().st_size > self._max_note_bytes:
                    warnings.append(f"Se omitió una nota demasiado grande: {relative}")
                else:
                    scanned_paths.add(relative)
                    content = path.read_text(encoding="utf-8")
                    source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    if indexed_sources.get(relative) == source_hash:
                        unchanged += 1
                    else:
                        changed_paths.append((relative, path))
            except (OSError, UnicodeDecodeError) as exc:
                scanned_paths.add(relative)
                warnings.append(f"No se pudo leer {relative} ({type(exc).__name__}).")
            self._notify_index_progress(
                progress_callback,
                SearchIndexProgress(
                    stage="scan",
                    current=current,
                    total=len(safe_paths),
                    detail=f"Comparando hashes: {relative}",
                    indexed=0,
                    unchanged=unchanged,
                ),
            )

        deleted_paths = sorted(set(indexed_sources) - scanned_paths)
        removed = self._remove_indexed_paths(deleted_paths)
        batch_count = math.ceil(len(changed_paths) / batch_size)
        indexed = 0
        pacer = _IndexPacer(
            work_interval_seconds=work_interval_seconds,
            cooldown_seconds=cooldown_seconds,
            progress_callback=progress_callback,
        )
        for batch_index, start in enumerate(
            range(0, len(changed_paths), batch_size),
            start=1,
        ):
            path_batch = changed_paths[start : start + batch_size]
            for relative, path in path_batch:
                try:
                    if path.stat().st_size > self._max_note_bytes:
                        scanned_paths.discard(relative)
                        warnings.append(
                            f"Se omitió una nota que superó el límite: {relative}"
                        )
                        continue
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    warnings.append(
                        f"No se pudo leer {relative} para indexar "
                        f"({type(exc).__name__})."
                    )
                    continue
                if hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest() == indexed_sources.get(relative):
                    unchanged += 1
                    continue
                note = parse_markdown_source(
                    relative,
                    content,
                    exclude_thinking_blocks=any(
                        relative.casefold().startswith(f"{directory.casefold()}/")
                        for directory in self._thinking_filter_dirs
                    ),
                )
                warnings.extend(
                    f"Frontmatter de {relative}: {warning}" for warning in note.warnings
                )
                self._index_note_batch(
                    note,
                    batch_index=batch_index,
                    batch_count=batch_count,
                    progress_callback=progress_callback,
                    pacer=pacer,
                    completed_sources=indexed,
                    total_sources=len(changed_paths),
                    unchanged=unchanged,
                )
                indexed += 1

        self._cleanup_orphaned_vectors(progress_callback)
        self._notify_index_progress(
            progress_callback,
            SearchIndexProgress(
                stage="complete",
                current=len(safe_paths),
                total=len(safe_paths),
                detail="Índice guardado y sincronizado.",
                indexed=indexed,
                unchanged=unchanged,
                batch_index=batch_count,
                batch_count=batch_count,
            ),
        )

        return SearchIndexStats(
            scanned=len(scanned_paths),
            indexed=indexed,
            unchanged=unchanged,
            removed=removed,
            skipped=len(warnings),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _notify_index_progress(
        callback: Callable[[SearchIndexProgress], None] | None,
        event: SearchIndexProgress,
    ) -> None:
        if callback is not None:
            callback(event)

    def _index_note_batch(
        self,
        note: SourceDocument,
        *,
        batch_index: int,
        batch_count: int,
        progress_callback: Callable[[SearchIndexProgress], None] | None,
        pacer: _IndexPacer,
        completed_sources: int,
        total_sources: int,
        unchanged: int,
    ) -> None:
        source_paths = {note.path}
        with closing(self._open_database()) as database:
            old_chunk_ids = self._chunk_ids_for_paths(database, source_paths)

        chunk_total = len(note.chunks)
        chunk_completed = 0
        client = self._open_qdrant()
        try:
            vector_dimension: int | None = None
            for start in range(0, len(note.chunks), self._embedding_batch_size):
                pending_chunks = [
                    (note, chunk)
                    for chunk in note.chunks[start : start + self._embedding_batch_size]
                ]
                vector_dimension = self._upsert_chunk_batch(
                    client,
                    pending_chunks,
                    vector_dimension,
                )
                chunk_completed += len(pending_chunks)
                self._notify_index_progress(
                    progress_callback,
                    SearchIndexProgress(
                        stage="embed",
                        current=completed_sources,
                        total=total_sources,
                        detail=(
                            f"Grupo {batch_index}/{batch_count} · "
                            f"{note.path} · fragmentos "
                            f"{chunk_completed}/{chunk_total}"
                        ),
                        indexed=completed_sources,
                        chunks_completed=chunk_completed,
                        chunks_total=chunk_total,
                        batch_index=batch_index,
                        batch_count=batch_count,
                    ),
                )
                pacer.after_embedding_batch(
                    batch_index=batch_index,
                    batch_count=batch_count,
                    source_path=note.path,
                    chunks_completed=chunk_completed,
                    chunks_total=chunk_total,
                    indexed=completed_sources,
                    unchanged=unchanged,
                )

            with closing(self._open_database()) as database:
                self._replace_source_records(database, [note], [])
                active_ids = self._chunk_ids_for_paths(database, source_paths)

            stale_ids = old_chunk_ids - active_ids
            if stale_ids and client.collection_exists(_COLLECTION_NAME):
                client.delete(
                    collection_name=_COLLECTION_NAME,
                    points_selector=sorted(stale_ids),
                    wait=True,
                )
        except (
            OSError,
            RuntimeError,
            sqlite3.Error,
            UnexpectedResponse,
            ValueError,
        ) as exc:
            raise SemanticSearchError(
                f"No se pudo guardar el lote {batch_index}/{batch_count}: {exc}"
            ) from exc
        finally:
            client.close()

        self._notify_index_progress(
            progress_callback,
            SearchIndexProgress(
                stage="embed",
                current=min(completed_sources + 1, total_sources),
                total=total_sources,
                detail=f"Checkpoint confirmado: {note.path}",
                indexed=completed_sources + 1,
                chunks_completed=chunk_total,
                chunks_total=chunk_total,
                batch_index=batch_index,
                batch_count=batch_count,
            ),
        )

    def _upsert_chunk_batch(
        self,
        client: QdrantClient,
        pending: list[tuple[SourceDocument, SourceChunk]],
        previous_dimension: int | None,
    ) -> int:
        vectors = self._embed_chunk_batch(pending, previous_dimension)
        dimension = len(vectors[0])
        self._ensure_collection(client, dimension)
        client.upsert(
            collection_name=_COLLECTION_NAME,
            points=self._qdrant_points(pending, vectors),
            wait=True,
        )
        return dimension

    def _embed_chunk_batch(
        self,
        pending_chunks: list[tuple[SourceDocument, SourceChunk]],
        previous_dimension: int | None,
    ) -> list[list[float]]:
        texts = [
            f"{note.title}\n{chunk.section_path}\n\n{chunk.content}".strip()
            for note, chunk in pending_chunks
        ]
        vectors = self._embedder.embed(texts)
        if len(vectors) != len(pending_chunks):
            raise SemanticSearchError(
                "El proveedor de embeddings devolvió una cantidad incorrecta."
            )
        dimension = len(vectors[0]) if vectors else 0
        if dimension == 0 or any(
            len(vector) != dimension
            or not all(math.isfinite(value) for value in vector)
            for vector in vectors
        ):
            raise SemanticSearchError(
                "Los embeddings no tienen dimensiones válidas y consistentes."
            )
        if previous_dimension is not None and previous_dimension != dimension:
            raise SemanticSearchError(
                "El modelo devolvió dimensiones distintas entre lotes."
            )
        return vectors

    def _remove_indexed_paths(self, deleted_paths: list[str]) -> int:
        if not deleted_paths:
            return 0
        with closing(self._open_database()) as database:
            self._replace_source_records(database, [], deleted_paths)
        return len(deleted_paths)

    def _cleanup_orphaned_vectors(
        self,
        progress_callback: Callable[[SearchIndexProgress], None] | None,
    ) -> None:
        self._notify_index_progress(
            progress_callback,
            SearchIndexProgress(
                stage="cleanup",
                current=0,
                total=1,
                detail="Sincronizando vectores y checkpoints.",
            ),
        )
        if not self._qdrant_dir.is_dir():
            return
        with closing(self._open_database()) as database:
            active_ids = self._active_chunk_ids(database)
        client = self._open_qdrant()
        try:
            if client.collection_exists(_COLLECTION_NAME):
                orphaned_ids = self._orphaned_chunk_ids(client, active_ids)
                if orphaned_ids:
                    client.delete(
                        collection_name=_COLLECTION_NAME,
                        points_selector=sorted(orphaned_ids),
                        wait=True,
                    )
        except (
            OSError,
            RuntimeError,
            UnexpectedResponse,
            ValueError,
        ) as exc:
            raise SemanticSearchError(
                f"No se pudo finalizar la sincronización vectorial: {exc}"
            ) from exc
        finally:
            client.close()
        self._notify_index_progress(
            progress_callback,
            SearchIndexProgress(
                stage="cleanup",
                current=1,
                total=1,
                detail="Vectores sincronizados.",
            ),
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        progress_callback: Callable[[str], None] | None = None,
    ) -> list[SearchHit]:
        """Fuse semantic and BM25 rankings while rejecting stale vectors."""
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Escribe una consulta para buscar.")
        if not 1 <= limit <= 50:
            raise ValueError("El límite de resultados debe estar entre 1 y 50.")
        if not self._database_path.is_file():
            raise SemanticSearchError(
                "Todavía no hay índice. Ejecuta `blackbelt run search index`."
            )
        if _is_project_overview_query(normalized_query):
            if progress_callback is not None:
                progress_callback("Consultando el inventario de proyectos en SQLite...")
            return self._project_overview(limit)
        query_terms = _search_terms(normalized_query)

        if progress_callback is not None:
            progress_callback("Buscando coincidencias léxicas en SQLite (BM25)...")
        with closing(self._open_database()) as database:
            active_chunks = {
                str(row[0]): _ActiveChunk(
                    source_id=str(row[1]),
                    path=str(row[2]),
                    folder=str(row[3]),
                    title=str(row[4]),
                    frontmatter_json=str(row[5]),
                    wikilinks=tuple(_decode_json_string_list(str(row[6]))),
                    source_hash=str(row[7]),
                    chunk_hash=str(row[8]),
                    chunk_index=int(row[9]),
                    chunk_count=int(row[10]),
                    section_titles=tuple(_decode_json_string_list(str(row[11]))),
                    char_start=int(row[12]),
                    char_end=int(row[13]),
                )
                for row in database.execute(
                    """
                    SELECT
                        chunks.chunk_id, sources.source_id, sources.path,
                        sources.folder, sources.title, sources.frontmatter_json,
                        sources.wikilinks_json, chunks.source_hash,
                        chunks.chunk_hash, chunks.chunk_index, chunks.chunk_count,
                        chunks.section_titles_json, chunks.char_start, chunks.char_end
                    FROM chunks
                    JOIN sources USING (source_id)
                    """
                ).fetchall()
            }
            if not active_chunks:
                return []
            if not query_terms:
                return []
            candidate_limit = min(
                max(limit * _SEARCH_CANDIDATE_MULTIPLIER, 20),
                _MAX_SEARCH_CANDIDATES,
            )
            lexical_results = self._lexical_search(
                database,
                query_terms,
                candidate_limit,
            )

        if progress_callback is not None:
            progress_callback("Calculando el embedding local de la consulta...")
        query_vectors = self._embedder.embed([normalized_query])
        if (
            len(query_vectors) != 1
            or not query_vectors[0]
            or not all(math.isfinite(value) for value in query_vectors[0])
        ):
            raise SemanticSearchError(
                "No se pudo generar un embedding válido para la consulta."
            )
        query_vector = query_vectors[0]
        if progress_callback is not None:
            progress_callback("Buscando pasajes cercanos en Qdrant local...")
        client = self._open_qdrant()
        try:
            semantic_results = self._semantic_search(
                client,
                query_vector,
                candidate_limit,
                self._min_cosine_score,
            )
        except (
            OSError,
            RuntimeError,
            UnexpectedResponse,
            ValueError,
        ) as exc:
            raise SemanticSearchError(
                f"No se pudo consultar el índice vectorial local: {exc}"
            ) from exc
        finally:
            client.close()

        if progress_callback is not None:
            progress_callback("Fusionando resultados y validando su procedencia...")
        fused: dict[str, _SearchCandidate] = {}
        self._accumulate_ranked_hits(
            fused,
            lexical_results,
            active_chunks,
            query_terms,
            rank_name="lexical_rank",
        )
        self._accumulate_ranked_hits(
            fused,
            semantic_results,
            active_chunks,
            query_terms,
            rank_name="semantic_rank",
        )

        best_by_path = _best_candidate_by_path(fused.values())

        ordered = sorted(
            best_by_path.values(),
            key=lambda candidate: (
                -candidate.score,
                candidate.path.casefold(),
            ),
        )
        return [
            SearchHit(
                path=candidate.path,
                title=candidate.title,
                content=_make_snippet(candidate.content, query_terms),
                wikilinks=candidate.wikilinks,
                score=candidate.score,
                semantic_rank=candidate.semantic_rank,
                lexical_rank=candidate.lexical_rank,
                provenance=candidate.provenance,
            )
            for candidate in ordered[:limit]
        ]

    def read_indexed_source(self, relative_path: str) -> IndexedSource:
        """Read an indexed Markdown source only if its indexed hash is current."""
        if not self._database_path.is_file():
            raise SemanticSearchError(
                "Todavía no hay índice. Ejecuta `blackbelt run companion index`."
            )
        normalized = relative_path.replace("\\", "/")
        source_path = PurePosixPath(normalized)
        if (
            source_path.is_absolute()
            or not source_path.parts
            or any(part in {"", ".", ".."} for part in source_path.parts)
            or not self._path_is_in_scope(source_path)
        ):
            raise SemanticSearchError(
                "La fuente solicitada está fuera del alcance permitido."
            )

        with closing(self._open_database()) as database:
            row = database.execute(
                "SELECT title, source_hash FROM sources WHERE path = ?",
                (source_path.as_posix(),),
            ).fetchone()
        if row is None:
            raise SemanticSearchError(
                f"La fuente no está en el índice actual: {source_path.as_posix()}"
            )

        vault_root = self._vault.resolve(strict=True)
        candidate = vault_root.joinpath(*source_path.parts)
        if any(
            _is_link_or_junction(vault_root.joinpath(*source_path.parts[:index]))
            for index in range(1, len(source_path.parts) + 1)
        ):
            raise SemanticSearchError("La fuente no puede atravesar enlaces.")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(vault_root)
            if not resolved.is_file():
                raise SemanticSearchError("La fuente no es un archivo.")
            if resolved.stat().st_size > self._max_note_bytes:
                raise SemanticSearchError(
                    "La fuente supera el límite configurado; actualiza la "
                    "configuración y vuelve a indexar."
                )
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            if isinstance(exc, SemanticSearchError):
                raise
            raise SemanticSearchError(
                "No se pudo leer la fuente indexada de forma segura."
            ) from exc

        current_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if current_hash != str(row[1]):
            raise SemanticSearchError(
                "La fuente cambió desde la indexación; ejecuta "
                "`blackbelt run companion index` antes de volver a consultarla."
            )
        return IndexedSource(
            path=source_path.as_posix(),
            title=str(row[0]),
            source_hash=current_hash,
            content=content,
        )

    def _path_is_in_scope(self, path: PurePosixPath) -> bool:
        if self._include_dirs is None:
            return True
        normalized_path = path.as_posix().casefold()
        return any(
            normalized_path == include_dir.casefold()
            or normalized_path.startswith(f"{include_dir.casefold()}/")
            for include_dir in self._include_dirs
        )

    def _project_overview(self, limit: int) -> list[SearchHit]:
        """List project notes even when their titles do not match the query."""
        with closing(self._open_database()) as database:
            rows = database.execute(
                """
                SELECT
                    sources.source_id,
                    sources.path,
                    sources.folder,
                    sources.title,
                    sources.frontmatter_json,
                    sources.source_hash,
                    sources.wikilinks_json,
                    chunks.chunk_id,
                    chunks.chunk_index,
                    chunks.chunk_count,
                    chunks.content,
                    chunks.section_titles_json,
                    chunks.char_start,
                    chunks.char_end,
                    chunks.chunk_hash
                FROM sources
                LEFT JOIN chunks ON chunks.source_id = sources.source_id
                WHERE sources.path LIKE ?
                ORDER BY sources.path, chunks.chunk_index
                """,
                (f"{_PROJECT_FOLDER}/%",),
            ).fetchall()

        grouped: dict[str, _ProjectInventorySource] = {}
        for row in rows:
            (
                source_id,
                path,
                folder,
                title,
                frontmatter_json,
                source_hash,
                wikilinks_json,
                chunk_id,
                chunk_index,
                chunk_count,
                content,
                section_titles_json,
                char_start,
                char_end,
                chunk_hash,
            ) = row
            normalized_path = str(path)
            if normalized_path not in grouped:
                grouped[normalized_path] = _ProjectInventorySource(
                    source_id=str(source_id),
                    folder=str(folder),
                    title=str(title),
                    frontmatter=_decode_json_object(str(frontmatter_json)),
                    source_hash=str(source_hash),
                    wikilinks=tuple(_decode_json_string_list(str(wikilinks_json))),
                    chunks=[],
                )
            if chunk_id is not None:
                grouped[normalized_path].chunks.append(
                    _ProjectInventoryChunk(
                        content=str(content),
                        section_titles_json=str(section_titles_json),
                        chunk_index=int(chunk_index),
                        chunk_count=int(chunk_count),
                        char_start=int(char_start),
                        char_end=int(char_end),
                        chunk_hash=str(chunk_hash),
                    )
                )

        results: list[SearchHit] = []
        for path, source in sorted(
            grouped.items(),
            key=lambda item: item[0].casefold(),
        )[:limit]:
            searchable_chunks = [
                chunk for chunk in source.chunks if _has_searchable_body(chunk.content)
            ]
            if searchable_chunks:
                selected_chunk = max(
                    searchable_chunks,
                    key=lambda chunk: _project_chunk_quality(chunk.content),
                )
                preview = _make_snippet(selected_chunk.content, [])
                provenance = SourceProvenance(
                    source_id=source.source_id,
                    folder=source.folder,
                    frontmatter=source.frontmatter,
                    section_titles=tuple(
                        json.loads(selected_chunk.section_titles_json)
                    ),
                    chunk_index=selected_chunk.chunk_index,
                    chunk_count=selected_chunk.chunk_count,
                    char_start=selected_chunk.char_start,
                    char_end=selected_chunk.char_end,
                    source_hash=source.source_hash,
                    chunk_hash=selected_chunk.chunk_hash,
                )
            else:
                preview = (
                    "La nota no contiene descripción textual indexable; "
                    "abre la fuente para ver sus imágenes o enlaces."
                )
                provenance = SourceProvenance(
                    source_id=source.source_id,
                    folder=source.folder,
                    frontmatter=source.frontmatter,
                    section_titles=(),
                    chunk_index=None,
                    chunk_count=0,
                    char_start=None,
                    char_end=None,
                    source_hash=source.source_hash,
                    chunk_hash=None,
                )
            results.append(
                SearchHit(
                    path=path,
                    title=source.title,
                    content=preview,
                    wikilinks=source.wikilinks,
                    score=0.0,
                    semantic_rank=None,
                    lexical_rank=None,
                    provenance=provenance,
                )
            )
        return results

    def _load_note_paths(
        self,
        vault_root: Path,
    ) -> tuple[list[tuple[str, Path]], list[str]]:
        safe_paths: list[tuple[str, Path]] = []
        warnings: list[str] = []
        walk_roots = self._walk_roots(vault_root)

        def record_walk_error(error: OSError) -> None:
            raise SemanticSearchError(
                "No se pudo completar el inventario de carpetas; el índice no "
                "se ha modificado."
            ) from error

        for walk_root in walk_roots:
            for current, directory_names, filenames in os.walk(
                walk_root,
                followlinks=False,
                onerror=record_walk_error,
            ):
                current_path = Path(current)
                directory_names[:] = sorted(
                    (
                        name
                        for name in directory_names
                        if not name.startswith(".")
                        and name.casefold() not in _EXCLUDED_DIRECTORY_NAMES
                        and not _is_link_or_junction(current_path / name)
                    ),
                    key=str.casefold,
                )
                for filename in filenames:
                    if Path(filename).suffix.casefold() != ".md":
                        continue
                    candidate = current_path / filename
                    if _is_link_or_junction(candidate):
                        continue
                    try:
                        resolved = candidate.resolve(strict=True)
                        relative = resolved.relative_to(vault_root)
                    except (OSError, ValueError):
                        continue
                    if any(
                        part.startswith(".")
                        or part.casefold() in _EXCLUDED_DIRECTORY_NAMES
                        for part in relative.parts
                    ):
                        continue
                    safe_paths.append((relative.as_posix(), resolved))

        safe_paths.sort(key=lambda item: item[0].casefold())
        return safe_paths, warnings

    def _walk_roots(self, vault_root: Path) -> tuple[Path, ...]:
        if self._include_dirs is None:
            return (vault_root,)

        roots: list[Path] = []
        for relative_dir in self._include_dirs:
            candidate = vault_root.joinpath(*PurePosixPath(relative_dir).parts)
            if _is_link_or_junction(candidate):
                raise SemanticSearchError(
                    f"La carpeta incluida no puede ser un enlace: {relative_dir}"
                )
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(vault_root)
            except (OSError, ValueError) as exc:
                raise SemanticSearchError(
                    "No se puede acceder a la carpeta incluida "
                    f"'{relative_dir}'; el índice no se ha modificado."
                ) from exc
            if not resolved.is_dir():
                raise SemanticSearchError(
                    f"La ruta incluida no es una carpeta: {relative_dir}"
                )
            roots.append(resolved)
        return tuple(roots)

    @staticmethod
    def _qdrant_points(
        pending_chunks: list[tuple[SourceDocument, SourceChunk]],
        vectors: list[list[float]],
    ) -> list[models.PointStruct]:
        points: list[models.PointStruct] = []
        for (note, chunk), vector in zip(
            pending_chunks,
            vectors,
            strict=True,
        ):
            points.append(
                models.PointStruct(
                    id=chunk.chunk_id,
                    vector=vector,
                    payload={
                        "chunk_id": chunk.chunk_id,
                        "source_id": note.source_id,
                        "path": note.path,
                        "folder": note.folder,
                        "title": note.title,
                        "frontmatter_json": note.frontmatter_json,
                        "source_hash": note.source_hash,
                        "chunk_hash": chunk.chunk_hash,
                        "chunk_index": chunk.chunk_index,
                        "chunk_count": len(note.chunks),
                        "section_titles": list(chunk.section_titles),
                        "char_start": chunk.char_start,
                        "char_end": chunk.char_end,
                        "content": chunk.content,
                        "wikilinks": list(note.wikilinks),
                    },
                )
            )
        return points

    def _open_qdrant(self) -> QdrantClient:
        try:
            self._qdrant_dir.mkdir(parents=True, exist_ok=True)
            return QdrantClient(path=str(self._qdrant_dir))
        except (OSError, RuntimeError, ValueError) as exc:
            raise SemanticSearchError(
                "No se pudo abrir Qdrant local. Comprueba que no haya otra "
                f"búsqueda usando el índice al mismo tiempo: {exc}"
            ) from exc

    @staticmethod
    def _ensure_collection(client: QdrantClient, dimension: int) -> None:
        if not client.collection_exists(_COLLECTION_NAME):
            client.create_collection(
                collection_name=_COLLECTION_NAME,
                vectors_config=models.VectorParams(
                    size=dimension,
                    distance=models.Distance.COSINE,
                ),
            )
            return
        collection = client.get_collection(_COLLECTION_NAME)
        vectors_config = collection.config.params.vectors
        if not isinstance(vectors_config, models.VectorParams):
            raise SemanticSearchError(
                "La configuración del índice vectorial no es compatible."
            )
        if vectors_config.size != dimension:
            raise SemanticSearchError(
                "La dimensión de los embeddings ha cambiado. Cambia el modelo "
                "de búsqueda o reconstruye el índice."
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30)
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS index_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO index_metadata(key, value) VALUES (?, ?)",
                ("schema_version", str(_INDEX_SCHEMA_VERSION)),
            )
            schema_version = connection.execute(
                "SELECT value FROM index_metadata WHERE key = 'schema_version'"
            ).fetchone()[0]
            if schema_version != str(_INDEX_SCHEMA_VERSION):
                raise sqlite3.DatabaseError(
                    "La versión del esquema local no es compatible."
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    folder TEXT NOT NULL,
                    title TEXT NOT NULL,
                    frontmatter_json TEXT NOT NULL,
                    wikilinks_json TEXT NOT NULL,
                    source_hash TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES sources(source_id)
                        ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    section_path TEXT NOT NULL,
                    section_titles_json TEXT NOT NULL,
                    char_start INTEGER NOT NULL,
                    char_end INTEGER NOT NULL,
                    chunk_hash TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    content TEXT NOT NULL,
                    UNIQUE(source_id, chunk_index)
                )
                """
            )
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    source_id UNINDEXED,
                    path,
                    folder,
                    title,
                    section,
                    content,
                    wikilinks,
                    metadata,
                    source_hash UNINDEXED,
                    chunk_hash UNINDEXED,
                    chunk_index UNINDEXED,
                    chunk_count UNINDEXED,
                    section_titles_json UNINDEXED,
                    char_start UNINDEXED,
                    char_end UNINDEXED,
                    tokenize = 'unicode61 remove_diacritics 2'
                )
                """
            )
            connection.commit()
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    def _open_database(self) -> sqlite3.Connection:
        try:
            return self._connect()
        except (OSError, sqlite3.Error) as exc:
            raise SemanticSearchError(
                f"No se pudo abrir el índice léxico local: {exc}"
            ) from exc

    @staticmethod
    def _active_chunk_ids(database: sqlite3.Connection) -> set[str]:
        return {
            row[0] for row in database.execute("SELECT chunk_id FROM chunks").fetchall()
        }

    @staticmethod
    def _chunk_ids_for_paths(
        database: sqlite3.Connection,
        paths: set[str],
    ) -> set[str]:
        if not paths:
            return set()
        placeholders = ",".join("?" for _ in paths)
        return {
            row[0]
            for row in database.execute(
                "SELECT chunks.chunk_id FROM chunks "
                "JOIN sources USING (source_id) "
                f"WHERE sources.path IN ({placeholders})",
                tuple(sorted(paths)),
            ).fetchall()
        }

    @staticmethod
    def _replace_source_records(
        database: sqlite3.Connection,
        changed_notes: list[SourceDocument],
        deleted_paths: list[str],
    ) -> None:
        try:
            database.execute("BEGIN")
            for path in deleted_paths:
                database.execute(
                    "DELETE FROM chunks_fts WHERE path = ?",
                    (path,),
                )
                database.execute(
                    "DELETE FROM sources WHERE path = ?",
                    (path,),
                )
            for note in changed_notes:
                database.execute(
                    "DELETE FROM chunks_fts WHERE path = ?",
                    (note.path,),
                )
                database.execute(
                    "DELETE FROM sources WHERE path = ?",
                    (note.path,),
                )
                database.execute(
                    """
                    INSERT INTO sources(
                        source_id, path, folder, title, frontmatter_json,
                        wikilinks_json, source_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        note.source_id,
                        note.path,
                        note.folder,
                        note.title,
                        note.frontmatter_json,
                        json.dumps(note.wikilinks, ensure_ascii=False),
                        note.source_hash,
                    ),
                )
                chunk_count = len(note.chunks)
                for chunk in note.chunks:
                    section_titles_json = json.dumps(
                        chunk.section_titles,
                        ensure_ascii=False,
                    )
                    section_path = chunk.section_path
                    metadata_text = note.frontmatter_json
                    database.execute(
                        """
                        INSERT INTO chunks(
                            chunk_id, source_id, chunk_index, chunk_count,
                            section_path, section_titles_json, char_start,
                            char_end, chunk_hash, source_hash, content
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            chunk.chunk_id,
                            note.source_id,
                            chunk.chunk_index,
                            chunk_count,
                            section_path,
                            section_titles_json,
                            chunk.char_start,
                            chunk.char_end,
                            chunk.chunk_hash,
                            note.source_hash,
                            chunk.content,
                        ),
                    )
                    database.execute(
                        """
                        INSERT INTO chunks_fts(
                            chunk_id, source_id, path, folder, title, section,
                            content, wikilinks, metadata, source_hash,
                            chunk_hash, chunk_index, chunk_count,
                            section_titles_json, char_start, char_end
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            chunk.chunk_id,
                            note.source_id,
                            note.path,
                            note.folder,
                            note.title,
                            section_path,
                            chunk.content,
                            json.dumps(note.wikilinks, ensure_ascii=False),
                            metadata_text,
                            note.source_hash,
                            chunk.chunk_hash,
                            chunk.chunk_index,
                            chunk_count,
                            section_titles_json,
                            chunk.char_start,
                            chunk.char_end,
                        ),
                    )
            database.commit()
        except sqlite3.Error:
            database.rollback()
            raise

    @staticmethod
    def _orphaned_chunk_ids(
        client: QdrantClient,
        active_chunk_ids: set[str],
    ) -> set[str]:
        orphaned: set[str] = set()
        offset: str | uuid.UUID | int | None = None
        while True:
            records, offset = client.scroll(
                collection_name=_COLLECTION_NAME,
                limit=256,
                offset=offset,
                with_payload=["chunk_id"],
                with_vectors=False,
            )
            orphaned.update(
                str(record.id)
                for record in records
                if str(record.id) not in active_chunk_ids
            )
            if offset is None:
                return orphaned

    @staticmethod
    def _lexical_search(
        database: sqlite3.Connection,
        query_terms: list[str],
        limit: int,
    ) -> list[_RankedHit]:
        if not query_terms:
            return []
        match_expression = " OR ".join(f'"{token}"' for token in query_terms)
        rows = database.execute(
            """
            SELECT
                chunk_id, source_id, path, title, content, wikilinks,
                folder, section, metadata, source_hash, chunk_hash,
                chunk_index, chunk_count, section_titles_json,
                char_start, char_end,
                bm25(
                    chunks_fts,
                    0, 0, 2.0, 4.0, 5.0, 3.0, 1.0, 2.0, 1.5,
                    0, 0, 0, 0, 0, 0, 0
                ) AS relevance
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
            ORDER BY relevance
            LIMIT ?
            """,
            (match_expression, limit),
        ).fetchall()
        paths = {str(row[2]) for row in rows}
        if not paths:
            return []
        placeholders = ", ".join("?" for _ in paths)
        coverage_rows = database.execute(
            f"""
            SELECT path, title, folder, section, content, wikilinks, metadata
            FROM chunks_fts
            WHERE path IN ({placeholders})
            """,
            tuple(paths),
        ).fetchall()
        covered_terms: dict[str, set[str]] = {}
        for path, title, folder, section, content, wikilinks, metadata in coverage_rows:
            path_terms = covered_terms.setdefault(str(path), set())
            path_terms.update(
                _search_terms(
                    f"{path} {title} {folder} {section} "
                    f"{content} {wikilinks} {metadata}"
                )
            )
        minimum_coverage = min(2, len(query_terms))
        query_term_set = set(query_terms)
        return [
            _RankedHit(
                chunk_id=str(row[0]),
                path=str(row[2]),
                title=str(row[3]),
                content=str(row[4]),
                wikilinks=tuple(_decode_json_string_list(str(row[5]))),
                provenance=SourceProvenance(
                    source_id=str(row[1]),
                    folder=str(row[6]),
                    frontmatter=_decode_json_object(str(row[8])),
                    section_titles=tuple(_decode_json_string_list(str(row[13]))),
                    chunk_index=int(row[11]),
                    chunk_count=int(row[12]),
                    char_start=int(row[14]),
                    char_end=int(row[15]),
                    source_hash=str(row[9]),
                    chunk_hash=str(row[10]),
                ),
            )
            for row in rows
            if len(covered_terms.get(str(row[2]), set()) & query_term_set)
            >= minimum_coverage
            and _has_searchable_body(str(row[4]))
        ]

    @staticmethod
    def _semantic_search(
        client: QdrantClient,
        query_vector: list[float],
        limit: int,
        min_cosine_score: float,
    ) -> list[_RankedHit]:
        if not client.collection_exists(_COLLECTION_NAME):
            return []
        response = client.query_points(
            collection_name=_COLLECTION_NAME,
            query=query_vector,
            limit=limit,
            with_payload=True,
            with_vectors=False,
            score_threshold=min_cosine_score,
        )
        results: list[_RankedHit] = []
        for point in response.points:
            payload = point.payload or {}
            chunk_id = payload.get("chunk_id", str(point.id))
            path = payload.get("path")
            source_id = payload.get("source_id")
            folder = payload.get("folder")
            title = payload.get("title")
            content = payload.get("content")
            frontmatter_json = payload.get("frontmatter_json")
            source_hash = payload.get("source_hash")
            chunk_hash = payload.get("chunk_hash")
            chunk_index = payload.get("chunk_index")
            chunk_count = payload.get("chunk_count")
            section_titles = payload.get("section_titles")
            char_start = payload.get("char_start")
            char_end = payload.get("char_end")
            wikilinks = payload.get("wikilinks")
            if (
                not isinstance(chunk_id, str)
                or not chunk_id
                or str(point.id) != chunk_id
                or not isinstance(path, str)
                or not path
                or not isinstance(source_id, str)
                or not source_id
                or not isinstance(folder, str)
                or not isinstance(title, str)
                or not title
                or not isinstance(content, str)
                or not content
                or not isinstance(frontmatter_json, str)
                or not isinstance(source_hash, str)
                or not source_hash
                or not isinstance(chunk_hash, str)
                or not chunk_hash
                or not isinstance(chunk_index, int)
                or not isinstance(chunk_count, int)
                or chunk_index < 0
                or chunk_count < 1
                or chunk_index >= chunk_count
                or not isinstance(section_titles, list)
                or not all(isinstance(item, str) for item in section_titles)
                or not isinstance(char_start, int)
                or not isinstance(char_end, int)
                or char_start < 0
                or char_end <= char_start
                or not isinstance(wikilinks, list)
                or not all(isinstance(item, str) for item in wikilinks)
                or not _has_searchable_body(content)
            ):
                continue
            frontmatter = _decode_json_object(frontmatter_json)
            results.append(
                _RankedHit(
                    chunk_id=chunk_id,
                    path=path,
                    title=title,
                    content=content,
                    wikilinks=tuple(wikilinks),
                    provenance=SourceProvenance(
                        source_id=source_id,
                        folder=folder,
                        frontmatter=frontmatter,
                        section_titles=tuple(section_titles),
                        chunk_index=chunk_index,
                        chunk_count=chunk_count,
                        char_start=char_start,
                        char_end=char_end,
                        source_hash=source_hash,
                        chunk_hash=chunk_hash,
                    ),
                )
            )
        return results

    @staticmethod
    def _accumulate_ranked_hits(
        fused: dict[str, _SearchCandidate],
        results: list[_RankedHit],
        active_chunks: dict[str, _ActiveChunk],
        query_terms: list[str],
        *,
        rank_name: Literal["semantic_rank", "lexical_rank"],
    ) -> None:
        for rank, result in enumerate(results, start=1):
            active_chunk = active_chunks.get(result.chunk_id)
            if active_chunk is None or not _matches_active_chunk(
                result,
                active_chunk,
            ):
                continue
            candidate = fused.setdefault(
                result.chunk_id,
                _SearchCandidate(
                    path=result.path,
                    title=result.title,
                    content=result.content,
                    wikilinks=result.wikilinks,
                    content_match_count=len(
                        set(_searchable_body_terms(result.content)) & set(query_terms)
                    ),
                    section_match_count=len(
                        set(_search_terms(" ".join(result.provenance.section_titles)))
                        & set(query_terms)
                    ),
                    provenance=result.provenance,
                    meaningful_term_count=_meaningful_body_term_count(result.content),
                ),
            )
            candidate.score += 1.0 / (_RRF_CONSTANT + rank)
            if rank_name == "semantic_rank":
                candidate.semantic_rank = rank
            else:
                candidate.lexical_rank = rank


def _matches_active_chunk(
    result: _RankedHit,
    active: _ActiveChunk,
) -> bool:
    provenance = result.provenance
    return (
        result.path == active.path
        and result.title == active.title
        and result.wikilinks == active.wikilinks
        and provenance.source_id == active.source_id
        and provenance.folder == active.folder
        and provenance.frontmatter == _decode_json_object(active.frontmatter_json)
        and provenance.source_hash == active.source_hash
        and provenance.chunk_hash == active.chunk_hash
        and provenance.chunk_index == active.chunk_index
        and provenance.chunk_count == active.chunk_count
        and provenance.section_titles == active.section_titles
        and provenance.char_start == active.char_start
        and provenance.char_end == active.char_end
        and hashlib.sha256(result.content.encode("utf-8")).hexdigest()
        == active.chunk_hash
    )


def _is_loopback_ollama_host(host: str) -> bool:
    candidate = host if "://" in host else f"http://{host}"
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname is not None
        and parsed.hostname.casefold() in {"localhost", "127.0.0.1", "::1"}
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _normalize_include_dirs(
    include_dirs: Sequence[str] | None,
) -> tuple[str, ...] | None:
    if include_dirs is None:
        return None
    if not include_dirs:
        raise ValueError("El alcance del índice debe incluir alguna carpeta.")

    normalized: set[str] = set()
    for include_dir in include_dirs:
        if not isinstance(include_dir, str) or not include_dir.strip():
            raise ValueError("Las carpetas incluidas deben ser rutas relativas.")
        raw_path = include_dir.strip().replace("\\", "/")
        path = PurePosixPath(raw_path)
        if (
            raw_path.startswith("/")
            or re.match(r"^[A-Za-z]:", raw_path)
            or any(part in {"", ".", ".."} for part in path.parts)
            or any(part.startswith(".") for part in path.parts)
            or any(part.casefold() in _EXCLUDED_DIRECTORY_NAMES for part in path.parts)
        ):
            raise ValueError(
                f"La carpeta incluida no es una ruta relativa permitida: {include_dir}"
            )
        normalized.add(path.as_posix())

    ordered = sorted(normalized, key=lambda item: (item.count("/"), item.casefold()))
    roots: list[str] = []
    for candidate in ordered:
        if not any(
            candidate.casefold().startswith(f"{root.casefold()}/") for root in roots
        ):
            roots.append(candidate)
    return tuple(roots)


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction()) if callable(is_junction) else False


def _extract_wikilinks(content: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                match.split("|", maxsplit=1)[0].strip()
                for match in _WIKILINK_PATTERN.findall(content)
                if match.split("|", maxsplit=1)[0].strip()
            },
            key=str.casefold,
        )
    )


def _decode_json_object(value: str) -> dict[str, object]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    return decoded


def _decode_json_string_list(value: str) -> list[str]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return [item for item in decoded if isinstance(item, str)]


def _search_terms(query: str) -> list[str]:
    """Keep unique informative tokens for lexical matching and snippets."""
    return list(
        dict.fromkeys(
            normalized
            for token in _TOKEN_PATTERN.findall(query.casefold())
            if (
                normalized := "".join(
                    character
                    for character in unicodedata.normalize("NFKD", token)
                    if not unicodedata.combining(character)
                )
            )
            and normalized not in _STOP_WORDS
        )
    )


def _is_project_overview_query(query: str) -> bool:
    tokens = set(_TOKEN_PATTERN.findall(query.casefold()))
    project_terms = {"proyecto", "proyectos"}
    if not tokens.intersection(project_terms):
        return False
    connective_terms = {
        "de",
        "del",
        "dime",
        "en",
        "mis",
        "mi",
        "que",
        "qué",
        "quiero",
        "por",
        "favor",
        "los",
        "las",
        "y",
    }
    intent_terms = tokens - project_terms - connective_terms
    return intent_terms <= _PROJECT_OVERVIEW_TERMS


def _searchable_body_terms(content: str) -> list[str]:
    without_images = _OBSIDIAN_IMAGE_PATTERN.sub(" ", content)
    without_images = _MARKDOWN_IMAGE_PATTERN.sub(" ", without_images)
    body = "\n".join(
        line
        for line in without_images.splitlines()
        if not line.lstrip().startswith("#")
    )
    return _search_terms(body)


def _meaningful_body_term_count(content: str) -> int:
    return sum(
        any(character.isalpha() for character in term)
        for term in _searchable_body_terms(content)
    )


def _has_searchable_body(content: str) -> bool:
    return bool(_searchable_body_terms(content))


def _project_chunk_quality(content: str) -> tuple[int, int, int, int]:
    folded_content = content.casefold()
    tracking_markers = (
        "estado",
        "en curso",
        "pendiente",
        "completado",
        "production-ready",
        "por hacer",
        "backlog",
        "activo",
        "pausado",
    )
    tracking_signals = sum(folded_content.count(marker) for marker in tracking_markers)
    open_tasks = folded_content.count("- [ ]")
    return (
        tracking_signals,
        open_tasks,
        len(_searchable_body_terms(content)),
        len(content),
    )


def _best_candidate_by_path(
    candidates: Sequence[_SearchCandidate],
) -> dict[str, _SearchCandidate]:
    best_by_path: dict[str, _SearchCandidate] = {}
    for candidate in candidates:
        current = best_by_path.get(candidate.path)
        if current is None or _candidate_quality(candidate) > _candidate_quality(
            current
        ):
            best_by_path[candidate.path] = candidate
    return best_by_path


def _candidate_quality(candidate: _SearchCandidate) -> tuple[int, int, int, int, float]:
    """Use section matches as evidence only when the passage also matches."""
    section_bonus = (
        candidate.section_match_count if candidate.content_match_count else 0
    )
    return (
        candidate.content_match_count + section_bonus,
        candidate.content_match_count,
        section_bonus,
        candidate.meaningful_term_count,
        candidate.score,
    )


def _make_snippet(content: str, tokens: list[str]) -> str:
    content = _OBSIDIAN_IMAGE_PATTERN.sub("", content)
    content = _MARKDOWN_IMAGE_PATTERN.sub("", content)
    folded_content = content.casefold()
    positions = [
        position for token in tokens if (position := folded_content.find(token)) >= 0
    ]
    start = max(0, min(positions, default=0) - 80)
    end = min(len(content), start + 260)
    snippet = content[start:end].strip()
    if start:
        snippet = f"...{snippet}"
    if end < len(content):
        snippet = f"{snippet}..."
    return snippet
