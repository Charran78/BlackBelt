"""CLI for incremental work search and federated local retrieval."""

from __future__ import annotations

import argparse
import json
import shlex
from collections.abc import Sequence

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from blackbelt.core import config as cfg
from blackbelt.knowledge.companion import CompanionMemory
from blackbelt.knowledge.semantic_search import (
    SearchHit,
    SearchIndexProgress,
    SearchIndexStats,
    SemanticSearchError,
    SemanticSearchService,
)

console = Console()


def run(args: Sequence[str] | str | None = None) -> None:
    """Run work indexing or search one or both local corpora."""
    arguments = shlex.split(args) if isinstance(args, str) else list(args or [])
    parser = argparse.ArgumentParser(
        prog="blackbelt run search",
        description="Busca notas de trabajo y memoria local de Companion.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "index",
        help="Crea o actualiza el índice incremental de trabajo.",
    )
    query_parser = subparsers.add_parser(
        "query",
        help="Busca en el índice de trabajo o en ambos corpus locales.",
    )
    query_parser.add_argument(
        "query",
        nargs="+",
        help="Consulta en lenguaje natural.",
    )
    query_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Máximo por índice (1-50; scope all puede mostrar hasta el doble).",
    )
    query_parser.add_argument(
        "--scope",
        choices=("work", "all"),
        default="work",
        help=(
            "work busca solo en trabajo; all añade el índice existente "
            "de Companion sin crear otro índice."
        ),
    )
    parsed = parser.parse_args(arguments)
    try:
        work_search = _make_work_search()
        if parsed.command == "index":
            _run_index(work_search)
            return
        if not 1 <= parsed.limit <= 50:
            raise ValueError("--limit debe estar entre 1 y 50.")
        query = " ".join(parsed.query)
        work_results = work_search.search(query, limit=parsed.limit)
        if parsed.scope == "work":
            _render_search_results(work_results)
            return

        companion = CompanionMemory(
            cfg.OBSIDIAN_VAULT,
            cfg.COMPANION_INDEX_DIR,
            cfg.COMPANION_CONTRACT_FILE,
            model=cfg.SEARCH_EMBEDDING_MODEL,
            min_cosine_score=cfg.SEARCH_MIN_COSINE_SCORE,
        )
        memory_results = companion.search(query, limit=parsed.limit)
        _render_search_results(
            work_results,
            heading="Bóveda de trabajo · resultados híbridos",
        )
        _render_search_results(
            memory_results,
            heading="Memoria Companion · conversaciones y notas",
        )
        console.print(
            "[dim]Cada bloque conserva el orden de su propio índice; "
            "las puntuaciones entre corpus no se comparan. Recuperación con "
            "embeddings Ollama local, sin generar respuesta ni usar Cloud.[/]"
        )
    except (OSError, SemanticSearchError, ValueError) as exc:
        console.print(Text(f"No se pudo completar search: {exc}", style="red"))


def _make_work_search() -> SemanticSearchService:
    return SemanticSearchService(
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


def _run_index(service: SemanticSearchService) -> None:
    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}", markup=False),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
        task_id = progress.add_task("Preparando índice de trabajo…", total=None)

        def update_progress(event: SearchIndexProgress) -> None:
            total = event.total if event.total > 0 else None
            completed = min(event.current, event.total) if total else 0
            description = event.detail
            if event.stage == "scan":
                description = f"Escaneando notas y comparando hashes · {event.detail}"
            elif event.stage == "embed":
                description = (
                    f"Generando embeddings · {event.indexed}/{event.total} notas · "
                    f"{event.detail}"
                )
            elif event.stage == "cooldown":
                description = f"Pausa de seguridad · {event.detail}"
                total = max(event.total, 1)
                completed = min(event.current, total)
            elif event.stage == "cleanup":
                description = f"Limpiando vectores obsoletos · {event.detail}"
                total = None
                completed = 0
            elif event.stage == "complete":
                description = f"Indexación completada · {event.detail}"
                total = max(event.total, 1)
                completed = min(event.current, total)
            progress.update(
                task_id,
                description=description,
                total=total,
                completed=completed,
            )

        stats = service.index(progress_callback=update_progress)
    _render_index_stats(stats)


