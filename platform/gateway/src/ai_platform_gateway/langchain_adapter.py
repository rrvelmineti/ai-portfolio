"""LangChain wrappers so products can use LangChain on top of the gateway.

    from ai_platform_gateway.langchain_adapter import GatewayChatModel, GatewayEmbeddings

    llm = GatewayChatModel(alias="default")
    llm.invoke("Hello")
    store = PGVector(embeddings=GatewayEmbeddings(), ...)

Every call still goes through ``complete()`` / ``embed()``, so routing, fallback,
retries, cost and logging all apply. Tool calling and streaming are not supported
yet; ``stop`` sequences are rejected rather than silently ignored.
"""

from __future__ import annotations

import base64
from typing import Any, Literal

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel

from ai_platform_gateway.errors import InvalidRequest
from ai_platform_gateway.gateway import Gateway, get_gateway
from ai_platform_gateway.types import ContentPart, ImagePart, Message, TextPart


def _content_text(content: str | list[str | dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    texts = []
    for block in content:
        if isinstance(block, str):
            texts.append(block)
        elif block.get("type") == "text":
            texts.append(str(block.get("text", "")))
    return "".join(texts)


def _convert_block(block: str | dict[str, Any]) -> ContentPart:
    if isinstance(block, str):
        return TextPart(text=block)
    kind = block.get("type")
    if kind == "text":
        return TextPart(text=str(block.get("text", "")))
    if kind == "image_url":
        url = (
            block["image_url"]["url"]
            if isinstance(block["image_url"], dict)
            else block["image_url"]
        )
        if not str(url).startswith("data:") or ";base64," not in str(url):
            raise InvalidRequest("only base64 data: URLs are supported for images")
        header, data = str(url).split(",", 1)
        media_type = header[len("data:") : header.index(";")]
        base64.b64decode(data, validate=True)
        return ImagePart.model_validate({"data": data, "media_type": media_type})
    if kind == "image" and block.get("source_type") == "base64":
        return ImagePart.model_validate({"data": block["data"], "media_type": block["mime_type"]})
    raise InvalidRequest(f"unsupported LangChain content block type {kind!r}")


def to_gateway_messages(messages: list[BaseMessage]) -> tuple[str | None, list[Message]]:
    """Split LangChain messages into (system prompt, gateway messages)."""
    system_parts: list[str] = []
    out: list[Message] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            system_parts.append(_content_text(msg.content))
            continue
        role: Literal["user", "assistant"]
        if isinstance(msg, HumanMessage):
            role = "user"
        elif isinstance(msg, AIMessage):
            role = "assistant"
        else:
            raise InvalidRequest(f"unsupported message type {type(msg).__name__}")
        if isinstance(msg.content, str):
            out.append(Message(role=role, content=msg.content))
        else:
            out.append(Message(role=role, content=[_convert_block(b) for b in msg.content]))
    return ("\n\n".join(system_parts) or None), out


class GatewayChatModel(BaseChatModel):
    """LangChain chat model backed by ``ai_platform_gateway.complete``."""

    alias: str = "default"
    max_tokens: int = 1024
    temperature: float = 0.0
    timeout_s: float = 30
    response_schema: type[BaseModel] | None = None
    gateway: Gateway | None = None

    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return "ai-platform-gateway"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"alias": self.alias, "max_tokens": self.max_tokens}

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,  # noqa: ANN401 - signature fixed by BaseChatModel
    ) -> ChatResult:
        if stop:
            raise InvalidRequest("stop sequences are not supported by the gateway yet")
        system, gw_messages = to_gateway_messages(messages)
        result = (self.gateway or get_gateway()).complete(
            gw_messages,
            self.alias,
            system=system,
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
            temperature=kwargs.get("temperature", self.temperature),
            response_schema=self.response_schema,
            timeout_s=self.timeout_s,
            metadata=kwargs.get("metadata"),
            request_id=kwargs.get("request_id"),
        )
        message = AIMessage(
            content=result.text,
            usage_metadata={
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "total_tokens": result.input_tokens + result.output_tokens,
            },
            response_metadata={
                "provider": result.provider,
                "model_name": result.model,
                "alias": result.alias,
                "cost_usd": result.cost_usd,
                "request_id": result.request_id,
                "fallback_used": result.fallback_used,
                "finish_reason": result.finish_reason,
            },
        )
        return ChatResult(
            generations=[ChatGeneration(message=message)],
            llm_output={"request_id": result.request_id, "provider": result.provider},
        )


class GatewayEmbeddings(BaseModel, Embeddings):
    """LangChain embeddings backed by ``ai_platform_gateway.embed``."""

    alias: str = "default-embed"
    timeout_s: float = 30
    gateway: Gateway | None = None

    model_config = {"arbitrary_types_allowed": True}

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of documents."""
        if not texts:
            return []
        gw = self.gateway or get_gateway()
        return gw.embed(texts, self.alias, timeout_s=self.timeout_s).vectors

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query."""
        return self.embed_documents([text])[0]
