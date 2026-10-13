"""Explicit third-party web reading for Companion."""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

_JINA_READER_ENDPOINT = "https://r.jina.ai/"
_JINA_SEARCH_ENDPOINT = "https://s.jina.ai/"
_MAX_URL_CHARS = 2_048
_MAX_SEARCH_QUERY_CHARS = 300
_MAX_RESPONSE_BYTES = 512 * 1_024
_MAX_CONTENT_CHARS = 24_000
_MAX_SEARCH_RESULTS = 5
_MAX_SEARCH_SNIPPET_CHARS = 800
_JINA_TIMEOUT_SECONDS = 45.0
_PRIVATE_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".test")


class JinaTargetError(ValueError):
    """The supplied target is not an acceptable public HTTP(S) URL."""


class JinaServiceError(RuntimeError):
    """A Jina endpoint did not return a usable bounded response."""


class JinaReaderError(JinaServiceError):
    """Jina Reader could not return a usable bounded extraction."""


class JinaSearchError(JinaServiceError):
    """Jina Search could not return usable public search results."""


class JinaSearchUnavailableError(JinaSearchError):
    """Jina Search cannot be used without a configured API key."""


@dataclass(frozen=True)
class JinaDocument:
    """A bounded, transient extraction with source provenance."""

    url: str
    title: str
    content: str
    truncated: bool


@dataclass(frozen=True)
class JinaSearchResult:
    """A bounded public result returned by Jina Search."""

    title: str
    url: str
    snippet: str


