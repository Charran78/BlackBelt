"""Sugiere comandos desde recetas revisadas, sin generar comandos con IA."""

import os
import platform
import re
import shlex
import unicodedata
from collections import deque
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from blackbelt.core import executor, gatekeeper

console = Console()
RECIPES_PATH = Path(__file__).parent.parent / "data" / "recipes.yaml"

Recipe = dict[str, Any]
IGNORED_PROJECT_DIRECTORIES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".svn",
    ".tox",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "venv",
}
MAX_PROJECT_DIRECTORIES = 250
MAX_PROJECT_ENTRIES = 20_000
MAX_PROJECT_CANDIDATES = 20


def run(args: list[str] | str | None = None) -> None:
    """Sugiere una receta para una intención y permite ejecutarla con Gatekeeper."""
    query = executor.args_to_str(args).strip()
    try:
        recipes = _load_recipes()
    except (OSError, yaml.YAMLError) as error:
        console.print(f"[red]No se pudo cargar el catálogo: {escape(str(error))}[/]")
        return

    if not recipes:
        console.print(f"[red]No hay recetas válidas en: {RECIPES_PATH}[/]")
        return

    current_os = _current_os()
    if current_os is None:
        console.print("[red]El sistema operativo actual no tiene recetas.[/]")
        return

    available = [
        recipe for recipe in recipes if current_os in recipe.get("os", [])
    ]
    matches = _search(available, query) if query else available
    if not matches:
        console.print(f"[yellow]No encontré recetas para:[/] {escape(query)}")
        console.print(
            "[dim]Prueba otras palabras o ejecuta `blackbelt run suggest` "
            "para explorar el catálogo.[/]"
        )
        return

    recipe = matches[0] if len(matches) == 1 else _choose(matches)
    if recipe is None:
        console.print("[dim]Cancelado.[/]")
        return
    _build_and_show(recipe, current_os)


def _load_recipes() -> list[Recipe]:
    with RECIPES_PATH.open(encoding="utf-8") as recipe_file:
        data = yaml.safe_load(recipe_file)
    if not isinstance(data, list):
        return []
    return [recipe for recipe in data if _is_recipe(recipe)]


def _is_recipe(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        isinstance(value.get("id"), str)
        and isinstance(value.get("titulo"), str)
        and isinstance(value.get("os"), list)
        and all(isinstance(os_name, str) for os_name in value["os"])
        and isinstance(value.get("keywords"), list)
        and all(isinstance(word, str) for word in value["keywords"])
        and isinstance(value.get("preguntas"), list)
        and isinstance(value.get("comandos"), dict)
    )


def _current_os() -> str | None:
    system = platform.system()
    return {
        "Linux": "linux",
        "Darwin": "macos",
        "Windows": "windows",
    }.get(system)


