"""HTTP side: request id propagation, access log, metrics, health, Sentry.

No ``from __future__ import annotations``: test endpoints use ``Depends`` on fixture values.
"""

import json
import logging
import uuid
from collections.abc import Iterator
from typing import Annotated, Any
from unittest.mock import MagicMock

import pytest
from ai_platform_auth import Auth, AuthSettings, Role, User
from ai_platform_auth.models import AuditLog
from ai_platform_common.context import current
from ai_platform_common.logging import JsonFormatter
from ai_platform_gateway import hooks, set_gateway
from ai_platform_gateway.testing import FakeProvider, fake_gateway
from ai_platform_observability import Observability, install_observability
from ai_platform_observability.models import LlmCall
from ai_platform_observability.recorder import unregister_recorders
from ai_platform_observability.sentry import init_sentry, scrub_breadcrumb, scrub_event
from ai_platform_observability.settings import ObservabilitySettings
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

SECRET = b"observability-test-jwt-secret-" + b"q" * 32
PASSWORD = "synthetic-pass-phrase-1"


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    saved = list(hooks.on_call_complete)
    set_gateway(fake_gateway({"anthropic": FakeProvider("anthropic")}))
    yield
    unregister_recorders()
    hooks.on_call_complete[:] = saved
    set_gateway(None)


@pytest.fixture
def setup(
    committed_session_factory: sessionmaker[Session],
) -> tuple[FastAPI, Observability, Auth]:
    session_factory = committed_session_factory
    from ai_platform_gateway import complete

    app = FastAPI()
    auth = Auth(AuthSettings(jwt_secret=SECRET), session_factory)
    auth.install(app)
    obs = install_observability(app, session_factory, product="test-product",
                                settings=ObservabilitySettings())  # fmt: skip

    @app.get("/items/{item_id}")
    def get_item(item_id: str) -> dict[str, Any]:
        ctx = current()
        return {"item_id": item_id, "request_id": ctx.request_id if ctx else None}

    @app.post("/ask")
    def ask(user: Annotated[User, Depends(auth.require_role(Role.VIEWER))]) -> dict[str, str]:
        result = complete("synthetic question")  # request_id comes from the context
        return {"answer": result.text, "request_id": result.request_id}

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("unhandled")

    return app, obs, auth


