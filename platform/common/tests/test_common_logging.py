import json
import logging
import sys

from ai_platform_common.logging import JsonFormatter, configure_logging, get_logger


def _record(**extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("x",), None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_formatter_emits_json_with_fields() -> None:
    out = json.loads(JsonFormatter().format(_record(fields={"request_id": "r1", "n": 2})))
    assert out["event"] == "hello x"
    assert out["level"] == "INFO"
    assert out["request_id"] == "r1"
    assert out["n"] == 2


def test_formatter_includes_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "err", (), sys.exc_info())
    out = json.loads(JsonFormatter().format(rec))
    assert "ValueError" in out["exc_info"]


def test_configure_logging_is_idempotent() -> None:
    root = logging.getLogger()
    configure_logging("DEBUG")
    configure_logging("DEBUG")
    ours = [h for h in root.handlers if getattr(h, "_ai_platform_json_handler", False)]
    assert len(ours) == 1
    assert root.level == logging.DEBUG
    assert get_logger("x").name == "x"