def _normalize_words(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    words = re.findall(r"[a-z0-9]+", ascii_text)
    return {
        word[:-1] if word.endswith("s") and len(word) > 4 else word
        for word in words
    }


def _search(recipes: list[Recipe], query: str) -> list[Recipe]:
    query_words = _normalize_words(query)
    if not query_words:
        return []

    scored: list[tuple[int, Recipe]] = []
    for recipe in recipes:
        searchable = " ".join(
            [
                recipe.get("titulo", ""),
                recipe.get("categoria", ""),
                *recipe.get("keywords", []),
            ]
        )
        score = len(query_words & _normalize_words(searchable))
        if score:
            scored.append((score, recipe))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return []
    best_score = scored[0][0]
    return [recipe for score, recipe in scored if score == best_score]


def _choose(recipes: list[Recipe]) -> Recipe | None:
    table = Table(title="Opciones encontradas")
    table.add_column("#", justify="right")
    table.add_column("Tarea")
    table.add_column("Categoría")
    for index, recipe in enumerate(recipes, 1):
        table.add_row(
            str(index),
            escape(recipe["titulo"]),
            escape(recipe.get("categoria", "-")),
        )
    console.print(table)

    while True:
        try:
            selection = Prompt.ask("Elige una opción o 'q' para cancelar")
        except (EOFError, KeyboardInterrupt):
            return None
        if selection.lower() in {"q", "quit", "salir"}:
            return None
        if selection.isdigit() and 1 <= int(selection) <= len(recipes):
            return recipes[int(selection) - 1]
        console.print("[red]Opción no válida.[/]")


def _ask_questions(
    recipe: Recipe,
    os_name: str,
) -> tuple[dict[str, str], dict[str, str]] | None:
    command_values: dict[str, str] = {}
    display_values: dict[str, str] = {}
    for question in recipe.get("preguntas", []):
        if not isinstance(question, dict):
            return None
        answer = _ask_question(question, os_name)
        if answer is None:
            return None
        label, value = answer
        answer_id = question.get("id")
        if not isinstance(answer_id, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]*",
            answer_id,
        ):
            return None

        value_maps = question.get("valores", {})
        os_values = value_maps.get(os_name, {}) if isinstance(value_maps, dict) else {}
        command_values[answer_id] = (
            os_values.get(label, value)
            if isinstance(os_values, dict)
            else value
        )
        display_values[answer_id] = label
        resolved_value = command_values[answer_id]
        if not isinstance(resolved_value, str):
            return None
        if question.get("tipo") == "opcion":
            if not re.fullmatch(r"[A-Za-z0-9_%+.-]*", resolved_value):
                return None
        else:
            command_values[answer_id] = _quote_argument(
                resolved_value,
                os_name,
                expand_user=question.get("tipo") == "path",
            )
    return command_values, display_values


def _ask_question(
    question: dict[str, Any],
    os_name: str,
) -> tuple[str, str] | None:
    answer_id = question.get("id")
    text = question.get("texto")
    question_type = question.get("tipo")
    if (
        not isinstance(answer_id, str)
        or not isinstance(text, str)
        or not isinstance(question_type, str)
        or question_type not in {"opcion", "path", "texto"}
    ):
        return None

    if question.get("tipo") == "opcion":
        options = question.get("opciones")
        if not isinstance(options, list) or not options:
            return None
        labels = [option for option in options if isinstance(option, str)]
        if len(labels) != len(options):
            return None
        default = question.get("default", labels[0])
        default_index = labels.index(default) + 1 if default in labels else 1
        console.print(f"[dim]{escape(text)}[/]")
        for index, option in enumerate(labels, 1):
            suffix = " (por defecto)" if index == default_index else ""
            console.print(f"  {index}. {escape(option)}{suffix}")
        try:
            selection = Prompt.ask("Elige", default=str(default_index))
        except (EOFError, KeyboardInterrupt):
            return None
        if selection.lower() in {"q", "salir", "cancelar"}:
            return None
        if selection.isdigit() and 1 <= int(selection) <= len(labels):
            label = labels[int(selection) - 1]
            return label, label
        console.print("[red]Opción no válida; se usará el valor por defecto.[/]")
        label = labels[default_index - 1]
        return label, label

    default = question.get("default", "")
    if not isinstance(default, str):
        return None
    autodetect = question.get("autodetect")
    candidates: list[tuple[str, str]] = []
    if isinstance(autodetect, dict):
        candidates = _question_candidates(question, autodetect)
        if candidates:
            console.print("[cyan]Candidatos detectados en el proyecto:[/]")
            for index, (candidate_path, _) in enumerate(candidates, 1):
                console.print(f"  {index}. {escape(candidate_path)}")
            default = candidates[0][1] if len(candidates) == 1 else ""
        else:
            default = ""
            console.print(
                "[yellow]No encontré un candidato en la carpeta actual "
                "ni en sus subcarpetas cercanas.[/]"
            )

    prompt_text = escape(text)
    if len(candidates) > 1:
        prompt_text += " (elige un número o escribe otra ruta)"
    elif len(candidates) == 1:
        prompt_text += " (Enter acepta el candidato detectado)"
    try:
        answer = Prompt.ask(prompt_text, default=default).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not answer and default:
        answer = default
    if answer.isdigit() and candidates:
        candidate_index = int(answer) - 1
        if 0 <= candidate_index < len(candidates):
            answer = candidates[candidate_index][1]
    if question_type == "path" and os_name == "windows":
        answer = _normalize_windows_path(answer)
        if re.fullmatch(r"[A-Za-z]:\\", answer):
            drive_root = escape(answer)
            console.print(
                f"[yellow]La ruta {drive_root} es la raíz de toda la unidad. "
                "La búsqueda recursiva puede tardar bastante.[/]"
            )
            try:
                confirmed = Confirm.ask(
                    "¿Quieres buscar en toda la unidad?",
                    default=False,
                )
            except (EOFError, KeyboardInterrupt):
                return None
            if not confirmed:
                return None
    if question.get("tipo") == "texto" and not answer:
        if len(candidates) > 1:
            console.print(
                "[red]Elige un candidato detectado o escribe un valor.[/]"
            )
        else:
            console.print("[red]El texto de búsqueda no puede estar vacío.[/]")
        return None
    if question_type == "path" and not answer:
        console.print(
            "[red]La ruta es obligatoria. Indica una ubicación o "
            "selecciona un candidato detectado.[/]"
        )
        return None
    return answer, answer


