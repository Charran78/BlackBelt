"""Ephemeral, validated local attachments for Companion turns."""

from __future__ import annotations

import io
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import PurePath
from threading import Lock
from uuid import UUID, uuid4

_MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
_MAX_ATTACHMENT_TEXT_CHARS = 40_000
_MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024
_MAX_ATTACHMENTS_PER_TURN = 5
_ATTACHMENT_TTL_SECONDS = 30 * 60
_MAX_PDF_PAGES = 200
_MAX_DOCX_UNCOMPRESSED_BYTES = 24 * 1024 * 1024
_MAX_DOCX_MEMBERS = 2_000
_ALLOWED_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
}
_IMAGE_MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


class AttachmentError(ValueError):
    """Raised when an attachment is unsupported, malformed, or exceeds limits."""


@dataclass(frozen=True)
class CompanionAttachment:
    """An attachment held only in process memory until its TTL expires."""

    id: str
    name: str
    kind: str
    media_type: str
    size_bytes: int
    text: str = ""
    image: bytes = b""
    expires_at: float = 0.0

    def public_metadata(self) -> dict[str, str | int]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "text_chars": len(self.text),
        }


class AttachmentStore:
    """Bounded in-memory storage; content is never written to disk."""

    def __init__(self) -> None:
        self._items: dict[str, CompanionAttachment] = {}
        self._lock = Lock()

    def add(
        self,
        filename: str,
        content: bytes,
    ) -> CompanionAttachment:
        if not content:
            raise AttachmentError("El archivo está vacío.")
        if len(content) > _MAX_ATTACHMENT_BYTES:
            raise AttachmentError("Cada adjunto debe ocupar como máximo 8 MiB.")
        name = _safe_filename(filename)
        extension = PurePath(name).suffix.casefold()
        item = _parse_attachment(name, extension, content)
        with self._lock:
            self._clean_expired()
            if len(self._items) >= _MAX_ATTACHMENTS_PER_TURN:
                raise AttachmentError(
                    f"Se permiten como máximo {_MAX_ATTACHMENTS_PER_TURN} adjuntos temporales."
                )
            total_bytes = sum(
                attachment.size_bytes for attachment in self._items.values()
            )
            if total_bytes + len(content) > _MAX_TOTAL_ATTACHMENT_BYTES:
                raise AttachmentError(
                    "Los adjuntos temporales superarían el límite total de 20 MiB."
                )
            stored = CompanionAttachment(
                id=str(uuid4()),
                name=name,
                kind=item.kind,
                media_type=item.media_type,
                size_bytes=len(content),
                text=item.text,
                image=item.image,
                expires_at=time.monotonic() + _ATTACHMENT_TTL_SECONDS,
            )
            self._items[stored.id] = stored
            return stored

    def get_many(self, identifiers: list[str]) -> list[CompanionAttachment]:
        if len(identifiers) > _MAX_ATTACHMENTS_PER_TURN:
            raise AttachmentError(
                f"Se permiten como máximo {_MAX_ATTACHMENTS_PER_TURN} adjuntos por turno."
            )
        if len(set(identifiers)) != len(identifiers):
            raise AttachmentError("La lista contiene adjuntos repetidos.")
        try:
            normalized = [str(UUID(identifier)) for identifier in identifiers]
        except ValueError as exc:
            raise AttachmentError("La referencia a un adjunto no es válida.") from exc
        with self._lock:
            self._clean_expired()
            missing = [
                identifier for identifier in normalized if identifier not in self._items
            ]
            if missing:
                raise AttachmentError(
                    "Un adjunto expiró o ya no está disponible. Vuelve a subirlo."
                )
            return [self._items[identifier] for identifier in normalized]

    def remove(self, identifier: str) -> None:
        try:
            normalized = str(UUID(identifier))
        except ValueError as exc:
            raise AttachmentError("La referencia a un adjunto no es válida.") from exc
        with self._lock:
            self._items.pop(normalized, None)

    def _clean_expired(self) -> None:
        now = time.monotonic()
        expired = [
            identifier
            for identifier, item in self._items.items()
            if item.expires_at <= now
        ]
        for identifier in expired:
            self._items.pop(identifier, None)


