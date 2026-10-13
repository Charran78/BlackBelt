"""Supported Ollama Cloud models and their bounded metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CloudModel:
    """An allowlisted Ollama Cloud model supported by Companion."""

    model: str
    label: str
    context_tokens: int
    context_description: str


CLOUD_MODELS: tuple[CloudModel, ...] = (
    CloudModel(
        model="gemma4:cloud",
        label="Gemma 4",
        context_tokens=256_000,
        context_description="256K",
    ),
    CloudModel(
        model="nemotron-3-nano:30b-cloud",
        label="Nemotron 3 Nano 30B",
        context_tokens=1_000_000,
        context_description="1M",
    ),
    CloudModel(
        model="gpt-oss:20b-cloud",
        label="GPT OSS 20B",
        context_tokens=128_000,
        context_description="128K",
    ),
    CloudModel(
        model="gpt-oss:120b-cloud",
        label="GPT OSS 120B",
        context_tokens=128_000,
        context_description="128K",
    ),
    CloudModel(
        model="deepseek-v4.1-flash:cloud",
        label="DeepSeek V4.1 Flash",
        context_tokens=1_000_000,
        context_description="1M",
    ),
    CloudModel(
        model="glm-5.3-flash:cloud",
        label="GLM-5.3 Flash",
        context_tokens=1_000_000,
        context_description="1M",
    ),
    CloudModel(
        model="nemotron-3-super:cloud",
        label="Nemotron 3 Super",
        context_tokens=256_000,
        context_description="256K",
    ),
    CloudModel(
        model="minimax-m2.7:cloud",
        label="MiniMax M2.7",
        context_tokens=200_000,
        context_description="200K",
    ),
    CloudModel(
        model="mistral-large-3:675b-cloud",
        label="Mistral Large 3 675B",
        context_tokens=256_000,
        context_description="256K",
    ),
)


def get_cloud_model(model: str) -> CloudModel | None:
    """Return catalog metadata only for explicitly supported Cloud models."""
    return next((item for item in CLOUD_MODELS if item.model == model), None)
