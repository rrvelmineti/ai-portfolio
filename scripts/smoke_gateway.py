"""Live smoke test for the model gateway. NOT run in CI; it spends real (small) money.

For every provider whose env vars are set, sends one short ``complete()`` call and,
where supported, one ``embed()`` call through the full gateway path (routing, retries,
cost, logging), then prints a table. Providers without credentials are skipped.

    set -a; source .env; set +a
    uv run python scripts/smoke_gateway.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass

from ai_platform_common.logging import configure_logging
from ai_platform_gateway import GatewayError
from ai_platform_gateway.config import AliasConfig, GatewayConfig, ModelEntry, default_config
from ai_platform_gateway.gateway import Gateway
from ai_platform_gateway.providers.registry import ProviderRegistry

PROVIDERS = ["anthropic", "openai", "azure_openai", "bedrock", "gemini", "groq"]


@dataclass
class Row:
    provider: str
    operation: str
    model: str
    status: str
    latency_ms: str = "-"
    cost_usd: str = "-"
    detail: str = ""


def _first(cfg: GatewayConfig, provider: str, kind: str) -> ModelEntry | None:
    return next((m for m in cfg.models.values() if m.provider == provider and m.kind == kind), None)


def _dims_for(cfg: GatewayConfig, key: str) -> int | None:
    for alias in cfg.aliases.values():
        if key in alias.fallback and alias.dimensions:
            return alias.dimensions
    return None


def _fmt_cost(cost: float | None) -> str:
    return "unknown" if cost is None else f"{cost:.6f}"


def run(timeout_s: float) -> list[Row]:
    base = default_config()
    registry = ProviderRegistry()
    rows: list[Row] = []
    for provider in PROVIDERS:
        chat = _first(base, provider, "chat")
        emb = _first(base, provider, "embed")
        missing = registry.get(provider).missing_env()
        if missing:
            rows.append(Row(provider, "-", "-", "SKIPPED", detail="missing " + ", ".join(missing)))
            continue
        # Entry-level env (e.g. Azure deployment names) only skips that operation.
        if chat and chat.missing_env():
            rows.append(Row(provider, "complete", chat.model, "SKIPPED",
                            detail="missing " + ", ".join(chat.missing_env())))  # fmt: skip
            chat = None
        if emb and emb.missing_env():
            rows.append(Row(provider, "embed", emb.model, "SKIPPED",
                            detail="missing " + ", ".join(emb.missing_env())))  # fmt: skip
            emb = None
        entries = [e for e in (chat, emb) if e is not None]
        if not entries:
            continue

        aliases: dict[str, AliasConfig] = {}
        if chat:
            aliases["smoke-chat"] = AliasConfig(kind="chat", fallback=[chat.key])
        if emb:
            dims = _dims_for(base, emb.key)
            aliases["smoke-embed"] = AliasConfig(kind="embed", dimensions=dims, fallback=[emb.key])
        cfg = GatewayConfig(models={e.key: e for e in entries}, aliases=aliases)
        gw = Gateway(config=cfg, registry=registry)

        if chat:
            try:
                res = gw.complete(
                    "Reply with the single word: pong", "smoke-chat",
                    max_tokens=16, timeout_s=timeout_s,
                )  # fmt: skip
                rows.append(
                    Row(provider, "complete", res.model, "ok", f"{res.latency_ms:.0f}",
                        _fmt_cost(res.cost_usd), res.text.strip()[:20])
                )  # fmt: skip
            except GatewayError as exc:
                rows.append(Row(provider, "complete", chat.model, "FAIL", detail=_reason(exc)))
        if emb:
            try:
                er = gw.embed(["gateway smoke test"], "smoke-embed", timeout_s=timeout_s)
                rows.append(
                    Row(provider, "embed", er.model, "ok", f"{er.latency_ms:.0f}",
                        _fmt_cost(er.cost_usd), f"dims={er.dimensions}")
                )  # fmt: skip
            except GatewayError as exc:
                rows.append(Row(provider, "embed", emb.model, "FAIL", detail=_reason(exc)))
        elif _first(base, provider, "embed") is None:
            rows.append(Row(provider, "embed", "-", "n/a", detail="embeddings not supported"))
    return rows


def _reason(exc: GatewayError) -> str:
    attempts = getattr(exc, "attempts", None)
    if attempts:
        return "; ".join(a.reason or a.outcome for a in attempts)
    return exc.describe()


def render(rows: list[Row]) -> str:
    headers = ["provider", "op", "model", "status", "latency_ms", "cost_usd", "detail"]
    data = [
        [r.provider, r.operation, r.model, r.status, r.latency_ms, r.cost_usd, r.detail]
        for r in rows
    ]
    widths = [max(len(h), *(len(row[i]) for row in data)) for i, h in enumerate(headers)]
    line = "| " + " | ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)) + " |"
    sep = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    body = [
        "| " + " | ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)) + " |"
        for row in data
    ]
    return "\n".join([line, sep, *body])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()
    configure_logging(args.log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    rows = run(args.timeout)
    print(render(rows))  # noqa: T201 - CLI output
    return 1 if any(r.status == "FAIL" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
