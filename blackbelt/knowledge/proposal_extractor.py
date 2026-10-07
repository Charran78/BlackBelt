"""Deterministic extraction of explicit project data from proposal Markdown."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_DATE = re.compile(r"\b(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})\b")
_BULLET = re.compile(r"^\s*[-*+]{1,2}\s+(.+?)\s*$")
_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")


@dataclass(frozen=True)
class ProposalExtraction:
    """Facts copied from a proposal without model interpretation."""

    project_name: str | None = None
    objective: str | None = None
    scope: dict[str, list[str]] = field(
        default_factory=lambda: {"incluye": [], "excluye": []}
    )
    scope_sources: dict[str, list[str]] = field(
        default_factory=lambda: {"incluye": [], "excluye": []}
    )
    acceptance_criteria: list[str] = field(default_factory=list)
    deliverables: list[dict[str, Any]] = field(default_factory=list)
    phases: list[dict[str, Any]] = field(default_factory=list)
    investment: dict[str, Any] | None = None
    assumptions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    mitigation: str | None = None
    questions: list[str] = field(default_factory=list)


def extract_proposal(
    content: str,
    metadata: dict[str, Any],
    relative_path: str,
    source_records: list[dict[str, Any]],
) -> ProposalExtraction:
    """Extract explicit proposal sections and attach their source IDs."""
    project_name = _string_value(metadata.get("proyecto"))
    sections = _markdown_sections(content)
    summary = sections.get(_normalize("Resumen"), "")
    objective = _summary_need(summary)

    include_section = _section_subsection(
        content,
        parent="Alcance",
        child="Incluye",
    )
    exclude_section = _section_subsection(
        content,
        parent="Alcance",
        child="No incluye",
    )
    scope = {
        "incluye": _bullet_values(include_section),
        "excluye": _bullet_values(exclude_section),
    }
    scope_sources = {
        "incluye": _source_ids(source_records, relative_path, ("incluye",)),
        "excluye": _source_ids(source_records, relative_path, ("no incluye",)),
    }

    deliverable_rows = _parse_table(sections.get(_normalize("Entregables"), ""))
    deliverable_sources = _source_ids(
        source_records,
        relative_path,
        ("entregables",),
    )
    deliverables: list[dict[str, Any]] = []
    acceptance_criteria: list[str] = []
    for row in deliverable_rows:
        title = _column(row, "entregable")
        criterion = _column(row, "criterio de aceptacion")
        if not title or not criterion:
            continue
        estimated_date = _first_date(_column(row, "fecha estimada"))
        deliverable: dict[str, Any] = {
            "titulo": title,
            "descripcion": title,
            "criterio_aceptacion": criterion,
            "estado": "pendiente",
            "fuentes": deliverable_sources,
        }
        if estimated_date:
            deliverable["fecha_estimada"] = estimated_date
            deliverable["origen_fecha"] = "externo"
        deliverables.append(deliverable)
        acceptance_criteria.append(f"{title}: {criterion}")

    phase_rows = _parse_table(sections.get(_normalize("Plan de trabajo"), ""))
    phase_sources = _source_ids(
        source_records,
        relative_path,
        ("plan de trabajo",),
    )
    phases: list[dict[str, Any]] = []
    for row in phase_rows:
        title = _column(row, "fase")
        activity = _column(row, "actividad")
        if not title or not activity:
            continue
        date_range = _dates_in(_column(row, "duracion o fecha"))
        phase: dict[str, Any] = {
            "fase": title,
            "actividad": activity,
            "fecha_inicio": date_range[0] if date_range else None,
            "fecha_fin": date_range[-1] if date_range else None,
            "origen_fecha": "externo" if date_range else None,
            "fuentes": phase_sources,
        }
        phases.append(phase)

    assumptions = _bullet_values(
        sections.get(_normalize("Supuestos y dependencias"), "")
    )
    risks, mitigation = _risks_and_mitigation(
        sections.get(_normalize("Riesgos y cambios de alcance"), "")
    )
    questions = _review_questions(
        sections.get(_normalize("Validez y condiciones"), "")
    )
    investment = _investment(
        sections.get(_normalize("Inversion"), ""),
        metadata,
        relative_path,
        source_records,
    )

    return ProposalExtraction(
        project_name=project_name,
        objective=objective,
        scope=scope,
        scope_sources=scope_sources,
        acceptance_criteria=acceptance_criteria,
        deliverables=deliverables,
        phases=phases,
        investment=investment,
        assumptions=assumptions,
        risks=risks,
        mitigation=mitigation,
        questions=questions,
    )


def _markdown_sections(content: str) -> dict[str, str]:
    lines = content.splitlines(keepends=True)
    headings: list[tuple[int, int, str]] = []
    offset = 0
    for index, line in enumerate(lines):
        match = _HEADING.match(line.rstrip("\r\n"))
        if match:
            headings.append((index, len(match.group(1)), match.group(2)))
        offset += len(line)

    sections: dict[str, str] = {}
    for heading_index, (line_index, level, title) in enumerate(headings):
        end_index = len(lines)
        for next_line_index, next_level, _ in headings[heading_index + 1:]:
            if next_level <= level:
                end_index = next_line_index
                break
        sections[_normalize(title)] = "".join(lines[line_index + 1:end_index])
    return sections


def _section_subsection(content: str, parent: str, child: str) -> str:
    lines = content.splitlines(keepends=True)
    parent_index: int | None = None
    parent_level: int | None = None
    child_index: int | None = None
    child_level: int | None = None
    for index, line in enumerate(lines):
        match = _HEADING.match(line.rstrip("\r\n"))
        if not match:
            continue
        title = _normalize(match.group(2))
        level = len(match.group(1))
        if title == _normalize(parent):
            parent_index = index
            parent_level = level
            child_index = None
            child_level = None
            continue
        if parent_index is None or parent_level is None:
            continue
        if level <= parent_level:
            break
        if title == _normalize(child):
            child_index = index
            child_level = level
            continue
        if child_index is not None and child_level is not None and level <= child_level:
            return "".join(lines[child_index + 1:index])
    if child_index is None:
        return ""
    end_index = len(lines)
    for index in range(child_index + 1, len(lines)):
        match = _HEADING.match(lines[index].rstrip("\r\n"))
        if match and child_level is not None and len(match.group(1)) <= child_level:
            end_index = index
            break
    return "".join(lines[child_index + 1:end_index])


def _parse_table(section: str) -> list[dict[str, str]]:
    lines = section.splitlines()
    for index in range(len(lines) - 1):
        headers = _split_table_row(lines[index])
        separator = _split_table_row(lines[index + 1])
        if not headers or len(headers) != len(separator):
            continue
        if not all(_TABLE_SEPARATOR.fullmatch(cell.replace(" ", "")) for cell in separator):
            continue
        result: list[dict[str, str]] = []
        for line in lines[index + 2:]:
            cells = _split_table_row(line)
            if not cells:
                if result:
                    break
                continue
            if len(cells) != len(headers):
                continue
            result.append(
                {
                    _normalize(header): _clean_markdown(value)
                    for header, value in zip(headers, cells, strict=True)
                }
            )
        return result
    return []


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if "|" not in stripped:
        return []
    stripped = stripped.removeprefix("|")
    stripped = stripped.removesuffix("|")
    return [cell.strip() for cell in stripped.split("|")]


def _bullet_values(section: str) -> list[str]:
    values: list[str] = []
    for line in section.splitlines():
        match = _BULLET.match(line)
        if match:
            value = _clean_markdown(match.group(1))
            if value and value not in {"-", "—"}:
                values.append(value)
    return _unique(values)


def _summary_need(summary: str) -> str | None:
    for line in summary.splitlines():
        cleaned = _clean_markdown(line.strip().lstrip("-* ").strip())
        match = re.match(
            r"Necesidad del cliente\s*:\s*(.+)",
            cleaned,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
    return None


def _risks_and_mitigation(section: str) -> tuple[list[str], str | None]:
    risks: list[str] = []
    mitigation: str | None = None
    for value in _bullet_values(section):
        match = re.match(r"Mitigaci[oó]n\s*:\s*(.+)", value, flags=re.IGNORECASE)
        if match:
            mitigation = match.group(1).strip()
        else:
            risks.append(value)
    return risks, mitigation


def _review_questions(section: str) -> list[str]:
    lines = section.splitlines()
    marker_index = next(
        (
            index
            for index, line in enumerate(lines)
            if re.search(
                r"condiciones que deben revisarse antes de enviar",
                line,
                flags=re.IGNORECASE,
            )
        ),
        None,
    )
    question_section = (
        "\n".join(lines[marker_index + 1:])
        if marker_index is not None
        else section
    )
    values = _bullet_values(question_section)
    return _unique(values)


def _investment(
    section: str,
    metadata: dict[str, Any],
    relative_path: str,
    source_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    rows = _parse_table(section)
    if not rows:
        return None
    source_ids = _source_ids(source_records, relative_path, ("inversion",))
    items: list[dict[str, Any]] = []
    for row in rows:
        concept = _column(row, "concepto")
        quantity = _number(_column(row, "cantidad"))
        unit_price = _number(_column(row, "precio unitario"))
        subtotal = _number(_column(row, "subtotal"))
        if not concept or quantity is None or unit_price is None or subtotal is None:
            continue
        items.append(
            {
                "concepto": concept,
                "cantidad": quantity,
                "precio_unitario": unit_price,
                "subtotal": subtotal,
                "fuentes": source_ids,
            }
        )
    summary: dict[str, Any] = {}
    for line in section.splitlines():
        match = re.match(r"^\s*[-*]\s*([^:]+):\s*(.+?)\s*$", line)
        if match:
            summary[_normalize(match.group(1))] = _clean_markdown(match.group(2))

    currency = _string_value(metadata.get("moneda"))
    if currency and "€" in currency:
        currency = "EUR"
    investment: dict[str, Any] = {
        "moneda": currency or "sin especificar",
        "partidas": items,
        "fuentes": source_ids,
    }
    for source_label, target_field in (
        ("base imponible", "base_imponible"),
        ("total", "total"),
    ):
        amount = _number(summary.get(_normalize(source_label)))
        if amount is not None:
            investment[target_field] = amount
    tax_text = summary.get(_normalize("Impuestos aplicables (verificar)"))
    if tax_text:
        investment["impuestos_texto"] = tax_text
        investment["impuestos_verificados"] = False
    payment = summary.get(_normalize("Forma y calendario de pago"))
    if payment:
        investment["forma_pago"] = payment
    return investment if items or len(investment) > 3 else None


def _source_ids(
    source_records: list[dict[str, Any]],
    relative_path: str,
    section_fragments: tuple[str, ...],
) -> list[str]:
    normalized_fragments = tuple(_normalize(value) for value in section_fragments)
    matches: list[str] = []
    for source in source_records:
        if source.get("nota") != relative_path:
            continue
        identifier = source.get("id")
        if not isinstance(identifier, str):
            continue
        segments = {
            _normalize(segment)
            for segment in str(source.get("seccion", "")).split(">")
        }
        if any(fragment in segments for fragment in normalized_fragments):
            matches.append(identifier)
    return matches


def _column(row: dict[str, str], label: str) -> str:
    normalized_label = _normalize(label)
    return next(
        (
            value.strip()
            for key, value in row.items()
            if normalized_label in key
        ),
        "",
    )


def _dates_in(value: str) -> list[str]:
    dates: list[str] = []
    for match in _DATE.findall(value):
        parsed = _parse_date(match)
        if parsed:
            dates.append(parsed)
    return dates


def _first_date(value: str) -> str | None:
    dates = _dates_in(value)
    return dates[0] if dates else None


def _parse_date(value: str) -> str | None:
    try:
        if "/" in value:
            day, month, year = (int(part) for part in value.split("/"))
            return date(year, month, day).isoformat()
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _number(value: str | None) -> int | float | None:
    if not value:
        return None
    normalized = re.sub(r"[^0-9,.-]", "", value)
    if not normalized:
        return None
    if "," in normalized and "." in normalized:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    elif "," in normalized:
        normalized = normalized.replace(",", ".")
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    if amount == amount.to_integral_value():
        return int(amount)
    return float(amount)


def _clean_markdown(value: str) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", value)
    value = re.sub(r"(?<!\*)\*{1,2}(?!\*)", "", value)
    return value.strip().strip(",")


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)
    ).strip()


def _string_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize(value)
        if normalized and normalized not in seen:
            result.append(value)
            seen.add(normalized)
    return result
