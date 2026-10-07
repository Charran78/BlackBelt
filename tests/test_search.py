"""Tests for incremental local hybrid search."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import closing
from dataclasses import replace
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from rich.console import Console

from blackbelt.knowledge import semantic_search
from blackbelt.knowledge.provenance import (
    SourceProvenance,
    parse_markdown_source,
)
from blackbelt.knowledge.semantic_search import (
    OllamaEmbeddingProvider,
    SearchHit,
    SemanticSearchError,
    SemanticSearchService,
)
from blackbelt.tools import search as search_cli


class DeterministicEmbedder:
    """Small offline embedding substitute for repeatable integration tests."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        vectors = []
        for text in texts:
            normalized = text.casefold()
            if any(
                term in normalized for term in ("celebración", "ceremonia", "banquete")
            ):
                vectors.append([1.0, 0.0, 0.0])
            elif "servidor" in normalized or "despliegue" in normalized:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors


def _write_note(vault: Path, relative_path: str, content: str) -> Path:
    note_path = vault / relative_path
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(content, encoding="utf-8")
    return note_path


def _service(
    vault: Path,
    index_dir: Path,
    embedder: DeterministicEmbedder,
) -> SemanticSearchService:
    return SemanticSearchService(
        vault,
        index_dir,
        model="test-embedding-model",
        embedder=embedder,
    )


