"""Tests for explicit Companion web-source readers."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from blackbelt.knowledge.companion_web_sources import (
    JinaReaderError,
    JinaSearchError,
    JinaSearchUnavailableError,
    JinaTargetError,
    read_with_jina,
    search_with_jina,
    validate_public_url,
)


def test_reader_requests_json_without_requiring_an_api_key() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "url": "https://example.com/article",
                    "title": "Example article",
                    "content": "Extracted page content.",
                }
            },
        )

    document = read_with_jina(
        "https://example.com/article?topic=blackbelt",
        transport=httpx.MockTransport(respond),
    )

    assert document.url == "https://example.com/article"
    assert document.title == "Example article"
    assert document.content == "Extracted page content."
    assert not document.truncated
    assert requests[0].url.host == "r.jina.ai"
    assert requests[0].headers["accept"] == "application/json"
    assert "authorization" not in requests[0].headers
    assert requests[0].headers["x-token-budget"] == "4000"
    assert "%3Ftopic%3Dblackbelt" in str(requests[0].url)


def test_reader_allows_one_explicit_larger_budget_without_api_key() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "url": "https://example.com/article",
                    "title": "A" * 400,
                    "content": "x" * 25_000,
                }
            },
        )

    document = read_with_jina(
        "https://example.com/article",
        token_budget=15_000,
        transport=httpx.MockTransport(respond),
    )

    assert requests[0].headers["x-token-budget"] == "15000"
    assert "authorization" not in requests[0].headers
    assert len(document.title) == 300
    assert len(document.content) == 24_000
    assert document.truncated


def test_reader_rejects_unapproved_token_budgets() -> None:
    with pytest.raises(ValueError, match="4.000 o 15.000"):
        read_with_jina("https://example.com/", token_budget=20_000)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/",
        "http://127.0.0.1/",
        "http://10.0.0.8/",
        "http://[::1]/",
        "https://user:password@example.com/",
        "https://example.com:8443/",
    ],
)
def test_reader_rejects_non_public_or_credentialed_urls(url: str) -> None:
    with pytest.raises(JinaTargetError):
        validate_public_url(url)


def test_reader_translates_rate_limit_and_invalid_content_errors() -> None:
    with pytest.raises(JinaReaderError, match="límite de solicitudes"):
        read_with_jina(
            "https://example.com/",
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(429),
            ),
        )

    with pytest.raises(JinaReaderError, match="JSON no válida"):
        read_with_jina(
            "https://example.com/",
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"not-json"),
            ),
        )


def test_reader_preserves_bounded_provider_details_for_budget_rejections() -> None:
    provider_message = (
        "BudgetExceededError: Token budget (4000) exceeded, "
        "intended charge amount 14791"
    )

    with pytest.raises(
        JinaReaderError,
        match="Token budget \\(4000\\) exceeded",
    ) as exc:
        read_with_jina(
            "https://github.com/Charran78/BlackBelt",
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    409,
                    json={
                        "name": "BudgetExceededError",
                        "readableMessage": provider_message,
                    },
                ),
            ),
        )

    assert provider_message in str(exc.value)


def test_reader_rejects_oversized_responses() -> None:
    response: dict[str, Any] = {
        "data": {
            "url": "https://example.com/",
            "title": "Example",
            "content": "x" * 700_000,
        }
    }

    with pytest.raises(JinaReaderError, match="512 KiB"):
        read_with_jina(
            "https://example.com/",
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json=response),
            ),
        )


def test_search_requires_an_api_key_without_making_a_request() -> None:
    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200),
    )

    with pytest.raises(JinaSearchUnavailableError, match="requiere una API key"):
        search_with_jina("blackbelt", transport=transport)

    assert requests == []


def test_search_sends_only_the_query_and_returns_five_bounded_public_results() -> None:
    requests: list[httpx.Request] = []
    raw_results: list[dict[str, str]] = [
        {
            "title": "Artículo " + str(index),
            "url": f"https://example{index}.com/article",
            "content": "x" * 900,
        }
        for index in range(6)
    ]
    raw_results.insert(
        1,
        {
            "title": "Local result",
            "url": "http://127.0.0.1/private",
            "content": "Must not be shown.",
        },
    )
    raw_results.append(
        {
            "title": "Duplicate",
            "url": "https://example0.com/article",
            "content": "Duplicate URL.",
        }
    )

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": raw_results})

    results = search_with_jina(
        "  privacidad Companion  ",
        api_key=" local-search-key ",
        transport=httpx.MockTransport(respond),
    )

    assert len(results) == 5
    assert results[0].url == "https://example0.com/article"
    assert len(results[0].snippet) == 800
    assert len(results[0].title) <= 300
    assert all("127.0.0.1" not in result.url for result in results)
    assert requests[0].url.host == "s.jina.ai"
    assert requests[0].url.params["q"] == "privacidad Companion"
    assert requests[0].headers["authorization"] == "Bearer local-search-key"


def test_search_translates_rate_limit_and_invalid_payload_errors() -> None:
    with pytest.raises(JinaSearchError, match="límite de solicitudes"):
        search_with_jina(
            "consulta",
            api_key="test-key",
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(429),
            ),
        )

    with pytest.raises(JinaSearchError, match="formato no válido"):
        search_with_jina(
            "consulta",
            api_key="test-key",
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"data": "unexpected"}),
            ),
        )