def _render_index_stats(stats: SearchIndexStats) -> None:
    table = Table(title="Índice local de trabajo")
    table.add_column("Escaneadas", justify="right")
    table.add_column("Actualizadas", justify="right")
    table.add_column("Sin cambios", justify="right")
    table.add_column("Eliminadas", justify="right")
    table.add_column("Omitidas", justify="right")
    table.add_row(
        str(stats.scanned),
        str(stats.indexed),
        str(stats.unchanged),
        str(stats.removed),
        str(stats.skipped),
    )
    console.print(table)
    for warning in stats.warnings:
        console.print(Text(f"Aviso: {warning}", style="yellow"))
    console.print(
        "[dim]Alcance: carpetas de trabajo permitidas; cursos y biblioteca "
        "incluidos. Newsletters, datos personales, chats de IA, plantillas, "
        "credenciales y borradores excluidos.[/]"
    )
    console.print(
        Text(
            "Carpetas incluidas: " + ", ".join(cfg.SEARCH_SOURCE_DIRS),
            style="dim",
        ),
        overflow="fold",
    )


def _render_search_results(
    results: list[SearchHit],
    *,
    heading: str | None = None,
) -> None:
    if heading is not None:
        console.print(Text(heading, style="bold cyan"))
    if not results:
        console.print("[dim]No se encontraron notas para esa consulta.[/]")
        return

    is_project_inventory = all(
        result.semantic_rank is None and result.lexical_rank is None
        for result in results
    )
    if heading is None:
        default_heading = (
            "[bold cyan]Inventario de proyectos (001 - PROYECTOS)[/]"
            if is_project_inventory
            else "[bold cyan]Resultados híbridos (BM25 + semántica)[/]"
        )
        console.print(default_heading)
    for index, result in enumerate(results, start=1):
        console.print()
        console.print(Text(f"{index}. {result.title}", style="bold"))
        console.print(Text(f"Fuente: {result.path}", style="dim"))
        if result.provenance is not None:
            provenance = result.provenance
            console.print(
                Text(
                    f"Procedencia: {provenance.source_id} · "
                    f"carpeta {provenance.folder or '(raíz)'}",
                    style="dim",
                ),
                overflow="fold",
            )
            if provenance.section_path:
                console.print(
                    Text(f"Sección: {provenance.section_path}", style="dim"),
                    overflow="fold",
                )
            if provenance.chunk_index is not None:
                console.print(
                    Text(
                        f"Fragmento: {provenance.chunk_index + 1}/"
                        f"{provenance.chunk_count} · caracteres "
                        f"{provenance.char_start}-{provenance.char_end}",
                        style="dim",
                    ),
                    overflow="fold",
                )
                console.print(
                    Text(
                        f"SHA-256 fuente: {provenance.source_hash}",
                        style="dim",
                    ),
                    overflow="fold",
                )
                console.print(
                    Text(
                        f"SHA-256 fragmento: {provenance.chunk_hash}",
                        style="dim",
                    ),
                    overflow="fold",
                )
            metadata_summary = {
                key: value
                for key, value in provenance.frontmatter.items()
                if key.casefold()
                in {
                    "autor",
                    "author",
                    "authors",
                    "editorial",
                    "publisher",
                    "isbn",
                    "tipo",
                    "type",
                    "cliente",
                    "proyecto",
                    "chapter",
                    "capitulo",
                    "estado",
                    "fecha",
                    "year",
                }
            }
            if metadata_summary:
                console.print(
                    Text(
                        "Metadatos: "
                        + json.dumps(metadata_summary, ensure_ascii=False),
                        style="dim",
                    ),
                    overflow="fold",
                )
        signals = []
        if result.semantic_rank is not None:
            signals.append(f"semántica #{result.semantic_rank}")
        if result.lexical_rank is not None:
            signals.append(f"BM25 #{result.lexical_rank}")
        if signals:
            console.print(Text(f"Coincidencias: {' · '.join(signals)}", style="cyan"))
        console.print(Text(result.content))
        if result.wikilinks:
            console.print(
                Text(
                    f"Wikilinks: {', '.join(result.wikilinks)}",
                    style="dim",
                )
            )
    if is_project_inventory:
        console.print(
            "[dim]Inventario tomado de las notas de proyecto; el contenido y "
            "el estado deben verificarse en cada fuente.[/]"
        )
    else:
        console.print(
            "[dim]Las posiciones indican el orden de cada motor, no una probabilidad. "
            "Verifica cada resultado en su nota fuente.[/]"
        )
