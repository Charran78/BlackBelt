"""Tests for the Companion Ollama Cloud model catalog."""

from blackbelt.knowledge.companion_cloud import (
    CLOUD_MODELS,
    get_cloud_model,
)


def test_cloud_catalog_contains_supported_tags_and_context_limits() -> None:
    assert [(model.model, model.context_tokens) for model in CLOUD_MODELS] == [
        ("gemma4:cloud", 256_000),
        ("nemotron-3-nano:30b-cloud", 1_000_000),
        ("gpt-oss:20b-cloud", 128_000),
        ("gpt-oss:120b-cloud", 128_000),
        ("deepseek-v4.1-flash:cloud", 1_000_000),
        ("glm-5.3-flash:cloud", 1_000_000),
        ("nemotron-3-super:cloud", 256_000),
        ("minimax-m2.7:cloud", 200_000),
        ("mistral-large-3:675b-cloud", 256_000),
    ]
    assert get_cloud_model("glm-5.3-flash:cloud") == CLOUD_MODELS[5]
    assert get_cloud_model("kimi-k2.7-code:cloud") is None
    assert get_cloud_model("minimax-m3:cloud") is None
    assert get_cloud_model("unlisted-model:cloud") is None