def _question_candidates(
    question: dict[str, Any],
    config: dict[str, Any],
) -> list[tuple[str, str]]:
    """Encuentra candidatos por nombre; no abre ni interpreta archivos."""
    paths = _discover_project_paths(config)
    suggestion_template = config.get("suggestion_template")
    candidates: list[tuple[str, str]] = []
    for path in paths:
        candidate_value = path
        if isinstance(suggestion_template, str):
            candidate_path = Path(path)
            module_name = ".".join(
                candidate_path.with_suffix("").parts
            )
            candidate_value = suggestion_template.replace(
                "{stem}",
                candidate_path.stem,
            ).replace("{name}", candidate_path.name).replace(
                "{module}",
                module_name,
            )
        if question.get("tipo") == "path":
            candidate_value = path
        candidates.append((path, candidate_value))
    return candidates


def _discover_project_paths(
    config: dict[str, Any],
    *,
    root: Path | None = None,
) -> list[str]:
    """Busca nombres concretos hasta dos niveles, omitiendo árboles generados."""
    names = config.get("filenames")
    kind = config.get("kind", "file")
    max_depth = config.get("max_depth", 2)
    if (
        not isinstance(names, list)
        or not names
        or any(not isinstance(name, str) or not name for name in names)
        or kind not in {"file", "directory"}
        or not isinstance(max_depth, int)
        or isinstance(max_depth, bool)
        or not 0 <= max_depth <= 2
    ):
        return []

    search_root = root or Path.cwd()
    try:
        search_root = search_root.resolve()
    except OSError:
        return []

    target_names = {name.casefold() for name in names}
    pending: deque[tuple[Path, int]] = deque([(search_root, 0)])
    discovered: list[str] = []
    visited_directories = 0
    inspected_entries = 0

    while (
        pending
        and visited_directories < MAX_PROJECT_DIRECTORIES
        and inspected_entries < MAX_PROJECT_ENTRIES
        and len(discovered) < MAX_PROJECT_CANDIDATES
    ):
        directory, depth = pending.popleft()
        visited_directories += 1
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    inspected_entries += 1
                    if inspected_entries >= MAX_PROJECT_ENTRIES:
                        break
                    try:
                        is_directory = entry.is_dir(follow_symlinks=False)
                        matches_kind = (
                            is_directory
                            if kind == "directory"
                            else entry.is_file(follow_symlinks=False)
                        )
                    except OSError:
                        continue
                    if (
                        entry.name.casefold() in target_names
                        and matches_kind
                    ):
                        candidate = Path(entry.path)
                        try:
                            relative = candidate.relative_to(search_root)
                        except ValueError:
                            continue
                        discovered.append(str(relative))
                    if (
                        is_directory
                        and depth < max_depth
                        and not entry.name.startswith(".")
                        and entry.name.casefold()
                        not in IGNORED_PROJECT_DIRECTORIES
                    ):
                        pending.append((Path(entry.path), depth + 1))
                    if len(discovered) >= MAX_PROJECT_CANDIDATES:
                        break
        except OSError:
            continue

    return sorted(set(discovered), key=str.casefold)


