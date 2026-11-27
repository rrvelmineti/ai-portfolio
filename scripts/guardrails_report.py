"""Score PII detection and injection defence on the synthetic corpus in eval/.

Prints recall/precision per entity type, injection detection rate and false-positive
rate, and writes docs/guardrails-results.md. With --check, exits 1 below the targets.

    uv run python scripts/guardrails_report.py --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai_platform_common.logging import configure_logging
from ai_platform_guardrails.evaluation import (
    evaluate_injection,
    evaluate_pii,
    render_markdown,
    target_failures,
)
from ai_platform_guardrails.pii.config import spacy_model_name

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="exit 1 if a target is missed")
    parser.add_argument("--eval-dir", type=Path, default=ROOT / "eval")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "guardrails-results.md")
    args = parser.parse_args(argv)
    configure_logging("WARNING")
    pii = evaluate_pii(args.eval_dir)
    inj = evaluate_injection(args.eval_dir)
    report = render_markdown(pii, inj, spacy_model_name())
    notes = args.eval_dir / "RESULTS_NOTES.md"
    if notes.exists():
        report += "\n" + notes.read_text(encoding="utf-8")
    args.out.write_text(report, encoding="utf-8")
    print(report)
    failures = target_failures(pii, inj)
    if failures:
        print("TARGETS MISSED:\n- " + "\n- ".join(failures))
        return 1 if args.check else 0
    print("All targets met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
