"""Módulo de lectura y escritura controlada de la bóveda Obsidian.

Lectura: list, find, grep, read, info, recent (sin Gatekeeper)
Escritura: append, new (con Gatekeeper WARN)

No hay 'write' (sobrescribir). Si algún día se añade, será con
CRITICAL y backup automático.
"""

from pathlib import Path
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax

from blackbelt.core import config as cfg
from blackbelt.core import gatekeeper

console = Console()


def run(args):
    """Bóveda Obsidian. Uso:
        blackbelt run obsidian list [carpeta]
        blackbelt run obsidian find "texto"
        blackbelt run obsidian grep "texto"
        blackbelt run obsidian read "ruta/nota.md"
        blackbelt run obsidian info "ruta/nota.md"
        blackbelt run obsidian recent [n]
        blackbelt run obsidian append "ruta/nota.md" "texto a añadir"
        blackbelt run obsidian new "ruta/nota.md" "contenido inicial"
    """
    # Normalizar entrada: acepta lista o string
    if isinstance(args, str):
        args = args.split()
    if not args:
        args = []

    cmd = args[0] if args else ""
    rest = args[1:] if len(args) > 1 else []

    try:
        vault = cfg.vault_path()
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/]")
        return

    if cmd == "list":
        _list(vault, " ".join(rest))
    elif cmd == "find":
        _find(vault, " ".join(rest))
    elif cmd == "grep":
        _grep(vault, " ".join(rest))
    elif cmd == "read":
        _read(vault, " ".join(rest))
    elif cmd == "info":
        _info(vault, " ".join(rest))
    elif cmd == "recent":
        n_str = " ".join(rest).strip()
        n = int(n_str) if n_str.isdigit() else 10
        _recent(vault, n)
    elif cmd == "append":
        _append(vault, rest)
    elif cmd == "new":
        _new(vault, rest)
    else:
        console.print("[bold]Obsidian[/]")
        console.print(f"Bóveda: [cyan]{vault}[/]")
        console.print("Lectura:  list, find, grep, read, info, recent")
        console.print("Escritura: append, new  (pasan por Gatekeeper)")
        console.print('Uso: blackbelt run obsidian list')


# ============================================================
# HELPERS
# ============================================================

def _is_hidden(path: Path, vault: Path) -> bool:
    """True si la ruta pasa por alguna carpeta oculta (ej: .obsidian)."""
    try:
        rel = path.relative_to(vault)
    except ValueError:
        return False
    return any(part.startswith(".") for part in rel.parts)


def _parse_two_args(rest_list) -> tuple[str, str] | None:
    """Toma ['ruta', 'texto', 'más texto'] y devuelve (ruta, texto unido)."""
    if isinstance(rest_list, str):
        rest_list = rest_list.split(maxsplit=1)
    if len(rest_list) < 2:
        return None
    path = rest_list[0]
    text = " ".join(rest_list[1:])
    return path, text


def _unescape(text: str) -> str:
    """Convierte \\n, \\t, \\\\ en caracteres reales."""
    return (
        text.replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\\\", "\\")
    )


# ============================================================
# COMANDOS DE LECTURA
# ============================================================

def _list(vault: Path, sub: str = ""):
    """Lista notas markdown de la bóveda (o de una subcarpeta)."""
    base = vault / sub if sub else vault
    if not base.exists():
        console.print(f"[red]Carpeta no encontrada: {base}[/]")
        return

    dirs = sorted([p for p in base.iterdir() if p.is_dir()])
    notes = sorted([p for p in base.iterdir() if p.is_file() and p.suffix == ".md"])

    console.print(f"[bold]Bóveda:[/] [cyan]{vault}[/]")
    console.print(f"[bold]Ruta:[/] {base.relative_to(vault) if base != vault else '.'}")
    console.print()

    if dirs:
        table = Table(title="Carpetas", show_header=False, box=None)
        table.add_column("📁", style="yellow")
        for d in dirs:
            table.add_row(f"{d.name}/")
        console.print(table)
        console.print()

    if notes:
        table = Table(title=f"Notas ({len(notes)})", show_header=False, box=None)
        table.add_column("📄", style="cyan")
        for n in notes:
            table.add_row(n.name)
        console.print(table)
    else:
        console.print("[dim]No hay notas .md en este nivel.[/]")


