from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx2
import pytest
from ai_platform_gateway.errors import (
    AuthError,
    GatewayError,
    GatewayTimeout,
    InvalidRequest,
    ProviderUnavailable,
    RateLimited,
)
from ai_platform_gateway.providers.base import CompletionRequest, EmbeddingRequest
from ai_platform_gateway.providers.gemini import GeminiProvider, map_gemini_error
from ai_platform_gateway.types import ImagePart, Message, TextPart
from google.genai import errors as genai_errors


def req(**kw: Any) -> CompletionRequest:
    base: dict[str, Any] = {
        "model": "gemini-x",
        "messages": [Message(role="user", content="hi"), Message(role="assistant", content="yo")],
        "system": "sys",
        "max_tokens": 50,
        "temperature": 0.4,
        "timeout_s": 2.5,
        "json_mode": True,
    }
    base.update(kw)
    return CompletionRequest(**base)


def response() -> SimpleNamespace:
    return SimpleNamespace(
        text="answer",
        usage_metadata=SimpleNamespace(
            prompt_token_count=12, candidates_token_count=5, thoughts_token_count=3
        ),
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(value="STOP"))],
    )


def test_complete_maps_request_and_usage() -> None:
    client = MagicMock()
    client.models.generate_content.return_value = response()
    out = GeminiProvider(client=client).complete(req())
    assert (out.text, out.input_tokens, out.output_tokens, out.finish_reason) == (
        "answer", 12, 8, "STOP",
    )  # fmt: skip
    kwargs = client.models.generate_content.call_args.kwargs
    config = kwargs["config"]
    assert config.system_instruction == "sys"
    assert config.temperature == 0.4
    assert config.response_mime_type == "application/json"
    assert config.http_options.timeout == 2500
    assert [c.role for c in kwargs["contents"]] == ["user", "model"]


def test_complete_with_image_and_no_usage() -> None:
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(
        text=None, usage_metadata=None, candidates=[]
    )
    img = ImagePart.from_bytes(b"\xff\xd8", "image/jpeg")
    msg = Message(role="user", content=[TextPart(text="what"), img])
    out = GeminiProvider(client=client).complete(
        req(messages=[msg], temperature=None, system=None, json_mode=False)
    )
    assert (out.text, out.input_tokens, out.output_tokens, out.finish_reason) == ("", 0, 0, None)
    part = client.models.generate_content.call_args.kwargs["contents"][0].parts[1]
    assert part.inline_data.mime_type == "image/jpeg"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (429, RateLimited),
        (500, ProviderUnavailable),
        (503, ProviderUnavailable),
        (400, InvalidRequest),
        (401, AuthError),
        (403, AuthError),
    ],
)
def test_error_mapping(code: int, expected: type[GatewayError]) -> None:
    err_cls = genai_errors.ClientError if code < 500 else genai_errors.ServerError
    client = MagicMock()
    client.models.generate_content.side_effect = err_cls(
        code, {"error": {"code": code, "message": "m", "status": "X"}}
    )
    with pytest.raises(expected) as info:
        GeminiProvider(client=client).complete(req())
    assert info.value.status_code == code


def test_transport_and_unknown_errors() -> None:
    timeout = httpx2.ReadTimeout("slow")
    assert isinstance(map_gemini_error(timeout), GatewayTimeout)
    assert isinstance(map_gemini_error(httpx2.ConnectError("down")), ProviderUnavailable)
    unknown = map_gemini_error(ValueError("x"))
    assert type(unknown) is GatewayError


def test_embed() -> None:
    client = MagicMock()
    client.models.embed_content.return_value = SimpleNamespace(
        embeddings=[SimpleNamespace(values=[1.0, 2.0]), SimpleNamespace(values=[3.0, 4.0])],
        usage_metadata=SimpleNamespace(prompt_token_count=4),
    )
    out = GeminiProvider(client=client).embed(EmbeddingRequest("emb", ["a", "b"], 2, 3.0))
    assert out.vectors == [[1.0, 2.0], [3.0, 4.0]]
    assert out.tokens == 4
    config = client.models.embed_content.call_args.kwargs["config"]
    assert config.output_dimensionality == 2


def test_embed_without_usage_and_errors() -> None:
    client = MagicMock()
    client.models.embed_content.return_value = SimpleNamespace(embeddings=None)
    out = GeminiProvider(client=client).embed(EmbeddingRequest("emb", ["a"], None, 3.0))
    assert out.vectors == [] and out.tokens is None
    client.models.embed_content.side_effect = genai_errors.ClientError(
        429, {"error": {"code": 429, "message": "m", "status": "RESOURCE_EXHAUSTED"}}
    )
    with pytest.raises(RateLimited):
        GeminiProvider(client=client).embed(EmbeddingRequest("emb", ["a"], None, 3.0))


def test_client_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    provider = GeminiProvider()
    assert provider.missing_env() == []
    assert provider.client() is provider.client()
