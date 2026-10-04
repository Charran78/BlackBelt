"""Validated runtime settings for Gmail triage."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class EmailSettingsError(ValueError):
    """Raised when email triage configuration is invalid."""


@dataclass(frozen=True, slots=True)
class EmailSettings:
    model: str
    hours: int
    message_limit: int
    imap_timeout: int
    ollama_timeout: int
    context_size: int
    num_predict: int
    num_threads: int | None
    keep_alive: str
    cache_path: Path
    min_confidence: int
    max_body_chars: int = 3000
    max_message_bytes: int = 1_000_000

    @classmethod
    def from_environment(cls) -> EmailSettings:
        hours = _integer_setting("EMAIL_HOURS", 48, minimum=1, maximum=24 * 90)
        message_limit = _integer_setting(
            "EMAIL_LIMIT",
            100,
            minimum=1,
            maximum=500,
        )
        imap_timeout = _integer_setting(
            "EMAIL_IMAP_TIMEOUT",
            30,
            minimum=1,
            maximum=300,
        )
        max_message_bytes = _integer_setting(
            "EMAIL_MAX_MESSAGE_BYTES",
            1_000_000,
            minimum=64_000,
            maximum=10_000_000,
        )
        ollama_timeout = _integer_setting(
            "OLLAMA_TIMEOUT",
            180,
            minimum=1,
            maximum=3600,
        )
        context_size = _integer_setting(
            "OLLAMA_NUM_CTX",
            512,
            minimum=128,
            maximum=131072,
        )
        num_predict = _integer_setting(
            "EMAIL_NUM_PREDICT",
            350,
            minimum=32,
            maximum=8192,
        )
        min_confidence = _integer_setting(
            "EMAIL_MIN_CONFIDENCE",
            70,
            minimum=0,
            maximum=100,
        )
        raw_threads = os.getenv("OLLAMA_NUM_THREAD", "").strip()
        num_threads = (
            _parse_integer(raw_threads, "OLLAMA_NUM_THREAD", 1, 256)
            if raw_threads
            else None
        )

        model = os.getenv("EMAIL_OLLAMA_MODEL", os.getenv(
            "OLLAMA_MODEL",
            "qwen2.5:0.5b",
        )).strip()
        if not model:
            raise EmailSettingsError("EMAIL_OLLAMA_MODEL no puede estar vacío.")

        cache_path = _cache_path(os.getenv("EMAIL_CACHE_PATH", "").strip())
        return cls(
            model=model,
            hours=hours,
            message_limit=message_limit,
            imap_timeout=imap_timeout,
            ollama_timeout=ollama_timeout,
            context_size=context_size,
            num_predict=num_predict,
            num_threads=num_threads,
            keep_alive=os.getenv("OLLAMA_KEEP_ALIVE", "0"),
            cache_path=cache_path,
            min_confidence=min_confidence,
            max_message_bytes=max_message_bytes,
        )


def _integer_setting(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    return _parse_integer(raw_value, name, minimum, maximum)


def _parse_integer(
    raw_value: str,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise EmailSettingsError(f"{name} debe ser un entero.") from exc
    if not minimum <= value <= maximum:
        raise EmailSettingsError(
            f"{name} debe estar entre {minimum} y {maximum}."
        )
    return value


def _cache_path(configured_path: str) -> Path:
    if configured_path:
        return Path(configured_path).expanduser()
    if os.name == "nt":
        root = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "BlackBelt" / "email-cache.sqlite3"
    root = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "blackbelt" / "email-cache.sqlite3"
