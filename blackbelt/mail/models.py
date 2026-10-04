"""Typed values shared by the email triage components."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


CATEGORIES = (
    "Newsletter",
    "Notificacion",
    "Transaccional",
    "Trabajo",
    "Personal",
    "Promocion",
    "Spam",
    "Phishing",
)
URGENCY_LEVELS = ("baja", "media", "alta")
SECURITY_LEVELS = ("ninguno", "bajo", "medio", "alto")


class ClassificationError(ValueError):
    """Raised when a model response cannot form a safe classification."""


@dataclass(frozen=True, slots=True)
class MailMessage:
    uid: str
    message_id: str
    sender: str
    subject: str
    body: str
    signals: dict[str, str | bool]


@dataclass(frozen=True, slots=True)
class MailClassification:
    summary: str
    category: str
    importance: int
    urgency: str
    requires_reply: bool
    requires_action: bool
    suggested_action: str
    security_risk: str
    confidence: int

    @classmethod
    def from_mapping(cls, payload: Any) -> MailClassification:
        if not isinstance(payload, dict):
            raise ClassificationError("La respuesta del clasificador no es un objeto.")

        summary = _bounded_text(payload, "resumen", 140)
        category = payload.get("categoria")
        urgency = payload.get("urgencia")
        security_risk = payload.get("riesgo_seguridad")
        action = _bounded_text(payload, "accion_sugerida", 100)

        if category not in CATEGORIES:
            raise ClassificationError("Categoría inválida en la respuesta.")
        if urgency not in URGENCY_LEVELS:
            raise ClassificationError("Urgencia inválida en la respuesta.")
        if security_risk not in SECURITY_LEVELS:
            raise ClassificationError("Riesgo de seguridad inválido en la respuesta.")

        importance = _bounded_integer(payload, "importancia", 1, 5)
        confidence = _bounded_integer(payload, "confianza", 0, 100)
        requires_reply = payload.get("requiere_respuesta")
        requires_action = payload.get("requiere_accion")
        if not isinstance(requires_reply, bool) or not isinstance(
            requires_action,
            bool,
        ):
            raise ClassificationError("Los indicadores de acción deben ser booleanos.")

        return cls(
            summary=summary,
            category=category,
            importance=importance,
            urgency=urgency,
            requires_reply=requires_reply,
            requires_action=requires_action,
            suggested_action=action,
            security_risk=security_risk,
            confidence=confidence,
        )

    @classmethod
    def from_json_mapping(cls, payload: dict[str, Any]) -> MailClassification:
        keys = {
            "resumen": "summary",
            "categoria": "category",
            "importancia": "importance",
            "urgencia": "urgency",
            "requiere_respuesta": "requires_reply",
            "requiere_accion": "requires_action",
            "accion_sugerida": "suggested_action",
            "riesgo_seguridad": "security_risk",
            "confianza": "confidence",
        }
        if any(name not in payload for name in keys.values()):
            raise ClassificationError(
                "La clasificación almacenada tiene un formato inválido."
            )
        return cls.from_mapping(
            {source: payload[target] for source, target in keys.items()}
        )

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


def _bounded_text(payload: dict[str, Any], key: str, max_length: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ClassificationError(f"El campo {key} está vacío o no es texto.")
    return value.strip()[:max_length]


def _bounded_integer(
    payload: dict[str, Any],
    key: str,
    minimum: int,
    maximum: int,
) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ClassificationError(f"El campo {key} no es un entero válido.")
    if not minimum <= value <= maximum:
        raise ClassificationError(f"El campo {key} está fuera de rango.")
    return value