def _find(vault: Path, term: str):
    """Busca notas por nombre que contengan el término."""
    if not term:
        console.print("[red]Falta el término a buscar.[/]")
        console.print('Uso: blackbelt run obsidian find "texto"')
        return

    term_lower = term.lower()
    matches = [
        p for p in vault.rglob("*.md")
        if term_lower in p.name.lower() and not _is_hidden(p, vault)
    ]

    if not matches:
        console.print(f"[dim]Sin resultados para '{term}'.[/]")
        return

    table = Table(title=f"{len(matches)} notas que contienen '{term}' en el nombre")
    table.add_column("Ruta", style="cyan")
    table.add_column("Modificada", style="dim")
    for m in matches[:50]:
        rel = m.relative_to(vault)
        mtime = datetime.fromtimestamp(m.stat().st_mtime).strftime("%Y-%m-%d")
        table.add_row(str(rel), mtime)

    console.print(table)
    if len(matches) > 50:
        console.print(f"[dim]({len(matches) - 50} resultados más)[/]")


def _grep(vault: Path, term: str):
    """Busca el término dentro del contenido de las notas."""
    if not term:
        console.print("[red]Falta el término a buscar.[/]")
        console.print('Uso: blackbelt run obsidian grep "texto"')
        return

    term_lower = term.lower()
    hits = []

    for p in vault.rglob("*.md"):
        if _is_hidden(p, vault):
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    if term_lower in line.lower():
                        hits.append((p.relative_to(vault), i, line.strip()))
        except Exception:
            continue

    if not hits:
        console.print(f"[dim]Sin resultados para '{term}'.[/]")
        return

    console.print(f"[bold]{len(hits)} coincidencias[/] para '{term}':\n")
    for rel, num, text in hits[:50]:
        snippet = text if len(text) <= 100 else text[:97] + "..."
        console.print(f"  [cyan]{rel}[/]:{num}  {snippet}")

    if len(hits) > 50:
        console.print(f"\n[dim]({len(hits) - 50} coincidencias más)[/]")


def _read(vault: Path, rel_path: str):
    """Muestra el contenido de una nota."""
    if not rel_path:
        console.print("[red]Falta la ruta de la nota.[/]")
        console.print('Uso: blackbelt run obsidian read "ruta/nota.md"')
        return

    note = vault / rel_path
    if not note.exists() or not note.is_file():
        console.print(f"[red]Nota no encontrada: {rel_path}[/]")
        return

    try:
        content = note.read_text(encoding="utf-8")
    except Exception as e:
        console.print(f"[red]Error leyendo la nota: {e}[/]")
        return

    console.print(Panel(
        f"[bold cyan]{rel_path}[/]",
        border_style="cyan",
        expand=False,
    ))
    console.print(Syntax(content, "markdown", theme="monokai", line_numbers=True))


def _info(vault: Path, rel_path: str):
    """Muestra metadatos de una nota."""
    if not rel_path:
        console.print("[red]Falta la ruta de la nota.[/]")
        console.print('Uso: blackbelt run obsidian info "ruta/nota.md"')
        return

    note = vault / rel_path
    if not note.exists() or not note.is_file():
        console.print(f"[red]Nota no encontrada: {rel_path}[/]")
        return

    stat = note.stat()
    content = note.read_text(encoding="utf-8", errors="ignore")

    table = Table(title=f"Info: {rel_path}")
    table.add_column("Campo", style="cyan")
    table.add_column("Valor", style="magenta")

    table.add_row("Ruta completa", str(note))
    table.add_row("Tamaño", f"{stat.st_size / 1024:.1f} KB")
    table.add_row(
        "Modificada",
        datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
    )
    table.add_row(
        "Creada",
        datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M"),
    )
    table.add_row("Líneas", str(content.count("\n") + 1))
    table.add_row("Palabras", str(len(content.split())))

    if content.startswith("---"):
        end = content.find("---", 3)
        if end > 0:
            fm = content[3:end].strip()
            table.add_row("Frontmatter", fm.replace("\n", " | ")[:80])

    console.print(table)


