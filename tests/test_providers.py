from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from blackbelt.providers.chat import (
    ChatMessage,
    ChatRequest,
    GroqProvider,
    ModelSelection,
    OllamaProvider,
    OpenRouterProvider,
    ProviderError,
)


def test_model_selection_rejects_whitespace_and_oversized_names() -> None:
    with pytest.raises(ValueError):
        ModelSelection(provider="groq", model=" gpt-oss-120b")
    with pytest.raises(ValueError):
        ModelSelection(provider="groq", model="x" * 257)


def test_ollama_adapter_preserves_native_options_and_json_mode() -> None:
    captured: dict[str, Any] = {}

    class FakeOllama:
        def chat(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"message": {"content": '{"ok": true}'}}

    request = ChatRequest(
        selection=ModelSelection("ollama", "model:cloud"),
        messages=(ChatMessage("user", "test"),),
        response_format="json_object",
        keep_alive="0",
        provider_options={"num_ctx": 4096},
    )

    result = OllamaProvider(FakeOllama()).complete(request)

    assert result == '{"ok": true}'
    assert captured["format"] == "json"
    assert captured["options"] == {"num_ctx": 4096}
    assert captured["keep_alive"] == "0"


def test_ollama_adapter_closes_stream_when_consumer_stops() -> None:
    class FakeStream:
        closed = False

        def __iter__(self) -> Iterator[dict[str, Any]]:
            yield {"message": {"content": "hola"}}
            yield {"message": {"content": " mundo"}}

        def close(self) -> None:
            self.closed = True

    stream = FakeStream()

    class FakeOllama:
        def chat(self, **_kwargs: Any) -> FakeStream:
            return stream

    chunks = OllamaProvider(FakeOllama()).stream(
        ChatRequest(
            selection=ModelSelection("ollama", "local-model"),
            messages=(ChatMessage("user", "test"),),
            stream=True,
        )
    )

    assert next(chunks) == "hola"
    chunks.close()
    assert stream.closed


def test_groq_adapter_uses_official_chat_completion_contract() -> None:
    captured: dict[str, Any] = {}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="respuesta"))]
    )

    class FakeCompletions:
        def create(self, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return response

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions()),
    )
    request = ChatRequest(
        selection=ModelSelection("groq", "openai/gpt-oss-120b"),
        messages=(ChatMessage("user", "pregunta"),),
        max_tokens=512,
        response_format="json_object",
        provider_options={"reasoning_effort": "low"},
    )

    assert GroqProvider(client).complete(request) == "respuesta"
    assert captured["model"] == "openai/gpt-oss-120b"
    assert captured["max_completion_tokens"] == 512
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["reasoning_effort"] == "low"


def test_groq_sdk_errors_are_normalized_without_exposing_provider_details() -> None:
    from groq import GroqError

    class FakeCompletions:
        def create(self, **_kwargs: Any) -> None:
            raise GroqError("sensitive provider response")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions()),
    )
    request = ChatRequest(
        selection=ModelSelection("groq", "openai/gpt-oss-120b"),
        messages=(ChatMessage("user", "pregunta"),),
    )

    with pytest.raises(ProviderError, match="Groq rechazó la solicitud") as error:
        GroqProvider(client).complete(request)

    assert "sensitive provider response" not in str(error.value)


def test_openrouter_adapter_discovers_supported_capabilities() -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "data": [
                    {
                        "id": "provider/model",
                        "context_length": 32768,
                        "architecture": {"input_modalities": ["text", "image"]},
                        "supported_parameters": ["response_format"],
                        "pricing": {"prompt": "0"},
                    }
                ]
            }

    class FakeClient:
        def get(self, url: str) -> FakeResponse:
            assert url == "https://openrouter.ai/api/v1/models"
            return FakeResponse()

    models = OpenRouterProvider(FakeClient()).discover_models()

    assert models[0].model == "provider/model"
    assert models[0].context_tokens == 32768
    assert models[0].supports_json is True
    assert models[0].supports_vision is True
    assert models[0].metadata["pricing"]["prompt"] == "0"


def test_openai_message_conversion_keeps_text_and_image() -> None:
    from blackbelt.providers.chat import _openai_messages

    result = _openai_messages(
        (ChatMessage("user", "describe", images=("Zm9v",)),)
    )

    assert result == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,Zm9v"},
                },
            ],
        }
    ]


def test_invalid_provider_is_rejected_without_fallback() -> None:
    from blackbelt.providers.chat import get_provider

    with pytest.raises(ValueError, match="Proveedor no admitido"):
        get_provider("other")  # type: ignore[arg-type]
