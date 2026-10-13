"""Tests for bounded, transient Companion attachment handling."""

from __future__ import annotations

import io
import time
import zipfile

import pytest

from blackbelt.knowledge.companion_attachments import (
    AttachmentError,
    AttachmentStore,
    extract_docx_text,
)


def _docx_bytes(text: str) -> bytes:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
        f"{text}"
        "</w:t></w:r></w:p></w:body></w:document>"
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    return stream.getvalue()


def test_plain_text_is_returned_only_from_ephemeral_store() -> None:
    store = AttachmentStore()

    item = store.add("idea.md", b"# Idea\n\nLocal only.")

    assert item.kind == "document"
    assert item.text == "# Idea\n\nLocal only."
    assert store.get_many([item.id]) == [item]
    assert item.public_metadata()["text_chars"] == len(item.text)


def test_image_is_kept_as_bytes_for_local_multimodal_model() -> None:
    store = AttachmentStore()
    png = b"\x89PNG\r\n\x1a\n" + b"not-decoded-by-blackbelt"

    item = store.add("sketch.png", png)

    assert item.kind == "image"
    assert item.media_type == "image/png"
    assert item.image == png
    assert item.text == ""


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("payload.exe", b"not text"),
        ("broken.txt", b"\xff"),
        ("fake.png", b"plain text"),
        ("empty.md", b""),
    ],
)
def test_rejects_unsupported_or_malformed_files(
    filename: str,
    content: bytes,
) -> None:
    with pytest.raises(AttachmentError):
        AttachmentStore().add(filename, content)


def test_docx_extracts_paragraph_text_without_extracting_files() -> None:
    content = _docx_bytes("Propuesta del proyecto")

    assert extract_docx_text(content) == "Propuesta del proyecto"
    item = AttachmentStore().add("plan.docx", content)
    assert item.text == "Propuesta del proyecto"


def test_missing_and_duplicate_attachment_ids_are_rejected() -> None:
    store = AttachmentStore()
    item = store.add("note.txt", b"local")

    with pytest.raises(AttachmentError, match="repetidos"):
        store.get_many([item.id, item.id])
    with pytest.raises(AttachmentError, match="ya no está disponible"):
        store.get_many(["00000000-0000-0000-0000-000000000000"])


def test_expired_attachment_is_removed_on_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AttachmentStore()
    monotonic = time.monotonic
    item = store.add("note.txt", b"temporary")
    monkeypatch.setattr(
        "blackbelt.knowledge.companion_attachments.time.monotonic",
        lambda: monotonic() + 31 * 60,
    )

    with pytest.raises(AttachmentError, match="expiró"):
        store.get_many([item.id])
