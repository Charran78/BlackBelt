"""Atajos de Git para el día a día.

Detecta el repo actual, evita que tengas que recordar la secuencia
add → commit → push, y pasa por el Gatekeeper las acciones que escriben.
"""

import subprocess
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from blackbelt.core import executor
from blackbelt.core import gatekeeper

console = Console()


def run(args=None):
    """Atajos de Git. Uso:
        blackbelt run git status
        blackbelt run git log [n]
        blackbelt run git sync "mensaje"
        blackbelt run git push
        blackbelt run git pull
        blackbelt run git branch "nombre"
        blackbelt run git init "url"
    """
    if isinstance(args, str):
        args = args.split()
    if not args:
        args = []

    cmd = args[0] if args else ""
    rest = args[1:] if len(args) > 1 else []

    if cmd == "status":
        _status()
    elif cmd == "log":
        n_str = " ".join(rest).strip()
        n = int(n_str) if n_str.isdigit() else 10
        _log(n)
    elif cmd == "sync":
        msg = " ".join(rest).strip() if rest else ""
        _sync(msg)
    elif cmd == "push":
        _push()
    elif cmd == "pull":
        _pull()
    elif cmd == "branch":
        name = " ".join(rest).strip()
        _branch(name)
    elif cmd == "init":
        url = " ".join(rest).strip()
        _init(url)
    else:
        console.print("[bold]Git[/]")
        console.print("Comandos: status, log, sync, push, pull, branch, init")
        console.print('Ejemplos:')
        console.print('  blackbelt run git status')
        console.print('  blackbelt run git sync "mensaje del commit"')
        console.print('  blackbelt run git branch "feature/x"')


# ============================================================
# HELPERS INTERNOS
# ============================================================

def _is_git_repo() -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _require_repo() -> bool:
    if not _is_git_repo():
        console.print("[red]No estás en un repositorio git.[/]")
        console.print("Usa [bold]blackbelt run git init <url>[/] para inicializar uno.")
        return False
    return True


