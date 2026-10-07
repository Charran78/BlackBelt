"""Shared Obsidian source links stored on project hub notes."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import yaml

PROJECT_SOURCE_FIELDS = {
    "propuestas": "013 - PROPUESTA _PRESUPUESTO",
    "reuniones": "019 - REUNIONES",
    "dossieres": "020 - DOSSIERES",
}
_FRONTMATTER_PATTERN = re.compile(
    r"\A(?P<opening>---[ \t]*\r?\n)"
    r"(?P<body>.*?)"
    r"(?P<closing>\r?\n---[ \t]*(?:\r?\n|\Z))",
    re.DOTALL,
)
_PROPERTY_PATTERN = re.compile(
    r"^(?P<key>propuestas|reuniones|dossieres):.*(?:\r?\n|\Z)$"
)
_WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")


@dataclass(frozen=True)
class ProjectSourceLink:
    """A selected project source used to update frontmatter relations."""

    kind: str
    relative_path: str
    title: str


def project_source_references(metadata: dict[str, Any]) -> set[str]:
    """Return normalized source targets from the project's link properties."""
    references: set[str] = set()
    for field in PROJECT_SOURCE_FIELDS:
        for value in _string_values(metadata.get(field)):
            references.update(_normalize_link_targets(value))
    return references


def project_source_paths(content: str) -> set[str]:
    """Read linked source paths from properties and managed/legacy body text."""
    metadata = _read_frontmatter(content)
    paths: set[str] = set()
    for field in PROJECT_SOURCE_FIELDS:
        for value in _string_values(metadata.get(field)):
            for target in _link_targets(value):
                path = _allowed_project_source_path(target)
                if path is not None:
                    paths.add(path)

    managed_section = _managed_section(content)
    for target in _WIKILINK_PATTERN.findall(managed_section):
        path = _allowed_project_source_path(target.split("|", maxsplit=1)[0])
        if path is not None:
            paths.add(path)
    for match in re.finditer(r"`([^`]+\.md)`", managed_section):
        path = _allowed_project_source_path(match.group(1))
        if path is not None:
            paths.add(path)
    return paths


def project_links_note(
    metadata: dict[str, Any],
    relative_path: str,
    stem: str,
) -> bool:
    """Match a selected source by full vault path or its unambiguous basename."""
    normalized_path = _normalize_target(relative_path)
    normalized_stem = _normalize_target(stem)
    for reference in project_source_references(metadata):
        if reference == normalized_path or reference == normalized_stem:
            return True
        if "/" not in reference and PurePosixPath(reference).stem == normalized_stem:
            return True
    return False


def update_project_source_properties(
    content: str,
    links: Iterable[ProjectSourceLink],
) -> str:
    """Replace only source-link frontmatter values, preserving other YAML text."""
    match = _FRONTMATTER_PATTERN.match(content)
    if match is None:
        raise ValueError("La nota del proyecto necesita frontmatter YAML.")

    values: dict[str, list[str]] = {
        field: [] for field in PROJECT_SOURCE_FIELDS
    }
    for link in links:
        field = _field_for_kind(link.kind)
        target = PurePosixPath(link.relative_path).as_posix()
        values[field].append(
            f"[[{target}|{link.title}]]"
        )
    for field, field_values in values.items():
        values[field] = sorted(set(field_values), key=str.casefold)

    old_lines = match.group("body").splitlines(keepends=True)
    new_lines: list[str] = []
    seen: set[str] = set()
    index = 0
    while index < len(old_lines):
        line = old_lines[index]
        property_match = _PROPERTY_PATTERN.match(line)
        if property_match is None:
            new_lines.append(line)
            index += 1
            continue

        field = property_match.group("key")
        if field in seen:
            raise ValueError(f"La propiedad '{field}' está duplicada.")
        seen.add(field)
        newline = "\r\n" if line.endswith("\r\n") else "\n"
        new_lines.append(f"{field}:{newline}")
        new_lines.extend(
            f"  - {json.dumps(value, ensure_ascii=False)}{newline}"
            for value in values[field]
        )
        index += 1
        while index < len(old_lines):
            nested_line = old_lines[index]
            if nested_line.strip() and not nested_line[0].isspace():
                break
            if nested_line.lstrip().startswith("#"):
                new_lines.append(nested_line)
            index += 1

    missing_fields = [
        field for field in PROJECT_SOURCE_FIELDS if field not in seen
    ]
    if missing_fields:
        if new_lines and not new_lines[-1].endswith(("\n", "\r")):
            new_lines[-1] += "\n"
        for field in missing_fields:
            new_lines.append(f"{field}:\n")
            new_lines.extend(
                f"  - {json.dumps(value, ensure_ascii=False)}\n"
                for value in values[field]
            )

    updated_frontmatter = "".join(new_lines)
    try:
        parsed = yaml.safe_load(updated_frontmatter) or {}
    except yaml.YAMLError as exc:
        raise ValueError("No se pudo actualizar el YAML del proyecto.") from exc
    if not isinstance(parsed, dict):
        raise TypeError("El frontmatter del proyecto debe ser un mapa YAML.")

    return (
        match.group("opening")
        + updated_frontmatter
        + match.group("closing")
        + content[match.end():]
    )


def _field_for_kind(kind: str) -> str:
    reverse_mapping = {
        "propuesta": "propuestas",
        "reunión": "reuniones",
        "dossier": "dossieres",
    }
    try:
        return reverse_mapping[kind]
    except KeyError as exc:
        raise ValueError(f"Tipo de fuente no permitido: {kind}.") from exc


def _read_frontmatter(content: str) -> dict[str, Any]:
    match = _FRONTMATTER_PATTERN.match(content)
    if match is None:
        return {}
    try:
        metadata = yaml.safe_load(match.group("body")) or {}
    except yaml.YAMLError:
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _managed_section(content: str) -> str:
    start_marker = "<!-- BLACKBELT:PANORAMA:START -->"
    end_marker = "<!-- BLACKBELT:PANORAMA:END -->"
    start = content.find(start_marker)
    if start < 0:
        return ""
    end = content.find(end_marker, start + len(start_marker))
    if end < 0:
        return ""
    return content[start + len(start_marker):end]


def _normalize_link_targets(value: str) -> set[str]:
    return {_normalize_target(target) for target in _link_targets(value)}


def _link_targets(value: str) -> set[str]:
    targets = _WIKILINK_PATTERN.findall(value)
    if not targets:
        return {value}
    return {target.split("|", maxsplit=1)[0] for target in targets}


def _normalize_target(value: str) -> str:
    target = value.strip().replace("\\", "/")
    if target.casefold().endswith(".md"):
        target = target[:-3]
    return target.strip("/").casefold()


def _allowed_project_source_path(value: str) -> str | None:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) < 2
        or path.parts[0] not in PROJECT_SOURCE_FIELDS.values()
        or not path.name.casefold().endswith(".md")
        or any(part.startswith(".") for part in path.parts)
    ):
        return None
    return path.as_posix()


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple):
        return [
            item
            for nested in value
            for item in _string_values(nested)
        ]
    return []
