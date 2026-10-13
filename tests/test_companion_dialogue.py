"""Tests for deep Companion answer orchestration."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from blackbelt.knowledge.companion import DeepReadWindow
from blackbelt.knowledge.companion_dialogue import (
    deep_plan_fingerprint,
    deep_read_answer,
)
from blackbelt.knowledge.semantic_search import SemanticSearchError


def _window(alias: str = "C01-W00001") -> DeepReadWindow:
    return DeepReadWindow(
        alias=alias,
        path="024 - CHAT_BD/Conversación.md",
        title="Conversación",
        conversation_date="2026-10-10",
        section="Ideas",
        speaker="usuario",
        source_hash="a" * 64,
        char_start=0,
        char_end=80,
        source_chars=80,
        content="El usuario propuso una idea importante.",
    )


class FakeMemory:
    def __init__(self) -> None:
        self.plan = SimpleNamespace(
            windows=(_window(),),
            conversation_paths=("024 - CHAT_BD/Conversación.md",),
        )

    def prepare_deep_read(
        self, query: str, *, conversations: int, progress_callback: Any
    ):
        assert query == "¿Qué idea propuse?"
        assert conversations == 3
        if progress_callback is not None:
            progress_callback("Recuperando conversaciones...")
        return self.plan

    def contract(self) -> str:
        return "Distingue hechos de sugerencias."


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs["stream"]:
            return [
                {"message": {"content": "Propusiste una idea importante "}},
                {"message": {"content": "[C01-W00001]"}},
            ]
        return {"message": {"content": "El usuario propuso una idea importante."}}


def test_deep_read_scans_windows_then_streams_a_cited_synthesis() -> None:
    memory = FakeMemory()
    client = FakeClient()
    statuses: list[str] = []
    tokens: list[str] = []

    result = deep_read_answer(
        memory,  # type: ignore[arg-type]
        client,
        model="gemma4:e2b",
        query="¿Qué idea propuse?",
        conversations=3,
        history=[],
        options={"num_ctx": 4096, "num_predict": 1024},
        additional_context='[{"name":"notes.txt","content":"Dato del documento."}]',
        progress_callback=statuses.append,
        token_callback=tokens.append,
        expected_fingerprint=deep_plan_fingerprint(memory.plan),
    )

    assert result.answer == "Propusiste una idea importante [C01-W00001]"
    assert result.window_count == 1
    assert result.conversation_count == 1
    assert result.references[0].alias == "C01-W00001"
    assert result.references[0].source_hash == "a" * 64
    assert tokens == [
        "Propusiste una idea importante ",
        "[C01-W00001]",
    ]
    assert any("Leyendo ventana 1/1" in status for status in statuses)
    assert len(client.calls) == 2
    assert client.calls[-1]["stream"] is True
    assert "Dato del documento." in client.calls[-1]["messages"][-1]["content"]


def test_deep_read_refuses_a_plan_changed_since_confirmation() -> None:
    memory = FakeMemory()
    memory.plan = SimpleNamespace(
        windows=(_window(),),
        conversation_paths=("024 - CHAT_BD/Conversación.md",),
    )
    client = FakeClient()
    stale_fingerprint = "0" * 64

    with pytest.raises(SemanticSearchError, match="cambió desde la previsualización"):
        deep_read_answer(
            memory,  # type: ignore[arg-type]
            client,
            model="gemma4:e2b",
            query="¿Qué idea propuse?",
            conversations=3,
            history=[],
            options={"num_ctx": 4096, "num_predict": 1024},
            expected_fingerprint=stale_fingerprint,
        )
    assert client.calls == []


def test_empty_deep_plan_returns_a_clear_no_evidence_answer() -> None:
    memory = FakeMemory()
    memory.plan = SimpleNamespace(windows=(), conversation_paths=())
    client = FakeClient()

    result = deep_read_answer(
        memory,  # type: ignore[arg-type]
        client,
        model="gemma4:e2b",
        query="¿Qué idea propuse?",
        conversations=3,
        history=[],
        options={"num_ctx": 4096, "num_predict": 1024},
    )

    assert result.references == ()
    assert "No encuentro evidencia" in result.answer
    assert client.calls == []


def test_deep_mode_can_answer_from_attachments_without_memory_windows() -> None:
    class AttachmentClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def chat(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return [{"message": {"content": "El adjunto resume el proyecto."}}]

    memory = FakeMemory()
    memory.plan = SimpleNamespace(windows=(), conversation_paths=())
    client = AttachmentClient()

    result = deep_read_answer(
        memory,  # type: ignore[arg-type]
        client,
        model="gemma4:e2b",
        query="¿Qué idea propuse?",
        conversations=3,
        history=[],
        options={"num_ctx": 4096, "num_predict": 1024},
        additional_context='[{"name":"notes.txt","content":"El proyecto es local."}]',
    )

    assert result.answer == "El adjunto resume el proyecto."
    assert result.references == ()
    assert "El proyecto es local." in client.calls[0]["messages"][-1]["content"]


def test_cancellation_stops_before_reading_the_next_window() -> None:
    from threading import Event

    memory = FakeMemory()
    memory.plan = SimpleNamespace(
        windows=(_window("C01-W00001"), _window("C01-W00002")),
        conversation_paths=("024 - CHAT_BD/Conversación.md",),
    )
    client = FakeClient()
    cancelled = Event()
    cancelled.set()

    try:
        deep_read_answer(
            memory,  # type: ignore[arg-type]
            client,
            model="gemma4:e2b",
            query="¿Qué idea propuse?",
            conversations=3,
            history=[],
            options={"num_ctx": 4096, "num_predict": 1024},
            cancelled=cancelled,
        )
    except InterruptedError:
        pass
    else:
        raise AssertionError("La cancelación debía interrumpir la lectura.")
    assert client.calls == []