@pytest.fixture
def client(setup: tuple[FastAPI, Observability, Auth]) -> Iterator[TestClient]:
    with TestClient(setup[0], raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def db(committed_session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with committed_session_factory() as s:
        yield s


@pytest.fixture
def tenant_slug() -> str:
    return f"obs-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def viewer_token(db: Session, client: TestClient, tenant_slug: str) -> str:
    from ai_platform_auth.models import Tenant
    from ai_platform_auth.passwords import hash_password

    t = Tenant(slug=tenant_slug, name="Obs Co")
    db.add(t)
    db.flush()
    db.add(User(tenant_id=t.id, username="vee", email="vee@example.com",
                password_hash=hash_password(PASSWORD), role=Role.VIEWER))  # fmt: skip
    db.commit()
    r = client.post("/auth/login", data={"username": f"{tenant_slug}/vee", "password": PASSWORD})
    assert r.status_code == 200, r.text
    token: str = r.json()["access_token"]
    return token


def access_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    fmt = JsonFormatter()
    return [json.loads(fmt.format(r)) for r in caplog.records if r.getMessage() == "http.access"]


# ------------------------------------------------------------------ request id end to end
def test_request_id_propagates_everywhere(
    client: TestClient, db: Session, viewer_token: str, tenant_slug: str,
    caplog: pytest.LogCaptureFixture,
) -> None:  # fmt: skip
    from ai_platform_auth.models import Tenant

    rid = f"req-e2e-{tenant_slug}"
    with caplog.at_level(logging.INFO):
        r = client.post("/ask", headers={"Authorization": f"Bearer {viewer_token}",
                                         "X-Request-ID": rid})  # fmt: skip
    assert r.status_code == 200
    assert r.headers["X-Request-ID"] == rid  # header in -> header out
    assert r.json()["request_id"] == rid  # contextvar -> gateway result
    row = db.scalar(select(LlmCall).where(LlmCall.request_id == rid))
    assert row is not None and row.product == "test-product"  # llm_calls row
    tenant = db.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
    assert tenant is not None
    user = db.scalar(select(User).where(User.username == "vee", User.tenant_id == tenant.id))
    assert user is not None and row.user_id == user.id and row.tenant_id == user.tenant_id
    audit = db.scalar(select(AuditLog).where(AuditLog.request_id == rid))
    assert audit is not None and audit.action == "http.post"  # audit log
    assert audit.user_id == user.id
    (line,) = [x for x in access_lines(caplog) if x["route"] == "/ask"]
    assert line["request_id"] == rid and line["user_id"] == str(user.id)
    gateway_lines = [json.loads(JsonFormatter().format(rec)) for rec in caplog.records
                     if rec.getMessage() == "gateway.call"]  # fmt: skip
    assert gateway_lines[0]["request_id"] == rid


def test_request_id_generated_when_missing(client: TestClient) -> None:
    r = client.get("/items/abc")
    rid = r.headers["X-Request-ID"]
    assert len(rid) == 36 and r.json()["request_id"] == rid


# ------------------------------------------------------------------ access log
def test_access_log_fields_use_route_template(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        client.get("/items/secret-id-123", headers={"X-Request-ID": "req-access"})
        client.get("/does/not/exist")
        client.get("/boom")
    item, missing, boom = access_lines(caplog)
    assert item["method"] == "GET" and item["route"] == "/items/{item_id}"
    assert item["status"] == 200 and item["latency_ms"] >= 0
    assert item["request_id"] == "req-access" and item["product"] == "test-product"
    assert "secret-id-123" not in json.dumps(item)
    assert missing["route"] == "unmatched" and missing["status"] == 404
    assert boom["route"] == "/boom" and boom["status"] == 500
    for key in ("timestamp", "level", "logger", "message"):
        assert key in item


def test_ops_routes_logged_at_debug_only(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        client.get("/healthz")
    assert access_lines(caplog) == []


# ------------------------------------------------------------------ metrics and health
def test_metrics_counters(setup: tuple[FastAPI, Observability, Auth], client: TestClient,
                          viewer_token: str) -> None:  # fmt: skip
    _, obs, _ = setup
    client.get("/items/1")
    client.get("/items/2")
    client.post("/ask", headers={"Authorization": f"Bearer {viewer_token}"})
    reg = obs.metrics.registry
    route = "/items/{item_id}"
    labels = {"method": "GET", "route": route, "status": "200"}
    assert reg.get_sample_value("http_requests_total", labels) == 2
    assert reg.get_sample_value("http_request_duration_seconds_count",
                                {"method": "GET", "route": "/items/{item_id}"}) == 2  # fmt: skip
    assert reg.get_sample_value("llm_calls_total", {"provider": "anthropic",
                                "model": "claude-sonnet-5-5", "status": "ok"}) == 1  # fmt: skip
    body = client.get("/metrics")
    assert body.status_code == 200 and "text/plain" in body.headers["content-type"]
    assert (
        b'http_requests_total{method="GET",route="/items/{item_id}",status="200"} 2.0'
        in body.content
    )


def test_healthz_and_readyz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["database"] == "ok"


def test_readyz_503_when_database_down() -> None:
    def broken_factory() -> Session:
        raise ConnectionError("db down")

    app = FastAPI()
    install_observability(app, broken_factory, settings=ObservabilitySettings())  # type: ignore[arg-type]
    with TestClient(app) as c:
        r = c.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "unavailable", "database": "unreachable"}
    no_db = FastAPI()
    install_observability(no_db, None, settings=ObservabilitySettings())
    with TestClient(no_db) as c:
        assert c.get("/readyz").json()["database"] == "not configured"


# ------------------------------------------------------------------ Sentry
def test_sentry_not_initialised_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    init = MagicMock()
    monkeypatch.setattr("ai_platform_observability.sentry.sentry_sdk.init", init)
    assert init_sentry(ObservabilitySettings()) is False
    init.assert_not_called()
    app = FastAPI()
    obs = install_observability(app, None, settings=ObservabilitySettings())
    assert obs.sentry_enabled is False
    init.assert_not_called()


def test_sentry_initialised_with_dsn_and_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    init = MagicMock()
    monkeypatch.setattr("ai_platform_observability.sentry.sentry_sdk.init", init)
    assert init_sentry(ObservabilitySettings(sentry_dsn="https://public@sentry.example/1")) is True
    kwargs = init.call_args.kwargs
    assert kwargs["dsn"] == "https://public@sentry.example/1"
    assert kwargs["data_collection"]["user_info"] is False
    assert kwargs["data_collection"]["cookies"] == {"mode": "off"}
    assert kwargs["data_collection"]["gen_ai"] == {"inputs": False, "outputs": False}
    assert kwargs["before_send"] is scrub_event and kwargs["before_breadcrumb"] is scrub_breadcrumb
    event = {"request": {"headers": {"Authorization": "Bearer abc", "accept": "json"},
                         "cookies": {"session": "s"}, "data": {"password": "pw", "q": "ok"}},
             "extra": {"api_key": "k"}}  # fmt: skip
    clean = scrub_event(event)
    assert clean["request"]["headers"] == {"Authorization": "[redacted]", "accept": "json"}
    assert clean["request"]["cookies"] == "[redacted]"
    assert clean["request"]["data"] == {"password": "[redacted]", "q": "ok"}
    assert clean["extra"] == {"api_key": "[redacted]"}
    assert scrub_breadcrumb({"data": {"token": "t"}}) == {"data": {"token": "[redacted]"}}
