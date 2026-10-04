import importlib
from rich.console import Console

from blackbelt.core import registry

console = Console()


def args_to_str(args) -> str:
    """Convierte la lista de args en un string único (para tools simples)."""
    if isinstance(args, str):
        return args
    if not args:
        return ""
    return " ".join(args)


def run_tool(tool_name: str, args=None):
    if args is None:
        args = []

    if tool_name not in registry.TOOLS:
        if tool_name == "env":
            console.print("`env` es un comando principal. Usa: blackbelt env")
            return
        console.print(f"[red]Herramienta {tool_name} no encontrada.[/]")
        return

    module_path = registry.TOOLS[tool_name]["module"]
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        console.print(f"[red]Error importando {module_path}: {e}[/]")
        return

    if not hasattr(module, "run"):
        console.print(f"[red]El modulo {module_path} no tiene funcion run().[/]")
        return

    module.run(args)
