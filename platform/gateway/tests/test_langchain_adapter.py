from __future__ import annotations

import base64

import pytest
from ai_platform_gateway import InvalidRequest, set_gateway
from ai_platform_gateway.langchain_adapter import (
    GatewayChatModel,
    GatewayEmbeddings,
    to_gateway_messages,
)
from ai_platform_gateway.testing import FakeProvider, GatewayFactory, completion
from ai_platform_gateway.types import ImagePart
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

PNG_B64 = base64.b64encode(b"\x89PNG").decode()


def test_chat_model_invoke(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [completion("pong", inp=8, out=2)])
    llm = GatewayChatModel(alias="single", gateway=make_gateway([a]))
    msg = llm.invoke(
        [SystemMessage(content="be brief"), HumanMessage(content="ping")],
        temperature=0.5,
    )
    assert isinstance(msg, AIMessage)
    assert msg.content == "pong"
    assert msg.usage_metadata == {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10}
    assert msg.response_metadata["provider"] == "a"
    req = a.requests[0]
    assert req.system == "be brief" and req.temperature == 0.5
    assert llm._llm_type == "ai-platform-gateway"


def test_chat_model_uses_default_gateway(make_gateway: GatewayFactory) -> None:
    set_gateway(make_gateway([FakeProvider("a")]))
    try:
        assert GatewayChatModel(alias="single").invoke("hi").content == "a:ok"
    finally:
        set_gateway(None)


def test_message_conversion_with_images() -> None:
    system, msgs = to_gateway_messages(
        [
            SystemMessage(content=[{"type": "text", "text": "sys"}, "tem"]),
            HumanMessage(
                content=[
                    "look ",
                    {"type": "text", "text": "here"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}},
                    {"type": "image", "source_type": "base64", "data": PNG_B64,
                     "mime_type": "image/jpeg"},
                ]
            ),
            AIMessage(content="ok"),
        ]
    )  # fmt: skip
    assert system == "system"
    assert [m.role for m in msgs] == ["user", "assistant"]
    parts = msgs[0].parts()
    assert msgs[0].text() == "look here"
    assert isinstance(parts[2], ImagePart) and parts[2].media_type == "image/png"
    assert isinstance(parts[3], ImagePart) and parts[3].media_type == "image/jpeg"


@pytest.mark.parametrize(
    "content",
    [
        [{"type": "image_url", "image_url": {"url": "https://example.com/x.png"}}],
        [{"type": "audio"}],
    ],
)
def test_unsupported_blocks(content: list[dict[str, object]]) -> None:
    with pytest.raises(InvalidRequest):
        to_gateway_messages([HumanMessage(content=content)])  # type: ignore[arg-type]


def test_unsupported_message_type_and_stop(make_gateway: GatewayFactory) -> None:
    with pytest.raises(InvalidRequest):
        to_gateway_messages([ToolMessage(content="x", tool_call_id="1")])
    llm = GatewayChatModel(alias="single", gateway=make_gateway([FakeProvider("a")]))
    with pytest.raises(InvalidRequest):
        llm.invoke("hi", stop=["\n"])


def test_structured_output_via_adapter(make_gateway: GatewayFactory) -> None:
    class Out(BaseModel):
        n: int

    a = FakeProvider("a", [completion('{"n": 3}')])
    llm = GatewayChatModel(alias="single", response_schema=Out, gateway=make_gateway([a]))
    assert llm.invoke("count").content == '{"n": 3}'
    assert a.requests[0].json_mode is True


def test_embeddings(make_gateway: GatewayFactory) -> None:
    emb = GatewayEmbeddings(
        alias="vec", gateway=make_gateway([FakeProvider("a"), FakeProvider("b")])
    )
    vectors = emb.embed_documents(["x", "y"])
    assert len(vectors) == 2 and len(vectors[0]) == 4
    assert len(emb.embed_query("q")) == 4
    assert emb.embed_documents([]) == []


def test_embeddings_default_gateway(make_gateway: GatewayFactory) -> None:
    set_gateway(make_gateway([FakeProvider("a"), FakeProvider("b")]))
    try:
        assert len(GatewayEmbeddings(alias="vec").embed_query("q")) == 4
    finally:
        set_gateway(None)
