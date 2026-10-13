"""Tests for the launcher's cooperative Companion index job manager."""

from __future__ import annotations

import time
from threading import Event
from typing import Any

import pytest

from blackbelt.core import config as cfg
from blackbelt.knowledge.semantic_search import SearchIndexCancelled, SearchIndexStats
from blackbelt.launcher_indexing import (
    CompanionIndexJobManager,
    IndexJobError,
    WorkIndexJobManager,
)
from blackbelt.tools import companion


def _wait_for_state(
    manager: CompanionIndexJobManager,
    expected_state: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        snapshot = manager.status()
        if snapshot["state"] == expected_state:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"El trabajo no alcanzó el estado {expected_state!r}.")


def test_index_manager_allows_one_job_and_stops_cooperatively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = Event()

    class BlockingMemory:
        def index(self, **kwargs: Any) -> None:
            started.set()
            kwargs["cancelled"].wait(timeout=2)
            raise SearchIndexCancelled("detención de prueba")

    monkeypatch.setattr(companion, "_make_memory", BlockingMemory)
    manager = CompanionIndexJobManager()

    initial = manager.start()
    assert initial["state"] == "running"
    assert started.wait(timeout=2)
    with pytest.raises(IndexJobError, match="Ya hay"):
        manager.start()
    with pytest.raises(IndexJobError, match="otra indexación activa"):
        WorkIndexJobManager().start()

    stopping = manager.cancel()
    assert stopping["state"] == "cancelling"
    assert stopping["cancel_requested"] is True

    finished = _wait_for_state(manager, "cancelled")
    assert finished["error"] is None
    assert "checkpoints confirmados" in finished["detail"]


def test_work_index_uses_work_allowlist_and_releases_shared_job_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = Event()
    release = Event()
    stats = SearchIndexStats(
        scanned=0,
        indexed=0,
        unchanged=0,
        removed=0,
        skipped=0,
        warnings=(),
    )

    def blocking_index(
        self: WorkIndexJobManager,
        cancelled: Event,
        progress_callback: Any,
    ) -> SearchIndexStats:
        started.set()
        release.wait(timeout=2)
        return stats

    monkeypatch.setattr(WorkIndexJobManager, "_index", blocking_index)
    manager = WorkIndexJobManager()

    configuration = manager.status()["configuration"]
    assert configuration["source_dirs"] == list(cfg.SEARCH_SOURCE_DIRS)
    assert configuration["work_interval_seconds"] == 60
    assert configuration["cooldown_seconds"] == 60
    assert configuration["embedding_threads"] == 2

    assert manager.start()["state"] == "running"
    assert started.wait(timeout=2)
    with pytest.raises(IndexJobError, match="otra indexación activa"):
        CompanionIndexJobManager().start()

    release.set()
    finished = _wait_for_state(manager, "completed")
    assert finished["stats"]["scanned"] == 0

    companion_manager = CompanionIndexJobManager()
    monkeypatch.setattr(
        companion_manager,
        "_index",
        lambda cancelled, progress_callback: stats,
    )
    assert companion_manager.start()["state"] == "running"
    completed = _wait_for_state(companion_manager, "completed")
    assert completed["state"] == "completed"
