"""
Gatekeeper: capa de seguridad para acciones sensibles.

Intercepta comandos y operaciones, evalúa su riesgo, y pide confirmación
humana cuando es necesario. Registra todo en un log de auditoría.
"""

import os
import re
import subprocess
from datetime import datetime
from enum import Enum
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from blackbelt.core import config as cfg

console = Console()

AUDIT_FILE = cfg.CONFIG_DIR / "audit.log"


class Risk(str, Enum):
    SAFE = "SAFE"         # verde
    WARN = "WARN"         # amarillo
    CRITICAL = "CRITICAL" # rojo


RISK_STYLE = {
    Risk.SAFE: "bold green",
    Risk.WARN: "bold yellow",
    Risk.CRITICAL: "bold red",
}

RISK_ICON = {
    Risk.SAFE: "✓",
    Risk.WARN: "⚠",
    Risk.CRITICAL: "☠",
}


# ============================================================
# ANÁLISIS DE RIESGO
# ============================================================

# Patrones que consideramos CRITICAL
CRITICAL_PATTERNS = [
    r"\brm\b",
    r"\bdd\b",
    r"\bmkfs\b",
    r"\bshred\b",
    r"\bwipefs\b",
    r">\s*/dev/sd",
    r">\s*/dev/nvme",
    r"\bchmod\s+777\s+/",
    r"\bchown\s+.*\s+/",
    r"\buserdel\b",
    r"\bgroupdel\b",
    r"\bpasswd\b",
    r"\bsudo\b",
    r"\bsu\b",
    r":\(\)\s*\{.*\};:",  # fork bomb clásica
]

# Patrones que consideramos WARN
WARN_PATTERNS = [
    r">\s*\S+",       # redirección de salida (sobrescribe fichero)
    r">>\s*\S+",      # append
    r"\bmv\b",
    r"\bcp\b",
    r"\btruncate\b",
    r"\btouch\b",
    r"\bmkdir\b",
    r"\bchmod\b",
    r"\bchown\b",
    r"\bkill\b",
    r"\bpkill\b",
]


def analyze_command(cmd: str) -> Risk:
    """Devuelve el nivel de riesgo estimado para un comando de shell."""
    for pattern in CRITICAL_PATTERNS:
        if re.search(pattern, cmd):
            return Risk.CRITICAL
    for pattern in WARN_PATTERNS:
        if re.search(pattern, cmd):
            return Risk.WARN
    return Risk.SAFE


# ============================================================
# LOG DE AUDITORÍA
# ============================================================

def _log(action: str, risk: Risk, allowed: bool, detail: str = ""):
    """Escribe una línea en el log de auditoría."""
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")
    status = "ALLOWED" if allowed else "DENIED"
    line = f"{ts} | {risk.value:<8} | {status:<7} | {action}"
    if detail:
        line += f" | {detail}"
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_log(lines: int = 50) -> list[str]:
    """Devuelve las últimas `lines` líneas del log de auditoría."""
    if not AUDIT_FILE.exists():
        return []
    with open(AUDIT_FILE, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return [l.rstrip("\n") for l in all_lines[-lines:]]


# ============================================================
# CONFIRMACIÓN INTERACTIVA
# ============================================================

def confirm(risk: Risk, action: str, detail: str = "", bypass: bool = False) -> bool:
    """
    Pide confirmación al usuario para una acción.

    - SAFE: no pregunta, permite directamente.
    - WARN: pide confirmación simple.
    - CRITICAL: pide confirmación escribiendo 'yes' literal.

    Devuelve True si se permite, False si se deniega.
    Siempre deja constancia en el log.
    """
    if bypass:
        _log(action, risk, True, detail + " (bypass --yes)")
        return True

    # SAFE: no pregunta
    if risk == Risk.SAFE:
        _log(action, risk, True, detail)
        return True

    style = RISK_STYLE[risk]
    icon = RISK_ICON[risk]

    # Caja de advertencia
    body = f"[bold]{action}[/]"
    if detail:
        body += f"\n\n[dim]{detail}[/]"

    console.print()
    console.print(Panel(body, title=f"{icon} {risk.value}", border_style=style))

    if risk == Risk.CRITICAL:
        console.print(
            f"[{style}]Acción CRÍTICA. Escribe 'yes' para confirmar "
            f"(cualquier otra cosa cancela):[/]"
        )
        try:
            answer = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[red]Cancelado.[/]")
            _log(action, risk, False, detail + " (interrumpido)")
            return False

        allowed = answer == "yes"
    else:
        try:
            allowed = Confirm.ask(f"[{style}]¿Permitir?[/]", default=False)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[red]Cancelado.[/]")
            _log(action, risk, False, detail + " (interrumpido)")
            return False

    if allowed:
        console.print(f"[{style}]{icon} Permitido.[/]")
    else:
        console.print("[red]✗ Denegado.[/]")

    _log(action, risk, allowed, detail)
    return allowed


# ============================================================
# EJECUCIÓN DE COMANDOS CON GATEKEEPER
# ============================================================

def run_command(cmd: str, bypass: bool = False, capture: bool = True) -> tuple[int, str, str]:
    """
    Ejecuta un comando de shell pasándolo por el Gatekeeper.

    Usa bash como intérprete para soportar ~, $VAR, pipes, globs y
    redirecciones. El Gatekeeper es la capa de seguridad.

    Devuelve (returncode, stdout, stderr).
    """
    risk = analyze_command(cmd)

    if not confirm(risk, action=f"Ejecutar: {cmd}", bypass=bypass):
        return (-1, "", "Cancelado por el usuario")

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            executable="/bin/bash",
            capture_output=capture,
            text=True,
            check=False,
        )
        return (result.returncode, result.stdout, result.stderr)
    except FileNotFoundError as e:
        return (127, "", f"Comando no encontrado: {e}")
    except Exception as e:
        return (1, "", f"Error ejecutando: {e}")
