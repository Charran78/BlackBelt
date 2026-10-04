"""
Gatekeeper: capa de seguridad para acciones sensibles.

Intercepta comandos y operaciones, evalúa su riesgo, y pide confirmación
humana cuando es necesario. Registra todo en un log de auditoría.
"""

import platform
import re
import shutil
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


class Shell(str, Enum):
    BASH = "bash"
    POWERSHELL = "powershell"


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

# Los patrones son intencionadamente conservadores: pueden pedir confirmacion
# para un comando inocuo, pero no deben rebajar una operacion destructiva.
BASH_CRITICAL_PATTERNS = [
    r"\brm\b",
    r"\bdd\b",
    r"\bmkfs\b",
    r"\brmdir\b",
    r"\bshred\b",
    r"\bwipefs\b",
    r">\s*/dev/sd",
    r">\s*/dev/nvme",
    r"\bchmod\s+777\s+/",
    r"\bchown\s+.*\s+/",
    r"\buserdel\b",
    r"\bgroupdel\b",
    r"\bpasswd\b",
    r"\bgit\s+reset\b",
    r"\bsudo\b",
    r"\bsu\b",
    r":\(\)\s*\{.*\};:",  # fork bomb clásica
]

BASH_WARN_PATTERNS = [
    r">{1,2}\s*\S+",
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

POWERSHELL_CRITICAL_PATTERNS = [
    r"(?<![\w-])(?:remove-item|ri|rm|del|erase|rd|rmdir)(?![\w-])",
    r"(?<![\w-])(?:clear-content|clear-disk|format-disk|format-volume|"
    r"initialize-disk|remove-itemproperty|remove-partition)(?![\w-])",
    r"(?<![\w-])(?:invoke-expression|iex|invoke-command)(?![\w-])",
    r"(?<![\w-])(?:diskpart|bcdedit|stop-computer|restart-computer)(?![\w-])",
    r"(?<![\w-])shutdown(?:\.exe)?(?![\w-])",
    r"(?<![\w-])reg(?:\.exe)?\s+delete\b",
    r"(?<![\w-])(?:cmd(?:\.exe)?|wsl(?:\.exe)?|bash(?:\.exe)?|"
    r"sh(?:\.exe)?|powershell(?:\.exe)?|pwsh(?:\.exe)?)(?![\w-])",
    r"(?<!\w)-(?:enc|encodedcommand)(?:\s|$)",
    r"(?<![\w-])(?:sudo|su)(?![\w-])",
    r"(?<![\w-])git(?:\.exe)?\s+reset\b",
]

POWERSHELL_WARN_PATTERNS = [
    r">{1,2}\s*\S+",
    r"(?<![\w-])(?:set-content|sc|add-content|ac|out-file)(?![\w-])",
    r"(?<![\w-])(?:new-item|ni|move-item|mi|mv|copy-item|cp|"
    r"rename-item|mkdir)(?![\w-])",
    r"(?<![\w-])(?:set-itemproperty|set-executionpolicy|"
    r"start-process|stop-process|invoke-webrequest)(?![\w-])",
    r"(?<![\w-])(?:touch|chmod|chown|truncate|kill|pkill)(?![\w-])",
]


def detect_shell() -> Shell:
    """Selecciona el shell nativo para el sistema operativo actual."""
    if platform.system() == "Windows":
        return Shell.POWERSHELL
    return Shell.BASH


def _normalize_shell(shell: Shell | str | None) -> Shell | None:
    if shell is None:
        return detect_shell()
    if isinstance(shell, Shell):
        return shell
    if not isinstance(shell, str):
        return None
    try:
        return Shell(shell.lower())
    except ValueError:
        return None


def analyze_command(cmd: str, shell: Shell | str | None = None) -> Risk:
    """Estima el riesgo usando las reglas del shell que ejecutara el comando."""
    selected_shell = _normalize_shell(shell)
    if selected_shell is None:
        return Risk.CRITICAL

    if selected_shell is Shell.POWERSHELL:
        critical_patterns = POWERSHELL_CRITICAL_PATTERNS
        warn_patterns = POWERSHELL_WARN_PATTERNS
    else:
        critical_patterns = BASH_CRITICAL_PATTERNS
        warn_patterns = BASH_WARN_PATTERNS

    for pattern in critical_patterns:
        if re.search(pattern, cmd, flags=re.IGNORECASE):
            return Risk.CRITICAL
    for pattern in warn_patterns:
        if re.search(pattern, cmd, flags=re.IGNORECASE):
            return Risk.WARN
    return Risk.SAFE


def _shell_command(cmd: str, shell: Shell) -> list[str]:
    """Construye argv sin pasar por un shell intermediario de Python."""
    if shell is Shell.POWERSHELL:
        executable = (
            shutil.which("powershell.exe")
            or shutil.which("powershell")
            or shutil.which("pwsh.exe")
            or shutil.which("pwsh")
        )
        if executable is None:
            raise FileNotFoundError(
                "No se encontro PowerShell (powershell.exe o pwsh)."
            )

        script = (
            "$utf8 = [System.Text.UTF8Encoding]::new(); "
            "[Console]::OutputEncoding = $utf8; "
            "$OutputEncoding = $utf8; "
            "$ErrorActionPreference = 'Stop'; "
            f"try {{ {cmd} }} catch {{ "
            "[Console]::Error.WriteLine($_.ToString()); exit 1 }; "
            "if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }"
        )
        return [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ]

    executable = shutil.which("bash")
    if executable is None and Path("/bin/bash").is_file():
        executable = "/bin/bash"
    if executable is None:
        raise FileNotFoundError("No se encontro Bash.")
    return [executable, "-c", cmd]


# ============================================================
# LOG DE AUDITORÍA
# ============================================================

def _log(
    action: str,
    risk: Risk,
    allowed: bool,
    detail: str = "",
) -> None:
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

def confirm(
    risk: Risk,
    action: str,
    detail: str = "",
    bypass: bool = False,
) -> bool:
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

def run_command(
    cmd: str,
    bypass: bool = False,
    capture: bool = True,
    minimum_risk: Risk | None = None,
) -> tuple[int, str, str]:
    """
    Ejecuta un comando bajo PowerShell (Windows) o Bash (Linux/macOS).

    Devuelve (returncode, stdout, stderr).
    """
    if not cmd.strip():
        return (2, "", "Falta el comando a ejecutar.")

    shell = detect_shell()
    risk = analyze_command(cmd, shell=shell)
    if minimum_risk is not None:
        if not isinstance(minimum_risk, Risk):
            return (2, "", "El nivel mínimo de riesgo no es válido.")
        risk_order = {
            Risk.SAFE: 0,
            Risk.WARN: 1,
            Risk.CRITICAL: 2,
        }
        if risk_order[minimum_risk] > risk_order[risk]:
            risk = minimum_risk

    if not confirm(
        risk,
        action=f"Ejecutar en {shell.value}: {cmd}",
        bypass=bypass,
    ):
        return (-1, "", "Cancelado por el usuario")

    try:
        command = _shell_command(cmd, shell)
        result = subprocess.run(
            command,
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return (result.returncode, result.stdout or "", result.stderr or "")
    except FileNotFoundError as e:
        return (127, "", f"Comando no encontrado: {e}")
    except (OSError, subprocess.SubprocessError) as e:
        return (1, "", f"Error ejecutando: {e}")