def _recent(vault: Path, n: int = 10):
    """Muestra las N notas modificadas más recientemente."""
    notes = [p for p in vault.rglob("*.md") if not _is_hidden(p, vault)]

    if not notes:
        console.print("[dim]No hay notas.[/]")
        return

    notes.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    top = notes[:n]

    table = Table(title=f"Últimas {len(top)} notas modificadas")
    table.add_column("Ruta", style="cyan")
    table.add_column("Modificada", style="magenta")
    table.add_column("Tamaño", style="dim", justify="right")

    for p in top:
        st = p.stat()
        rel = p.relative_to(vault)
        mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        size = f"{st.st_size / 1024:.1f} KB"
        table.add_row(str(rel), mtime, size)

    console.print(table)


# ============================================================
# COMANDOS DE ESCRITURA (con Gatekeeper)
# ============================================================

def _append(vault: Path, rest):
    """Añade texto al final de una nota existente. Pasa por Gatekeeper WARN."""
    parsed = _parse_two_args(rest)
    if not parsed:
        console.print("[red]Faltan argumentos.[/]")
        console.print('Uso: blackbelt run obsidian append "ruta/nota.md" "texto a añadir"')
        return

    rel_path, text = parsed
    text = _unescape(text)
    note = vault / rel_path

    if not note.exists():
        console.print(f"[red]La nota no existe: {rel_path}[/]")
        console.print("Usa [bold]new[/] para crearla.")
        return

    if not note.is_file() or note.suffix != ".md":
        console.print(f"[red]No es una nota markdown: {rel_path}[/]")
        return

    preview = text if len(text) <= 200 else text[:197] + "..."
    detail = f"Ruta: {rel_path}\nTexto a añadir al final:\n  {preview}"

    if not gatekeeper.confirm(
        gatekeeper.Risk.WARN,
        action=f"Añadir a {rel_path}",
        detail=detail,
    ):
        return

    try:
        existing = note.read_text(encoding="utf-8")
        needs_newline = existing and not existing.endswith("\n")

        with open(note, "a", encoding="utf-8") as f:
            if needs_newline:
                f.write("\n")
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")

        console.print(f"[green]Añadido a {rel_path}[/]")
    except Exception as e:
        console.print(f"[red]Error escribiendo: {e}[/]")


def _new(vault: Path, rest):
    """Crea una nota nueva. No sobrescribe si ya existe. Gatekeeper WARN."""
    parsed = _parse_two_args(rest)
    if not parsed:
        console.print("[red]Faltan argumentos.[/]")
        console.print('Uso: blackbelt run obsidian new "ruta/nota.md" "contenido inicial"')
        return

    rel_path, text = parsed
    text = _unescape(text)
    note = vault / rel_path

    if note.exists():
        console.print(f"[red]Ya existe: {rel_path}[/]")
        console.print("Usa [bold]append[/] para añadir contenido, o [bold]read[/] para verla.")
        return

    preview = text if len(text) <= 200 else text[:197] + "..."
    detail = f"Ruta: {rel_path}\nContenido inicial:\n  {preview}"

    if not gatekeeper.confirm(
        gatekeeper.Risk.WARN,
        action=f"Crear nota {rel_path}",
        detail=detail,
    ):
        return

    try:
        note.parent.mkdir(parents=True, exist_ok=True)
        content = text if text.endswith("\n") else text + "\n"
        note.write_text(content, encoding="utf-8")
        console.print(f"[green]Creada: {rel_path}[/]")
    except Exception as e:
        console.print(f"[red]Error creando nota: {e}[/]")
