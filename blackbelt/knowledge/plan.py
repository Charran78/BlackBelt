"""Plan schema, Markdown serialization, and deterministic validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import yaml

_FRONTMATTER_PATTERN = re.compile(
    r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)
_ID_PATTERNS = {
    "plan": re.compile(r"PLAN-\d{3,}\Z"),
    "template": re.compile(r"PLT-\d{3,}\Z"),
    "deliverable": re.compile(r"E-\d{3,}\Z"),
    "task": re.compile(r"T-\d{3,}\Z"),
    "dependency": re.compile(r"D-\d{3,}\Z"),
    "risk": re.compile(r"R-\d{3,}\Z"),
    "constraint": re.compile(r"C-\d{3,}\Z"),
    "assumption": re.compile(r"S-\d{3,}\Z"),
    "question": re.compile(r"Q-\d{3,}\Z"),
    "source": re.compile(r"F-\d{3,}\Z"),
}
_ENTITY_FIELDS = {
    "entregables": ("E", "deliverable"),
    "tareas": ("T", "task"),
    "dependencias": ("D", "dependency"),
    "riesgos": ("R", "risk"),
    "restricciones": ("C", "constraint"),
    "supuestos": ("S", "assumption"),
    "preguntas": ("Q", "question"),
    "fuentes": ("F", "source"),
}
_ENUMS = {
    ("plan", "estado"): {"borrador", "revisado", "aprobado", "archivado"},
    ("entregable", "estado"): {
        "pendiente", "en_curso", "entregado", "aceptado",
    },
    ("tarea", "estado"): {"pendiente", "en_curso", "bloqueada", "hecha"},
    ("tarea", "responsable"): {"equipo", "cliente", "tercero"},
    ("tarea", "origen"): {"inferido_ia", "humano", "plantilla", "mixto"},
    ("dependencia", "tipo"): {
        "fin_a_inicio", "inicio_a_inicio", "fin_a_fin", "inicio_a_fin",
    },
    ("dependencia", "rigidez"): {"dura", "blanda"},
    ("riesgo", "probabilidad"): {"baja", "media", "alta", "sin_evaluar"},
    ("riesgo", "impacto"): {"bajo", "medio", "alto", "sin_evaluar"},
    ("riesgo", "estado"): {"abierto", "mitigado", "materializado", "cerrado"},
    ("riesgo", "origen"): {"inferido_ia", "humano", "externo"},
    ("restriccion", "tipo"): {
        "tiempo", "personas", "dinero", "acceso", "dependencia_externa", "otra",
    },
    ("restriccion", "categoria"): {"candidata", "confirmada"},
    ("restriccion", "origen"): {"inferido_ia", "humano", "externo"},
    ("supuesto", "origen"): {"inferido_ia", "humano", "externo"},
    ("pregunta", "estado"): {"abierta", "respondida", "descartada"},
    ("fuente", "tipo"): {"documental", "instruccion", "nota_completa", "externa"},
}
_REQUIRED_PLAN_FIELDS = (
    "id",
    "cliente",
    "estado",
    "objetivo",
    "alcance",
    "criterios_aceptacion",
    "creado",
    "actualizado",
    "plantillas_aplicadas",
    "generacion",
    *tuple(_ENTITY_FIELDS),
)
_REQUIRED_ENTITY_FIELDS = {
    "entregables": (
        "id", "titulo", "descripcion", "criterio_aceptacion", "estado",
    ),
    "tareas": (
        "id", "titulo", "descripcion", "entregable", "fase", "estado",
        "estimacion", "estimado_real_h", "responsable",
        "criterio_aceptacion", "origen", "fuentes",
    ),
    "dependencias": ("id", "desde", "hacia", "tipo", "rigidez", "motivo"),
    "riesgos": (
        "id", "descripcion", "probabilidad", "impacto", "senal_alerta",
        "mitigacion", "plan_respuesta", "propietario", "estado", "fuentes",
        "origen",
    ),
    "restricciones": (
        "id", "tipo", "descripcion", "categoria", "no_negociable", "fuentes",
        "motivo", "origen",
    ),
    "supuestos": (
        "id", "descripcion", "impacto_si_falla", "verificado", "fuentes",
        "origen",
    ),
    "preguntas": ("id", "texto", "bloquea", "responsable", "estado"),
    "fuentes": ("id", "tipo", "capturado"),
}


class PlanFormatError(ValueError):
    """Raised when a plan note cannot be parsed safely."""


@dataclass(frozen=True)
class ValidationIssue:
    """One deterministic validation result."""

    level: str
    path: str
    message: str


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys so the validated value is unambiguous."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise PlanFormatError(f"Clave YAML duplicada: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def canonical_plan_json(plan: dict[str, Any]) -> str:
    """Serialize a plan deterministically for integrity and idempotency checks."""
    return json.dumps(
        plan,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def plan_hash(plan: dict[str, Any]) -> str:
    """Return the SHA-256 digest of the canonical validated plan JSON."""
    return hashlib.sha256(canonical_plan_json(plan).encode("utf-8")).hexdigest()


def render_plan_markdown(
    plan: dict[str, Any],
    narrative: str = "",
) -> str:
    """Render canonical plan data plus a readable Markdown projection."""
    frontmatter = yaml.safe_dump(
        {"plan": plan},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    body = render_plan_summary(plan, narrative)
    return f"---\n{frontmatter}---\n\n{body}\n"


def render_plan_summary(
    plan: dict[str, Any],
    narrative: str = "",
) -> str:
    """Render human-readable sections derived from the canonical plan object."""
    lines = [
        f"# Plan {plan.get('id', 'sin ID')}",
        "",
        (
            f"> Cliente: {plan.get('cliente', 'sin cliente')} | "
            f"Proyecto: {_project_label(plan)} | "
            f"Estado: {plan.get('estado', 'desconocido')}"
        ),
        "",
        "## Objetivo",
        "",
        str(plan.get("objetivo") or "Pendiente de definir."),
        "",
        "## Alcance",
        "",
    ]
    scope = plan.get("alcance")
    scope = scope if isinstance(scope, dict) else {}
    for field, title in (("incluye", "Incluye"), ("excluye", "No incluye")):
        lines.extend((f"### {title}", ""))
        values = scope.get(field)
        lines.extend(_markdown_list(values))
        lines.append("")

    sections = (
        ("criterios_aceptacion", "Criterios de aceptación", "texto"),
        ("entregables", "Entregables", "titulo"),
        ("tareas", "Tareas", "titulo"),
        ("riesgos", "Riesgos", "descripcion"),
        ("restricciones", "Restricciones candidatas", "descripcion"),
        ("supuestos", "Supuestos", "descripcion"),
        ("preguntas", "Preguntas abiertas", "texto"),
    )
    for field, title, text_field in sections:
        lines.extend((f"## {title}", ""))
        values = plan.get(field)
        if field == "tareas" and isinstance(values, list):
            lines.extend(_render_tasks(values))
        elif isinstance(values, list):
            lines.extend(
                _markdown_list(
                    [
                        _human_entity(item, text_field)
                        for item in values
                    ]
                )
            )
        else:
            lines.append("- Sin datos.")
        lines.append("")

    phases = plan.get("fases_propuestas")
    if isinstance(phases, list) and phases:
        lines.extend(("## Fases propuestas", ""))
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            start = phase.get("fecha_inicio")
            end = phase.get("fecha_fin")
            date_label = (
                f" ({start}" + (f" a {end}" if end and end != start else "") + ")"
                if start
                else ""
            )
            lines.append(
                f"- **{phase.get('fase', 'Fase')}**{date_label}: "
                f"{phase.get('actividad', 'Sin actividad descrita.')}"
            )
        lines.append("")

    investment = plan.get("inversion")
    if isinstance(investment, dict):
        lines.extend(("## Inversión propuesta", ""))
        lines.append(f"Moneda: {investment.get('moneda', 'sin especificar')}")
        lines.append("")
        items = investment.get("partidas")
        if isinstance(items, list) and items:
            lines.append("| Concepto | Cantidad | Precio unitario | Subtotal |")
            lines.append("| --- | ---: | ---: | ---: |")
            for item in items:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"| {item.get('concepto', '')} | {item.get('cantidad', '')} | "
                    f"{item.get('precio_unitario', '')} | "
                    f"{item.get('subtotal', '')} |"
                )
            lines.append("")
        for field, label in (
            ("base_imponible", "Base imponible"),
            ("impuestos_texto", "Impuestos"),
            ("total", "Total"),
            ("forma_pago", "Forma de pago"),
        ):
            if field in investment:
                suffix = (
                    " (pendiente de verificar)"
                    if field == "impuestos_texto"
                    and not investment.get("impuestos_verificados", False)
                    else ""
                )
                lines.append(f"- **{label}{suffix}:** {investment[field]}")
        lines.append("")

    if not plan_has_actionable_content(plan):
        lines.extend(
            (
                (
                    "> **Borrador incompleto:** no contiene objetivo definido, "
                    "preguntas ni elementos de trabajo. No debe tratarse como "
                    "un plan operativo."
                ),
                "",
            )
        )

    source_count = len(plan.get("fuentes", [])) if isinstance(
        plan.get("fuentes"), list
    ) else 0
    if source_count:
        lines.extend(
            (
                "## Trazabilidad",
                "",
                (
                    f"Este borrador se apoya en {source_count} fragmentos "
                    "documentales. Rutas, secciones, offsets y hashes se "
                    "conservan en las propiedades estructuradas del plan."
                ),
                "",
            )
        )
    if narrative.strip():
        lines.extend(("## Notas de generación", "", narrative.strip(), ""))
    return "\n".join(lines).strip()


def plan_has_actionable_content(plan: dict[str, Any]) -> bool:
    """Return whether a plan contains a defined objective or useful content."""
    objective = plan.get("objetivo")
    has_objective = (
        isinstance(objective, str)
        and objective.strip()
        and objective.strip().casefold() != "pendiente de definir"
    )
    if not has_objective:
        return False
    scope = plan.get("alcance")
    if isinstance(scope, dict) and any(
        isinstance(scope.get(key), list) and scope[key]
        for key in ("incluye", "excluye")
    ):
        return True
    if isinstance(plan.get("criterios_aceptacion"), list) and plan[
        "criterios_aceptacion"
    ]:
        return True
    return any(
        isinstance(plan.get(field), list) and plan[field]
        for field in (
            "entregables",
            "tareas",
            "dependencias",
            "riesgos",
            "restricciones",
            "supuestos",
            "preguntas",
        )
    )


def _markdown_list(values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        return ["- Sin datos."]
    return [
        f"- {value.strip() if isinstance(value, str) else str(value)}"
        for value in values
    ]


def _human_entity(item: Any, text_field: str) -> str:
    if not isinstance(item, dict):
        return str(item)
    title = item.get(text_field) or item.get("id", "Elemento")
    details: list[str] = []
    for field in ("estado", "probabilidad", "impacto", "responsable"):
        value = item.get(field)
        if value:
            details.append(f"{field}: {value}")
    if item.get("fecha_estimada"):
        origin = item.get("origen_fecha", "no especificado")
        details.append(f"fecha propuesta: {item['fecha_estimada']} ({origin})")
    if item.get("criterio_aceptacion") and text_field != "criterio_aceptacion":
        details.append(f"aceptación: {item['criterio_aceptacion']}")
    if text_field != "descripcion" and item.get("descripcion"):
        details.append(str(item["descripcion"]))
    if item.get("estimacion") and isinstance(item["estimacion"], dict):
        estimate = item["estimacion"]
        values = (
            estimate.get("min_h"),
            estimate.get("prob_h"),
            estimate.get("max_h"),
        )
        if all(value is not None for value in values):
            details.append(
                f"estimación: {values[0]} / {values[1]} / {values[2]} h"
            )
    suffix = f" ({'; '.join(details)})" if details else ""
    return f"**{title}**{suffix}"


def _project_label(plan: dict[str, Any]) -> str:
    project_id = plan.get("proyecto")
    project_name = plan.get("proyecto_nombre")
    if project_id and project_name:
        return f"{project_name} ({project_id})"
    if project_name:
        return f"{project_name} (sin ID PROY)"
    return str(project_id or "sin proyecto")


def _render_tasks(tasks: list[Any]) -> list[str]:
    if not tasks:
        return ["- Sin tareas."]
    return [
        f"- {_human_entity(task, 'titulo')}"
        for task in tasks
    ]


def parse_plan_markdown(content: str) -> tuple[dict[str, Any], str]:
    """Parse one plan note and reject malformed or ambiguous frontmatter."""
    match = _FRONTMATTER_PATTERN.match(content)
    if match is None:
        raise PlanFormatError("La nota no tiene frontmatter YAML válido.")
    try:
        metadata = yaml.load(match.group(1), Loader=_UniqueKeyLoader)
    except (yaml.YAMLError, PlanFormatError) as exc:
        raise PlanFormatError(f"Frontmatter YAML no válido: {exc}") from exc
    if not isinstance(metadata, dict) or set(metadata) != {"plan"}:
        raise PlanFormatError(
            "El frontmatter debe contener únicamente el objeto raíz 'plan'."
        )
    plan = metadata["plan"]
    if not isinstance(plan, dict):
        raise PlanFormatError("El objeto raíz 'plan' debe ser un mapa YAML.")
    return plan, content[match.end():]


def validate_plan(plan: dict[str, Any]) -> list[ValidationIssue]:
    """Validate schema, references, provenance, estimates, and dependency cycles."""
    issues: list[ValidationIssue] = []
    if not isinstance(plan, dict):
        return [ValidationIssue("error", "plan", "Debe ser un objeto YAML.")]

    _require_fields(plan, _REQUIRED_PLAN_FIELDS, "plan", issues)
    _validate_identifier(plan.get("id"), "plan", "plan.id", issues)
    _validate_enum(plan, "estado", "plan", "plan", issues)
    _validate_nonempty_text(plan.get("cliente"), "plan.cliente", issues)
    project_id = plan.get("proyecto")
    if project_id is not None and (
        not isinstance(project_id, str)
        or re.fullmatch(r"PROY\d{3,}", project_id) is None
    ):
        issues.append(
            ValidationIssue(
                "error",
                "plan.proyecto",
                "Debe ser null o un ID con prefijo PROY.",
            )
        )
    _validate_nonempty_text(plan.get("objetivo"), "plan.objetivo", issues)

    scope = plan.get("alcance")
    if not isinstance(scope, dict):
        issues.append(ValidationIssue("error", "plan.alcance", "Debe ser un mapa."))
    else:
        for key in ("incluye", "excluye"):
            if not isinstance(scope.get(key), list):
                issues.append(
                    ValidationIssue(
                        "error",
                        f"plan.alcance.{key}",
                        "Debe existir y ser una lista.",
                    )
                )
    _validate_string_list(plan.get("criterios_aceptacion"), "plan.criterios_aceptacion", issues)
    for date_field in ("creado", "actualizado"):
        if not _is_iso_date(plan.get(date_field)):
            issues.append(
                ValidationIssue(
                    "error", f"plan.{date_field}", "Debe usar formato YYYY-MM-DD."
                )
            )
    _validate_templates(plan.get("plantillas_aplicadas"), issues)
    _validate_generation(plan.get("generacion"), issues)
    _validate_proposal_fields(plan, issues)
    if not plan_has_actionable_content(plan):
        issues.append(
            ValidationIssue(
                "error",
                "plan",
                "El plan no tiene objetivo definido ni contenido accionable.",
            )
        )

    entities: dict[str, dict[str, dict[str, Any]]] = {}
    for field, (prefix, entity_name) in _ENTITY_FIELDS.items():
        items = plan.get(field)
        if not isinstance(items, list):
            issues.append(
                ValidationIssue("error", f"plan.{field}", "Debe ser una lista.")
            )
            entities[field] = {}
            continue
        indexed: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(items):
            path = f"plan.{field}[{index}]"
            if not isinstance(item, dict):
                issues.append(
                    ValidationIssue("error", path, "Debe ser un objeto YAML.")
                )
                continue
            _require_fields(item, _REQUIRED_ENTITY_FIELDS[field], path, issues)
            identifier = item.get("id")
            _validate_identifier(
                identifier,
                entity_name,
                f"{path}.id",
                issues,
            )
            if isinstance(identifier, str):
                if not identifier.startswith(f"{prefix}-"):
                    issues.append(
                        ValidationIssue(
                            "error",
                            f"{path}.id",
                            f"Debe usar el prefijo {prefix}-.",
                        )
                    )
                elif identifier in indexed:
                    issues.append(
                        ValidationIssue(
                            "error", f"{path}.id", "El ID está duplicado."
                        )
                    )
                else:
                    indexed[identifier] = item
            _validate_entity(field, item, path, issues)
        entities[field] = indexed

    _validate_references(entities, issues)
    _validate_proposal_references(
        plan,
        entities.get("fuentes", {}),
        issues,
    )
    _validate_dependency_cycles(entities.get("dependencias", {}), issues)
    return issues


def _require_fields(
    value: dict[str, Any],
    required: tuple[str, ...],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    for field in required:
        if field not in value:
            issues.append(
                ValidationIssue("error", f"{path}.{field}", "Campo obligatorio.")
            )


def _validate_identifier(
    value: Any,
    kind: str,
    path: str,
    issues: list[ValidationIssue],
) -> None:
    pattern = _ID_PATTERNS[kind]
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        issues.append(
            ValidationIssue(
                "error",
                path,
                f"Debe cumplir el patrón {pattern.pattern}.",
            )
        )


def _validate_enum(
    item: dict[str, Any],
    field: str,
    entity: str,
    path: str,
    issues: list[ValidationIssue],
) -> None:
    value = item.get(field)
    choices = _ENUMS.get((entity, field))
    if choices is not None and (
        not isinstance(value, str) or value not in choices
    ):
        issues.append(
            ValidationIssue(
                "error",
                f"{path}.{field}",
                f"Valor no permitido: {value!r}.",
            )
        )


def _validate_entity(
    collection: str,
    item: dict[str, Any],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    entity = _ENTITY_FIELDS[collection][1]
    for field in _ENUMS:
        enum_entity, enum_field = field
        if enum_entity == entity:
            _validate_enum(item, enum_field, entity, path, issues)

    text_fields = {
        "entregables": ("titulo", "descripcion", "criterio_aceptacion"),
        "tareas": ("titulo", "descripcion", "criterio_aceptacion"),
        "dependencias": ("desde", "hacia", "motivo"),
        "riesgos": (
            "descripcion", "senal_alerta", "mitigacion", "plan_respuesta",
            "propietario",
        ),
        "restricciones": ("descripcion", "motivo"),
        "supuestos": ("descripcion", "impacto_si_falla"),
        "preguntas": ("texto", "responsable"),
    }.get(collection, ())
    for field in text_fields:
        if field in item:
            _validate_nonempty_text(item[field], f"{path}.{field}", issues)

    if collection == "tareas":
        estimate = item.get("estimacion")
        if estimate is None:
            pass
        elif not isinstance(estimate, dict):
            issues.append(
                ValidationIssue(
                    "error", f"{path}.estimacion", "Debe ser un mapa."
                )
            )
        else:
            values = [estimate.get(key) for key in ("min_h", "prob_h", "max_h")]
            if not all(_is_finite_number(value) for value in values):
                issues.append(
                    ValidationIssue(
                        "error",
                        f"{path}.estimacion",
                        "min_h, prob_h y max_h deben ser números finitos.",
                    )
                )
            elif not 0 < values[0] <= values[1] <= values[2]:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"{path}.estimacion",
                        "Debe cumplirse 0 < min_h <= prob_h <= max_h.",
                    )
                )
        actual = item.get("estimado_real_h")
        if actual is not None and not _is_finite_number(actual):
            issues.append(
                ValidationIssue(
                    "error",
                    f"{path}.estimado_real_h",
                    "Debe ser un número finito o null.",
                )
            )
        if item.get("estado") == "hecha" and actual is None:
            issues.append(
                ValidationIssue(
                    "error",
                    f"{path}.estimado_real_h",
                    "Una tarea hecha requiere el valor real en horas.",
                )
            )

    if collection == "entregables" and item.get("fecha_estimada") is not None:
        if not _is_iso_date(item["fecha_estimada"]):
            issues.append(
                ValidationIssue(
                    "error",
                    f"{path}.fecha_estimada",
                    "Debe usar formato YYYY-MM-DD o null.",
                )
            )
        if not isinstance(item.get("origen_fecha"), str) or item.get(
            "origen_fecha"
        ) not in {"humano", "cliente", "externo"}:
            issues.append(
                ValidationIssue(
                    "error",
                    f"{path}.origen_fecha",
                    "Debe identificar el origen humano o externo de la fecha.",
                )
            )
    elif collection == "entregables" and item.get("origen_fecha") is not None:
        issues.append(
            ValidationIssue(
                "error",
                f"{path}.origen_fecha",
                "No se admite origen_fecha si no hay fecha_estimada.",
            )
        )

    if collection in {
        "entregables",
        "tareas",
        "riesgos",
        "restricciones",
        "supuestos",
        "preguntas",
    } and "fuentes" in item:
        _validate_string_list(item.get("fuentes"), f"{path}.fuentes", issues)
        if item.get("origen") == "inferido_ia" and not item.get("fuentes"):
            issues.append(
                ValidationIssue(
                    "error",
                    f"{path}.fuentes",
                    "El contenido inferido por IA requiere al menos una fuente.",
                )
            )
    if collection == "restricciones":
        if not isinstance(item.get("no_negociable"), bool):
            issues.append(
                ValidationIssue(
                    "error",
                    f"{path}.no_negociable",
                    "Debe ser booleano.",
                )
            )
        if (
            item.get("categoria") == "candidata"
            and (not item.get("fuentes") or not item.get("motivo"))
        ):
            issues.append(
                ValidationIssue(
                    "error",
                    path,
                    "Una restricción candidata requiere fuente y motivo.",
                )
            )
    if collection == "supuestos" and not isinstance(item.get("verificado"), bool):
        issues.append(
            ValidationIssue("error", f"{path}.verificado", "Debe ser booleano.")
        )
    if collection == "preguntas":
        _validate_string_list(item.get("bloquea"), f"{path}.bloquea", issues)
    if collection == "fuentes":
        _validate_source(item, path, issues)


def _validate_proposal_fields(
    plan: dict[str, Any],
    issues: list[ValidationIssue],
) -> None:
    project_name = plan.get("proyecto_nombre")
    if project_name is not None:
        _validate_nonempty_text(
            project_name,
            "plan.proyecto_nombre",
            issues,
        )

    scope_sources = plan.get("alcance_fuentes")
    if scope_sources is not None:
        if not isinstance(scope_sources, dict):
            issues.append(
                ValidationIssue(
                    "error",
                    "plan.alcance_fuentes",
                    "Debe ser un mapa de listas de IDs de fuentes.",
                )
            )
        else:
            for key in ("incluye", "excluye"):
                _validate_string_list(
                    scope_sources.get(key, []),
                    f"plan.alcance_fuentes.{key}",
                    issues,
                )

    phases = plan.get("fases_propuestas", [])
    if not isinstance(phases, list):
        issues.append(
            ValidationIssue(
                "error",
                "plan.fases_propuestas",
                "Debe ser una lista.",
            )
        )
    else:
        for index, phase in enumerate(phases):
            path = f"plan.fases_propuestas[{index}]"
            if not isinstance(phase, dict):
                issues.append(
                    ValidationIssue("error", path, "Debe ser un objeto.")
                )
                continue
            for field in ("fase", "actividad"):
                _validate_nonempty_text(
                    phase.get(field),
                    f"{path}.{field}",
                    issues,
                )
            for field in ("fecha_inicio", "fecha_fin"):
                value = phase.get(field)
                if value is not None and not _is_iso_date(value):
                    issues.append(
                        ValidationIssue(
                            "error",
                            f"{path}.{field}",
                            "Debe ser una fecha ISO o null.",
                        )
                    )
            if (
                phase.get("fecha_inicio")
                and phase.get("fecha_fin")
                and phase["fecha_inicio"] > phase["fecha_fin"]
            ):
                issues.append(
                    ValidationIssue(
                        "error",
                        path,
                        "La fecha de inicio no puede ser posterior al fin.",
                    )
                )
            date_origin = phase.get("origen_fecha")
            if date_origin is not None and (
                not isinstance(date_origin, str)
                or date_origin not in {"humano", "cliente", "externo"}
            ):
                issues.append(
                    ValidationIssue(
                        "error",
                        f"{path}.origen_fecha",
                        "Debe ser humano, cliente, externo o null.",
                    )
                )
            _validate_string_list(phase.get("fuentes"), f"{path}.fuentes", issues)

    investment = plan.get("inversion")
    if investment is None:
        return
    if not isinstance(investment, dict):
        issues.append(
            ValidationIssue("error", "plan.inversion", "Debe ser un objeto.")
        )
        return
    _validate_nonempty_text(
        investment.get("moneda"),
        "plan.inversion.moneda",
        issues,
    )
    _validate_string_list(
        investment.get("fuentes"),
        "plan.inversion.fuentes",
        issues,
    )
    for field in ("base_imponible", "total"):
        if field in investment and not _is_finite_number(investment[field]):
            issues.append(
                ValidationIssue(
                    "error",
                    f"plan.inversion.{field}",
                    "Debe ser un número finito.",
                )
            )
    if "impuestos_texto" in investment:
        _validate_nonempty_text(
            investment["impuestos_texto"],
            "plan.inversion.impuestos_texto",
            issues,
        )
    if "impuestos_verificados" in investment and not isinstance(
        investment["impuestos_verificados"],
        bool,
    ):
        issues.append(
            ValidationIssue(
                "error",
                "plan.inversion.impuestos_verificados",
                "Debe ser booleano.",
            )
        )
    if "forma_pago" in investment:
        _validate_nonempty_text(
            investment["forma_pago"],
            "plan.inversion.forma_pago",
            issues,
        )
    items = investment.get("partidas")
    if not isinstance(items, list):
        issues.append(
            ValidationIssue(
                "error",
                "plan.inversion.partidas",
                "Debe ser una lista.",
            )
        )
        return
    for index, item in enumerate(items):
        path = f"plan.inversion.partidas[{index}]"
        if not isinstance(item, dict):
            issues.append(
                ValidationIssue("error", path, "Debe ser un objeto.")
            )
            continue
        _validate_nonempty_text(item.get("concepto"), f"{path}.concepto", issues)
        for field in ("cantidad", "precio_unitario", "subtotal"):
            value = item.get(field)
            if not _is_finite_number(value) or value < 0:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"{path}.{field}",
                        "Debe ser un número finito no negativo.",
                    )
                )
        _validate_string_list(item.get("fuentes"), f"{path}.fuentes", issues)


def _validate_proposal_references(
    plan: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    issues: list[ValidationIssue],
) -> None:
    def validate_ids(values: Any, path: str) -> None:
        if not isinstance(values, list):
            return
        for identifier in values:
            if isinstance(identifier, str) and identifier not in sources:
                issues.append(
                    ValidationIssue(
                        "error",
                        path,
                        f"La fuente {identifier!r} no existe.",
                    )
                )

    scope_sources = plan.get("alcance_fuentes")
    if isinstance(scope_sources, dict):
        for key in ("incluye", "excluye"):
            validate_ids(
                scope_sources.get(key),
                f"plan.alcance_fuentes.{key}",
            )
    phases = plan.get("fases_propuestas", [])
    for index, phase in enumerate(phases if isinstance(phases, list) else []):
        if isinstance(phase, dict):
            validate_ids(
                phase.get("fuentes"),
                f"plan.fases_propuestas[{index}].fuentes",
            )
    investment = plan.get("inversion")
    if isinstance(investment, dict):
        validate_ids(investment.get("fuentes"), "plan.inversion.fuentes")
        items = investment.get("partidas", [])
        for index, item in enumerate(items if isinstance(items, list) else []):
            if isinstance(item, dict):
                validate_ids(
                    item.get("fuentes"),
                    f"plan.inversion.partidas[{index}].fuentes",
                )


def _validate_source(
    source: dict[str, Any],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    _validate_nonempty_text(source.get("capturado"), f"{path}.capturado", issues)
    source_type = source.get("tipo")
    if source_type == "documental":
        for field in ("nota", "seccion"):
            _validate_nonempty_text(
                source.get(field), f"{path}.{field}", issues
            )
        start = source.get("char_start")
        end = source.get("char_end")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or start >= end
        ):
            issues.append(
                ValidationIssue(
                    "error",
                    path,
                    "Una fuente documental requiere 0 <= char_start < char_end.",
                )
            )
        if not _is_sha256(source.get("hash_nota")):
            issues.append(
                ValidationIssue(
                    "error", f"{path}.hash_nota", "Debe ser SHA-256 hexadecimal."
                )
            )
    elif isinstance(source_type, str) and source_type in {
        "instruccion",
        "nota_completa",
        "externa",
    }:
        _validate_nonempty_text(
            source.get("descripcion"), f"{path}.descripcion", issues
        )
        if source.get("nota") is not None and not isinstance(source.get("nota"), str):
            issues.append(
                ValidationIssue(
                    "error", f"{path}.nota", "Debe ser una ruta o null."
                )
            )
        if source.get("char_start") is not None or source.get("char_end") is not None:
            issues.append(
                ValidationIssue(
                    "error",
                    path,
                    "Las fuentes sin pasaje no deben declarar offsets.",
                )
            )
def _validate_references(
    entities: dict[str, dict[str, dict[str, Any]]],
    issues: list[ValidationIssue],
) -> None:
    deliverables = entities.get("entregables", {})
    tasks = entities.get("tareas", {})
    sources = entities.get("fuentes", {})
    questions = entities.get("preguntas", {})

    for task_id, task in tasks.items():
        deliverable_id = task.get("entregable")
        if not isinstance(deliverable_id, str) or deliverable_id not in deliverables:
            issues.append(
                ValidationIssue(
                    "error",
                    f"plan.tareas.{task_id}.entregable",
                    "El entregable referenciado no existe.",
                )
            )
        if (
            task.get("origen") in {"inferido_ia", "mixto", "plantilla"}
            and not task.get("fuentes")
        ):
            issues.append(
                ValidationIssue(
                    "error",
                    f"plan.tareas.{task_id}.fuentes",
                    "La tarea generada requiere al menos una fuente.",
                )
            )

    for collection in (
        "entregables",
        "tareas",
        "riesgos",
        "restricciones",
        "supuestos",
        "preguntas",
    ):
        for entity_id, entity in entities.get(collection, {}).items():
            source_ids = entity.get("fuentes")
            if not isinstance(source_ids, list):
                continue
            for source_id in source_ids:
                if not isinstance(source_id, str):
                    continue
                source = sources.get(source_id)
                if source is None:
                    issues.append(
                        ValidationIssue(
                            "error",
                            f"plan.{collection}.{entity_id}.fuentes",
                            f"La fuente {source_id!r} no existe.",
                        )
                    )
                elif (
                    entity.get("origen") == "inferido_ia"
                    and source.get("tipo") != "documental"
                ):
                    issues.append(
                        ValidationIssue(
                            "error",
                            f"plan.{collection}.{entity_id}.fuentes",
                            "Una inferencia de IA debe citar una fuente documental.",
                        )
                    )

    for dependency_id, dependency in entities.get("dependencias", {}).items():
        for field in ("desde", "hacia"):
            task_id = dependency.get(field)
            if not isinstance(task_id, str) or task_id not in tasks:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"plan.dependencias.{dependency_id}.{field}",
                        "La tarea referenciada no existe.",
                    )
                )
    for question_id, question in questions.items():
        blocked_tasks = question.get("bloquea")
        if not isinstance(blocked_tasks, list):
            continue
        for blocked_id in blocked_tasks:
            if not isinstance(blocked_id, str) or blocked_id not in tasks:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"plan.preguntas.{question_id}.bloquea",
                        f"La tarea {blocked_id!r} no existe.",
                    )
                )


def _validate_dependency_cycles(
    dependencies: dict[str, dict[str, Any]],
    issues: list[ValidationIssue],
) -> None:
    adjacency: dict[str, set[str]] = {}
    for dependency in dependencies.values():
        source = dependency.get("desde")
        target = dependency.get("hacia")
        if isinstance(source, str) and isinstance(target, str):
            adjacency.setdefault(source, set()).add(target)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> bool:
        if task_id in visiting:
            return True
        if task_id in visited:
            return False
        visiting.add(task_id)
        for target in adjacency.get(task_id, set()):
            if visit(target):
                return True
        visiting.remove(task_id)
        visited.add(task_id)
        return False

    if any(visit(task_id) for task_id in tuple(adjacency)):
        issues.append(
            ValidationIssue(
                "error",
                "plan.dependencias",
                "El grafo de dependencias contiene un ciclo.",
            )
        )


def _validate_templates(value: Any, issues: list[ValidationIssue]) -> None:
    if not isinstance(value, list) or not value:
        issues.append(
            ValidationIssue(
                "error",
                "plan.plantillas_aplicadas",
                "Debe incluir al menos una plantilla.",
            )
        )
        return
    seen: set[str] = set()
    for index, template in enumerate(value):
        path = f"plan.plantillas_aplicadas[{index}]"
        if not isinstance(template, dict):
            issues.append(ValidationIssue("error", path, "Debe ser un mapa."))
            continue
        identifier = template.get("id")
        _validate_identifier(identifier, "template", f"{path}.id", issues)
        if isinstance(identifier, str) and identifier in seen:
            issues.append(ValidationIssue("error", path, "Plantilla duplicada."))
        if isinstance(identifier, str):
            seen.add(identifier)
        version = template.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            issues.append(
                ValidationIssue("error", f"{path}.version", "Debe ser un entero positivo.")
            )


def _validate_generation(value: Any, issues: list[ValidationIssue]) -> None:
    if not isinstance(value, dict):
        issues.append(
            ValidationIssue("error", "plan.generacion", "Debe ser un mapa.")
        )
        return
    if not isinstance(value.get("motor"), str) or value.get("motor") not in {
        "local",
        "cloud",
    }:
        issues.append(
            ValidationIssue(
                "error", "plan.generacion.motor", "Motor no permitido."
            )
        )
    for field in ("proveedor", "modelo", "fecha"):
        _validate_nonempty_text(
            value.get(field), f"plan.generacion.{field}", issues
        )
    timestamp = value.get("fecha")
    if isinstance(timestamp, str):
        try:
            if "T" not in timestamp:
                raise ValueError("Falta separador de fecha y hora.")
            datetime.fromisoformat(timestamp)
        except ValueError:
            issues.append(
                ValidationIssue(
                    "error",
                    "plan.generacion.fecha",
                    "Debe ser un datetime ISO 8601.",
                )
            )


def _validate_string_list(
    value: Any,
    path: str,
    issues: list[ValidationIssue],
) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        issues.append(
            ValidationIssue("error", path, "Debe ser una lista de textos no vacíos.")
        )


def _validate_nonempty_text(
    value: Any,
    path: str,
    issues: list[ValidationIssue],
) -> None:
    if not isinstance(value, str) or not value.strip():
        issues.append(
            ValidationIssue("error", path, "Debe ser un texto no vacío.")
        )


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_finite_number(value: Any) -> bool:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    return math.isfinite(value)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
