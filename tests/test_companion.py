"""Tests for the scoped, traceable conversational memory companion."""

from __future__ import annotations

import json
from collections.abc import Sequence
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from rich.console import Console

from blackbelt.core import config as cfg
from blackbelt.knowledge import companion as companion_memory_module
from blackbelt.knowledge.companion import (
    CompanionMemory,
    DeepReadPlan,
    DeepReadWindow,
    PreparedTurn,
    initialize_contract,
    validate_cloud_review,
)
from blackbelt.knowledge.semantic_search import (
    SearchIndexProgress,
    SearchIndexStats,
    SemanticSearchError,
)
from blackbelt.tools import companion as companion_cli


class DeterministicEmbedder:
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


def _write_note(root: Path, relative_path: str, content: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _memory(tmp_path: Path, *, contract: Path | None = None) -> CompanionMemory:
    vault = tmp_path / "vault"
    _write_note(
        vault,
        "024 - CHAT_BD/Gemini - Idea reservada.md",
        """# Conversación

**Created:** 2025-03-04 10:00

## Usuario:
2025-03-04 10:00

Quiero recuperar mi prototipo histórico de memoria narrativa.

## Gemini:
Una opción sería crear un prototipo local con citas.
""",
    )
    _write_note(
        vault,
        "025 - MIS_NOTAS/Preferencias.md",
        """# Preferencias

## Decisiones
Prefiero validar las citas antes de confiar en una síntesis.
""",
    )
    _write_note(
        vault,
        "999 - DIARIO/Diario.md",
        "# Diario\n\nDIARIO-NO-INDEXADO",
    )
    memory = CompanionMemory(
        vault,
        tmp_path / "companion-index",
        contract or tmp_path / "contract.md",
        model="test-embedding-model",
        embedder=DeterministicEmbedder(),
        max_note_bytes=16 * 1024 * 1024,
    )
    memory.index()
    return memory


def test_companion_chat_timeout_argument_uses_long_generation_default() -> None:
    parsed = companion_cli._argument_parser().parse_args(["chat"])
    overridden = companion_cli._argument_parser().parse_args(
        ["chat", "--timeout", "1500"]
    )

    assert parsed.timeout == 900
    assert overridden.timeout == 1500


def test_companion_ollama_client_accepts_generation_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options: list[dict[str, object]] = []

    def create_client(**kwargs: object) -> object:
        options.append(kwargs)
        return object()

    monkeypatch.setattr(companion_cli.ollama, "Client", create_client)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    companion_cli._make_ollama_client(timeout=1500)

    assert options == [{"host": None, "timeout": 1500, "trust_env": False}]


def test_companion_embedding_timeout_is_independent_from_global_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options: dict[str, object] = {}

    class RecordingSearchService:
        def __init__(self, vault: Path, index_dir: Path, **kwargs: object) -> None:
            options.update(kwargs)

    monkeypatch.setattr(
        companion_memory_module,
        "SemanticSearchService",
        RecordingSearchService,
    )
    monkeypatch.setattr(cfg, "OLLAMA_TIMEOUT", 180)
    monkeypatch.setattr(cfg, "COMPANION_EMBEDDING_TIMEOUT_SECONDS", 900)

    CompanionMemory(
        tmp_path / "vault",
        tmp_path / "index",
        tmp_path / "contract.md",
    )

    assert options["embedding_timeout"] == 900


def test_chat_keeps_session_open_after_generation_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    monkeypatch.setattr(
        companion_cli,
        "console",
        Console(file=output, force_terminal=False),
    )
    monkeypatch.setattr(
        companion_cli,
        "_list_models",
        lambda client: ["gemma4:e2b"],
    )
    monkeypatch.setattr(
        companion_cli,
        "_select_model",
        lambda models, requested: "gemma4:e2b",
    )
    prompts = iter(["primera pregunta", "segunda pregunta", "salir"])
    monkeypatch.setattr(
        companion_cli.Prompt,
        "ask",
        lambda *args, **kwargs: next(prompts),
    )
    monkeypatch.setattr(companion_cli, "_model_options", dict)
    turn = PreparedTurn(
        messages=(
            {"role": "system", "content": "Contrato local"},
            {"role": "user", "content": "Pregunta recuperada"},
        ),
        references=(),
    )
    prepared_queries: list[str] = []

    class TimeoutThenSuccessClient:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def chat(self, **kwargs: object) -> dict[str, object]:
            self.requests.append(kwargs)
            if len(self.requests) == 1:
                raise httpx.ReadTimeout("simulated generation timeout")
            return {"message": {"content": "Respuesta tras continuar."}}

    client = TimeoutThenSuccessClient()

    def prepare_turn(
        query: str,
        *,
        limit: int,
        progress_callback: object = None,
    ) -> PreparedTurn:
        prepared_queries.append(query)
        if callable(progress_callback):
            progress_callback("Recuperación lista.")
        return turn

    memory = SimpleNamespace(prepare_turn=prepare_turn)
    companion_cli._chat(
        memory,
        client,
        "gemma4:e2b",
        5,
        "quick",
        3,
        timeout_seconds=12,
    )

    assert prepared_queries == ["primera pregunta", "segunda pregunta"]
    assert len(client.requests) == 2
    second_messages = client.requests[1]["messages"]
    assert second_messages == [*turn.messages[:1], turn.messages[1]]
    assert "superó 12 s" in output.getvalue()
    assert "La sesión sigue abierta" in output.getvalue()
    assert "Respuesta tras continuar." in output.getvalue()
    assert all(request["stream"] is True for request in client.requests)


def test_streaming_chat_reports_received_text_without_fabricated_percentage() -> None:
    class StreamingClient:
        def chat(self, **kwargs: object) -> object:
            assert kwargs["stream"] is True
            return iter(
                [
                    {"message": {"content": "Respuesta "}},
                    {"message": {"content": "visible."}},
                ]
            )

    with companion_cli._interaction_progress() as (progress, task_id):
        text = companion_cli._streaming_chat_text(
            StreamingClient(),
            model="gemma4:e2b",
            messages=({"role": "user", "content": "Pregunta"},),
            keep_alive="2m",
            options={},
            progress=progress,
            task_id=task_id,
        )
        description = progress.tasks[0].description

    assert text == "Respuesta visible."
    assert "18 caracteres recibidos" in description


@pytest.mark.parametrize("streaming", [False, True])
def test_streaming_chat_rejects_empty_model_responses(streaming: bool) -> None:
    class EmptyResponseClient:
        def chat(self, **kwargs: object) -> object:
            assert kwargs["stream"] is True
            response = {"message": {"content": ""}}
            return iter([response]) if streaming else response

    with companion_cli._interaction_progress() as (progress, task_id):
        with pytest.raises(TypeError, match="sin devolver texto"):
            companion_cli._streaming_chat_text(
                EmptyResponseClient(),
                model="gemma4:e2b",
                messages=({"role": "user", "content": "Pregunta"},),
                keep_alive="2m",
                options={},
                progress=progress,
                task_id=task_id,
            )


def test_companion_retrieves_local_evidence_with_speaker_and_citations(
    tmp_path: Path,
) -> None:
    contract = tmp_path / "contract.md"
    contract.write_text("# Mi contrato\n\nTono directo.", encoding="utf-8")
    memory = _memory(tmp_path, contract=contract)

    turn = memory.prepare_turn("prototipo histórico memoria narrativa", limit=3)

    assert turn.references
    reference = turn.references[0]
    assert reference.alias == "M1"
    assert reference.path.startswith("024 - CHAT_BD/")
    assert reference.speaker == "usuario"
    assert reference.conversation_date == "2025-03-04 10:00"
    assert reference.char_start is not None
    assert reference.char_end is not None
    assert "M1" in turn.messages[1]["content"]
    assert "Tono directo." in turn.messages[0]["content"]
    assert "DIARIO-NO-INDEXADO" not in str(turn.messages)


def test_deep_read_selects_whole_conversation_and_covers_last_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from blackbelt.core import config as cfg

    monkeypatch.setattr(cfg, "COMPANION_DEEP_WINDOW_CHARS", 900)
    monkeypatch.setattr(cfg, "COMPANION_DEEP_WINDOW_OVERLAP", 100)
    vault = tmp_path / "vault"
    final_decision = "DECISION_FINAL_SOLO_AL_FINAL_DE_LA_CONVERSACION"
    content = (
        "# Conversación extensa\n\n"
        "## Usuario\n"
        "La conversación trata sobre organizar proyectos.\n"
        + "".join(
            f"Turno {index}: conversación y contexto que deben leerse completos.\n"
            for index in range(350)
        )
        + f"\n## Usuario\n\n{final_decision}\n"
    )
    _write_note(vault, "024 - CHAT_BD/Extensa.md", content)
    _write_note(vault, "025 - MIS_NOTAS/Notas.md", "# Notas\n\nMemoria.")
    memory = CompanionMemory(
        vault,
        tmp_path / "companion-index",
        tmp_path / "contract.md",
        model="test-embedding-model",
        embedder=DeterministicEmbedder(),
        max_note_bytes=16 * 1024 * 1024,
    )
    memory.index()

    plan = memory.prepare_deep_read(final_decision, conversations=1)

    assert plan.conversation_paths == ("024 - CHAT_BD/Extensa.md",)
    assert len(plan.windows) > 10
    assert plan.windows[0].char_start == 0
    assert plan.windows[-1].char_end == len(content)
    assert final_decision in plan.windows[-1].content
    assert plan.windows[-1].section.endswith("Usuario")
    assert plan.windows[-1].speaker == "usuario"
    assert all(
        following.char_start <= current.char_end
        for current, following in zip(plan.windows, plan.windows[1:])
    )
    assert "".join(window.content for window in plan.windows).find(final_decision) >= 0


def test_deep_answer_processes_final_window_and_rejects_unknown_citations() -> None:
    plan = DeepReadPlan(
        query="¿Qué se decidió?",
        conversation_paths=("024 - CHAT_BD/Larga.md",),
        windows=(
            DeepReadWindow(
                alias="C01-W00001",
                path="024 - CHAT_BD/Larga.md",
                title="Larga",
                conversation_date="2025-01-01",
                section="Conversación > Usuario",
                speaker="usuario",
                source_hash="source-hash",
                char_start=0,
                char_end=40,
                source_chars=80,
                content="Contexto inicial sin decisión.",
            ),
            DeepReadWindow(
                alias="C01-W00002",
                path="024 - CHAT_BD/Larga.md",
                title="Larga",
                conversation_date="2025-01-01",
                section="Conversación > Usuario",
                speaker="usuario",
                source_hash="source-hash",
                char_start=30,
                char_end=80,
                source_chars=80,
                content="La decisión aparece al final: leerlo todo.",
            ),
        ),
    )
    sent_messages: list[list[dict[str, str]]] = []

    class RecordingClient:
        def chat(self, **kwargs: object) -> dict[str, object]:
            messages = kwargs["messages"]
            assert isinstance(messages, list)
            sent_messages.append(messages)
            if "Responde de forma natural" in messages[0]["content"]:
                return {
                    "message": {"content": "La decisión aparece al final [C01-W00002]."}
                }
            return {"message": {"content": "La decisión es leer el texto completo."}}

    client = RecordingClient()
    memory = SimpleNamespace(
        prepare_deep_read=lambda query, conversations, progress_callback=None: plan,
        contract=lambda: "Cita fuentes.",
    )

    answer = companion_cli._deep_read_answer(
        memory,
        client,
        "qwen2.5:1.5b",
        plan.query,
        conversations=1,
    )

    assert "C01-W00002" in answer
    assert len(sent_messages) == 3
    assert "La decisión aparece al final" in sent_messages[1][1]["content"]

    class InvalidCitationClient(RecordingClient):
        def chat(self, **kwargs: object) -> dict[str, object]:
            messages = kwargs["messages"]
            if "Responde de forma natural" in messages[0]["content"]:
                return {"message": {"content": "Decidimos esto [C99-W99999]."}}
            return {"message": {"content": "Hallazgo relevante."}}

    with pytest.raises(SemanticSearchError, match="no pertenecen"):
        companion_cli._deep_read_answer(
            memory,
            InvalidCitationClient(),
            "qwen2.5:1.5b",
            plan.query,
            conversations=1,
        )


def test_deep_reduction_preserves_only_known_window_citations() -> None:
    summaries = [
        companion_cli._EvidenceSummary(
            text=f"[C01-W{index:05d}] Hallazgo {index}.",
            references=(f"C01-W{index:05d}",),
        )
        for index in range(1, 8)
    ]

    class ReducingClient:
        def chat(self, **kwargs: object) -> dict[str, object]:
            system = kwargs["messages"][0]["content"]
            if "Sintetiza exclusivamente" in system:
                user = kwargs["messages"][1]["content"]
                citation = "C01-W00007" if "Hallazgo 7" in user else "C01-W00001"
                return {"message": {"content": f"Hallazgo consolidado [{citation}]"}}
            raise AssertionError("Esta prueba no debe solicitar síntesis final.")

    reduced = companion_cli._reduce_summaries(
        ReducingClient(),
        "qwen2.5:1.5b",
        "¿Qué ocurrió?",
        summaries,
    )

    assert len(reduced) == 2
    assert reduced[0].references == ("C01-W00001",)
    assert reduced[1].references == ("C01-W00007",)


def test_cloud_payload_uses_aliases_without_local_paths(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    turn = memory.prepare_turn("prototipo histórico memoria narrativa", limit=1)

    payload = turn.cloud_payload(
        model="gpt-oss:120b-cloud",
        options={"num_ctx": 4096, "num_predict": 512},
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["model"] == "gpt-oss:120b-cloud"
    assert payload["keep_alive"] == "0"
    assert "Gemini - Idea reservada.md" not in serialized
    assert "024 - CHAT_BD" not in serialized
    assert "[M1]" in serialized
    assert turn.references[0].path in str(turn.references)


def test_cloud_review_fails_closed_and_matches_configured_model(
    tmp_path: Path,
) -> None:
    review_path = tmp_path / "cloud-review.yaml"
    review = {
        "proveedor": "ollama",
        "modelo": "gpt-oss:120b-cloud",
        "revisado": True,
        "fecha_revision": "2026-10-08",
        "url_terminos": "https://ollama.com/terms",
        "region": "documentada",
        "retencion": "documentada",
        "entrenamiento": "documentado",
    }
    review_path.write_text(
        yaml.safe_dump(review, allow_unicode=True),
        encoding="utf-8",
    )

    assert validate_cloud_review(review_path, "gpt-oss:120b-cloud") == review
    with pytest.raises(ValueError, match="coincidir"):
        validate_cloud_review(review_path, "otro-modelo:cloud")

    review_path.unlink()
    with pytest.raises(ValueError, match="falta la revisión"):
        validate_cloud_review(review_path, "gpt-oss:120b-cloud")


def test_contract_initialization_never_overwrites_existing_content(
    tmp_path: Path,
) -> None:
    contract = tmp_path / "companion" / "contract.md"

    assert initialize_contract(contract)
    initial = contract.read_text(encoding="utf-8")
    assert "No modifiques notas" in initial
    assert not initialize_contract(contract)
    assert contract.read_text(encoding="utf-8") == initial


def test_model_listing_excludes_cloud_models() -> None:
    client = SimpleNamespace(
        list=lambda: SimpleNamespace(
            models=[
                SimpleNamespace(model="qwen2.5:1.5b"),
                SimpleNamespace(name="gpt-oss:120b-cloud"),
                {"model": "gemma3:1b"},
            ]
        )
    )

    assert companion_cli._list_models(client) == ["gemma3:1b", "qwen2.5:1.5b"]


def test_index_cli_renders_scan_embedding_checkpoint_and_cooldown_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    monkeypatch.setattr(companion_cli, "console", Console(file=output, width=120))
    stats = SearchIndexStats(
        scanned=5,
        indexed=5,
        unchanged=0,
        removed=0,
        skipped=0,
        warnings=(),
    )

    class FakeMemory:
        def index(self, **kwargs: object) -> SearchIndexStats:
            callback = kwargs["progress_callback"]
            assert callable(callback)
            callback(
                SearchIndexProgress(
                    stage="scan",
                    current=5,
                    total=5,
                    detail="Hashes comparados",
                )
            )
            callback(
                SearchIndexProgress(
                    stage="embed",
                    current=0,
                    total=5,
                    detail="Lote 1/1",
                    chunks_completed=4,
                    chunks_total=8,
                    batch_index=1,
                    batch_count=1,
                )
            )
            callback(
                SearchIndexProgress(
                    stage="cooldown",
                    current=60,
                    total=60,
                    detail="Descanso",
                )
            )
            callback(
                SearchIndexProgress(
                    stage="cleanup",
                    current=1,
                    total=1,
                    detail="Sincronizando",
                )
            )
            callback(
                SearchIndexProgress(
                    stage="complete",
                    current=5,
                    total=5,
                    detail="Terminado",
                )
            )
            return stats

    result = companion_cli._index_memory(
        FakeMemory(),
        batch_size=5,
        work_interval_seconds=60,
        cooldown_seconds=60,
    )

    assert result == stats
    assert "Indexación incremental" in output.getvalue()
    assert "checkpoint por conversación" in output.getvalue().casefold()
    assert "60 s de trabajo / 60 s de descanso" in output.getvalue()


def test_cloud_cancel_does_not_call_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from blackbelt.core import config as cfg

    review_path = tmp_path / "cloud-review.yaml"
    review_path.write_text(
        yaml.safe_dump(
            {
                "proveedor": "ollama",
                "modelo": "gpt-oss:120b-cloud",
                "revisado": True,
                "fecha_revision": "2026-10-08",
                "url_terminos": "https://ollama.com/terms",
                "region": "documentada",
                "retencion": "documentada",
                "entrenamiento": "documentado",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "COMPANION_CLOUD_MODEL", "gpt-oss:120b-cloud")
    monkeypatch.setattr(cfg, "COMPANION_CLOUD_REVIEW_FILE", review_path)
    monkeypatch.setattr(companion_cli.Confirm, "ask", lambda *args, **kwargs: False)

    turn = PreparedTurn(
        messages=(
            {"role": "system", "content": "Contrato"},
            {"role": "user", "content": "Pregunta con [M1]"},
        ),
        references=(),
    )
    client = SimpleNamespace(
        chat=lambda **kwargs: pytest.fail("Cloud no debe llamarse")
    )
    memory = SimpleNamespace(prepare_turn=lambda query, limit: turn)

    companion_cli._ask_cloud(memory, client, "pregunta", 5)


def test_cloud_confirmation_sends_the_previewed_payload_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from blackbelt.core import config as cfg

    review_path = tmp_path / "cloud-review.yaml"
    review_path.write_text(
        yaml.safe_dump(
            {
                "proveedor": "ollama",
                "modelo": "gpt-oss:120b-cloud",
                "revisado": True,
                "fecha_revision": "2026-10-08",
                "url_terminos": "https://ollama.com/terms",
                "region": "documentada",
                "retencion": "documentada",
                "entrenamiento": "documentado",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "COMPANION_CLOUD_MODEL", "gpt-oss:120b-cloud")
    monkeypatch.setattr(cfg, "COMPANION_CLOUD_REVIEW_FILE", review_path)
    monkeypatch.setattr(companion_cli.Confirm, "ask", lambda *args, **kwargs: True)

    turn = PreparedTurn(
        messages=(
            {"role": "system", "content": "Contrato"},
            {"role": "user", "content": "Pregunta con [M1]"},
        ),
        references=(),
    )
    sent: list[dict[str, object]] = []
    client = SimpleNamespace(
        chat=lambda **kwargs: (
            sent.append(kwargs) or {"message": {"content": "Respuesta cloud."}}
        )
    )
    memory = SimpleNamespace(prepare_turn=lambda query, limit: turn)

    companion_cli._ask_cloud(memory, client, "pregunta", 5)

    assert sent == [
        turn.cloud_payload(
            model="gpt-oss:120b-cloud",
            options=companion_cli._model_options(),
        )
    ]
