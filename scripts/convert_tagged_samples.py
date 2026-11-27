"""Convert a hand-written tagged sample file into held-out evaluation JSONL.

    uv run python scripts/convert_tagged_samples.py eval/heldout/raghu_samples.txt
    uv run python scripts/convert_tagged_samples.py eval/heldout/raghu_samples.txt --check

Writes, under --out-dir (default eval/heldout):
    pii/texts_<prefix>.jsonl, injection/attacks_<prefix>.jsonl, injection/benign_<prefix>.jsonl

The tag format is documented in ai_platform_guardrails.eval_tags. Every gold span is
validated, and samples that also appear in the dev corpus (eval/) are rejected. This
script never runs the detectors. --check exits 1 if the JSONL is out of date.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from ai_platform_guardrails.eval_tags import TagError, find_overlaps, parse_tagged_text

ROOT = Path(__file__).resolve().parents[1]


def _dev_texts(dev_dir: Path) -> list[str]:
    texts: list[str] = []
    for path in (
        dev_dir / "pii" / "texts.jsonl",
        dev_dir / "injection" / "attacks.jsonl",
        dev_dir / "injection" / "benign.jsonl",
    ):
        if path.exists():
            texts += [
                json.loads(x)["text"] for x in path.read_text(encoding="utf-8").splitlines() if x
            ]
    return texts


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert tagged samples to eval JSONL.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--prefix", help="id prefix (default: file name before '_samples')")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "eval" / "heldout")
    parser.add_argument("--dev-dir", type=Path, default=ROOT / "eval")
    parser.add_argument("--check", action="store_true", help="exit 1 if outputs are stale")
    args = parser.parse_args(argv)
    prefix = args.prefix or args.source.stem.removesuffix("_samples")

    try:
        parsed = parse_tagged_text(args.source.read_text(encoding="utf-8"), prefix)
    except TagError as exc:
        print(f"{args.source}: invalid samples:\n{exc}", file=sys.stderr)
        return 1
    leaks = find_overlaps(parsed.pii + parsed.attacks + parsed.benign, _dev_texts(args.dev_dir))
    if leaks:
        print(f"samples also in the dev corpus: {', '.join(leaks)}", file=sys.stderr)
        return 1

    outputs = {
        args.out_dir / "pii" / f"texts_{prefix}.jsonl": _jsonl(parsed.pii),
        args.out_dir / "injection" / f"attacks_{prefix}.jsonl": _jsonl(parsed.attacks),
        args.out_dir / "injection" / f"benign_{prefix}.jsonl": _jsonl(parsed.benign),
    }
    if args.check:
        stale = [str(p) for p, body in outputs.items()
                 if not p.exists() or p.read_text(encoding="utf-8") != body]  # fmt: skip
        if stale:
            print("out of date (re-run the converter): " + ", ".join(stale), file=sys.stderr)
            return 1
        print("up to date")
        return 0
    for path, body in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    counts = Counter(e["type"] for s in parsed.pii for e in s["entities"])
    negatives = sum(1 for s in parsed.pii if not s["entities"])
    print(f"{prefix}: {len(parsed.pii)} PII samples ({sum(counts.values())} entities, "
          f"{negatives} with no PII), {len(parsed.attacks)} attacks, "
          f"{len(parsed.benign)} benign")  # fmt: skip
    for etype, n in sorted(counts.items()):
        print(f"  {etype}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
