"""SQLite cache containing classifications but never raw message bodies."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from blackbelt.mail.models import ClassificationError, MailClassification


class EmailCacheError(RuntimeError):
    """Raised when the local classification cache cannot be read or written."""


class EmailCache:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        try:
            self._database_path.parent.mkdir(
                mode=0o700,
                parents=True,
                exist_ok=True,
            )
            with self._connection() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS email_classifications (
                        cache_key TEXT PRIMARY KEY,
                        result_json TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            if os.name != "nt":
                self._database_path.chmod(0o600)
        except sqlite3.Error as exc:
            raise EmailCacheError("No se pudo inicializar la caché local.") from exc
        except OSError as exc:
            raise EmailCacheError(
                "No se pudo crear la carpeta de caché local."
            ) from exc

    def get(self, cache_key: str) -> MailClassification | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT result_json FROM email_classifications "
                    "WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise EmailCacheError("No se pudo leer la caché local.") from exc
        if row is None:
            return None
        try:
            payload = json.loads(row[0])
            if not isinstance(payload, dict):
                raise ClassificationError(
                    "La clasificación almacenada no es un objeto."
                )
            return MailClassification.from_json_mapping(payload)
        except (json.JSONDecodeError, ClassificationError) as exc:
            raise EmailCacheError(
                "La caché contiene una clasificación dañada; "
                "revisa EMAIL_CACHE_PATH."
            ) from exc

    def set(self, cache_key: str, result: MailClassification) -> None:
        payload = json.dumps(
            result.to_mapping(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO email_classifications (cache_key, result_json)
                    VALUES (?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        result_json = excluded.result_json,
                        created_at = CURRENT_TIMESTAMP
                    """,
                    (cache_key, payload),
                )
        except sqlite3.Error as exc:
            raise EmailCacheError("No se pudo guardar en la caché local.") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()
