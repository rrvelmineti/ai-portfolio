import json
import logging
import sys
import uuid

import pytest
from ai_platform_common.context import request_context, set_user
from ai_platform_common.logging import (
    REDACTED,
    JsonFormatter,
    RedactionFilter,
    configure_logging,
    get_logger,
    is_sensitive_key,
    redact,
)


def _record(msg: str = "hello %s", args: object = ("x",), **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)  # type: ignore[arg-type]
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_formatter_emits_json_with_fields() -> None:
    out = json.loads(JsonFormatter().format(_record(fields={"request_id": "r1", "n": 2})))
    assert out["message"] == "hello x"
    assert out["level"] == "INFO" and out["logger"] == "t" and "timestamp" in out
    assert out["request_id"] == "r1" and out["n"] == 2


def test_formatter_adds_request_context() -> None:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    with request_context("req-ctx"):
        set_user(tid, uid)
        out = json.loads(JsonFormatter().format(_record()))
    assert out["request_id"] == "req-ctx"
    assert out["tenant_id"] == str(tid) and out["user_id"] == str(uid)
    assert "request_id" not in json.loads(JsonFormatter().format(_record()))


def test_formatter_includes_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "err", (), sys.exc_info())
    out = json.loads(JsonFormatter().format(rec))
    assert "ValueError" in out["exc_info"]


@pytest.mark.parametrize(
    "key",
    ["password", "PASSWORD", "new_password", "access_token", "refresh-token", "token",
     "client_secret", "Authorization", "api_key", "X-API-Key", "apikey", "cookie",
     "Set-Cookie", "cookies", "SENTRY_DSN", "jwt", "aws_secret_access_key", "private_key"],
)  # fmt: skip
def test_sensitive_keys(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize(
    "key", ["input_tokens", "output_tokens", "user_id", "request_id", "route", "status",
            "cost_usd", "tokens", "keyboard", "monkey"],
)  # fmt: skip
def test_non_sensitive_keys(key: str) -> None:
    assert not is_sensitive_key(key)


def test_redact_nested_structures() -> None:
    data = {
        "user": "ana",
        "password": "hunter2-long-enough",
        "nested": {"refresh_token": "abc", "input_tokens": 12, "list": [{"api_key": "k"}]},
        "headers": [("Authorization", "Bearer xyz"), ("accept", "json"), (b"cookie", b"s=1")],
    }
    out = redact(data)
    assert out["user"] == "ana" and out["password"] == REDACTED
    assert out["nested"]["refresh_token"] == REDACTED
    assert out["nested"]["input_tokens"] == 12
    assert out["nested"]["list"] == [{"api_key": REDACTED}]
    assert out["headers"] == [("Authorization", REDACTED), ("accept", "json"),
                              (b"cookie", REDACTED)]  # fmt: skip
    assert data["password"] == "hunter2-long-enough"  # input not mutated
    assert redact("plain") == "plain" and redact([1, 2]) == [1, 2]


def test_redaction_filter_and_formatter() -> None:
    rec = _record("login %(user)s", {"user": "ana", "password": "p"},
                  fields={"token": "t", "ok": 1})  # fmt: skip
    assert RedactionFilter().filter(rec) is True
    assert rec.fields == {"token": REDACTED, "ok": 1}  # type: ignore[attr-defined]
    raw = _record(fields={"secret": "s3cr3t-value"})
    assert "s3cr3t-value" not in JsonFormatter().format(raw)


def test_configure_logging_is_idempotent_and_routes_frameworks() -> None:
    root = logging.getLogger()
    logging.getLogger("uvicorn.error").addHandler(logging.NullHandler())
    configure_logging("DEBUG")
    configure_logging("DEBUG")
    ours = [h for h in root.handlers if getattr(h, "_ai_platform_json_handler", False)]
    assert len(ours) == 1
    assert any(isinstance(f, RedactionFilter) for f in ours[0].filters)
    assert root.level == logging.DEBUG
    for name in ("uvicorn", "uvicorn.error", "fastapi", "sqlalchemy"):
        lg = logging.getLogger(name)
        assert lg.handlers == [] and lg.propagate
    assert logging.getLogger("uvicorn.access").disabled
    assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING
    assert get_logger("x").name == "x"


def test_context_is_stamped_at_record_creation() -> None:
    configure_logging("INFO")
    with request_context("req-stamped"):
        rec = logging.getLogger("t").makeRecord("t", logging.INFO, __file__, 1, "m", (), None)
    out = json.loads(JsonFormatter().format(rec))  # formatted after the context ended
    assert out["request_id"] == "req-stamped"
    assert "tenant_id" not in out  # None values are omitted
