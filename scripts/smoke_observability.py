"""Live observability check (NOT run in CI; spends a tiny amount on one AI call).

Makes one gateway call through the first configured provider, confirms the
observability.llm_calls row exists, and, if LANGSMITH_API_KEY is set, prints the
LangSmith run URL.

    set -a; source .env; set +a
    uv run python scripts/smoke_observability.py
"""

from __future__ import annotations

import sys
import uuid

from ai_platform_common.context import request_context
from ai_platform_common.db import database_url, get_engine, make_session_factory
from ai_platform_common.logging import configure_logging
from ai_platform_gateway import GatewayError, get_gateway
from ai_platform_observability import setup_tracing, traced
from ai_platform_observability.db import migrate
from ai_platform_observability.models import LlmCall
from sqlalchemy import select


def first_configured_alias() -> str | None:
    gw = get_gateway()
    for name, alias in gw.config.aliases.items():
        if alias.kind != "chat":
            continue
        for entry in gw.config.chain(name):
            provider = gw.registry.get(entry.provider)
            if not provider.missing_env() and not entry.missing_env():
                return name
    return None


def main() -> int:
    configure_logging("WARNING")
    try:
        url = database_url()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    alias = first_configured_alias()
    if alias is None:
        print("SKIPPED: no provider has its env vars set (see .env.example)")
        return 0
    migrate(url)
    factory = make_session_factory(get_engine(url))
    obs = setup_tracing(factory)
    request_id = f"smoke-obs-{uuid.uuid4()}"
    try:
        with request_context(request_id, product="smoke"), traced("smoke_observability") as span:
            result = get_gateway().complete("Reply with the single word: pong", alias,
                                            max_tokens=16)  # fmt: skip
    except GatewayError as exc:
        print(f"FAIL: gateway call failed: {exc.describe()}")
        return 1
    print(f"call ok: alias={alias} provider={result.provider} model={result.model} "
          f"latency_ms={result.latency_ms:.0f} cost_usd={result.cost_usd}")  # fmt: skip
    with factory() as session:
        row = session.scalar(select(LlmCall).where(LlmCall.request_id == request_id))
    if row is None:
        print("FAIL: no llm_calls row for this request_id")
        return 1
    print(f"llm_calls row ok: id={row.id} status={row.status} tokens={row.input_tokens}/"
          f"{row.output_tokens} cost_unknown={row.cost_unknown}")  # fmt: skip
    if obs.tracer is not None and span.run is not None:
        obs.tracer.client.flush()
        print(f"LangSmith run: {span.run.get_url()}")
    else:
        print("LangSmith: not configured (LANGSMITH_API_KEY unset)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
