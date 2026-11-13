"""Provider-neutral chat contract with isolated provider-specific adapters."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from blackbelt.providers.credentials import load_api_key

ProviderName = Literal["ollama", "groq", "openrouter"]


class ProviderError(RuntimeError):
    """A safe error raised for provider requests or discovery failures."""


@dataclass(frozen=True, slots=True)
class ModelSelection:
    """An explicit provider and model pair selected by a BlackBelt service."""

    provider: ProviderName
    model: str

    def __post_init__(self) -> None:
        if not self.model or len(self.model) > 256 or self.model.strip() != self.model:
            raise ValueError("El identificador del modelo no es válido.")


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A chat message with optional image attachments encoded as base64."""

    role: Literal["system", "user", "assistant"]
    content: str
    images: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Provider-neutral request fields used by current BlackBelt services."""

    selection: ModelSelection
    messages: tuple[ChatMessage, ...]
    temperature: float | None = None
    max_tokens: int | None = None
    response_format: Literal["json_object"] | None = None
    stream: bool = False
    keep_alive: str | None = None
    provider_options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """Discovered provider model metadata; unknown capabilities stay unknown."""

    provider: ProviderName
    model: str
    context_tokens: int | None = None
    supports_json: bool | None = None
    supports_vision: bool | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ChatProvider(Protocol):
    """Synchronous provider operations, called from service worker threads."""

    def preview_payload(self, request: ChatRequest) -> dict[str, Any]: ...

    def complete(self, request: ChatRequest) -> str: ...

    def stream(self, request: ChatRequest) -> Iterator[str]: ...

    def discover_models(self) -> list[ModelInfo]: ...


def get_provider(
    name: ProviderName,
    *,
    ollama_client: Any | None = None,
) -> ChatProvider:
    """Construct one provider adapter; no fallback or provider switching."""
    if name == "ollama":
        return OllamaProvider(ollama_client)
    if name == "groq":
        return GroqProvider()
    if name == "openrouter":
        return OpenRouterProvider()
    raise ValueError(f"Proveedor no admitido: {name}.")


def discover_models(
    name: ProviderName,
    *,
    ollama_client: Any | None = None,
) -> list[ModelInfo]:
    """Discover models only from the explicitly requested provider."""
    try:
        return get_provider(name, ollama_client=ollama_client).discover_models()
    except ProviderError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise ProviderError(
            f"No se pudo consultar el catálogo de {name} ({type(exc).__name__})."
        ) from exc


class OllamaProvider:
    """Adapter for local and Ollama Cloud models using the native SDK."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def complete(self, request: ChatRequest) -> str:
        response = self._get_client().chat(**self.preview_payload(request))
        return _ollama_content(response)

    def stream(self, request: ChatRequest) -> Iterator[str]:
        payload = self.preview_payload(request)
        response = self._get_client().chat(**payload)
        try:
            for chunk in response:
                text = _ollama_chunk_text(chunk)
                if text:
                    yield text
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def discover_models(self) -> list[ModelInfo]:
        response = self._get_client().list()
        raw_models = (
            response.get("models")
            if isinstance(response, dict)
            else getattr(response, "models", None)
        )
        models = [
            ModelInfo(provider="ollama", model=name)
            for item in raw_models or []
            if (name := _ollama_model_name(item))
        ]
        return sorted(models, key=lambda item: item.model.casefold())

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import ollama
            except ImportError as exc:
                raise ProviderError(
                    "Falta la dependencia Ollama de BlackBelt."
                ) from exc
            self._client = ollama.Client()
        return self._client

    @staticmethod
    def preview_payload(request: ChatRequest) -> dict[str, Any]:
        options = dict(request.provider_options)
        if request.temperature is not None:
            options.setdefault("temperature", request.temperature)
        if request.max_tokens is not None:
            options.setdefault("num_predict", request.max_tokens)
        payload: dict[str, Any] = {
            "model": request.selection.model,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    **({"images": list(message.images)} if message.images else {}),
                }
                for message in request.messages
            ],
            "options": options,
            "stream": request.stream,
        }
        if request.keep_alive is not None:
            payload["keep_alive"] = request.keep_alive
        if request.response_format == "json_object":
            payload["format"] = "json"
        return payload


