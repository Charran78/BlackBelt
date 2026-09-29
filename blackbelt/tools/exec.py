"""Ejecuta comandos de shell pasándolos por el Gatekeeper."""

from rich.console import Console

from blackbelt.core import executor
from blackbelt.core import gatekeeper

console = Console()


def run(args=None):
    """Ejecuta un comando de shell. Uso: blackbelt run exec "ls -la"."""
    args = executor.args_to_str(args)
    cmd = args.strip()

    if not cmd:
        console.print("[red]Falta el comando a ejecutar.[/]")
        console.print('Uso: blackbelt run exec "ls -la"')
        return

    rc, out, err = gatekeeper.run_command(cmd)

    if out:
        console.print(out, end="")
    if err:
        console.print(f"[red]{err}[/]", end="")

    if rc == -1:
        console.print("[yellow]Ejecución cancelada por el Gatekeeper.[/]")