def _run_git(args: list[str]) -> tuple[int, str, str]:
    """Ejecuta git con los argumentos dados. Devuelve (rc, stdout, stderr)."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def _current_branch() -> str:
    rc, out, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    return out.strip() if rc == 0 else "?"


def _has_remote() -> bool:
    rc, out, _ = _run_git(["remote"])
    return rc == 0 and bool(out.strip())


def _has_changes() -> bool:
    rc, out, _ = _run_git(["status", "--porcelain"])
    return rc == 0 and bool(out.strip())


# ============================================================
# COMANDOS
# ============================================================

def _status():
    """Resumen del estado actual del repo."""
    if not _require_repo():
        return

    branch = _current_branch()
    console.print(f"[bold]Rama:[/] [cyan]{branch}[/]")

    rc, out, _ = _run_git(["status", "--short"])
    if rc != 0 or not out.strip():
        console.print("[green]Árbol de trabajo limpio.[/]")
        return

    console.print("[yellow]Cambios pendientes:[/]")
    console.print(out, end="")

    console.print()
    console.print(
        "[dim]Sugerencia: [bold]blackbelt run git sync \"mensaje\"[/] "
        "para subir los cambios.[/]"
    )


def _log(n: int = 10):
    """Últimos n commits."""
    if not _require_repo():
        return

    rc, out, err = _run_git([
        "log", f"-{n}",
        "--pretty=format:%h|%ad|%an|%s",
        "--date=format:%Y-%m-%d %H:%M",
    ])

    if rc != 0:
        console.print(f"[red]Error: {err.strip()}[/]")
        return

    if not out.strip():
        console.print("[dim]No hay commits todavía.[/]")
        return

    table = Table(title=f"Últimos {n} commits")
    table.add_column("Hash", style="yellow")
    table.add_column("Fecha", style="dim")
    table.add_column("Autor", style="cyan")
    table.add_column("Mensaje")

    for line in out.strip().split("\n"):
        parts = line.split("|", 3)
        if len(parts) == 4:
            table.add_row(*parts)

    console.print(table)


def _sync(msg: str = ""):
    """add + commit + push en un paso. Pide mensaje si no se da."""
    if not _require_repo():
        return

    if not _has_changes():
        console.print("[green]Nada que commitear. Árbol limpio.[/]")
        return

    # Mensaje auto si no se da
    if not msg:
        msg = f"Update: {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    branch = _current_branch()

    # Preview para el Gatekeeper
    rc, out, _ = _run_git(["status", "--short"])
    files = out.strip().split("\n")[:10]
    files_preview = "\n".join(f"  {f}" for f in files)
    if len(out.strip().split("\n")) > 10:
        files_preview += f"\n  ... (+{len(out.strip().split(chr(10))) - 10} más)"

    detail = (
        f"Rama: {branch}\n"
        f"Mensaje: {msg}\n"
        f"Ficheros:\n{files_preview}"
    )

    if not gatekeeper.confirm(
        gatekeeper.Risk.WARN,
        action=f"git add . && git commit && git push ({branch})",
        detail=detail,
    ):
        return

    # 1. add
    rc, _, err = _run_git(["add", "-A"])
    if rc != 0:
        console.print(f"[red]Error en git add: {err.strip()}[/]")
        return

    # 2. commit
    rc, _, err = _run_git(["commit", "-m", msg])
    if rc != 0:
        console.print(f"[red]Error en git commit: {err.strip()}[/]")
        return

    console.print(f"[green]Commit hecho:[/] {msg}")

    # 3. push (solo si hay remote)
    if not _has_remote():
        console.print("[yellow]Sin remote configurado. Añádelo con:[/]")
        console.print("  git remote add origin <url>")
        return

    # Si la rama no tiene upstream, lo configuramos
    rc, _, _ = _run_git(["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"])
    has_upstream = rc == 0

    push_args = ["push"]
    if not has_upstream:
        push_args += ["-u", "origin", branch]

    rc, out, err = _run_git(push_args)
    if rc != 0:
        console.print(f"[red]Error en git push: {err.strip()}[/]")
        return

    console.print(f"[green]Subido a origin/{branch}.[/]")


def _push():
    """git push a la rama actual."""
    if not _require_repo():
        return
    if not _has_remote():
        console.print("[red]Sin remote configurado.[/]")
        return

    branch = _current_branch()

    if not gatekeeper.confirm(
        gatekeeper.Risk.WARN,
        action=f"git push ({branch})",
    ):
        return

    rc, _, err = _run_git(["push"])
    if rc != 0:
        console.print(f"[red]Error en git push: {err.strip()}[/]")
        return
    console.print(f"[green]Subido a origin/{branch}.[/]")


def _pull():
    """git pull en la rama actual."""
    if not _require_repo():
        return
    if not _has_remote():
        console.print("[red]Sin remote configurado.[/]")
        return

    branch = _current_branch()
    console.print(f"[dim]Pull de origin/{branch}...[/]")

    rc, out, err = _run_git(["pull"])
    if rc != 0:
        console.print(f"[red]Error en git pull: {err.strip()}[/]")
        return

    console.print(out if out.strip() else "[green]Ya estaba actualizado.[/]")


def _branch(name: str):
    """Crea una rama y cambia a ella."""
    if not _require_repo():
        return

    if not name:
        console.print("[red]Falta el nombre de la rama.[/]")
        console.print('Uso: blackbelt run git branch "feature/x"')
        return

    if not gatekeeper.confirm(
        gatekeeper.Risk.WARN,
        action=f"Crear y cambiar a rama '{name}'",
    ):
        return

    rc, _, err = _run_git(["checkout", "-b", name])
    if rc != 0:
        console.print(f"[red]Error: {err.strip()}[/]")
        return

    console.print(f"[green]En rama:[/] {name}")


def _init(url: str):
    """Inicializa el repo, añade remote y hace el primer push."""
    if _is_git_repo():
        console.print("[red]Ya estás en un repositorio git.[/]")
        return

    if not url:
        console.print("[red]Falta la URL del remoto.[/]")
        console.print('Uso: blackbelt run git init "https://github.com/usuario/repo.git"')
        return

    if not gatekeeper.confirm(
        gatekeeper.Risk.WARN,
        action=f"git init + remote + primer push",
        detail=f"Remote: {url}\nRama: main",
    ):
        return

    # git init
    rc, _, err = subprocess.run(["git", "init"], capture_output=True, text=True).returncode, "", ""
    if rc != 0:
        console.print(f"[red]Error en git init: {err}[/]")
        return

    # renombrar a main
    _run_git(["branch", "-M", "main"])

    # remote
    rc, _, err = _run_git(["remote", "add", "origin", url])
    if rc != 0 and "already exists" not in err:
        console.print(f"[red]Error añadiendo remote: {err.strip()}[/]")
        return

    console.print(f"[green]Repo inicializado.[/] Remote: {url}")
    console.print("[dim]Añade archivos con: git add . && blackbelt run git sync[/]")
