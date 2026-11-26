"""Anthropic, OpenAI, Azure OpenAI and Groq adapters with mocked SDK clients."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import anthropic
import groq
import httpx
import httpx2
import openai
import pytest
from ai_platform_gateway.errors import (
    AuthError,
    GatewayError,
    GatewayTimeout,
    InvalidRequest,
    ModelNotFound,
    NotSupported,
    ProviderUnavailable,
    RateLimited,
)
from ai_platform_gateway.providers.anthropic import AnthropicProvider
from ai_platform_gateway.providers.azure_openai import AzureOpenAIProvider
from ai_platform_gateway.providers.base import CompletionRequest, EmbeddingRequest
from ai_platform_gateway.providers.groq import GroqProvider
from ai_platform_gateway.providers.openai import OpenAIProvider
from ai_platform_gateway.types import ImagePart, Message, TextPart

IMG = ImagePart.from_bytes(b"\x89PNG", "image/png")


def req(**kw: Any) -> CompletionRequest:
    base: dict[str, Any] = {
        "model": "m",
        "messages": [Message(role="user", content="hi")],
        "system": "sys",
        "max_tokens": 50,
        "temperature": 0.2,
        "timeout_s": 7.0,
    }
    base.update(kw)
    return CompletionRequest(**base)


def status_error(sdk: Any, cls_name: str, status: int) -> Exception:
    """Build a real SDK status exception (Stainless SDKs share this shape)."""
    lib = httpx if sdk is groq else httpx2
    request = lib.Request("POST", "https://example.invalid/v1")
    response = lib.Response(status, request=request)
    exc: Exception = getattr(sdk, cls_name)("error body", response=response, body=None)
    return exc


def timeout_error(sdk: Any) -> Exception:
    lib = httpx if sdk is groq else httpx2
    exc: Exception = sdk.APITimeoutError(request=lib.Request("POST", "https://example.invalid"))
    return exc


def connection_error(sdk: Any) -> Exception:
    lib = httpx if sdk is groq else httpx2
    exc: Exception = sdk.APIConnectionError(request=lib.Request("POST", "https://example.invalid"))
    return exc


ERROR_CASES = [
    ("RateLimitError", 429, RateLimited),
    ("InternalServerError", 500, ProviderUnavailable),
    ("InternalServerError", 503, ProviderUnavailable),
    ("BadRequestError", 400, InvalidRequest),
    ("AuthenticationError", 401, AuthError),
    ("PermissionDeniedError", 403, AuthError),
    ("NotFoundError", 404, ModelNotFound),
    ("UnprocessableEntityError", 422, InvalidRequest),
]


# ------------------------------------------------------------------ Anthropic
def anthropic_response(text: str = "hello") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        stop_reason="end_turn",
    )


def test_anthropic_complete_maps_request_and_response() -> None:
    client = MagicMock()
    client.messages.create.return_value = anthropic_response()
    provider = AnthropicProvider(client=client)
    msg = Message(role="user", content=[TextPart(text="look"), IMG])
    out = provider.complete(req(messages=[msg], temperature=None))
    assert (out.text, out.input_tokens, out.output_tokens) == ("hello", 11, 7)
    assert out.finish_reason == "end_turn"
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["system"] == "sys" and kwargs["timeout"] == 7.0
    assert "extra_body" not in kwargs  # temperature dropped upstream
    blocks = kwargs["messages"][0]["content"]
    assert blocks[1]["source"] == {"type": "base64", "media_type": "image/png", "data": IMG.data}


def test_anthropic_sends_temperature_only_when_given() -> None:
    client = MagicMock()
    client.messages.create.return_value = anthropic_response()
    AnthropicProvider(client=client).complete(req(system=None))
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["extra_body"] == {"temperature": 0.2}
    assert "system" not in kwargs
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.parametrize(("cls_name", "status", "expected"), ERROR_CASES)
def test_anthropic_error_mapping(cls_name: str, status: int, expected: type[GatewayError]) -> None:
    client = MagicMock()
    client.messages.create.side_effect = status_error(anthropic, cls_name, status)
    with pytest.raises(expected) as info:
        AnthropicProvider(client=client).complete(req())
    assert info.value.status_code == status
    assert info.value.provider == "anthropic"


@pytest.mark.parametrize(
    ("factory", "expected"),
    [(timeout_error, GatewayTimeout), (connection_error, ProviderUnavailable)],
)
def test_anthropic_transport_errors(factory: Any, expected: type[GatewayError]) -> None:
    client = MagicMock()
    client.messages.create.side_effect = factory(anthropic)
    with pytest.raises(expected):
        AnthropicProvider(client=client).complete(req())


def test_anthropic_unexpected_error_and_no_embeddings() -> None:
    client = MagicMock()
    client.messages.create.side_effect = ValueError("weird")
    provider = AnthropicProvider(client=client)
    with pytest.raises(GatewayError, match="unexpected ValueError"):
        provider.complete(req())
    with pytest.raises(NotSupported):
        provider.embed(EmbeddingRequest("m", ["x"], None, 5))


def test_anthropic_client_built_from_env_without_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-test-key")
    provider = AnthropicProvider()
    assert provider.missing_env() == []
    assert provider.missing_env({}) == ["ANTHROPIC_API_KEY"]
    client = provider.client()
    assert client.max_retries == 0
    assert provider.client() is client


# ------------------------------------------------------------------ OpenAI-shaped
def chat_response(text: str | None = "hi there") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=9, completion_tokens=4),
    )


def embed_response() -> SimpleNamespace:
    return SimpleNamespace(
        data=[
            SimpleNamespace(index=1, embedding=[0.3, 0.4]),
            SimpleNamespace(index=0, embedding=[0.1, 0.2]),
        ],
        usage=SimpleNamespace(prompt_tokens=6),
    )


PROVIDERS: list[tuple[Any, Any]] = [
    (OpenAIProvider, openai),
    (AzureOpenAIProvider, openai),
    (GroqProvider, groq),
]


@pytest.mark.parametrize(("provider_cls", "sdk"), PROVIDERS)
def test_chat_request_mapping(provider_cls: Any, sdk: Any) -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = chat_response()
    out = provider_cls(client=client).complete(req(json_mode=True))
    assert (out.text, out.input_tokens, out.output_tokens) == ("hi there", 9, 4)
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["messages"][0] == {"role": "system", "content": "sys"}
    assert kwargs["max_completion_tokens"] == 50
    assert kwargs["temperature"] == 0.2
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["timeout"] == 7.0


@pytest.mark.parametrize(("provider_cls", "sdk"), PROVIDERS)
@pytest.mark.parametrize(("cls_name", "status", "expected"), ERROR_CASES)
def test_chat_error_mapping(
    provider_cls: Any, sdk: Any, cls_name: str, status: int, expected: type[GatewayError]
) -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = status_error(sdk, cls_name, status)
    with pytest.raises(expected) as info:
        provider_cls(client=client).complete(req())
    assert info.value.status_code == status


@pytest.mark.parametrize(("provider_cls", "sdk"), PROVIDERS)
def test_chat_timeout_and_connection(provider_cls: Any, sdk: Any) -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = timeout_error(sdk)
    with pytest.raises(GatewayTimeout):
        provider_cls(client=client).complete(req())
    client.chat.completions.create.side_effect = connection_error(sdk)
    with pytest.raises(ProviderUnavailable):
        provider_cls(client=client).complete(req())


def test_openai_images_and_no_temperature() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = chat_response(None)
    msg = Message(role="user", content=[TextPart(text="what"), IMG])
    out = OpenAIProvider(client=client).complete(req(messages=[msg], temperature=None, system=None))
    assert out.text == ""
    kwargs = client.chat.completions.create.call_args.kwargs
    assert "temperature" not in kwargs and "response_format" not in kwargs
    image = kwargs["messages"][0]["content"][1]
    assert image["image_url"]["url"].startswith("data:image/png;base64,")


def test_groq_rejects_images_and_embeddings() -> None:
    provider = GroqProvider(client=MagicMock())
    msg = Message(role="user", content=[IMG])
    with pytest.raises(NotSupported):
        provider.complete(req(messages=[msg]))
    with pytest.raises(NotSupported):
        provider.embed(EmbeddingRequest("m", ["x"], None, 5))


@pytest.mark.parametrize("provider_cls", [OpenAIProvider, AzureOpenAIProvider])
def test_embeddings(provider_cls: Any) -> None:
    client = MagicMock()
    client.embeddings.create.return_value = embed_response()
    out = provider_cls(client=client).embed(EmbeddingRequest("emb", ["a", "b"], 2, 5.0))
    assert out.vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert out.tokens == 6
    kwargs = client.embeddings.create.call_args.kwargs
    assert kwargs["dimensions"] == 2 and kwargs["input"] == ["a", "b"]


def test_embedding_errors_mapped() -> None:
    client = MagicMock()
    client.embeddings.create.side_effect = status_error(openai, "RateLimitError", 429)
    with pytest.raises(RateLimited):
        OpenAIProvider(client=client).embed(EmbeddingRequest("emb", ["a"], None, 5.0))


def test_clients_built_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    monkeypatch.setenv("GROQ_API_KEY", "dummy")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "dummy")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid")
    for provider in (OpenAIProvider(), AzureOpenAIProvider(), GroqProvider()):
        assert provider.missing_env() == []
        client = provider.client()
        assert client.max_retries == 0
        assert provider.client() is client
    assert AzureOpenAIProvider().missing_env({}) == [
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
    ]