class GroqProvider:
    """Adapter using Groq's official Python SDK."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def complete(self, request: ChatRequest) -> str:
        from groq import GroqError

        try:
            response = self._get_client().chat.completions.create(
                **self.preview_payload(request)
            )
        except GroqError as exc:
            raise ProviderError(
                f"Groq rechazó la solicitud ({type(exc).__name__})."
            ) from exc
        return _openai_content(response)

    def stream(self, request: ChatRequest) -> Iterator[str]:
        from groq import GroqError

        response: Any | None = None
        try:
            response = self._get_client().chat.completions.create(
                **self.preview_payload(request)
            )
            for chunk in response:
                text = _openai_chunk_text(chunk)
                if text:
                    yield text
        except GroqError as exc:
            raise ProviderError(
                f"Groq rechazó la solicitud ({type(exc).__name__})."
            ) from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def discover_models(self) -> list[ModelInfo]:
        from groq import GroqError

        try:
            response = self._get_client().models.list()
        except GroqError as exc:
            raise ProviderError(
                f"No se pudo consultar el catálogo de Groq "
                f"({type(exc).__name__})."
            ) from exc
        models = [
            ModelInfo(
                provider="groq",
                model=item.id,
                context_tokens=getattr(item, "context_window", None),
            )
            for item in getattr(response, "data", [])
            if isinstance(getattr(item, "id", None), str)
        ]
        return sorted(models, key=lambda item: item.model.casefold())

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from groq import Groq, GroqError
            except ImportError as exc:
                raise ProviderError(
                    "Falta el SDK oficial de Groq. Reinstala las dependencias de "
                    "BlackBelt."
                ) from exc
            try:
                self._client = Groq(
                    api_key=load_api_key("groq"),
                    timeout=httpx.Timeout(300.0, connect=15.0),
                    max_retries=2,
                )
            except GroqError as exc:
                raise ProviderError(
                    f"No se pudo inicializar Groq ({type(exc).__name__})."
                ) from exc
        return self._client

    @staticmethod
    def preview_payload(
        request: ChatRequest,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.selection.model,
            "messages": _openai_messages(request.messages),
            "stream": request.stream,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_completion_tokens"] = request.max_tokens
        if request.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        effort = request.provider_options.get("reasoning_effort")
        if effort in {"low", "medium", "high"}:
            payload["reasoning_effort"] = effort
        return payload


class OpenRouterProvider:
    """Adapter for OpenRouter's documented OpenAI-compatible HTTP API."""

    _BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def complete(self, request: ChatRequest) -> str:
        response = self._get_client().post(
            f"{self._BASE_URL}/chat/completions",
            json=self.preview_payload(request),
        )
        response.raise_for_status()
        return _openai_content(response.json())

    def stream(self, request: ChatRequest) -> Iterator[str]:
        payload = self.preview_payload(request)
        with self._get_client().stream(
            "POST",
            f"{self._BASE_URL}/chat/completions",
            json=payload,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ProviderError(
                        "OpenRouter devolvió un fragmento de streaming inválido."
                    ) from exc
                text = _openai_chunk_text(chunk)
                if text:
                    yield text

    def discover_models(self) -> list[ModelInfo]:
        response = self._get_client().get(f"{self._BASE_URL}/models")
        response.raise_for_status()
        payload = response.json()
        models: list[ModelInfo] = []
        for item in payload.get("data", []):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            architecture = item.get("architecture")
            modalities = (
                architecture.get("input_modalities", [])
                if isinstance(architecture, dict)
                else []
            )
            parameters = item.get("supported_parameters", [])
            context_length = item.get("context_length")
            models.append(
                ModelInfo(
                    provider="openrouter",
                    model=item["id"],
                    context_tokens=(
                        context_length if isinstance(context_length, int) else None
                    ),
                    supports_json=(
                        "response_format" in parameters
                        if isinstance(parameters, list)
                        else None
                    ),
                    supports_vision=(
                        "image" in modalities if isinstance(modalities, list) else None
                    ),
                    metadata={
                        key: item[key]
                        for key in ("name", "pricing")
                        if key in item
                    },
                )
            )
        return sorted(models, key=lambda item: item.model.casefold())

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={
                    "Authorization": f"Bearer {load_api_key('openrouter')}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://blackbelt.local",
                    "X-Title": "BlackBelt",
                },
                timeout=httpx.Timeout(300.0, connect=15.0),
                trust_env=False,
            )
        return self._client

    @staticmethod
    def preview_payload(
        request: ChatRequest,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.selection.model,
            "messages": _openai_messages(request.messages),
            "stream": request.stream,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        return payload


def _openai_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        if not message.images:
            content: str | list[dict[str, Any]] = message.content
        else:
            content = [
                {"type": "text", "text": message.content},
                *[
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(image)},
                    }
                    for image in message.images
                ],
            ]
        result.append({"role": message.role, "content": content})
    return result


def _image_data_url(image: str) -> str:
    if image.startswith("data:image/"):
        return image
    return f"data:image/jpeg;base64,{image}"


def _ollama_model_name(item: Any) -> str | None:
    name = (
        item.get("model") or item.get("name")
        if isinstance(item, dict)
        else getattr(item, "model", None) or getattr(item, "name", None)
    )
    return name.strip() if isinstance(name, str) and name.strip() else None


def _ollama_content(response: Any) -> str:
    message = (
        response.get("message")
        if isinstance(response, dict)
        else getattr(response, "message", None)
    )
    content = (
        message.get("content")
        if isinstance(message, dict)
        else getattr(message, "content", None)
    )
    if not isinstance(content, str):
        raise ProviderError("Ollama no devolvió contenido textual.")
    return content


def _ollama_chunk_text(chunk: Any) -> str:
    try:
        return _ollama_content(chunk)
    except ProviderError:
        return ""


def _openai_content(response: Any) -> str:
    choices = (
        response.get("choices")
        if isinstance(response, dict)
        else getattr(response, "choices", None)
    )
    if not choices:
        raise ProviderError("El proveedor no devolvió opciones de respuesta.")
    first = choices[0]
    message = (
        first.get("message")
        if isinstance(first, dict)
        else getattr(first, "message", None)
    )
    content = (
        message.get("content")
        if isinstance(message, dict)
        else getattr(message, "content", None)
    )
    if not isinstance(content, str):
        raise ProviderError("El proveedor no devolvió contenido textual.")
    return content


def _openai_chunk_text(chunk: Any) -> str:
    choices = (
        chunk.get("choices")
        if isinstance(chunk, dict)
        else getattr(chunk, "choices", None)
    )
    if not choices:
        return ""
    first = choices[0]
    delta = (
        first.get("delta")
        if isinstance(first, dict)
        else getattr(first, "delta", None)
    )
    content = (
        delta.get("content")
        if isinstance(delta, dict)
        else getattr(delta, "content", None)
    )
    return content if isinstance(content, str) else ""
