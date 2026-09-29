import os
import platform
import sys
import shutil


def detect_os() -> str:
    return platform.system()


def detect_distro() -> str:
    if platform.system() != "Linux":
        return platform.system()
    try:
        with open("/etc/os-release") as f:
            data = {}
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    data[k] = v.strip('"')
        return data.get("PRETTY_NAME", data.get("NAME", "Linux desconocida"))
    except Exception:
        return "Linux (distro desconocida)"


def detect_shell() -> str:
    return os.environ.get("SHELL", "desconocido")


def detect_python() -> str:
    return sys.version.split()[0]


def detect_ollama() -> bool:
    return shutil.which("ollama") is not None


def summary() -> dict:
    return {
        "OS": detect_os(),
        "Distro": detect_distro(),
        "Shell": detect_shell(),
        "Python": detect_python(),
        "Ollama": "OK" if detect_ollama() else "NO",
    }
