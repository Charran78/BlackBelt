"""Contract tests for deterministic proposal-to-plan extraction."""

from __future__ import annotations

from typing import Any

from blackbelt.knowledge.proposal_extractor import extract_proposal

_PROPOSAL = """---
proyecto: Aura & Sueños
moneda: €€
---
# Propuesta - Aura & Sueños

## Resumen
- Necesidad del cliente: Creación de sitio web con pasarela de pagos.
- Resultado propuesto: Sitio web de venta online.

## Alcance
### Incluye
- Desarrollo del proyecto
- Alojar en la nube, falta decidir proveedor.
-

### No incluye
- Creación de imágenes.
- Campañas de marketing.

## Entregables
| Entregable | Criterio de aceptacion | Fecha estimada |
| --- | --- | --- |
| MVP | Validación de necesidades | 05/11/2026 |
| Pasarela de pagos | Stripe/Bizum validado | 12/11/2026 |

## Plan de trabajo
| Fase | Actividad | Duracion o fecha |
| --- | --- | --- |
| Setup técnico | Hosting, dominio y repositorio | 20/10/2026 – 25/10/2026 |
| MVP | Creación y validación | 05/11/2026 |

## Inversion
| Concepto | Cantidad | Precio unitario | Subtotal |
| --- | ---: | ---: | ---: |
| Creación de MVP | 1 | 350 | 350 |
| Mensualidad | 12 | 40 | 480 |
- Base imponible: 830
- Impuestos aplicables (verificar): 21%
- Total: 1004.3
- Forma y calendario de pago: Pago inicial y mensualidad.

## Supuestos y dependencias
- El cliente proporciona textos e imágenes.
- Se decide hosting antes del 20/10/2026.

## Riesgos y cambios de alcance
- Retraso en la decisión del proveedor.
- Dependencia de APIs externas.
- Mitigación: validar por hitos.

## Validez y condiciones
- Valida hasta: 30/11/2026
- Condiciones que deben revisarse antes de enviar:
  - Confirmar si el IVA es finalmente el 21%.
  - Detallar qué incluye la mensualidad.
"""

_SOURCE_RECORDS: list[dict[str, Any]] = [
    {
        "id": "F-001",
        "nota": "013 - PROPUESTA _PRESUPUESTO/PR001 - Aura.md",
        "seccion": "Propuesta > Alcance > Incluye",
    },
    {
        "id": "F-002",
        "nota": "013 - PROPUESTA _PRESUPUESTO/PR001 - Aura.md",
        "seccion": "Propuesta > Alcance > No incluye",
    },
    {
        "id": "F-003",
        "nota": "013 - PROPUESTA _PRESUPUESTO/PR001 - Aura.md",
        "seccion": "Propuesta > Entregables",
    },
    {
        "id": "F-004",
        "nota": "013 - PROPUESTA _PRESUPUESTO/PR001 - Aura.md",
        "seccion": "Propuesta > Plan de trabajo",
    },
    {
        "id": "F-005",
        "nota": "013 - PROPUESTA _PRESUPUESTO/PR001 - Aura.md",
        "seccion": "Propuesta > Inversion",
    },
    {
        "id": "F-006",
        "nota": "013 - PROPUESTA _PRESUPUESTO/PR001 - Aura.md",
        "seccion": "Propuesta > Supuestos y dependencias",
    },
    {
        "id": "F-007",
        "nota": "013 - PROPUESTA _PRESUPUESTO/PR001 - Aura.md",
        "seccion": "Propuesta > Riesgos y cambios de alcance",
    },
    {
        "id": "F-008",
        "nota": "013 - PROPUESTA _PRESUPUESTO/PR001 - Aura.md",
        "seccion": "Propuesta > Validez y condiciones",
    },
]


def test_extracts_explicit_proposal_facts_and_attaches_source_ids() -> None:
    result = extract_proposal(
        _PROPOSAL,
        {"proyecto": "Aura & Sueños", "moneda": "€€"},
        "013 - PROPUESTA _PRESUPUESTO/PR001 - Aura.md",
        _SOURCE_RECORDS,
    )

    assert result.project_name == "Aura & Sueños"
    assert result.objective == "Creación de sitio web con pasarela de pagos."
    assert result.scope == {
        "incluye": [
            "Desarrollo del proyecto",
            "Alojar en la nube, falta decidir proveedor.",
        ],
        "excluye": ["Creación de imágenes.", "Campañas de marketing."],
    }
    assert result.scope_sources == {
        "incluye": ["F-001"],
        "excluye": ["F-002"],
    }
    assert [item["titulo"] for item in result.deliverables] == [
        "MVP",
        "Pasarela de pagos",
    ]
    assert result.deliverables[0]["fecha_estimada"] == "2026-11-05"
    assert result.deliverables[0]["origen_fecha"] == "externo"
    assert result.deliverables[0]["fuentes"] == ["F-003"]
    assert result.phases[0]["fecha_inicio"] == "2026-10-20"
    assert result.phases[0]["fecha_fin"] == "2026-10-25"
    assert result.phases[1]["fecha_inicio"] == "2026-11-05"
    assert result.investment == {
        "moneda": "EUR",
        "partidas": [
            {
                "concepto": "Creación de MVP",
                "cantidad": 1,
                "precio_unitario": 350,
                "subtotal": 350,
                "fuentes": ["F-005"],
            },
            {
                "concepto": "Mensualidad",
                "cantidad": 12,
                "precio_unitario": 40,
                "subtotal": 480,
                "fuentes": ["F-005"],
            },
        ],
        "fuentes": ["F-005"],
        "base_imponible": 830,
        "total": 1004.3,
        "impuestos_texto": "21%",
        "impuestos_verificados": False,
        "forma_pago": "Pago inicial y mensualidad.",
    }
    assert result.assumptions == [
        "El cliente proporciona textos e imágenes.",
        "Se decide hosting antes del 20/10/2026.",
    ]
    assert result.risks == [
        "Retraso en la decisión del proveedor.",
        "Dependencia de APIs externas.",
    ]
    assert result.mitigation == "validar por hitos."
    assert result.questions == [
        "Confirmar si el IVA es finalmente el 21%.",
        "Detallar qué incluye la mensualidad.",
    ]


def test_extraction_tolerates_missing_or_malformed_optional_sections() -> None:
    result = extract_proposal(
        "# Propuesta\n\n## Resumen\n- Necesidad del cliente: Servicio.\n",
        {},
        "propuesta.md",
        [],
    )

    assert result.objective == "Servicio."
    assert result.deliverables == []
    assert result.phases == []
    assert result.investment is None


def test_extraction_removes_unmatched_markdown_emphasis() -> None:
    result = extract_proposal(
        (
            "# Propuesta\n\n## Supuestos y dependencias\n"
            "- Nombre del proyecto: Aura & Sueños**.\n"
        ),
        {},
        "propuesta.md",
        [],
    )

    assert result.assumptions == ["Nombre del proyecto: Aura & Sueños."]
