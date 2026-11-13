"""Shared model-provider adapters used by BlackBelt services."""

from blackbelt.providers.chat import (
    ChatMessage,
    ChatRequest,
    ModelInfo,
    ModelSelection,
    ProviderError,
    discover_models,
    get_provider,
)

__all__ = [
    "ChatMessage",
    "ChatRequest",
    "ModelInfo",
    "ModelSelection",
    "ProviderError",
    "discover_models",
    "get_provider",
]
