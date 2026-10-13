"""Explicit, single-job local indexing for the BlackBelt launcher."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from blackbelt.core import config as cfg
from blackbelt.knowledge.semantic_search import (
    SearchIndexCancelled,
    SearchIndexProgress,
    SearchIndexStats,
    SemanticSearchService,
)

LOGGER = logging.getLogger(__name__)
_ACTIVE_INDEX_JOB_LOCK = threading.Lock()


class IndexJobError(RuntimeError):
    """Raised when an indexing job cannot be started or cancelled."""


class _IndexJobManager:
    """Run one paced indexing job and expose a thread-safe progress snapshot."""

    job_kind = "index"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cancelled = threading.Event()
        self._worker: threading.Thread | None = None
        self._owns_global_lock = False
        self._snapshot: dict[str, Any] = {
            "state": "idle",
            "job_id": None,
            "job_kind": self.job_kind,
            "stage": None,
            "current": 0,
            "total": 0,
            "detail": "Indexación no iniciada.",
            "indexed": 0,
            "unchanged": 0,
            "batch_index": 0,
            "batch_count": 0,
            "cancel_requested": False,
            "started_at": None,
            "finished_at": None,
            "stats": None,
            "error": None,
            "configuration": self._configuration(),
        }

    def _configuration(self) -> dict[str, Any]:
        raise NotImplementedError

    def _index(
        self,
        cancelled: threading.Event,
        progress_callback: Callable[[SearchIndexProgress], None],
    ) -> SearchIndexStats:
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._snapshot,
                "configuration": dict(self._snapshot["configuration"]),
            }

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._snapshot["state"] in {"running", "cancelling"}:
                raise IndexJobError("Ya hay una indexación de este tipo activa.")
            if not _ACTIVE_INDEX_JOB_LOCK.acquire(blocking=False):
                raise IndexJobError(
                    "Ya hay otra indexación activa. Espera a que termine o se "
                    "detenga antes de iniciar este índice."
                )
            self._owns_global_lock = True
            self._cancelled = threading.Event()
            job_id = uuid.uuid4().hex
            self._snapshot = {
                "state": "running",
                "job_id": job_id,
                "job_kind": self.job_kind,
                "stage": "starting",
                "current": 0,
                "total": 0,
                "detail": f"Preparando el índice local de {self.job_kind}.",
                "indexed": 0,
                "unchanged": 0,
                "batch_index": 0,
                "batch_count": 0,
                "cancel_requested": False,
                "started_at": _timestamp(),
                "finished_at": None,
                "stats": None,
                "error": None,
                "configuration": self._snapshot["configuration"],
            }
            worker = threading.Thread(
                target=self._run,
                args=(job_id, self._cancelled),
                name=f"blackbelt-launcher-{self.job_kind}-index",
                daemon=True,
            )
            self._worker = worker
            try:
                worker.start()
            except RuntimeError as exc:
                self._release_global_lock()
                self._snapshot.update(
                    state="failed",
                    detail="No se pudo iniciar el proceso de indexación.",
                    finished_at=_timestamp(),
                )
                raise IndexJobError(
                    "No se pudo iniciar el proceso de indexación."
                ) from exc
            return self.status()

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            if self._snapshot["state"] != "running":
                raise IndexJobError("No hay una indexación activa que detener.")
            self._cancelled.set()
            self._snapshot.update(
                state="cancelling",
                cancel_requested=True,
                detail=(
                    "Detención solicitada; se completará la operación segura "
                    "en curso y se conservarán los checkpoints."
                ),
            )
            return self.status()

    def request_shutdown(self) -> None:
        with self._lock:
            if self._snapshot["state"] == "running":
                self._cancelled.set()
                self._snapshot.update(
                    state="cancelling",
                    cancel_requested=True,
                    detail="Detención solicitada por el cierre del launcher.",
                )

    def _run(self, job_id: str, cancelled: threading.Event) -> None:
        try:
            stats = self._index(
                cancelled,
                lambda event: self._update_progress(job_id, event),
            )
        except SearchIndexCancelled:
            self._finish(
                job_id,
                state="cancelled",
                detail=(
                    "Indexación detenida; los checkpoints confirmados se conservan."
                ),
            )
        except Exception as exc:
            LOGGER.exception("%s index job %s failed", self.job_kind, job_id)
            self._finish(
                job_id,
                state="failed",
                detail="La indexación terminó con un error.",
                error=str(exc),
            )
        else:
            self._finish(
                job_id,
                state="completed",
                detail=f"Índice de {self.job_kind} sincronizado.",
                stats=_serialize_stats(stats),
            )

    def _update_progress(
        self,
        job_id: str,
        event: SearchIndexProgress,
    ) -> None:
        with self._lock:
            if self._snapshot["job_id"] != job_id:
                return
            if self._snapshot["state"] == "cancelling":
                return
            self._snapshot.update(
                stage=event.stage,
                current=event.current,
                total=event.total,
                detail=event.detail,
                indexed=event.indexed,
                unchanged=event.unchanged,
                batch_index=event.batch_index,
                batch_count=event.batch_count,
            )

    def _finish(
        self,
        job_id: str,
        *,
        state: str,
        detail: str,
        stats: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            if self._snapshot["job_id"] != job_id:
                return
            self._snapshot.update(
                state=state,
                detail=detail,
                finished_at=_timestamp(),
                stats=stats,
                error=error,
            )
            self._release_global_lock()

    def _release_global_lock(self) -> None:
        if self._owns_global_lock:
            self._owns_global_lock = False
            _ACTIVE_INDEX_JOB_LOCK.release()


class CompanionIndexJobManager(_IndexJobManager):
    """Manage the approved conversations and notes memory index."""

    job_kind = "Companion"

    def _configuration(self) -> dict[str, Any]:
        return {
            "source_dirs": list(cfg.COMPANION_SOURCE_DIRS),
            "embedding_model": cfg.SEARCH_EMBEDDING_MODEL,
            "batch_size": cfg.COMPANION_INDEX_BATCH_SIZE,
            "embedding_batch_size": cfg.COMPANION_INDEX_EMBED_BATCH_SIZE,
            "work_interval_seconds": cfg.COMPANION_INDEX_WORK_INTERVAL_SECONDS,
            "cooldown_seconds": cfg.COMPANION_INDEX_COOLDOWN_SECONDS,
            "embedding_threads": cfg.COMPANION_EMBED_NUM_THREAD,
        }

    def _index(
        self,
        cancelled: threading.Event,
        progress_callback: Callable[[SearchIndexProgress], None],
    ) -> SearchIndexStats:
        from blackbelt.tools.companion import _make_memory

        return _make_memory().index(
            batch_size=cfg.COMPANION_INDEX_BATCH_SIZE,
            work_interval_seconds=cfg.COMPANION_INDEX_WORK_INTERVAL_SECONDS,
            cooldown_seconds=cfg.COMPANION_INDEX_COOLDOWN_SECONDS,
            progress_callback=progress_callback,
            cancelled=cancelled,
        )


class WorkIndexJobManager(_IndexJobManager):
    """Manage the explicit allowlist of non-personal work vault folders."""

    job_kind = "work"

    def _configuration(self) -> dict[str, Any]:
        return {
            "source_dirs": list(cfg.SEARCH_SOURCE_DIRS),
            "embedding_model": cfg.SEARCH_EMBEDDING_MODEL,
            "batch_size": cfg.SEARCH_INDEX_BATCH_SIZE,
            "embedding_batch_size": cfg.SEARCH_INDEX_EMBED_BATCH_SIZE,
            "work_interval_seconds": cfg.SEARCH_INDEX_WORK_INTERVAL_SECONDS,
            "cooldown_seconds": cfg.SEARCH_INDEX_COOLDOWN_SECONDS,
            "embedding_threads": cfg.SEARCH_EMBED_NUM_THREAD,
        }

    def _index(
        self,
        cancelled: threading.Event,
        progress_callback: Callable[[SearchIndexProgress], None],
    ) -> SearchIndexStats:
        service = SemanticSearchService(
            cfg.OBSIDIAN_VAULT,
            cfg.SEARCH_WORK_INDEX_DIR,
            model=cfg.SEARCH_EMBEDDING_MODEL,
            embedding_timeout=cfg.OLLAMA_TIMEOUT,
            min_cosine_score=cfg.SEARCH_MIN_COSINE_SCORE,
            include_dirs=cfg.SEARCH_SOURCE_DIRS,
            embedding_batch_size=cfg.SEARCH_INDEX_EMBED_BATCH_SIZE,
            embedding_keep_alive=cfg.COMPANION_EMBED_KEEP_ALIVE,
            embedding_num_thread=cfg.SEARCH_EMBED_NUM_THREAD,
        )
        return service.index(
            batch_size=cfg.SEARCH_INDEX_BATCH_SIZE,
            work_interval_seconds=cfg.SEARCH_INDEX_WORK_INTERVAL_SECONDS,
            cooldown_seconds=cfg.SEARCH_INDEX_COOLDOWN_SECONDS,
            progress_callback=progress_callback,
            cancelled=cancelled,
        )


def _serialize_stats(stats: SearchIndexStats) -> dict[str, Any]:
    return {
        "scanned": stats.scanned,
        "indexed": stats.indexed,
        "unchanged": stats.unchanged,
        "removed": stats.removed,
        "skipped": stats.skipped,
        "warnings": list(stats.warnings[:20]),
    }


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
