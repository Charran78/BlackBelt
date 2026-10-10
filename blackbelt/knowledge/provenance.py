"""Typed source and chunk provenance for local Markdown retrieval."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any

import yaml

_SOURCE_NAMESPACE = uuid.UUID("fa790845-c452-4072-9d69-85fed08ac632")
_CHUNK_NAMESPACE = uuid.UUID("c4d146a2-60a0-4a53-80fb-65ad02e6abfd")
_MAX_CHUNK_CHARS = 1200
_CHUNK_OVERLAP_CHARS = 160
_HEADING_PATTERN = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_SETEXT_HEADING_PATTERN = re.compile(r"^ {0,3}(=+|-+)\s*$")
_AUXILIARY_THINKING_PATTERN = re.compile(
    r"^ {0,3}>[ \t]*\*\*(?:Thinking:|Thinking steps)\*\*[ \t]*$",
    re.IGNORECASE,
)
_CONVERSATION_TURN_PATTERN = re.compile(
    r"^ {0,3}#{1,6}[ \t]+(?:Usuario|Asistente|User|Assistant|Gemini|"
    r"DeepSeek|ChatGPT|Claude|Modelo)[ \t]*:",
    re.IGNORECASE,
)
_OBSIDIAN_IMAGE_PATTERN = re.compile(r"!\[\[[^\]]+\]\]")
_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_WIKILINK_PATTERN = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
_WORD_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class SourceChunk:
    """One source passage with its original file location and hierarchy."""

    chunk_id: str
    chunk_index: int
    content: str
    section_titles: tuple[str, ...]
    char_start: int
    char_end: int
    chunk_hash: str

    @property
    def section_path(self) -> str:
        return " > ".join(self.section_titles)


@dataclass(frozen=True)
class SourceDocument:
    """Canonical identity and metadata for one Markdown source."""

    source_id: str
    path: str
    folder: str
    title: str
    frontmatter: dict[str, Any]
    source_hash: str
    wikilinks: tuple[str, ...]
    chunks: tuple[SourceChunk, ...]
    warnings: tuple[str, ...]

    @property
    def frontmatter_json(self) -> str:
        return json.dumps(
            self.frontmatter,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class SourceProvenance:
    """Search-result citation back to its source and exact passage."""

    source_id: str
    folder: str
    frontmatter: dict[str, Any]
    section_titles: tuple[str, ...]
    chunk_index: int | None
    chunk_count: int
    char_start: int | None
    char_end: int | None
    source_hash: str
    chunk_hash: str | None

    @property
    def section_path(self) -> str:
        return " > ".join(self.section_titles)


@dataclass(frozen=True)
class _Paragraph:
    content: str
    section_titles: tuple[str, ...]
    char_start: int
    char_end: int
    break_before: bool = False


def parse_markdown_source(
    path: str,
    content: str,
    *,
    exclude_thinking_blocks: bool = False,
) -> SourceDocument:
    """Parse a Markdown source while preserving offsets into the original file."""
    normalized_path = PurePosixPath(path).as_posix()
    source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    source_id = str(uuid.uuid5(_SOURCE_NAMESPACE, normalized_path))
    frontmatter, body_start, warning = _parse_frontmatter(content)
    body = content[body_start:]
    title = _source_title(frontmatter, body, normalized_path)
    paragraphs = _extract_paragraphs(
        body,
        body_start,
        exclude_thinking_blocks=exclude_thinking_blocks,
    )
    chunks = _build_chunks(
        source_id=source_id,
        source_hash=source_hash,
        paragraphs=paragraphs,
    )
    wikilinks = tuple(
        sorted(
            {
                target.split("|", maxsplit=1)[0].strip()
                for target in _WIKILINK_PATTERN.findall(content)
                if target.split("|", maxsplit=1)[0].strip()
            },
            key=str.casefold,
        )
    )
    folder = str(PurePosixPath(normalized_path).parent)
    if folder == ".":
        folder = ""
    warnings = (warning,) if warning else ()
    return SourceDocument(
        source_id=source_id,
        path=normalized_path,
        folder=folder,
        title=title,
        frontmatter=frontmatter,
        source_hash=source_hash,
        wikilinks=wikilinks,
        chunks=chunks,
        warnings=warnings,
    )


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], int, str | None]:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].lstrip("\ufeff").strip() != "---":
        return {}, 0, None

    offset = len(lines[0])
    closing_index: int | None = None
    closing_end = offset
    for index, line in enumerate(lines[1:], start=1):
        offset += len(line)
        if line.strip() in {"---", "..."}:
            closing_index = index
            closing_end = offset
            break
    if closing_index is None:
        return {}, 0, "frontmatter sin delimitador de cierre"

    raw_frontmatter = "".join(lines[1:closing_index])
    try:
        parsed = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as exc:
        return {}, closing_end, f"frontmatter YAML no válido ({type(exc).__name__})"
    if not isinstance(parsed, dict):
        return {}, closing_end, "frontmatter YAML no contiene un mapa"
    normalized = {str(key): _normalize_metadata(value) for key, value in parsed.items()}
    return normalized, closing_end, None


def _normalize_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_metadata(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_normalize_metadata(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _source_title(
    frontmatter: dict[str, Any],
    body: str,
    path: str,
) -> str:
    metadata_title = frontmatter.get("title")
    if isinstance(metadata_title, str) and metadata_title.strip():
        return metadata_title.strip()
    lines = body.splitlines()
    for index, line in enumerate(lines):
        match = _HEADING_PATTERN.match(line.strip())
        if match:
            return match.group(2).strip()
        if (
            line.strip()
            and index + 1 < len(lines)
            and _SETEXT_HEADING_PATTERN.match(lines[index + 1])
        ):
            return line.strip()
    return PurePosixPath(path).stem


def _extract_paragraphs(
    body: str,
    body_start: int,
    *,
    exclude_thinking_blocks: bool,
) -> list[_Paragraph]:
    paragraphs: list[_Paragraph] = []
    heading_stack: list[tuple[int, str]] = []
    paragraph_lines: list[str] = []
    paragraph_start = body_start
    paragraph_end = body_start
    paragraph_section: tuple[str, ...] = ()
    offset = body_start
    fence_marker: str | None = None
    skipping_thinking_block = False
    break_before_next_paragraph = False

    def flush_paragraph() -> None:
        nonlocal break_before_next_paragraph, paragraph_lines
        if paragraph_lines:
            paragraphs.append(
                _Paragraph(
                    content="".join(paragraph_lines).strip(),
                    section_titles=paragraph_section,
                    char_start=paragraph_start,
                    char_end=paragraph_end,
                    break_before=break_before_next_paragraph,
                )
            )
            paragraph_lines = []
            break_before_next_paragraph = False

    for line in body.splitlines(keepends=True):
        line_content = line.rstrip("\r\n")
        stripped = line_content.lstrip()
        if exclude_thinking_blocks and _CONVERSATION_TURN_PATTERN.match(line_content):
            flush_paragraph()
            fence_marker = None
            skipping_thinking_block = False
        fence_match = re.match(r"(```+|~~~+)", stripped)
        if fence_match:
            marker = fence_match.group(1)
            flush_paragraph()
            if fence_marker is None:
                fence_marker = marker[0]
            elif marker.startswith(fence_marker):
                fence_marker = None
            paragraph_start = offset
            paragraph_end = offset + len(line)
            paragraph_section = tuple(title for _, title in heading_stack)
            paragraph_lines.append(line)
            offset += len(line)
            continue

        if (
            exclude_thinking_blocks
            and fence_marker is None
            and _AUXILIARY_THINKING_PATTERN.match(line_content)
        ):
            flush_paragraph()
            paragraph_start = offset + len(line)
            paragraph_end = paragraph_start
            skipping_thinking_block = True
            break_before_next_paragraph = True
            offset += len(line)
            continue

        if skipping_thinking_block:
            if line_content.lstrip().startswith(">") or not line_content.strip():
                offset += len(line)
                continue
            skipping_thinking_block = False

        heading_match = _HEADING_PATTERN.match(line_content)
        if fence_marker is None and heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            heading_stack = [item for item in heading_stack if item[0] < level]
            heading_stack.append((level, heading_match.group(2).strip()))
            offset += len(line)
            continue

        setext_match = _SETEXT_HEADING_PATTERN.match(line_content)
        if fence_marker is None and setext_match and len(paragraph_lines) == 1:
            heading_title = paragraph_lines[0].strip()
            paragraph_lines = []
            level = 1 if setext_match.group(1).startswith("=") else 2
            heading_stack = [item for item in heading_stack if item[0] < level]
            heading_stack.append((level, heading_title))
            offset += len(line)
            continue

        if not line_content.strip() and fence_marker is None:
            flush_paragraph()
            offset += len(line)
            continue

        if not paragraph_lines:
            paragraph_start = offset
            paragraph_section = tuple(title for _, title in heading_stack)
        paragraph_lines.append(line)
        paragraph_end = offset + len(line)
        offset += len(line)

    flush_paragraph()
    return paragraphs


def _build_chunks(
    *,
    source_id: str,
    source_hash: str,
    paragraphs: list[_Paragraph],
) -> tuple[SourceChunk, ...]:
    chunks: list[SourceChunk] = []
    pending: list[_Paragraph] = []
    pending_size = 0

    def flush_pending() -> None:
        nonlocal pending, pending_size
        if not pending:
            return
        content = _clean_chunk("\n\n".join(item.content for item in pending))
        if _has_searchable_text(content):
            chunks.append(
                _make_chunk(
                    source_id,
                    source_hash,
                    len(chunks),
                    content,
                    pending[0].section_titles,
                    pending[0].char_start,
                    pending[-1].char_end,
                )
            )
        pending = []
        pending_size = 0

    for paragraph in paragraphs:
        if len(paragraph.content) > _MAX_CHUNK_CHARS:
            flush_pending()
            start = 0
            while start < len(paragraph.content):
                end = min(start + _MAX_CHUNK_CHARS, len(paragraph.content))
                content = _clean_chunk(paragraph.content[start:end])
                if _has_searchable_text(content):
                    chunks.append(
                        _make_chunk(
                            source_id,
                            source_hash,
                            len(chunks),
                            content,
                            paragraph.section_titles,
                            paragraph.char_start + start,
                            paragraph.char_start + end,
                        )
                    )
                if end == len(paragraph.content):
                    break
                start = end - _CHUNK_OVERLAP_CHARS
            continue

        if pending and (
            paragraph.break_before
            or pending[0].section_titles != paragraph.section_titles
            or pending_size + len(paragraph.content) > _MAX_CHUNK_CHARS
        ):
            flush_pending()
        pending.append(paragraph)
        pending_size += len(paragraph.content) + (2 if len(pending) > 1 else 0)

    flush_pending()
    return tuple(chunks)


def _make_chunk(
    source_id: str,
    source_hash: str,
    chunk_index: int,
    content: str,
    section_titles: tuple[str, ...],
    char_start: int,
    char_end: int,
) -> SourceChunk:
    chunk_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    identity = f"{source_id}\0{source_hash}\0{chunk_index}\0{chunk_hash}"
    return SourceChunk(
        chunk_id=str(uuid.uuid5(_CHUNK_NAMESPACE, identity)),
        chunk_index=chunk_index,
        content=content,
        section_titles=section_titles,
        char_start=char_start,
        char_end=char_end,
        chunk_hash=chunk_hash,
    )


def _clean_chunk(content: str) -> str:
    content = _OBSIDIAN_IMAGE_PATTERN.sub(" ", content)
    content = _MARKDOWN_IMAGE_PATTERN.sub(" ", content)
    return content.strip()


def _has_searchable_text(content: str) -> bool:
    return bool(_WORD_PATTERN.search(content))
