import time
from collections import deque

import psutil
from rich.console import Console
from rich.table import Table

from blackbelt.core import executor

try:
    import plotext as plt
    HAS_PLOTEXT = True
except ImportError:
    HAS_PLOTEXT = False

console = Console()

HISTORY_SIZE = 60
REFRESH_SECONDS = 1.0


def run(args: list[str] | str | None = None) -> None:
    """Snapshot o monitor en vivo. Uso: blackbelt run graphics [snapshot|monitor]"""
    args = executor.args_to_str(args)
    parts = args.split()
    mode = parts[0] if parts else "snapshot"

    if mode == "monitor":
        monitor()
    elif mode == "snapshot":
        snapshot()
    else:
        console.print(f"[red]Modo no reconocido: {mode}[/]")
        console.print("Uso: blackbelt run graphics [snapshot|monitor]")



# ============================================================
# SNAPSHOT (sin cambios — usa psutil y Rich, no plotext)
# ============================================================

def snapshot() -> None:
    table = Table(title="Estado del sistema")
    table.add_column("Recurso", style="cyan")
    table.add_column("Valor", style="magenta")

    cpu_pct = psutil.cpu_percent(interval=1)
    cpu_threads = psutil.cpu_count(logical=True)
    table.add_row("CPU", f"{cpu_pct:.1f}%  ({cpu_threads} hilos)")

    mem = psutil.virtual_memory()
    table.add_row(
        "RAM",
        f"{mem.used / 1024**3:.2f} / {mem.total / 1024**3:.2f} GB  ({mem.percent:.1f}%)",
    )

    try:
        swap = psutil.swap_memory()
        if swap.total > 0:
            table.add_row(
                "Swap",
                f"{swap.used / 1024**3:.2f} / {swap.total / 1024**3:.2f} GB  ({swap.percent:.1f}%)",
            )
    except (OSError, psutil.Error, NotImplementedError):
        pass

    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            table.add_row(
                f"Disco {part.mountpoint}",
                f"{usage.used / 1024**3:.1f} / {usage.total / 1024**3:.1f} GB  ({usage.percent:.1f}%)",
            )
        except (PermissionError, OSError):
            continue

    net = psutil.net_io_counters()
    table.add_row(
        "Red",
        f"↓ {net.bytes_recv / 1024**2:.1f} MB   ↑ {net.bytes_sent / 1024**2:.1f} MB",
    )

    try:
        temps = psutil.sensors_temperatures()
        for name, entries in temps.items():
            if entries:
                t = entries[0]
                table.add_row(f"Temp {name}", f"{t.current:.0f}°C")
    except (OSError, psutil.Error, NotImplementedError):
        pass

    console.print(table)


# ============================================================
# MONITOR EN VIVO — API plotext 6.1.0
# ============================================================

def monitor() -> None:
    """Bucle en vivo con gráficas de CPU y RAM. Salir con Ctrl+C."""
    if not HAS_PLOTEXT:
        console.print("[red]plotext no está instalado.[/]")
        console.print("Instálalo con: [bold]pip install plotext[/]")
        return

    cpu_hist = deque([0.0] * HISTORY_SIZE, maxlen=HISTORY_SIZE)
    ram_hist = deque([0.0] * HISTORY_SIZE, maxlen=HISTORY_SIZE)

    psutil.cpu_percent(interval=None)  # referencia

    # --- Configurar figura (API v6) ---
    fig = plt.figure
    fig.clear()
    fig.title("CPU / RAM")

    frame = 0

    console.print("[bold]Monitor en vivo[/] — [dim]Ctrl+C para salir[/]")
    time.sleep(0.8)

    try:
        while True:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()

            cpu_hist.append(cpu)
            ram_hist.append(mem.percent)

            # Tamaño de terminal actualizado
            w, h = plt.terminal.size(update=True)

            # Limpiar frame anterior (a partir del segundo)
            if frame > 0:
                plt.terminal.clean(h)

            # Limpiar solo datos, conservar ejes/título
            fig.clear.data()

            # Adaptar al terminal (dejar 1 fila para el resumen)
            fig.plot_size(w, max(6, h - 4))

            # Crear señal de CPU (línea continua)
            cpu_signal = fig.signal(list(cpu_hist)).lines()
            cpu_signal.color = "cyan"

            # Crear señal de RAM (línea continua)
            ram_signal = fig.signal(list(ram_hist)).lines()
            ram_signal.color = "green"

            # Dibujar ambas señales
            fig.draw(cpu_signal)
            fig.draw(ram_signal)

            # Etiquetas
            fig.ruler("y").lim(0, 100)
            fig.label("CPU (cyan) / RAM (green)", axis=1)

            # Renderizar todo de una vez
            fig.show(flush=True)

            # Resumen textual debajo
            print(
                f"  CPU: {cpu:5.1f}%   RAM: {mem.percent:5.1f}%   "
                f"[Ctrl+C para salir]"
            )

            frame += 1
            time.sleep(REFRESH_SECONDS)

    except KeyboardInterrupt:
        print("\033[H\033[J", end="")  # limpiar pantalla
        console.print("[dim]Monitor detenido.[/]")


if __name__ == "__main__":
    run()
