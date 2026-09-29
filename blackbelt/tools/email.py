from rich.console import Console

from blackbelt.core import executor

console = Console()


def run(args=None):
    args = executor.args_to_str(args)
    console.print("[bold]Email[/]")
    console.print("Módulo de clasificador de correo (pendiente).")
