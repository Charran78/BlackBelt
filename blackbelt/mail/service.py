"""Orchestration for fetching, classifying, and caching email triage results."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from blackbelt.mail import classifier as classifier_module
from blackbelt.mail.classifier import classify_message, preclassify_newsletter
from blackbelt.mail.imap_client import fetch_recent_messages
from blackbelt.mail.models import (
    ClassificationError,
    MailClassification,
    MailMessage,
)
from blackbelt.mail.repository import EmailCache, EmailCacheError
from blackbelt.mail.settings import EmailSettings


class MailFetcher(Protocol):
    def __call__(
        self,
        username: str,
        app_password: str,
        settings: EmailSettings,
    ) -> list[MailMessage]: ...


class MessageClassifier(Protocol):
    def __call__(
        self,
        message: MailMessage,
        settings: EmailSettings,
    ) -> MailClassification: ...


@dataclass(frozen=True, slots=True)
class TriageItem:
    message: MailMessage
    classification: MailClassification
    source: str
    review_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def requires_review(self) -> bool:
        return bool(self.review_reasons)


@dataclass(slots=True)
class TriageReport:
    items: list[TriageItem] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cache_hits: int = 0
    newsletter_shortcuts: int = 0


@dataclass(frozen=True, slots=True)
class EmailScanProgress:
    """One local progress update during a bounded email scan."""

    stage: Literal["fetch", "classify", "complete"]
    current: int
    total: int
    detail: str


class EmailTriageService:
    def __init__(
        self,
        settings: EmailSettings,
        cache: EmailCache,
        *,
        fetcher: MailFetcher = fetch_recent_messages,
        classifier: MessageClassifier = classify_message,
    ) -> None:
        self._settings = settings
        self._cache = cache
        self._fetcher = fetcher
        self._classifier = classifier

    def scan(
        self,
        username: str,
        app_password: str,
        *,
        refresh: bool = False,
        progress_callback: Callable[[EmailScanProgress], None] | None = None,
    ) -> TriageReport:
        _report_progress(
            progress_callback,
            EmailScanProgress(
                stage="fetch",
                current=0,
                total=0,
                detail="Conectando con Gmail y descargando mensajes recientes.",
            ),
        )
        messages = self._fetcher(username, app_password, self._settings)
        report = TriageReport()
        _report_progress(
            progress_callback,
            EmailScanProgress(
                stage="classify",
                current=0,
                total=len(messages),
                detail=f"Procesando {len(messages)} mensajes.",
            ),
        )
        for index, message in enumerate(messages, start=1):
            _report_progress(
                progress_callback,
                EmailScanProgress(
                    stage="classify",
                    current=index - 1,
                    total=len(messages),
                    detail=f"Clasificando mensaje {index}/{len(messages)}.",
                ),
            )
            cache_key = _cache_key(message, self._settings.model)
            cached = None if refresh else self._read_cache(cache_key, report)
            if cached is not None:
                report.cache_hits += 1
                review_reasons, warnings = _classification_signals(
                    cached,
                    self._settings.min_confidence,
                )
                report.items.append(
                    TriageItem(
                        message,
                        cached,
                        "cache",
                        review_reasons,
                        warnings,
                    )
                )
                _report_progress(
                    progress_callback,
                    EmailScanProgress(
                        stage="classify",
                        current=index,
                        total=len(messages),
                        detail=f"Mensaje {index}/{len(messages)} · caché local.",
                    ),
                )
                continue

            classification = preclassify_newsletter(message)
            source = "newsletter" if classification is not None else "ollama"
            if classification is None:
                try:
                    classification = self._classifier(message, self._settings)
                except ClassificationError as exc:
                    report.failures.append(str(exc))
                    _report_progress(
                        progress_callback,
                        EmailScanProgress(
                            stage="classify",
                            current=index,
                            total=len(messages),
                            detail=(
                                f"Mensaje {index}/{len(messages)} · "
                                "clasificación fallida."
                            ),
                        ),
                    )
                    continue
            elif source == "newsletter":
                report.newsletter_shortcuts += 1

            self._write_cache(cache_key, classification, report)
            review_reasons, warnings = _classification_signals(
                classification,
                self._settings.min_confidence,
            )
            report.items.append(
                TriageItem(
                    message,
                    classification,
                    source,
                    review_reasons,
                    warnings,
                )
            )
            _report_progress(
                progress_callback,
                EmailScanProgress(
                    stage="classify",
                    current=index,
                    total=len(messages),
                    detail=f"Mensaje {index}/{len(messages)} procesado.",
                ),
            )
        _report_progress(
            progress_callback,
            EmailScanProgress(
                stage="complete",
                current=len(messages),
                total=len(messages),
                detail="Triaje finalizado.",
            ),
        )
        return report

    def _read_cache(
        self,
        cache_key: str,
        report: TriageReport,
    ) -> MailClassification | None:
        try:
            return self._cache.get(cache_key)
        except EmailCacheError:
            _add_cache_warning(report)
            return None

    def _write_cache(
        self,
        cache_key: str,
        classification: MailClassification,
        report: TriageReport,
    ) -> None:
        try:
            self._cache.set(cache_key, classification)
        except EmailCacheError:
            _add_cache_warning(report)


def _cache_key(message: MailMessage, model: str) -> str:
    payload = json.dumps(
        {
            "model": model,
            "classifier_fingerprint": (classifier_module.classifier_fingerprint()),
            "message_id": message.message_id,
            "sender": message.sender,
            "subject": message.subject,
            "body": message.body,
            "signals": message.signals,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _report_progress(
    callback: Callable[[EmailScanProgress], None] | None,
    event: EmailScanProgress,
) -> None:
    if callback is not None:
        callback(event)


def _classification_signals(
    classification: MailClassification,
    minimum_confidence: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    review_reasons: list[str] = []
    warnings: list[str] = []
    confidence_is_low = classification.confidence < minimum_confidence
    if confidence_is_low:
        review_reasons.append(
            f"confianza {classification.confidence} inferior al umbral "
            f"{minimum_confidence}"
        )
    if classification.category in {"Spam", "Phishing"}:
        review_reasons.append(
            f"categoría {classification.category}: verificar antes de actuar"
        )
    if classification.security_risk in {"medio", "alto"}:
        review_reasons.append(f"riesgo de seguridad {classification.security_risk}")
    action_is_unusual_for_category = classification.category in {
        "Newsletter",
        "Promocion",
    } and (classification.requires_action or classification.requires_reply)
    if action_is_unusual_for_category:
        review_reasons.append(
            f"acción o respuesta inesperada para {classification.category}"
        )

    priority_conflicts = (
        classification.importance <= 1 and classification.urgency != "baja"
    ) or (classification.importance >= 4 and classification.urgency == "baja")
    if priority_conflicts:
        warnings.append(
            f"prioridad incoherente: importancia {classification.importance}, "
            f"urgencia {classification.urgency}"
        )
    return tuple(review_reasons), tuple(warnings)


def _add_cache_warning(report: TriageReport) -> None:
    warning = "La caché local no está disponible; el análisis continúa sin utilizarla."
    if warning not in report.warnings:
        report.warnings.append(warning)