def validate_public_url(value: str) -> str:
    """Normalize an HTTP(S) URL while rejecting local and credentialed targets."""
    candidate = value.strip()
    if not candidate or len(candidate) > _MAX_URL_CHARS:
        raise JinaTargetError("La URL debe tener entre 1 y 2.048 caracteres.")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise JinaTargetError("La URL no tiene un formato válido.") from exc

    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").rstrip(".").casefold()
    if scheme not in {"http", "https"} or not host:
        raise JinaTargetError("Solo se admiten direcciones web HTTP o HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise JinaTargetError("La URL no puede incluir credenciales.")
    if port is not None and port != (443 if scheme == "https" else 80):
        raise JinaTargetError("Solo se admiten los puertos web estándar.")
    if host == "localhost" or host.endswith(_PRIVATE_HOST_SUFFIXES):
        raise JinaTargetError("No se admiten direcciones locales o privadas.")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise JinaTargetError("No se admiten direcciones IP locales o reservadas.")

    return urlunsplit((scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def read_with_jina(
    value: str,
    *,
    token_budget: int = 4_000,
    transport: httpx.BaseTransport | None = None,
) -> JinaDocument:
    """Read one approved public URL without credentials and within a bounded budget."""
    if token_budget not in {4_000, 15_000}:
        raise ValueError(
            "Jina Reader solo admite presupuestos de 4.000 o 15.000 tokens."
        )
    target_url = validate_public_url(value)
    encoded_url = quote(target_url, safe=":/")
    headers = {
        "Accept": "application/json",
        "X-Return-Format": "markdown",
        "X-Token-Budget": str(token_budget),
    }

    payload = _request_json(
        f"{_JINA_READER_ENDPOINT}{encoded_url}",
        headers=headers,
        transport=transport,
        endpoint_name="Jina Reader",
        error_type=JinaReaderError,
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise JinaReaderError("Jina no devolvió el documento solicitado.")
    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        raise JinaReaderError("Jina no encontró contenido legible en esa página.")

    source_url = data.get("url")
    if not isinstance(source_url, str):
        source_url = target_url
    try:
        source_url = validate_public_url(source_url)
    except JinaTargetError as exc:
        raise JinaReaderError("Jina devolvió una URL de origen no válida.") from exc
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        title = source_url
    truncated = len(content) > _MAX_CONTENT_CHARS
    return JinaDocument(
        url=source_url,
        title=title.strip()[:300],
        content=content[:_MAX_CONTENT_CHARS],
        truncated=truncated,
    )


def search_with_jina(
    query: str,
    *,
    api_key: str = "",
    transport: httpx.BaseTransport | None = None,
) -> tuple[JinaSearchResult, ...]:
    """Search the web explicitly and return bounded, selectable public results."""
    normalized_query = query.strip()
    if not normalized_query or len(normalized_query) > _MAX_SEARCH_QUERY_CHARS:
        raise JinaSearchError("La consulta debe tener entre 1 y 300 caracteres.")
    if not api_key.strip():
        raise JinaSearchUnavailableError(
            "Jina Search requiere una API key. Configura JINA_API_KEY "
            "en el entorno local del servidor."
        )

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key.strip()}",
    }
    encoded_query = quote(normalized_query, safe="")
    payload = _request_json(
        f"{_JINA_SEARCH_ENDPOINT}?q={encoded_query}",
        headers=headers,
        transport=transport,
        endpoint_name="Jina Search",
        error_type=JinaSearchError,
    )
    raw_results = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        raise JinaSearchError("Jina Search devolvió resultados con formato no válido.")

    results: list[JinaSearchResult] = []
    seen_urls: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        raw_url = item.get("url")
        if not isinstance(raw_url, str):
            continue
        try:
            result_url = validate_public_url(raw_url)
        except JinaTargetError:
            continue
        if result_url in seen_urls:
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            title = result_url
        snippet = item.get("description") or item.get("content") or ""
        if not isinstance(snippet, str):
            snippet = ""
        results.append(
            JinaSearchResult(
                title=title.strip()[:300],
                url=result_url,
                snippet=snippet.strip()[:_MAX_SEARCH_SNIPPET_CHARS],
            )
        )
        seen_urls.add(result_url)
        if len(results) == _MAX_SEARCH_RESULTS:
            break

    if not results:
        raise JinaSearchError("Jina Search no encontró resultados web públicos.")
    return tuple(results)


def _request_json(
    url: str,
    *,
    headers: dict[str, str],
    transport: httpx.BaseTransport | None,
    endpoint_name: str,
    error_type: type[JinaServiceError],
) -> Any:
    try:
        with (
            httpx.Client(
                timeout=_JINA_TIMEOUT_SECONDS,
                trust_env=False,
                follow_redirects=False,
                transport=transport,
            ) as client,
            client.stream("GET", url, headers=headers) as response,
        ):
            response_body = bytearray()
            for chunk in response.iter_bytes():
                response_body.extend(chunk)
                if len(response_body) > _MAX_RESPONSE_BYTES:
                    if response.is_success:
                        raise error_type(
                            f"{endpoint_name}: respuesta superior a 512 KiB."
                        )
                    del response_body[_MAX_RESPONSE_BYTES:]
                    break
            if response.is_error:
                status_code = response.status_code
                if status_code == 429:
                    raise error_type(
                        f"{endpoint_name} ha alcanzado su límite de solicitudes."
                    )
                if endpoint_name == "Jina Search" and status_code in {401, 403}:
                    raise JinaSearchUnavailableError(
                        "Jina Search rechazó la API key. Comprueba JINA_API_KEY "
                        "y los permisos de la cuenta."
                    )
                detail = _provider_error_detail(response_body)
                suffix = f": {detail}" if detail else ""
                raise error_type(
                    f"{endpoint_name} respondió con HTTP {status_code}{suffix}."
                )
    except httpx.TimeoutException as exc:
        raise error_type(f"{endpoint_name} agotó el tiempo de espera.") from exc
    except httpx.HTTPError as exc:
        raise error_type(f"No se pudo conectar con {endpoint_name}.") from exc

    try:
        return json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise error_type(
            f"{endpoint_name} devolvió una respuesta JSON no válida."
        ) from exc


def _provider_error_detail(response_body: bytearray) -> str:
    try:
        payload = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    detail = payload.get("readableMessage") or payload.get("message")
    if not isinstance(detail, str):
        return ""
    return " ".join(detail.split())[:500]
