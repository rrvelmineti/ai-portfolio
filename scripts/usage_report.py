"""Print AI usage from observability.llm_calls: cost, calls, latency, fallback rate.

    set -a; source .env; set +a
    uv run python scripts/usage_report.py                       # last 30 days, product/provider/day
    uv run python scripts/usage_report.py --by product --days 7
    uv run python scripts/usage_report.py --by product,model --format csv > usage.csv

"avg_cost" is the average cost per priced call (cost per answer / per file when the
product makes one call per item); "unpriced" counts calls whose model has no verified
price in models.yaml.
"""

from __future__ import annotations

import argparse
import sys

from ai_platform_common.db import database_url, get_engine, make_session_factory
from ai_platform_observability.usage import (
    DIMENSIONS,
    Dimension,
    format_csv,
    format_table,
    usage_summary,
)


def parse_dims(raw: str) -> list[Dimension]:
    dims = [d.strip() for d in raw.split(",") if d.strip()]
    bad = [d for d in dims if d not in DIMENSIONS]
    if bad:
        raise argparse.ArgumentTypeError(f"unknown dimension(s) {bad}; choose from {DIMENSIONS}")
    return dims  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI usage report from llm_calls.")
    parser.add_argument("--by", type=parse_dims, default=["product", "provider", "day"],
                        help=f"comma-separated, from {', '.join(DIMENSIONS)}")  # fmt: skip
    parser.add_argument("--days", type=int, default=30, help="look-back window; 0 = all time")
    parser.add_argument("--product", action="append", help="only this product (repeatable)")
    parser.add_argument("--format", choices=["table", "csv"], default="table")
    args = parser.parse_args(argv)
    try:
        url = database_url()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    factory = make_session_factory(get_engine(url))
    with factory() as session:
        rows = usage_summary(session, args.by, days=args.days or None, products=args.product)
    out = format_csv(rows, args.by) if args.format == "csv" else format_table(rows, args.by)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
