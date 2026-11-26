"""AWS Bedrock adapter (boto3): Claude via the Converse API, Titan for embeddings."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

import boto3
from ai_platform_common.config import get_env
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
)

from ai_platform_gateway.errors import (
    AuthError,
    GatewayError,
    GatewayTimeout,
    InvalidRequest,
    ModelNotFound,
    ProviderUnavailable,
    RateLimited,
    error_from_status,
)
from ai_platform_gateway.providers.base import (
    BaseProvider,
    CompletionRequest,
    EmbeddingRequest,
    ProviderCompletion,
    ProviderEmbedding,
)
from ai_platform_gateway.types import ImagePart, Message

if TYPE_CHECKING:
    from mypy_boto3_bedrock_runtime import BedrockRuntimeClient

# Any one of these means "standard AWS credentials are configured".
AWS_CREDENTIAL_ENV = (
    "AWS_ACCESS_KEY_ID",
    "AWS_PROFILE",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
)

_CODE_MAP: dict[str, type[GatewayError]] = {
    "ThrottlingException": RateLimited,
    "TooManyRequestsException": RateLimited,
    "ServiceQuotaExceededException": RateLimited,
    "ServiceUnavailableException": ProviderUnavailable,
    "InternalServerException": ProviderUnavailable,
    "ModelNotReadyException": ProviderUnavailable,
    "ModelTimeoutException": GatewayTimeout,
    "AccessDeniedException": AuthError,
    "UnrecognizedClientException": AuthError,
    "ExpiredTokenException": AuthError,
    "ValidationException": InvalidRequest,
    "ResourceNotFoundException": ModelNotFound,
}


def map_bedrock_error(exc: Exception, provider: str = "bedrock") -> GatewayError:
    """Map botocore errors to gateway errors."""
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code", ""))
        status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) or 0)
        cls = _CODE_MAP.get(code)
        if cls is not None:
            return cls(code, provider=provider, status_code=status or None)
        return error_from_status(status, code or "ClientError", provider)
    if isinstance(exc, ReadTimeoutError | ConnectTimeoutError):
        return GatewayTimeout("request timed out", provider=provider)
    if isinstance(exc, EndpointConnectionError | ConnectionClosedError):
        return ProviderUnavailable("connection error", provider=provider)
    if isinstance(exc, NoCredentialsError):
        return AuthError("no AWS credentials", provider=provider)
    if isinstance(exc, BotoCoreError):
        return ProviderUnavailable(type(exc).__name__, provider=provider)
    return GatewayError(f"unexpected {type(exc).__name__}", provider=provider)


def to_converse_message(message: Message) -> dict[str, Any]:
    """Convert a gateway message to the Converse API shape."""
    content: list[dict[str, Any]] = []
    for part in message.parts():
        if isinstance(part, ImagePart):
            fmt = part.media_type.split("/", 1)[1]
            content.append({"image": {"format": fmt, "source": {"bytes": part.raw_bytes()}}})
        else:
            content.append({"text": part.text})
    return {"role": message.role, "content": content}


class BedrockProvider(BaseProvider):
    """Claude (Converse) and Titan embeddings on AWS Bedrock.

    Available when ``AWS_REGION`` and standard AWS credentials are present in the env.
    """

    name = "bedrock"
    required_env = ("AWS_REGION",)

    def __init__(
        self, client_factory: Callable[[Config], BedrockRuntimeClient] | None = None
    ) -> None:
        self._client_factory = client_factory
        self._clients: dict[float, BedrockRuntimeClient] = {}

    def missing_env(self, environ: Mapping[str, str] | None = None) -> list[str]:
        missing_vars = super().missing_env(environ)
        if not any(get_env(v, environ) for v in AWS_CREDENTIAL_ENV):
            missing_vars.append("AWS_ACCESS_KEY_ID (or AWS_PROFILE / web identity / container)")
        return missing_vars

    def client(self, timeout_s: float) -> BedrockRuntimeClient:
        """One boto3 client per timeout value; botocore retries are off."""
        if timeout_s not in self._clients:
            config = Config(
                read_timeout=timeout_s,
                connect_timeout=min(timeout_s, 10.0),
                retries={"max_attempts": 1, "mode": "standard"},
            )
            if self._client_factory is not None:
                self._clients[timeout_s] = self._client_factory(config)
            else:
                self._clients[timeout_s] = boto3.client(
                    "bedrock-runtime", region_name=get_env("AWS_REGION"), config=config
                )
        return self._clients[timeout_s]

    def complete(self, request: CompletionRequest) -> ProviderCompletion:
        inference: dict[str, Any] = {"maxTokens": request.max_tokens}
        if request.temperature is not None:
            inference["temperature"] = request.temperature
        kwargs: dict[str, Any] = {
            "modelId": request.model,
            "messages": [to_converse_message(m) for m in request.messages],
            "inferenceConfig": inference,
        }
        if request.system:
            kwargs["system"] = [{"text": request.system}]
        try:
            resp = self.client(request.timeout_s).converse(**kwargs)
        except Exception as exc:
            raise map_bedrock_error(exc, self.name) from exc
        blocks = resp.get("output", {}).get("message", {}).get("content", [])
        text = "".join(str(b["text"]) for b in blocks if "text" in b)
        usage = resp.get("usage", {})
        return ProviderCompletion(
            text=text,
            input_tokens=int(usage.get("inputTokens", 0)),
            output_tokens=int(usage.get("outputTokens", 0)),
            finish_reason=str(resp.get("stopReason")) if resp.get("stopReason") else None,
        )

    def embed(self, request: EmbeddingRequest) -> ProviderEmbedding:
        """Titan Text Embeddings take one text per call."""
        client = self.client(request.timeout_s)
        vectors: list[list[float]] = []
        tokens = 0
        for text in request.texts:
            body: dict[str, Any] = {"inputText": text, "normalize": True}
            if request.dimensions:
                body["dimensions"] = request.dimensions
            try:
                resp = client.invoke_model(modelId=request.model, body=json.dumps(body))
                payload = json.loads(resp["body"].read())
            except Exception as exc:
                raise map_bedrock_error(exc, self.name) from exc
            vectors.append([float(x) for x in payload["embedding"]])
            tokens += int(payload.get("inputTextTokenCount", 0))
        return ProviderEmbedding(vectors=vectors, tokens=tokens)
