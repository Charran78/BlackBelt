from rich.console import Console
from rich.prompt import Prompt
import ollama

from blackbelt.core import config as cfg
from blackbelt.core import executor

console = Console()


def run(args=None):
    """Chat interactivo con Ollama. Uso: blackbelt run chat [modelo]"""
    args = executor.args_to_str(args)
    model = args.strip() if args.strip() else cfg.OLLAMA_MODEL

    console.print(f"[bold]Chat con Ollama[/] (modelo: [cyan]{model}[/])")
    console.print("Escribe [bold]salir[/] o [bold]exit[/] para terminar.\n")

    history = [
        {
            "role": "system",
            "content": (
                "Eres BlackBelt, un asistente técnico conciso. "
                "Respondes en español, directo, sin florituras. "
                "Si no sabes algo, di 'no lo sé' en lugar de inventar. "
                "Ayudas con Linux, Python y desarrollo local."
            ),
        }
    ]

    while True:
        try:
            user_input = Prompt.ask("[bold green]tú[/]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Saliendo...[/]")
            break

        if user_input.strip().lower() in ("salir", "exit", "quit"):
            console.print("[dim]Hasta luego.[/]")
            break

        if not user_input.strip():
            continue

        history.append({"role": "user", "content": user_input})

        try:
            response = ollama.chat(
                model=model,
                messages=history,
                keep_alive=cfg.OLLAMA_KEEP_ALIVE,
                options=cfg.ollama_options(),
            )
            reply = response["message"]["content"]
        except Exception as e:
            console.print(f"[red]Error hablando con Ollama: {e}[/]")
            continue

        console.print(f"[bold cyan]blackbelt[/] {reply}\n")
        history.append({"role": "assistant", "content": reply})
