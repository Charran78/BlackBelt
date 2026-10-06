import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Carga el .env de la raíz del proyecto (si existe)
load_dotenv()

# ============================================================
# CONFIG GENERAL (YAML en ~/.config/blackbelt/config.yaml)
# ============================================================

CONFIG_DIR = Path.home() / ".config" / "blackbelt"
CONFIG_FILE = CONFIG_DIR / "config.yaml"


def load() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f) or {}


def save(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        yaml.safe_dump(config, f)


# ============================================================
# CONFIG DE OLLAMA (variables de entorno con defaults)
# ============================================================

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:0.5b")
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "2h")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "180"))
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "512"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "180"))

_cpu_threads = os.getenv("OLLAMA_NUM_THREAD", "").strip()
OLLAMA_NUM_THREAD = int(_cpu_threads) if _cpu_threads else None

# PATH A L VAULT DE OBSIDIAN
OBSIDIAN_VAULT = Path(
    os.getenv("OBSIDIAN_VAULT", str(Path.home() / "Obsidian"))
).expanduser()
SEARCH_EMBEDDING_MODEL = os.getenv(
    "SEARCH_EMBEDDING_MODEL",
    "nomic-embed-text",
).strip()
SEARCH_MIN_COSINE_SCORE = float(os.getenv("SEARCH_MIN_COSINE_SCORE", "0.65"))
SEARCH_INDEX_DIR = Path(
    os.getenv(
        "BLACKBELT_SEARCH_DIR",
        str(Path.home() / ".blackbelt" / "search"),
    )
).expanduser()


def ollama_options() -> dict:
    """Opciones listas para pasar al cliente ollama."""
    opts = {
        "num_ctx": OLLAMA_NUM_CTX,
        "num_predict": OLLAMA_NUM_PREDICT,
    }
    if OLLAMA_NUM_THREAD is not None:
        opts["num_thread"] = OLLAMA_NUM_THREAD
    return opts


def vault_path() -> Path:
    """Devuelve la ruta de la bóveda Obsidian, validando que exista."""
    if not OBSIDIAN_VAULT.exists():
        raise FileNotFoundError(
            f"La bóveda configurada no existe: {OBSIDIAN_VAULT}\n"
            f"Revisa OBSIDIAN_VAULT en tu .env"
        )
    return OBSIDIAN_VAULT
