import typer
from rich.console import Console
from rich.table import Table

from blackbelt.core import environment, registry, executor

app = typer.Typer(help="BlackBelt - El cinturon de herramientas CLI")
console = Console()


@app.command()
def version():
    """Muestra la version de BlackBelt."""
    console.print("[bold green]BlackBelt[/] v0.1.0 - MVP")


@app.command()
def env():
    """Detecta y muestra el entorno actual."""
    info = environment.summary()
    table = Table(title="Entorno detectado")
    table.add_column("Clave", style="cyan")
    table.add_column("Valor", style="magenta")
    for k, v in info.items():
        table.add_row(k, str(v))
    console.print(table)


@app.command()
def tools():
    """Lista las herramientas registradas."""
    table = Table(title="Herramientas registradas")
    table.add_column("Nombre", style="cyan")
    table.add_column("Descripcion", style="magenta")
    for name, meta in registry.TOOLS.items():
        table.add_row(name, meta["description"])
    console.print(table)


@app.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
def run(ctx: typer.Context, tool: str, args: list[str] = typer.Argument(None)):
    """Ejecuta una herramienta. Ej: blackbelt run linux info"""
    executor.run_tool(tool, args or [])


def main():
    app()


if __name__ == "__main__":
    main()
