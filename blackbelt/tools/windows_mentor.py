"""Mentor de Windows y PowerShell con explicaciones mediante Ollama local."""

import json
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Sequence

import ollama
from rich.console import Console
from rich.table import Table

from blackbelt.core import config as cfg
from blackbelt.core import environment, executor

console = Console()
MAX_REFERENCE_CHARS = 1400
MAX_PARAMETERS = 5
MAX_SUMMARY_WORDS = 30


@dataclass(frozen=True, slots=True)
class HelpReference:
    name: str
    synopsis: str
    description: str
    parameters: tuple[tuple[str, str], ...]
    incomplete: bool


def run(args: Sequence[str] | str | None = None) -> None:
    """Uso: blackbelt run windows [info|explain <comando>|diagnose]."""
    command_line = executor.args_to_str(args)
    parts = command_line.split(maxsplit=1)
    command = parts[0].lower() if parts else ""
    argument = parts[1] if len(parts) > 1 else ""

    console.print("[bold]Windows Mentor[/]")
    if command in ("", "info"):
        _info()
    elif command == "explain":
        _explain(argument)
    elif command == "diagnose":
        _diagnose()
    else:
        console.print(f"Comando no reconocido: {command}")
        console.print(
            "Comandos disponibles: info, explain <comando>, diagnose"
        )


def _info() -> None:
    """Muestra el sistema actual y las herramientas de Windows disponibles."""
    table = Table(title="Entorno Windows")
    table.add_column("Clave")
    table.add_column("Valor")
    table.add_row("Sistema", platform.platform())
    table.add_row("Shell", environment.detect_shell())
    table.add_row("Python", environment.detect_python())
    table.add_row(
        "PowerShell",
        shutil.which("powershell.exe") or "No encontrado",
    )
    table.add_row("PowerShell Core", shutil.which("pwsh.exe") or "No encontrado")
    ollama_status = "Disponible" if environment.detect_ollama() else "No encontrado"
    table.add_row("Ollama", ollama_status)
    console.print(table)