def extract_docx_text(content: bytes) -> str:
    """Read paragraph text from a bounded DOCX archive without extracting files."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if (
                len(members) > _MAX_DOCX_MEMBERS
                or sum(member.file_size for member in members)
                > _MAX_DOCX_UNCOMPRESSED_BYTES
            ):
                raise AttachmentError(
                    "El DOCX excede los límites de expansión permitidos."
                )
            document = archive.read("word/document.xml")
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise AttachmentError("El archivo DOCX no es válido o está dañado.") from exc
    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise AttachmentError("El contenido XML del DOCX no es válido.") from exc
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", _DOCX_NS):
        text = "".join(
            node.text or "" for node in paragraph.findall(".//w:t", _DOCX_NS)
        )
        if text:
            paragraphs.append(text)
    return _bounded_text("\n".join(paragraphs))


@dataclass(frozen=True)
class _ParsedAttachment:
    kind: str
    media_type: str
    text: str = ""
    image: bytes = b""


def _parse_attachment(
    filename: str,
    extension: str,
    content: bytes,
) -> _ParsedAttachment:
    if extension in _IMAGE_MIME_BY_EXTENSION:
        media_type = _IMAGE_MIME_BY_EXTENSION[extension]
        if not _matches_image_signature(media_type, content):
            raise AttachmentError(
                "La extensión y el contenido de la imagen no coinciden."
            )
        return _ParsedAttachment("image", media_type, image=content)
    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise AttachmentError("El contenido no tiene una cabecera PDF válida.")
        return _ParsedAttachment(
            "document",
            "application/pdf",
            text=_extract_pdf_text(content),
        )
    if extension == ".docx":
        return _ParsedAttachment(
            "document",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            text=extract_docx_text(content),
        )
    if extension in _ALLOWED_TEXT_EXTENSIONS:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AttachmentError(
                f"El archivo {filename} no es texto UTF-8 válido."
            ) from exc
        return _ParsedAttachment(
            "document",
            "text/plain",
            text=_bounded_text(text),
        )
    raise AttachmentError(
        "Formato no admitido. Usa PDF, DOCX, texto/código UTF-8, PNG, JPG o WEBP."
    )


def _extract_pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:
        raise AttachmentError(
            "Para leer PDF instala las dependencias actuales de BlackBelt."
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if len(reader.pages) > _MAX_PDF_PAGES:
            raise AttachmentError(
                f"El PDF supera el límite de {_MAX_PDF_PAGES} páginas."
            )
        extracted: list[str] = []
        current_size = 0
        for page in reader.pages:
            remaining = _MAX_ATTACHMENT_TEXT_CHARS - current_size
            if remaining <= 0:
                break
            page_text = page.extract_text() or ""
            extracted.append(page_text[:remaining])
            current_size += len(page_text)
    except AttachmentError:
        raise
    except (OSError, ValueError, PdfReadError) as exc:
        raise AttachmentError(
            "No se pudo leer el PDF; comprueba que no esté dañado."
        ) from exc
    text = _bounded_text("\n".join(extracted))
    if not text:
        raise AttachmentError(
            "El PDF no contiene texto extraíble. El OCR no está incluido."
        )
    return text


def _bounded_text(value: str) -> str:
    normalized = value.replace("\x00", "").strip()
    if not normalized:
        raise AttachmentError("No se encontró texto legible en el archivo.")
    return normalized[:_MAX_ATTACHMENT_TEXT_CHARS]


def _safe_filename(filename: str) -> str:
    basename = PurePath(filename.replace("\\", "/")).name
    normalized = _CONTROL_CHARACTERS.sub("", basename).strip()
    if not normalized or normalized in {".", ".."}:
        raise AttachmentError("El nombre del archivo no es válido.")
    return normalized[:180]


def _matches_image_signature(media_type: str, content: bytes) -> bool:
    if media_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if media_type == "image/webp":
        return (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        )
    return False
