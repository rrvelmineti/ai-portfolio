from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from ai_platform_gateway.errors import (
    AuthError,
    GatewayError,
    GatewayTimeout,
    InvalidRequest,
    ModelNotFound,
    ProviderUnavailable,
    RateLimited,
)
from ai_platform_gateway.providers.base import CompletionRequest, EmbeddingRequest
from ai_platform_gateway.providers.bedrock import BedrockProvider, map_bedrock_error
from ai_platform_gateway.types import ImagePart, Message, TextPart
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
)


def req(**kw: Any) -> CompletionRequest:
    base: dict[str, Any] = {
        "model": "global.anthropic.claude-sonnet-5-5",
        "messages": [Message(role="user", content="hi")],
        "system": "sys",
        "max_tokens": 64,
        "temperature": None,
        "timeout_s": 12.0,
    }
    base.update(kw)
    return CompletionRequest(**base)


def client_error(code: str, status: int) -> ClientError:
    response: Any = {
        "Error": {"Code": code, "Message": "m"},
        "ResponseMetadata": {"HTTPStatusCode": status},
    }
    return ClientError(response, "Converse")


def make(client: MagicMock) -> tuple[BedrockProvider, list[Config]]:
    configs: list[Config] = []

    def factory(config: Config) -> Any:
        configs.append(config)
        return client

    return BedrockProvider(client_factory=factory), configs


def test_converse_mapping() -> None:
    client = MagicMock()
    client.converse.return_value = {
        "output": {"message": {"content": [{"text": "hel"}, {"text": "lo"}, {"other": 1}]}},
        "usage": {"inputTokens": 20, "outputTokens": 3},
        "stopReason": "end_turn",
    }
    provider, configs = make(client)
    img = ImagePart.from_bytes(b"\x89PNG", "image/png")
    msg = Message(role="user", content=[TextPart(text="see"), img])
    out = provider.complete(req(messages=[msg]))
    assert (out.text, out.input_tokens, out.output_tokens, out.finish_reason) == (
        "hello", 20, 3, "end_turn",
    )  # fmt: skip
    kwargs = client.converse.call_args.kwargs
    assert kwargs["system"] == [{"text": "sys"}]
    assert kwargs["inferenceConfig"] == {"maxTokens": 64}
    image = kwargs["messages"][0]["content"][1]["image"]
    assert image == {"format": "png", "source": {"bytes": b"\x89PNG"}}
    # one client per timeout, botocore retries disabled
    provider.complete(req(system=None, temperature=0.5))
    assert len(configs) == 1
    assert getattr(configs[0], "retries") == {"max_attempts": 1, "mode": "standard"}  # noqa: B009
    assert getattr(configs[0], "read_timeout") == 12.0  # noqa: B009
    assert client.converse.call_args.kwargs["inferenceConfig"]["temperature"] == 0.5


@pytest.mark.parametrize(
    ("code", "status", "expected"),
    [
        ("ThrottlingException", 429, RateLimited),
        ("ServiceUnavailableException", 503, ProviderUnavailable),
        ("InternalServerException", 500, ProviderUnavailable),
        ("ModelTimeoutException", 408, GatewayTimeout),
        ("ValidationException", 400, InvalidRequest),
        ("AccessDeniedException", 403, AuthError),
        ("UnrecognizedClientException", 401, AuthError),
        ("ResourceNotFoundException", 404, ModelNotFound),
        ("SomethingNew", 502, ProviderUnavailable),
        ("SomethingElse", 400, InvalidRequest),
    ],
)
def test_client_error_mapping(code: str, status: int, expected: type[GatewayError]) -> None:
    client = MagicMock()
    client.converse.side_effect = client_error(code, status)
    provider, _ = make(client)
    with pytest.raises(expected) as info:
        provider.complete(req())
    assert info.value.provider == "bedrock"


def test_botocore_transport_errors() -> None:
    assert isinstance(map_bedrock_error(ReadTimeoutError(endpoint_url="u")), GatewayTimeout)
    assert isinstance(map_bedrock_error(ConnectTimeoutError(endpoint_url="u")), GatewayTimeout)
    assert isinstance(
        map_bedrock_error(EndpointConnectionError(endpoint_url="u")), ProviderUnavailable
    )
    assert isinstance(map_bedrock_error(NoCredentialsError()), AuthError)
    assert type(map_bedrock_error(ValueError("x"))) is GatewayError


def test_titan_embeddings() -> None:
    client = MagicMock()
    client.invoke_model.side_effect = [
        {"body": io.BytesIO(json.dumps({"embedding": [1, 2], "inputTextTokenCount": 3}).encode())},
        {"body": io.BytesIO(json.dumps({"embedding": [3, 4], "inputTextTokenCount": 4}).encode())},
    ]
    provider, _ = make(client)
    out = provider.embed(EmbeddingRequest("amazon.titan-embed-text-v2:0", ["a", "b"], 256, 5.0))
    assert out.vectors == [[1.0, 2.0], [3.0, 4.0]]
    assert out.tokens == 7
    body = json.loads(client.invoke_model.call_args_list[0].kwargs["body"])
    assert body == {"inputText": "a", "normalize": True, "dimensions": 256}


def test_titan_embedding_error() -> None:
    client = MagicMock()
    client.invoke_model.side_effect = client_error("ThrottlingException", 429)
    provider, _ = make(client)
    with pytest.raises(RateLimited):
        provider.embed(EmbeddingRequest("titan", ["a"], None, 5.0))


def test_availability_needs_region_and_credentials() -> None:
    provider = BedrockProvider()
    assert provider.missing_env({}) == [
        "AWS_REGION",
        "AWS_ACCESS_KEY_ID (or AWS_PROFILE / web identity / container)",
    ]
    assert provider.missing_env({"AWS_REGION": "us-east-1", "AWS_PROFILE": "dev"}) == []


def test_real_boto3_client_constructed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "dummy")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "dummy")
    provider = BedrockProvider()
    client = provider.client(5.0)
    assert client.meta.region_name == "us-east-1"
    assert provider.client(5.0) is client