def _explain(command: str) -> None:
    if not command:
        console.print('Uso: blackbelt run windows explain "Get-ChildItem -Force"')
        return

    command_name = command.split(maxsplit=1)[0]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", command_name):
        console.print(
            "No puedo consultar ayuda para ese formato. "
            "Indica un nombre de comando PowerShell, por ejemplo Get-ChildItem."
        )
        return

    executable = (
        shutil.which("powershell.exe")
        or shutil.which("powershell")
        or shutil.which("pwsh.exe")
        or shutil.which("pwsh")
    )
    if executable is None:
        console.print("No se encontró PowerShell para consultar su ayuda local.")
        return

    if re.search(r"[|;&\r\n]", _without_quoted_values(command)):
        console.print(
            "Explica un solo comando cada vez; no se aceptan tuberías "
            "ni varios comandos en la misma consulta."
        )
        return

    parameter_names = _extract_parameter_names(command)
    script = _build_help_script(command_name, parameter_names)
    try:
        result = subprocess.run(
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
    except FileNotFoundError:
        console.print("No se encontró PowerShell para consultar su ayuda local.")
        return
    except subprocess.TimeoutExpired:
        console.print("PowerShell tardó demasiado en consultar la ayuda local.")
        return
    except (OSError, subprocess.SubprocessError) as error:
        console.print(f"No se pudo consultar la ayuda de PowerShell: {error}")
        return

    if result.returncode != 0:
        message = result.stderr.strip() or "PowerShell devolvió un error."
        console.print(f"No se pudo consultar la ayuda de PowerShell: {message}")
        return

    try:
        reference = _parse_help_reference(result.stdout)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        console.print(
            "PowerShell devolvió una referencia que no pude interpretar. "
            "No generaré un resumen sin una fuente verificable."
        )
        return

    if not reference.synopsis and not reference.description:
        console.print(
            f"PowerShell no encontró documentación útil para {command_name}."
        )
        return

    source = _format_reference(reference, command)
    console.print(f"Fuente local de PowerShell para: {command}")
    console.print(source)
    if reference.incomplete:
        console.print(
            "\nLa ayuda local está marcada como incompleta. "
            "El resumen solo podrá usar los datos mostrados arriba."
        )
    summary_source = _format_summary_source(reference, command)
    _summarize_reference(summary_source)


def _without_quoted_values(command: str) -> str:
    without_single_quotes = re.sub(r"'(?:''|[^'])*'", "''", command)
    return re.sub(r'"(?:`.|[^"])*"', '""', without_single_quotes)


def _extract_parameter_names(command: str) -> tuple[str, ...]:
    """Extrae parámetros nombrados de la primera invocación del comando."""
    first_command = re.split(r"[|;&\r\n]", command, maxsplit=1)[0]
    names = re.findall(
        r"(?<![\w])-(?P<name>[A-Za-z][A-Za-z0-9]*)(?=[:\s]|$)",
        _without_quoted_values(first_command),
    )
    return tuple(dict.fromkeys(names))[:MAX_PARAMETERS]


def _build_help_script(
    command_name: str,
    parameter_names: tuple[str, ...],
) -> str:
    """Construye un script usando solo identificadores ya validados."""
    quoted_parameters = ", ".join(
        f"'{name}'" for name in parameter_names
    )
    return (
        "$ErrorActionPreference = 'Stop'; "
        f"$help = Get-Help -Name '{command_name}' -Full; "
        "$description = (@($help.description | "
        "ForEach-Object { $_.Text }) -join \"`n\").Trim(); "
        f"$requested = @({quoted_parameters}); "
        "$parameters = @("
        "foreach ($name in $requested) { "
        "$parameter = $help.parameters.parameter | "
        "Where-Object { $_.name -ieq $name } | Select-Object -First 1; "
        "if ($null -ne $parameter) { "
        "[PSCustomObject]@{ "
        "Name = $parameter.name; "
        "Description = (@($parameter.description | "
        "ForEach-Object { $_.Text }) -join \"`n\").Trim() "
        "} "
        "} else { "
        "[PSCustomObject]@{ "
        "Name = $name; "
        "Description = 'No hay descripción local para este parámetro.' "
        "} "
        "}"
        "}"
        "); "
        "$helpText = $help | Out-String -Width 100; "
        "$incomplete = $helpText -match "
        "'no encuentra los archivos de Ayuda|does not find the Help files'; "
        "[PSCustomObject]@{ "
        "Name = $help.Name; "
        "Synopsis = $help.Synopsis; "
        "Description = $description; "
        "Parameters = $parameters; "
        "Incomplete = $incomplete "
        "} | ConvertTo-Json -Depth 6 -Compress"
    )


def _parse_help_reference(payload: str) -> HelpReference:
    """Valida y limita los metadatos estructurados recibidos de PowerShell."""
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise TypeError("La ayuda de PowerShell debe ser un objeto JSON.")

    parameters_data = data.get("Parameters", [])
    if isinstance(parameters_data, dict):
        parameters_data = [parameters_data]
    if not isinstance(parameters_data, list):
        raise TypeError("Los parámetros de ayuda deben ser una lista.")

    parameters: list[tuple[str, str]] = []
    for parameter in parameters_data:
        if not isinstance(parameter, dict):
            continue
        name = parameter.get("Name")
        description = parameter.get("Description")
        if isinstance(name, str) and isinstance(description, str):
            parameters.append(
                (name[:80], description[:MAX_REFERENCE_CHARS])
            )

    synopsis = data.get("Synopsis")
    description = data.get("Description")
    name = data.get("Name")
    incomplete = data.get("Incomplete", False)
    if not isinstance(name, str) or not isinstance(incomplete, bool):
        raise TypeError("Los metadatos de ayuda tienen tipos inválidos.")

    return HelpReference(
        name=name[:120],
        synopsis=synopsis[:500] if isinstance(synopsis, str) else "",
        description=(
            description[:MAX_REFERENCE_CHARS]
            if isinstance(description, str)
            else ""
        ),
        parameters=tuple(parameters),
        incomplete=incomplete,
    )


def _format_reference(reference: HelpReference, command: str) -> str:
    sections = [
        f"Comando consultado: {command}",
        f"Nombre documentado: {reference.name}",
    ]
    if reference.synopsis:
        sections.append(f"Sinopsis oficial: {reference.synopsis}")
    if reference.description:
        sections.append(f"Descripción oficial:\n{reference.description}")
    for name, description in reference.parameters:
        sections.append(
            f"Parámetro presente -{name}:\n"
            f"{description or 'La ayuda local no incluye una descripción.'}"
        )
    return "\n\n".join(sections)[:MAX_REFERENCE_CHARS]


def _format_summary_source(reference: HelpReference, command: str) -> str:
    """Limita al SLM a la sinopsis y parámetros escritos por el usuario."""
    sections = [f"Comando: {command}"]
    if reference.synopsis:
        sections.append(f"Sinopsis oficial: {reference.synopsis}")
    for name, description in reference.parameters:
        sections.append(f"Parámetro -{name}: {description}")
    return "\n\n".join(sections)[:MAX_REFERENCE_CHARS]


def _summarize_reference(source: str) -> None:
    prompt = (
        "Explica en español esta ayuda de PowerShell en una sola frase de "
        "máximo 25 palabras. Traduce fielmente. No añadas ejemplos, "
        "parámetros ni efectos que no aparezcan en la fuente.\n"
        f"Fuente:\n{source}"
    )
    options = cfg.ollama_options()
    options["temperature"] = 0
    options["num_predict"] = min(options["num_predict"], 64)
    try:
        response = ollama.chat(
            model=cfg.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            keep_alive=cfg.OLLAMA_KEEP_ALIVE,
            options=options,
        )
    except ollama.RequestError as error:
        console.print(
            f"No se pudo conectar con Ollama; fuente mostrada arriba: {error}"
        )
        return
    except ollama.ResponseError as error:
        console.print(
            f"Ollama no pudo resumir; fuente mostrada arriba: {error}"
        )
        return

    summary = _response_content(response)
    if not isinstance(summary, str) or not summary.strip():
        console.print(
            "Ollama no devolvió un resumen válido; usa la fuente mostrada arriba."
        )
        return
    if _response_done_reason(response) == "length":
        console.print(
            "Ollama no terminó el resumen; consulta la fuente oficial "
            "mostrada arriba."
        )
        return
    summary = summary.strip()
    if len(summary.split()) > MAX_SUMMARY_WORDS:
        console.print(
            "Ollama devolvió un resumen demasiado largo o fuera del formato "
            "esperado; consulta la fuente oficial mostrada arriba."
        )
        return
    console.print("\nResumen generado a partir de la ayuda oficial:")
    console.print(summary)


def _response_content(response: object) -> str | None:
    """Lee respuestas Ollama del SDK tipado y del formato dict legacy."""
    if isinstance(response, dict):
        message = response.get("message")
        if isinstance(message, dict):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)
    else:
        message = getattr(response, "message", None)
        content = getattr(message, "content", None)
    return content if isinstance(content, str) else None


def _response_done_reason(response: object) -> str | None:
    if isinstance(response, dict):
        reason = response.get("done_reason")
    else:
        reason = getattr(response, "done_reason", None)
    return reason if isinstance(reason, str) else None


def _diagnose() -> None:
    checks = (
        ("Sistema Windows", platform.system() == "Windows"),
        ("Windows PowerShell", shutil.which("powershell.exe") is not None),
        ("PowerShell Core", shutil.which("pwsh.exe") is not None),
        (
            "Git",
            shutil.which("git.exe") is not None or shutil.which("git") is not None,
        ),
        ("Ollama", environment.detect_ollama()),
    )
    table = Table(title="Diagnóstico de herramientas")
    table.add_column("Comprobación")
    table.add_column("Estado")
    for name, available in checks:
        table.add_row(name, "Disponible" if available else "No disponible")
    console.print(table)