def _normalize_windows_path(value: str) -> str:
    """Interpreta una unidad sola como su raíz, no como ruta drive-relative."""
    if re.fullmatch(r"[A-Za-z]:(?:[\\/])?", value):
        return value[0].upper() + ":\\"
    return value


def _quote_argument(
    value: str,
    os_name: str,
    *,
    expand_user: bool = False,
) -> str:
    if expand_user:
        value = os.path.expanduser(value)
    if os_name == "windows":
        return "'" + value.replace("'", "''") + "'"
    return shlex.quote(value)


def _render(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def _build_and_show(recipe: Recipe, os_name: str) -> None:
    console.print(
        Panel(
            f"[bold cyan]{escape(recipe['titulo'])}[/]\n"
            f"[dim]{escape(recipe.get('categoria', ''))}[/]",
            expand=False,
        )
    )
    answers = _ask_questions(recipe, os_name)
    if answers is None:
        console.print("[dim]Cancelado.[/]")
        return
    command_values, display_values = answers

    commands = recipe.get("comandos", {}).get(os_name)
    if not isinstance(commands, dict):
        console.print(f"[red]La receta no tiene comando para {os_name}.[/]")
        return
    mode = display_values.get("modo")
    if mode == "preview" and "cmd_preview" in commands:
        template = commands["cmd_preview"]
        description = "VISTA PREVIA: " + commands.get("descripcion", "")
        risk_key = "riesgo_preview"
    elif mode == "borrar" and "cmd_borrar" in commands:
        template = commands["cmd_borrar"]
        description = "BORRAR: " + commands.get("descripcion", "")
        risk_key = "riesgo_borrar"
    else:
        template = commands.get("cmd", "")
        description = commands.get("descripcion", "")
        risk_key = "riesgo"
    if not isinstance(template, str) or not isinstance(description, str):
        console.print("[red]La receta tiene un comando o descripción inválidos.[/]")
        return
    try:
        minimum_risk = gatekeeper.Risk(commands[risk_key])
    except (KeyError, ValueError, TypeError):
        console.print("[red]La receta no declara un nivel de riesgo válido.[/]")
        return

    command = _render(template, command_values)
    description = escape(_render(description, display_values))
    detected_risk = gatekeeper.analyze_command(command)
    risk_order = {
        gatekeeper.Risk.SAFE: 0,
        gatekeeper.Risk.WARN: 1,
        gatekeeper.Risk.CRITICAL: 2,
    }
    risk = max(
        (detected_risk, minimum_risk),
        key=risk_order.__getitem__,
    )
    console.print("\n[bold]Comando sugerido[/]")
    console.print(f"[bold]{description}[/]")
    console.print(f"[yellow]Riesgo aplicado por Gatekeeper: {risk.value}[/]")
    console.print(f"\n[green]{escape(command)}[/]")

    if recipe.get("ejecucion") == "manual":
        console.print(
            "[yellow]Pega este comando en la terminal actual. "
            "BlackBelt no puede modificar el entorno de su proceso padre.[/]"
        )
        return

    foreground = recipe.get("ejecucion") == "foreground"
    if foreground:
        console.print(
            "[dim]Se ejecutará en primer plano con salida en vivo. "
            "Pulsa Ctrl+C para detenerlo.[/]"
        )

    try:
        execute = Confirm.ask(
            "¿Ejecutarlo? Pasará por la confirmación del Gatekeeper.",
            default=False,
        )
    except (EOFError, KeyboardInterrupt):
        execute = False
    if not execute:
        console.print("[dim]No ejecutado.[/]")
        return

    try:
        return_code, stdout, stderr = gatekeeper.run_command(
            command,
            capture=not foreground,
            minimum_risk=minimum_risk,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Proceso interrumpido.[/]")
        return
    if stdout:
        console.print(stdout, end="")
    if stderr:
        console.print(f"[red]{stderr}[/]", end="")
    if return_code == -1:
        console.print("[yellow]Ejecución cancelada por el Gatekeeper.[/]")
