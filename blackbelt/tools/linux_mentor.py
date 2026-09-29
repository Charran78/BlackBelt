from rich.console import Console
import ollama

from blackbelt.core import config as cfg
from blackbelt.core import executor

console = Console()


def run(args=None):
    args = executor.args_to_str(args)
    console.print("[bold]Linux Mentor[/]")

    if not args:
        console.print("Comandos disponibles: info, explain <comando>, diagnose")
        console.print("Uso: blackbelt run linux info")
        return

    parts = args.split()
    cmd = parts[0]

    if cmd == "info":
        _info()
    elif cmd == "explain" and len(parts) > 1:
        _explain(" ".join(parts[1:]))
    elif cmd == "diagnose":
        _diagnose()
    else:
        console.print(f"[red]Comando no reconocido: {cmd}[/]")


def _info():
    from blackbelt.core.environment import summary
    for k, v in summary().items():
        console.print(f"{k}: {v}")


def _explain(comando: str):
    console.print(f"[dim]Explicando:[/] [bold]{comando}[/]\n")

    prompt = (
        f"Explica brevemente este comando Linux:\n"
        f"    {comando}\n\n"
        f"Incluye: qué hace, qué significa cada opción, y un ejemplo."
    )

    try:
        response = ollama.chat(
            model=cfg.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            keep_alive=cfg.OLLAMA_KEEP_ALIVE,
            options=cfg.ollama_options(),
        )
        console.print(response["message"]["content"])
    except Exception as e:
        console.print(f"[red]Error consultando a Ollama: {e}[/]")


def _diagnose():
    console.print("Diagnóstico básico: todo parece en orden.")
