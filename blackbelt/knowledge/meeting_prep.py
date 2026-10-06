"""Read-only meeting context assembly from an allowlisted Obsidian vault."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ALLOWED_FOLDERS = (
    "001 - PROYECTOS",
    "002 - TAREAS",
    "005 - CLIENTES",
    "013 - PROPUESTA _PRESUPUESTO",
    "019 - REUNIONES",
)
CLIENT_FOLDER = "005 - CLIENTES"
PROJECT_FOLDER = "001 - PROYECTOS"
PROPOSAL_FOLDER = "013 - PROPUESTA _PRESUPUESTO"
PROJECT_FIELDS = ("proyectos_activos", "proyectos_previos")
CLIENT_REFERENCE_FIELDS = (
    "cliente",
    "client",
    "cliente_id",
    "id_cliente",
    "cliente_ref",
)
_WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")
_HEADING_PATTERN = re.compile(r"(?m)^#\s+(.+?)\s*$")
_SECTION_PATTERN = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")
_IDENTIFIER_PATTERN = re.compile(r"^([A-Za-z]{2,}[-_]?\d+)(?:\s|$)")
_FRONTMATTER_PATTERN = re.compile(
    r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)


class MeetingPreparationError(ValueError):
    """Raised when a safe meeting dossier cannot be assembled."""


class MeetingClientNotFoundError(MeetingPreparationError):
    """Raised when the requested client is not present in the CRM folder."""


class AmbiguousMeetingClientError(MeetingPreparationError):
    """Raised when a client query matches multiple CRM notes."""


@dataclass(frozen=True)
class VaultNote:
    """A Markdown note loaded from one explicitly allowed vault folder."""

    path: Path
    content: str
    body: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MeetingDossier:
    """Resolved client context and auditable source references."""

    client: VaultNote
    related_notes: tuple[VaultNote, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class MeetingClientSummary:
    """Minimal client identity for a local read-only interface."""

    client_id: str
    name: str
    status: str
    source: str


class MeetingPreparationService:
    """Build a deterministic dossier without changing the vault."""

    def __init__(self, vault: Path) -> None:
        self._vault = vault.expanduser()

    def list_clients(self) -> tuple[MeetingClientSummary, ...]:
        """List client identities without reading other vault folders."""
        if not self._vault.is_dir():
            raise MeetingPreparationError(
                f"La bóveda configurada no existe o no es una carpeta: "
                f"{self._vault}"
            )
        safe_roots, _ = self._safe_roots()
        client_root = safe_roots.get(CLIENT_FOLDER)
        if client_root is None:
            raise MeetingPreparationError(
                f"No existe la carpeta permitida '{CLIENT_FOLDER}' en la bóveda."
            )

        notes, _ = self._load_notes({CLIENT_FOLDER: client_root})
        summaries = [
            self.summarize_client(note)
            for note in notes
            if note.path.is_relative_to(client_root)
        ]
        return tuple(sorted(summaries, key=lambda item: item.name.casefold()))

    def summarize_client(self, note: VaultNote) -> MeetingClientSummary:
        """Return only the display-safe identity fields for a client note."""
        return _client_summary(note, self.relative_path(note.path))

    def prepare(self, client_query: str) -> MeetingDossier:
        """Resolve a client and gather only explicit, allowlisted references."""
        if not self._vault.is_dir():
            raise MeetingPreparationError(
                f"La bóveda configurada no existe o no es una carpeta: "
                f"{self._vault}"
            )

        safe_roots, warnings = self._safe_roots()
        client_root = safe_roots.get(CLIENT_FOLDER)
        if client_root is None:
            raise MeetingPreparationError(
                f"No existe la carpeta permitida '{CLIENT_FOLDER}' en la bóveda."
            )

        notes, read_warnings = self._load_notes(safe_roots)
        warnings.extend(read_warnings)
        clients = [
            note for note in notes
            if note.path.is_relative_to(client_root)
        ]
        client = self._resolve_client(clients, client_query)
        related_notes = self._find_related_notes(notes, client)
        return MeetingDossier(
            client=client,
            related_notes=tuple(related_notes),
            warnings=tuple(warnings),
        )

    def _safe_roots(self) -> tuple[dict[str, Path], list[str]]:
        roots: dict[str, Path] = {}
        warnings: list[str] = []
        for folder_name in ALLOWED_FOLDERS:
            candidate = self._vault / folder_name
            if candidate.is_symlink():
                warnings.append(
                    f"Se omitió una carpeta permitida que es un enlace simbólico: "
                    f"{folder_name}"
                )
                continue
            if candidate.is_dir():
                roots[folder_name] = candidate
            elif folder_name == CLIENT_FOLDER:
                continue
            else:
                warnings.append(f"No existe la carpeta permitida: {folder_name}")
        return roots, warnings

    def _load_notes(
        self,
        roots: dict[str, Path],
    ) -> tuple[list[VaultNote], list[str]]:
        notes: list[VaultNote] = []
        warnings: list[str] = []
        vault_root = self._vault.resolve()

        for folder_name in ALLOWED_FOLDERS:
            root = roots.get(folder_name)
            if root is None:
                continue
            for path in sorted(root.rglob("*.md"), key=lambda item: str(item).casefold()):
                if self._contains_hidden_or_symlink(path, root):
                    continue
                try:
                    resolved = path.resolve(strict=True)
                    resolved.relative_to(root.resolve(strict=True))
                    resolved.relative_to(vault_root)
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError, ValueError) as exc:
                    warnings.append(
                        f"No se pudo leer {self.relative_path(path)}: {exc}"
                    )
                    continue

                metadata, body, parse_warning = _parse_markdown(content)
                notes.append(
                    VaultNote(
                        path=path,
                        content=content,
                        body=body,
                        metadata=metadata,
                    )
                )
                if parse_warning:
                    warnings.append(
                        f"{self.relative_path(path)}: {parse_warning}"
                    )
        return notes, warnings

    @staticmethod
    def _contains_hidden_or_symlink(path: Path, root: Path) -> bool:
        try:
            relative = path.relative_to(root)
        except ValueError:
            return True
        current = root
        if root.is_symlink():
            return True
        for part in relative.parts:
            if part.startswith("."):
                return True
            current = current / part
            if current.is_symlink():
                return True
        return False

    def _resolve_client(
        self,
        clients: list[VaultNote],
        query: str,
    ) -> VaultNote:
        normalized_query = _normalize_reference(query)
        if not normalized_query:
            raise MeetingPreparationError("Indica un ID o nombre de cliente.")

        matches = [
            note
            for note in clients
            if normalized_query in _client_aliases(note)
        ]
        if not matches:
            raise MeetingClientNotFoundError(
                f"No se encontró '{query}' en {CLIENT_FOLDER}. "
                "Usa el ID o nombre exacto de la ficha."
            )
        if len(matches) > 1:
            paths = ", ".join(self.relative_path(note.path) for note in matches)
            raise AmbiguousMeetingClientError(
                f"El cliente '{query}' es ambiguo. Coincide con: {paths}"
            )
        return matches[0]

    def _find_related_notes(
        self,
        notes: list[VaultNote],
        client: VaultNote,
    ) -> list[VaultNote]:
        aliases = _client_aliases(client)
        identifiers = _client_identifiers(client)
        project_names = _project_names(client)
        related: list[VaultNote] = []

        for note in notes:
            if note.path == client.path:
                continue
            is_project_name_match = (
                self._folder_name(note.path) == PROJECT_FOLDER
                and _normalize_reference(note.path.stem) in project_names
            )
            if is_project_name_match or _has_explicit_client_reference(
                note,
                aliases,
                identifiers,
            ):
                related.append(note)

        return sorted(
            related,
            key=lambda note: (
                self._folder_order(note.path),
                str(note.path).casefold(),
            ),
        )

    def _folder_name(self, path: Path) -> str:
        try:
            return path.relative_to(self._vault).parts[0]
        except (ValueError, IndexError):
            return ""

    def _folder_order(self, path: Path) -> int:
        folder_order = {
            name: index for index, name in enumerate(ALLOWED_FOLDERS)
        }
        return folder_order.get(self._folder_name(path), len(folder_order))

    def relative_path(self, path: Path) -> str:
        try:
            return path.relative_to(self._vault).as_posix()
        except ValueError:
            return path.name


def _parse_markdown(
    content: str,
) -> tuple[dict[str, Any], str, str | None]:
    match = _FRONTMATTER_PATTERN.match(content)
    if match is None:
        return {}, content, None
    raw_metadata = match.group(1)
    try:
        loaded = yaml.safe_load(raw_metadata) or {}
    except yaml.YAMLError as exc:
        return {}, content[match.end():], f"frontmatter YAML no válido ({exc})."
    if not isinstance(loaded, dict):
        return {}, content[match.end():], "el frontmatter no contiene un mapa YAML."
    return loaded, content[match.end():], None


def _normalize_reference(value: str) -> str:
    reference = value.strip()
    if reference.startswith("[[") and reference.endswith("]]"):
        reference = reference[2:-2]
    reference = reference.split("|", maxsplit=1)[0].strip()
    reference = reference.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    if reference.casefold().endswith(".md"):
        reference = reference[:-3]
    normalized = unicodedata.normalize("NFKD", reference)
    without_accents = "".join(
        character for character in normalized
        if not unicodedata.combining(character)
    )
    return " ".join(without_accents.casefold().split())


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple):
        return [
            item
            for nested in value
            for item in _string_values(nested)
        ]
    if isinstance(value, dict):
        return [
            item
            for nested in value.values()
            for item in _string_values(nested)
        ]
    return []


def _client_aliases(note: VaultNote) -> set[str]:
    aliases = {
        _normalize_reference(note.path.stem),
        _normalize_reference(note.path.name),
    }
    identifier_match = _IDENTIFIER_PATTERN.match(note.path.stem)
    if identifier_match:
        aliases.add(_normalize_reference(identifier_match.group(1)))
    for field_name in (
        "id",
        "cliente_id",
        "client_id",
        "nombre",
        "name",
        "alias",
        "aliases",
    ):
        for value in _string_values(note.metadata.get(field_name)):
            aliases.add(_normalize_reference(value))
    for title in _HEADING_PATTERN.findall(note.body):
        aliases.add(_normalize_reference(title))
    return {alias for alias in aliases if alias}


def _client_identifiers(note: VaultNote) -> set[str]:
    identifiers: set[str] = set()
    identifier_match = _IDENTIFIER_PATTERN.match(note.path.stem)
    if identifier_match:
        identifiers.add(identifier_match.group(1).casefold())
    for key in ("id", "cliente_id", "client_id"):
        for value in _string_values(note.metadata.get(key)):
            identifiers.add(value.strip().casefold())
    return identifiers


def _client_summary(note: VaultNote, source: str) -> MeetingClientSummary:
    explicit_id = next(
        (
            value.strip()
            for key in ("id", "cliente_id", "client_id")
            for value in _string_values(note.metadata.get(key))
            if value.strip()
        ),
        "",
    )
    identifier_match = _IDENTIFIER_PATTERN.match(note.path.stem)
    client_id = explicit_id or (
        identifier_match.group(1) if identifier_match else note.path.stem
    )
    name = next(
        (
            value.strip()
            for key in ("nombre", "name")
            for value in _string_values(note.metadata.get(key))
            if value.strip()
        ),
        "",
    )
    if not name:
        name = next(
            (
                heading.strip()
                for heading in _HEADING_PATTERN.findall(note.body)
                if heading.strip()
            ),
            note.path.stem,
        )
    status_values = _string_values(note.metadata.get("estado"))
    return MeetingClientSummary(
        client_id=client_id,
        name=name,
        status=status_values[0] if status_values else "",
        source=source,
    )


def _project_names(note: VaultNote) -> set[str]:
    return {
        _normalize_reference(value)
        for field_name in PROJECT_FIELDS
        for value in _string_values(note.metadata.get(field_name))
        if _normalize_reference(value)
    }


def _has_explicit_client_reference(
    note: VaultNote,
    aliases: set[str],
    identifiers: set[str],
) -> bool:
    for field_name in CLIENT_REFERENCE_FIELDS:
        for value in _string_values(note.metadata.get(field_name)):
            normalized = _normalize_reference(value)
            if normalized in aliases:
                return True

    for target in _WIKILINK_PATTERN.findall(note.content):
        if _normalize_reference(target) in aliases:
            return True

    return any(
        re.search(rf"(?<![\w]){re.escape(identifier)}(?![\w])", note.content, re.I)
        is not None
        for identifier in identifiers
    )


def client_context_markdown(note: VaultNote) -> str:
    """Return client note sections useful for meetings, excluding contact tables."""
    selected: list[str] = []
    skipped_heading_level: int | None = None
    for line in note.body.splitlines():
        heading = _SECTION_PATTERN.match(line)
        if heading is not None:
            level = len(heading.group(1))
            title = heading.group(2)
            if skipped_heading_level is not None and level <= skipped_heading_level:
                skipped_heading_level = None
            if skipped_heading_level is not None:
                continue
            if "contacto" in _normalize_reference(title):
                skipped_heading_level = level
                continue
        elif skipped_heading_level is not None:
            continue
        selected.append(line)
    return "\n".join(selected).strip()