def test_index_is_incremental_excludes_templates_and_credentials_and_searches_hybrid(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    client_note = _write_note(
        vault,
        "005 - CLIENTES/CL001 - Ana.md",
        """---
nombre: Ana García
---
# Ana García

Ficha del cliente CL001. Preparar la ceremonia y el banquete.

Proyecto relacionado: [[001 - PROYECTOS/Proyecto Aura|Aura]].
""",
    )
    _write_note(
        vault,
        "006 - CREDENCIALES/secretos.md",
        "# Contraseñas\n\nTEXTO-ULTRASECRETO",
    )
    _write_note(
        vault,
        "000 - PLANTILLAS/Ficha CRM.md",
        "# Plantilla\n\nCONTENIDO-PLANTILLA",
    )
    _write_note(
        vault,
        "022 - PLANES_BORRADOR/PLAN-001.md",
        "# Borrador\n\nCONTENIDO-BORRADOR-PLAN",
    )
    _write_note(
        vault,
        ".obsidian/private.md",
        "# Nota oculta\n\nCONTENIDO-OCULTO",
    )
    embedder = DeterministicEmbedder()
    service = _service(vault, tmp_path / "index", embedder)

    first = service.index()
    batches_after_first = len(embedder.batches)
    second = service.index()

    assert (first.scanned, first.indexed, first.unchanged, first.skipped) == (
        1,
        1,
        0,
        0,
    )
    assert (second.scanned, second.indexed, second.unchanged) == (1, 0, 1)
    assert len(embedder.batches) == batches_after_first

    semantic_hits = service.search("celebración de una boda")
    assert semantic_hits
    assert semantic_hits[0].path == "005 - CLIENTES/CL001 - Ana.md"
    assert semantic_hits[0].semantic_rank == 1
    assert semantic_hits[0].wikilinks == ("001 - PROYECTOS/Proyecto Aura",)

    lexical_hits = service.search("CL001")
    assert lexical_hits
    assert lexical_hits[0].path == "005 - CLIENTES/CL001 - Ana.md"
    assert lexical_hits[0].lexical_rank == 1
    assert all("CREDENCIALES" not in hit.path for hit in semantic_hits)
    assert all("ULTRASECRETO" not in hit.content for hit in semantic_hits)
    assert all("PLANTILLAS" not in hit.path for hit in semantic_hits)
    assert all("022 - PLANES_BORRADOR" not in hit.path for hit in semantic_hits)
    assert all(
        excluded not in batch
        for batch in embedder.batches
        for excluded in ("CONTENIDO-PLANTILLA", "CONTENIDO-BORRADOR-PLAN")
    )

    client_note.write_text(
        "# Ana García\n\nPreparar el despliegue del servidor.\n",
        encoding="utf-8",
    )
    updated = service.index()
    assert updated.indexed == 1
    assert service.search("celebración de una boda") == []


def test_reindex_removes_newly_excluded_directories_from_older_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = tmp_path / "vault"
    _write_note(
        vault,
        "000 - PLANTILLAS/Ficha CRM.md",
        "# Plantilla\n\nContenido de plantilla.",
    )
    _write_note(
        vault,
        "022 - PLANES_BORRADOR/PLAN-001.md",
        "# Borrador\n\nContenido de borrador.",
    )
    embedder = DeterministicEmbedder()
    service = _service(vault, tmp_path / "index", embedder)
    monkeypatch.setattr(
        semantic_search,
        "_EXCLUDED_DIRECTORY_NAMES",
        {"006 - credenciales"},
    )

    assert service.index().indexed == 2

    monkeypatch.setattr(
        semantic_search,
        "_EXCLUDED_DIRECTORY_NAMES",
        {
            "000 - plantillas",
            "006 - credenciales",
            "022 - planes_borrador",
        },
    )
    stats = service.index()

    assert stats.removed == 2
    assert service.search("plantilla") == []
    assert service.search("borrador") == []


def test_index_removes_deleted_notes_from_both_rankers(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    note_path = _write_note(
        vault,
        "001 - PROYECTOS/Aura.md",
        "# Aura\n\nPreparar la ceremonia.\n",
    )
    embedder = DeterministicEmbedder()
    service = _service(vault, tmp_path / "index", embedder)

    assert service.index().indexed == 1
    note_path.unlink()

    stats = service.index()

    assert stats.removed == 1
    assert service.search("ceremonia") == []


def test_failed_embedding_leaves_index_retryable(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_note(
        vault,
        "001 - PROYECTOS/Aura.md",
        "# Aura\n\nPreparar una ceremonia.\n",
    )

    class FailsOnceEmbedder(DeterministicEmbedder):
        def __init__(self) -> None:
            super().__init__()
            self.fail_next = True

        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            if self.fail_next:
                self.fail_next = False
                raise SemanticSearchError("Fallo local simulado.")
            return super().embed(texts)

    embedder = FailsOnceEmbedder()
    service = _service(vault, tmp_path / "index", embedder)

    with pytest.raises(SemanticSearchError, match="simulado"):
        service.index()

    retried = service.index()

    assert retried.indexed == 1
    assert service.search("ceremonia")


def test_search_requires_index_and_rejects_invalid_query_and_limit(
    tmp_path: Path,
) -> None:
    embedder = DeterministicEmbedder()
    service = _service(tmp_path / "vault", tmp_path / "index", embedder)

    with pytest.raises(SemanticSearchError, match="Todavía no hay índice"):
        service.search("búsqueda")
    with pytest.raises(ValueError, match="consulta"):
        service.search("  ")
    with pytest.raises(ValueError, match="entre 1 y 50"):
        service.search("nota", limit=0)


@pytest.mark.parametrize(
    "query",
    [
        "proyectos",
        "mis proyectos",
        "qué proyectos tengo en curso",
        "estado de proyectos activos",
    ],
)
def test_project_overview_queries_list_project_notes_without_embeddings(
    tmp_path: Path,
    query: str,
) -> None:
    vault = tmp_path / "vault"
    _write_note(
        vault,
        "001 - PROYECTOS/Audio Guía.md",
        "# Audio Guía\n\nBuscar 20 beta testers.\n\nEstado: en curso.",
    )
    _write_note(
        vault,
        "001 - PROYECTOS/Aura.md",
        "# Aura\n\n![[logo.png|383]]",
    )
    _write_note(
        vault,
        "005 - CLIENTES/Cliente.md",
        "# Cliente\n\nProyecto activo: Aura.",
    )
    embedder = DeterministicEmbedder()
    service = _service(vault, tmp_path / "index", embedder)
    service.index()
    batch_count = len(embedder.batches)

    results = service.search(query)

    assert [result.path for result in results] == [
        "001 - PROYECTOS/Audio Guía.md",
        "001 - PROYECTOS/Aura.md",
    ]
    assert "20 beta testers" in results[0].content
    assert "no contiene descripción textual indexable" in results[1].content
    assert results[0].provenance is not None
    assert results[0].provenance.chunk_count == 1
    assert results[0].provenance.char_start is not None
    assert results[1].provenance is not None
    assert results[1].provenance.chunk_index is None
    assert all(result.semantic_rank is None for result in results)
    assert all(result.lexical_rank is None for result in results)
    assert len(embedder.batches) == batch_count


def test_project_inventory_prefers_status_and_open_tasks() -> None:
    technical_chunk = "Detalles de arquitectura " * 20
    tracking_chunk = (
        "## Estado del Proyecto\n- [ ] Buscar beta testers.\nEstado: en curso."
    )

    assert semantic_search._project_chunk_quality(
        tracking_chunk
    ) > semantic_search._project_chunk_quality(technical_chunk)


def test_project_overview_is_rendered_as_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    monkeypatch.setattr(
        search_cli,
        "console",
        Console(file=output, width=80, force_terminal=False),
    )

    search_cli._render_search_results(
        [
            SearchHit(
                path="001 - PROYECTOS/Aura.md",
                title="Aura",
                content="La nota no contiene descripción textual indexable.",
                wikilinks=(),
                score=0.0,
                semantic_rank=None,
                lexical_rank=None,
            )
        ]
    )

    rendered = output.getvalue()
    assert "Inventario de proyectos (001 - PROYECTOS)" in rendered
    assert "Coincidencias:" not in rendered
    assert "no una probabilidad" not in rendered


def test_default_semantic_threshold_prioritizes_precision(tmp_path: Path) -> None:
    service = SemanticSearchService(
        tmp_path / "vault",
        tmp_path / "index",
        embedder=DeterministicEmbedder(),
    )

    assert service._min_cosine_score == 0.65


def test_stop_words_do_not_trigger_semantic_search(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_note(vault, "001 - PROYECTOS/Aura.md", "# Aura\n\nPlanificar ceremonia.")
    embedder = DeterministicEmbedder()
    service = _service(vault, tmp_path / "index", embedder)
    service.index()
    embedding_batches_before = len(embedder.batches)

    assert service.search("de la y para") == []
    assert len(embedder.batches) == embedding_batches_before


def test_search_terms_preserve_negations() -> None:
    assert semantic_search._search_terms("no enviar notas sin permiso") == [
        "no",
        "enviar",
        "notas",
        "sin",
        "permiso",
    ]


def test_media_embeds_are_not_searchable_content_or_wikilinks() -> None:
    content = "# Aura\n\n![[logo1.png|383]]\n\n![Logo](https://example.com/logo.png)"

    assert not semantic_search._has_searchable_body(content)
    assert semantic_search._extract_wikilinks(content) == ()
    assert "logo1.png" not in semantic_search._make_snippet(
        content,
        ["aura"],
    )


def test_best_candidate_prefers_query_matches_over_higher_rank_noise() -> None:
    weak = semantic_search._SearchCandidate(
        path="001 - PROYECTOS/Aura.md",
        title="Aura",
        content="Un pasaje genérico.",
        wikilinks=(),
        content_match_count=0,
        score=0.04,
    )
    specific = semantic_search._SearchCandidate(
        path="001 - PROYECTOS/Aura.md",
        title="Aura",
        content="Ceremonia y reservas del proyecto.",
        wikilinks=(),
        content_match_count=2,
        score=0.02,
    )

    selected = semantic_search._best_candidate_by_path([weak, specific])

    assert selected["001 - PROYECTOS/Aura.md"] is specific


def test_lexical_search_ignores_stop_words(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_note(
        vault,
        "001 - PROYECTOS/Aura.md",
        "# Aura\n\nPlanificar la ceremonia y las reservas.",
    )
    _write_note(
        vault,
        "001 - PROYECTOS/Otra.md",
        "# Otra\n\nLa y de la o en para con por.",
    )
    service = _service(vault, tmp_path / "index", DeterministicEmbedder())
    service.index()

    with closing(service._open_database()) as database:
        results = service._lexical_search(
            database,
            semantic_search._search_terms("de la ceremonia"),
            10,
        )

    assert [result.path for result in results] == ["001 - PROYECTOS/Aura.md"]


def test_folder_metadata_is_searchable_and_returned_as_provenance(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _write_note(
        vault,
        "005 - CLIENTES/CL001 - Ana.md",
        "---\ntipo: cliente\nnombre: Ana García\n---\n"
        "# Ana García\n\nPreparar la ceremonia.",
    )
    service = _service(vault, tmp_path / "index", DeterministicEmbedder())
    service.index()

    results = service.search("clientes")
    id_results = service.search("CL001")

    assert results
    assert id_results
    assert id_results[0].path == "005 - CLIENTES/CL001 - Ana.md"
    assert results[0].provenance is not None
    assert results[0].provenance.folder == "005 - CLIENTES"
    assert results[0].provenance.frontmatter["nombre"] == "Ana García"
    assert results[0].provenance.source_id


def test_source_parser_preserves_frontmatter_section_and_exact_offsets() -> None:
    content = (
        "---\nautor: Amélie Nothomb\ntipo: libro\n---\n"
        "# Estupor y temblores\n\n"
        "## Capítulo 1\n\nLa protagonista llega a la empresa.\n\n"
        "### Contexto\n\nLa escena sucede en Tokio.\n"
    )

    source = parse_markdown_source("017 - BIBLIOTECA/Libro.md", content)

    assert source.folder == "017 - BIBLIOTECA"
    assert source.title == "Estupor y temblores"
    assert source.frontmatter == {
        "autor": "Amélie Nothomb",
        "tipo": "libro",
    }
    chapter = next(chunk for chunk in source.chunks if "empresa" in chunk.content)
    context = next(chunk for chunk in source.chunks if "Tokio" in chunk.content)
    assert chapter.section_titles == ("Estupor y temblores", "Capítulo 1")
    assert context.section_titles == (
        "Estupor y temblores",
        "Capítulo 1",
        "Contexto",
    )
    assert (
        "La protagonista llega a la empresa."
        in content[chapter.char_start : chapter.char_end]
    )
    assert (
        "La escena sucede en Tokio." in content[context.char_start : context.char_end]
    )
    assert 0 <= chapter.char_start < chapter.char_end <= len(content)
    assert chapter.chunk_hash
    assert source.source_hash


def test_frontmatter_and_section_are_searchable_and_citable(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _write_note(
        vault,
        "017 - BIBLIOTECA/Estupor y temblores.md",
        "---\nautor: Amélie Nothomb\ntipo: libro\n---\n"
        "# Estupor y temblores\n\n## Capítulo 3\n\n"
        "La escena sucede en Tokio.",
    )
    service = _service(vault, tmp_path / "index", DeterministicEmbedder())
    service.index()

    author_results = service.search("Amelie Nothomb")
    section_results = service.search("Capitulo 3 Tokio")

    assert author_results
    assert author_results[0].provenance is not None
    assert author_results[0].provenance.frontmatter["autor"] == "Amélie Nothomb"
    assert section_results
    assert section_results[0].provenance is not None
    assert section_results[0].provenance.section_titles == (
        "Estupor y temblores",
        "Capítulo 3",
    )


def test_source_parser_supports_setext_book_and_chapter_headings() -> None:
    source = parse_markdown_source(
        "017 - BIBLIOTECA/Libro.md",
        "Libro importado\n===============\n\n"
        "Capítulo 4\n----------\n\n"
        "El personaje llega a la ciudad.",
    )

    assert source.title == "Libro importado"
    assert source.chunks[0].section_titles == (
        "Libro importado",
        "Capítulo 4",
    )


def test_source_and_chunk_ids_are_deterministic_and_content_sensitive() -> None:
    original = parse_markdown_source(
        "001 - PROYECTOS/Aura.md",
        "# Aura\n\nPlanificar la ceremonia.",
    )
    same_source = parse_markdown_source(
        "001 - PROYECTOS/Aura.md",
        "# Aura\n\nPlanificar la ceremonia.",
    )
    changed_source = parse_markdown_source(
        "001 - PROYECTOS/Aura.md",
        "# Aura\n\nConfirmar la ceremonia.",
    )

    assert original.source_id == same_source.source_id == changed_source.source_id
    assert original.chunks[0].chunk_id == same_source.chunks[0].chunk_id
    assert original.chunks[0].chunk_id != changed_source.chunks[0].chunk_id
    assert original.source_hash != changed_source.source_hash


def test_malformed_frontmatter_is_reported_but_body_remains_searchable() -> None:
    source = parse_markdown_source(
        "001 - PROYECTOS/Aura.md",
        "---\nautor: [yaml roto\n---\n# Aura\n\nPlanificar la ceremonia.",
    )

    assert source.frontmatter == {}
    assert source.warnings
    assert source.chunks
    assert "ceremonia" in source.chunks[0].content


def test_lexical_coverage_is_aggregated_across_note_chunks(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _write_note(
        vault,
        "001 - PROYECTOS/Aura.md",
        "# Aura\n\nCeremonia.\n\n" + ("detalle " * 180) + "\n\nReservas.",
    )
    service = _service(vault, tmp_path / "index", DeterministicEmbedder())
    service.index()

    with closing(service._open_database()) as database:
        results = service._lexical_search(
            database,
            semantic_search._search_terms("ceremonia reservas"),
            10,
        )

    assert results
    assert all(result.path == "001 - PROYECTOS/Aura.md" for result in results)


def test_lexical_search_rejects_single_term_noise_in_longer_query(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _write_note(
        vault,
        "001 - PROYECTOS/Modelfile.md",
        "# Cómo domar LLMs\n\nConfigurar un Modelfile local.",
    )
    service = _service(vault, tmp_path / "index", DeterministicEmbedder())
    service.index()

    with closing(service._open_database()) as database:
        results = service._lexical_search(
            database,
            semantic_search._search_terms("receta de paella marinera"),
            10,
        )

    assert results == []


def test_source_selector_prefers_context_over_metadata_only_numbered_lists(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _write_note(
        vault,
        "019 - REUNIONES/RE001 - CL001.md",
        "---\ncliente: CL001\nestado: por preparar\n---\n"
        "# Reunión\n\n## Agenda\n\n1.\n2.\n3.\n\n"
        "## Contexto\n\nRevisar presupuesto y próximos pasos con el cliente.",
    )
    service = _service(vault, tmp_path / "index", DeterministicEmbedder())
    service.index()

    results = service.search("CL001")

    assert results
    assert results[0].path == "019 - REUNIONES/RE001 - CL001.md"
    assert results[0].provenance is not None
    assert results[0].provenance.section_titles == ("Reunión", "Contexto")
    assert "Revisar presupuesto" in results[0].content
    assert results[0].content != "1.\n2.\n3."


def test_semantic_search_requires_complete_traceable_qdrant_payloads() -> None:
    class FakeQdrantClient:
        def collection_exists(self, collection_name: str) -> bool:
            return True

        def query_points(self, **_: object) -> SimpleNamespace:
            return SimpleNamespace(
                points=[
                    SimpleNamespace(
                        id="valid-id",
                        payload={
                            "chunk_id": "valid-id",
                            "source_id": "source-id",
                            "path": "001 - PROYECTOS/Aura.md",
                            "folder": "001 - PROYECTOS",
                            "title": "Aura",
                            "content": "Planificar la ceremonia.",
                            "frontmatter_json": '{"tipo":"proyecto"}',
                            "source_hash": "source-hash",
                            "chunk_hash": "chunk-hash",
                            "chunk_index": 0,
                            "chunk_count": 1,
                            "section_titles": ["Aura", "Seguimiento"],
                            "char_start": 42,
                            "char_end": 67,
                            "wikilinks": ["005 - CLIENTES/Ana"],
                        },
                    ),
                    SimpleNamespace(
                        id="incomplete-id",
                        payload={
                            "chunk_id": "incomplete-id",
                            "path": "001 - PROYECTOS/Incomplete.md",
                            "title": "Incomplete",
                            "content": "Nota incompleta.",
                        },
                    ),
                    SimpleNamespace(
                        id="image-only-id",
                        payload={
                            "chunk_id": "image-only-id",
                            "path": "001 - PROYECTOS/Image.md",
                            "title": "Image",
                            "content": "# Image\n\n![[logo.png|383]]",
                            "source_hash": "image-source-hash",
                            "chunk_hash": "image-chunk-hash",
                        },
                    ),
                ]
            )

    results = SemanticSearchService._semantic_search(
        FakeQdrantClient(),  # type: ignore[arg-type]
        [1.0, 0.0],
        10,
        0.35,
    )

    assert len(results) == 1
    assert results[0].chunk_id == "valid-id"
    assert results[0].provenance.section_titles == ("Aura", "Seguimiento")
    assert results[0].provenance.source_hash == "source-hash"


def test_vector_hit_must_match_canonical_source_and_chunk_metadata() -> None:
    source = parse_markdown_source(
        "005 - CLIENTES/Ana.md",
        "---\nnombre: Ana García\n---\n"
        "# Ana\n\n## Seguimiento\n\nPreparar la ceremonia.",
    )
    chunk = source.chunks[0]
    provenance = SourceProvenance(
        source_id=source.source_id,
        folder=source.folder,
        frontmatter=source.frontmatter,
        section_titles=chunk.section_titles,
        chunk_index=chunk.chunk_index,
        chunk_count=len(source.chunks),
        char_start=chunk.char_start,
        char_end=chunk.char_end,
        source_hash=source.source_hash,
        chunk_hash=chunk.chunk_hash,
    )
    ranked_hit = semantic_search._RankedHit(
        chunk_id=chunk.chunk_id,
        path=source.path,
        title=source.title,
        content=chunk.content,
        wikilinks=source.wikilinks,
        provenance=provenance,
    )
    active_chunk = semantic_search._ActiveChunk(
        source_id=source.source_id,
        path=source.path,
        folder=source.folder,
        title=source.title,
        frontmatter_json=source.frontmatter_json,
        wikilinks=source.wikilinks,
        source_hash=source.source_hash,
        chunk_hash=chunk.chunk_hash,
        chunk_index=chunk.chunk_index,
        chunk_count=len(source.chunks),
        section_titles=chunk.section_titles,
        char_start=chunk.char_start,
        char_end=chunk.char_end,
    )

    assert semantic_search._matches_active_chunk(ranked_hit, active_chunk)
    mismatched_source = replace(ranked_hit, path="001 - PROYECTOS/Otra.md")
    mismatched_content = replace(ranked_hit, content="Un pasaje alterado.")
    assert not semantic_search._matches_active_chunk(
        mismatched_source,
        active_chunk,
    )
    assert not semantic_search._matches_active_chunk(
        mismatched_content,
        active_chunk,
    )


def test_remote_ollama_endpoint_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "https://example.com")

    with pytest.raises(SemanticSearchError, match="endpoint Ollama local"):
        OllamaEmbeddingProvider("nomic-embed-text")


def test_ollama_embedding_retries_local_connection_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []
    client_options: dict[str, object] = {}

    class FlakyClient:
        def __init__(self, **options: object) -> None:
            client_options.update(options)

        def embed(self, **options: object) -> object:
            requests.append(options)
            if len(requests) == 1:
                raise httpx.ConnectError("temporary local outage")
            return type("EmbeddingResponse", (), {"embeddings": [[0.25, 0.75]]})()

    monkeypatch.setattr(semantic_search.ollama, "Client", FlakyClient)
    monkeypatch.setattr(semantic_search.time, "sleep", lambda _: None)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    vectors = OllamaEmbeddingProvider("nomic-embed-text").embed(["nota local"])

    assert vectors == [[0.25, 0.75]]
    assert len(requests) == 2
    assert all(request["keep_alive"] == "0" for request in requests)
    assert client_options["trust_env"] is False


def test_index_directory_cannot_overlap_obsidian_vault(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    embedder = DeterministicEmbedder()
    service = _service(vault, vault / ".blackbelt-index", embedder)

    with pytest.raises(SemanticSearchError, match="fuera de la bóveda"):
        service.index()


def test_long_markdown_paragraphs_are_chunked_with_overlap_and_offsets() -> None:
    content = "palabra " * 400

    source = parse_markdown_source("libro.md", content)
    chunks = source.chunks

    assert len(chunks) == 3
    assert all(len(chunk.content) <= 1200 for chunk in chunks)
    assert chunks[0].char_end - chunks[1].char_start == 160
    assert chunks[1].char_end - chunks[2].char_start == 160
    assert content[chunks[0].char_start : chunks[0].char_end].startswith(
        chunks[0].content[:100]
    )


def test_search_cli_keeps_full_source_path_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    monkeypatch.setattr(
        search_cli,
        "console",
        Console(file=output, width=42, force_terminal=False),
    )
    source_path = "001 - PROYECTOS/Ceremonia y reservas.md"

    search_cli._render_search_results(
        [
            SearchHit(
                path=source_path,
                title="Ceremonia",
                content="Planificar reservas.",
                wikilinks=(),
                score=0.02,
                semantic_rank=1,
                lexical_rank=1,
                provenance=SourceProvenance(
                    source_id="source-id",
                    folder="001 - PROYECTOS",
                    frontmatter={"estado": "en curso"},
                    section_titles=("Aura", "Seguimiento"),
                    chunk_index=0,
                    chunk_count=2,
                    char_start=25,
                    char_end=70,
                    source_hash="source-hash",
                    chunk_hash="chunk-hash",
                ),
            )
        ]
    )

    rendered = output.getvalue()
    assert source_path in " ".join(rendered.split())
    assert "semántica #1 · BM25 #1" in rendered
    assert "Sección: Aura > Seguimiento" in rendered
    assert "Fragmento: 1/2 · caracteres 25-70" in rendered
    assert 'Metadatos: {"estado": "en curso"}' in rendered
    normalized_output = " ".join(rendered.split())
    assert "SHA-256 fuente: source-hash" in normalized_output
    assert "SHA-256 fragmento: chunk-hash" in normalized_output
    assert "no una probabilidad" in rendered
