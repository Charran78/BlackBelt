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
COMPANION_INDEX_DIR = Path(
    os.getenv(
        "BLACKBELT_COMPANION_INDEX_DIR",
        str(Path.home() / ".blackbelt" / "companion" / "search"),
    )
).expanduser()
COMPANION_CONTRACT_FILE = Path(
    os.getenv(
        "COMPANION_CONTRACT_FILE",
        str(Path.home() / ".blackbelt" / "companion" / "contract.md"),
    )
).expanduser()
COMPANION_CLOUD_REVIEW_FILE = Path(
    os.getenv(
        "COMPANION_CLOUD_REVIEW_FILE",
        str(Path.home() / ".blackbelt" / "plan-cloud-review.yaml"),
    )
).expanduser()
COMPANION_CONVERSATION_DIR = "024 - CHAT_BD"
COMPANION_SOURCE_DIRS = (COMPANION_CONVERSATION_DIR, "025 - MIS_NOTAS")
COMPANION_MAX_NOTE_BYTES = 16 * 1024 * 1024
COMPANION_EMBED_KEEP_ALIVE = os.getenv("COMPANION_EMBED_KEEP_ALIVE", "2m").strip()
COMPANION_EMBEDDING_TIMEOUT_SECONDS = int(
    os.getenv("COMPANION_EMBEDDING_TIMEOUT_SECONDS", "900")
)
COMPANION_INDEX_BATCH_SIZE = int(os.getenv("COMPANION_INDEX_BATCH_SIZE", "5"))
COMPANION_INDEX_EMBED_BATCH_SIZE = int(
    os.getenv("COMPANION_INDEX_EMBED_BATCH_SIZE", "4")
)
COMPANION_INDEX_WORK_INTERVAL_SECONDS = float(
    os.getenv("COMPANION_INDEX_WORK_INTERVAL_SECONDS", "60")
)
COMPANION_INDEX_COOLDOWN_SECONDS = float(
    os.getenv("COMPANION_INDEX_COOLDOWN_SECONDS", "60")
)
COMPANION_EMBED_NUM_THREAD = int(os.getenv("COMPANION_EMBED_NUM_THREAD", "2"))
COMPANION_DEEP_WINDOW_CHARS = int(os.getenv("COMPANION_DEEP_WINDOW_CHARS", "4000"))
COMPANION_DEEP_WINDOW_OVERLAP = int(os.getenv("COMPANION_DEEP_WINDOW_OVERLAP", "300"))
COMPANION_DEEP_MAX_CONVERSATIONS = int(
    os.getenv("COMPANION_DEEP_MAX_CONVERSATIONS", "3")
)
COMPANION_LOCAL_MODEL = os.getenv("COMPANION_LOCAL_MODEL", "").strip()
COMPANION_CLOUD_MODEL = os.getenv(
    "COMPANION_CLOUD_MODEL",
    os.getenv("PLAN_CLOUD_MODEL", "gpt-oss:120b-cloud"),
).strip()
COMPANION_NUM_CTX = int(os.getenv("COMPANION_NUM_CTX", "4096"))
COMPANION_NUM_PREDICT = int(os.getenv("COMPANION_NUM_PREDICT", "1024"))
COMPANION_GENERATION_TIMEOUT_SECONDS = int(
    os.getenv("COMPANION_GENERATION_TIMEOUT_SECONDS", "900")
)
PLAN_DRAFT_DIR = Path(
    os.getenv(
        "BLACKBELT_PLAN_DRAFT_DIR",
        str(Path.home() / ".blackbelt" / "plans" / "drafts"),
    )
).expanduser()
PLAN_AUDIT_FILE = Path(
    os.getenv(
        "BLACKBELT_PLAN_AUDIT_FILE",
        str(Path.home() / ".blackbelt" / "audit" / "plan.jsonl"),
    )
).expanduser()
PLAN_CLOUD_REVIEW_FILE = Path(
    os.getenv(
        "BLACKBELT_PLAN_CLOUD_REVIEW_FILE",
        str(Path.home() / ".blackbelt" / "plan-cloud-review.yaml"),
    )
).expanduser()
PLAN_LOCAL_MODEL = os.getenv("PLAN_LOCAL_MODEL", "qwen2.5:1.5b").strip()
PLAN_CLOUD_MODEL = os.getenv(
    "PLAN_CLOUD_MODEL",
    os.getenv("GHOSTWRITER_OLLAMA_CLOUD_MODEL", "gpt-oss:120b-cloud"),
).strip()
PLAN_NUM_CTX = int(os.getenv("PLAN_NUM_CTX", "4096"))
PLAN_NUM_PREDICT = int(os.getenv("PLAN_NUM_PREDICT", "3000"))


def ollama_options() -> dict:
    """Opciones listas para pasar al cliente ollama."""
    opts = {
        "num_ctx": OLLAMA_NUM_CTX,
        "num_predict": OLLAMA_NUM_PREDICT,
    }
    if OLLAMA_NUM_THREAD is not None:
        opts["num_thread"] = OLLAMA_NUM_THREAD
    return opts


def plan_ollama_options() -> dict:
    """Return planning-specific generation limits without changing other apps."""
    opts = ollama_options()
    opts.update(
        {
            "num_ctx": PLAN_NUM_CTX,
            "num_predict": PLAN_NUM_PREDICT,
        }
    )
    return opts


def vault_path() -> Path:
    """Devuelve la ruta de la bóveda Obsidian, validando que exista."""
    if not OBSIDIAN_VAULT.exists():
        raise FileNotFoundError(
            f"La bóveda configurada no existe: {OBSIDIAN_VAULT}\n"
            f"Revisa OBSIDIAN_VAULT en tu .env"
        )
    return OBSIDIAN_VAULT
