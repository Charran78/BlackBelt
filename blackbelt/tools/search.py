"""CLI for incremental hybrid semantic search over an Obsidian vault."""

from __future__ import annotations

import argparse
import json
import shlex
from collections.abc import Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text

from blackbelt.core import config as cfg
from blackbelt.knowledge.semantic_search import (
    SearchHit,
    SearchIndexStats,
    SemanticSearchError,
    SemanticSearchService,
)

console = Console()


def run(args: Sequence[str] | str | None = None) -> None:
    """Run the local indexer or a traceable hybrid search."""
    arguments = shlex.split(args) if isinstance(args, str) else list(args or [])
    parser = argparse.ArgumentParser(
        prog="blackbelt run search",
        description="Busca notas de Obsidian localmente con semántica y BM25.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "index",
        help="Crea o actualiza el índice incremental.",
    )
    query_parser = subparsers.add_parser(
        "query",
        help="Busca notas por significado y coincidencia léxica.",
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
        help="Máximo de notas que mostrar (1-50; por defecto: 5).",
    )
    parsed = parser.parse_args(arguments)
    try:
        service = SemanticSearchService(
            cfg.OBSIDIAN_VAULT,
            cfg.SEARCH_INDEX_DIR,
            model=cfg.SEARCH_EMBEDDING_MODEL,
            embedding_timeout=cfg.OLLAMA_TIMEOUT,
            min_cosine_score=cfg.SEARCH_MIN_COSINE_SCORE,
        )
        if parsed.command == "index":
            _render_index_stats(service.index())
        else:
            _render_search_results(
                service.search(" ".join(parsed.query), limit=parsed.limit)
            )
    except (OSError, SemanticSearchError, ValueError) as exc:
        console.print(Text(f"No se pudo completar search: {exc}", style="red"))


def _render_index_stats(stats: SearchIndexStats) -> None:
    table = Table(title="Índice local de Obsidian")
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
        "[dim]Índice local v2 con procedencia; 000 - PLANTILLAS, "
        "006 - CREDENCIALES y carpetas ocultas excluidas.[/]"
    )


def _render_search_results(results: list[SearchHit]) -> None:
    if not results:
        console.print("[dim]No se encontraron notas para esa consulta.[/]")
        return

    is_project_inventory = all(
        result.semantic_rank is None and result.lexical_rank is None
        for result in results
    )
    heading = (
        "[bold cyan]Inventario de proyectos (001 - PROYECTOS)[/]"
        if is_project_inventory
        else "[bold cyan]Resultados híbridos (BM25 + semántica)[/]"
    )
    console.print(heading)
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
