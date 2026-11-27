"""Score PII detection and injection defence on the synthetic corpus in eval/.

Prints recall/precision per entity type, injection detection rate and false-positive
rate, and writes docs/guardrails-results.md. With --check, exits 1 below the targets
(development corpus only).

--heldout adds the frozen held-out set (eval/heldout) and --public the downloaded public
injection set (eval/public, see scripts/download_public_eval.py) side by side. Those
columns are report-only: they never change the exit code.

    uv run python scripts/guardrails_report.py --check
    uv run python scripts/guardrails_report.py --check --heldout --public
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai_platform_common.logging import configure_logging
from ai_platform_guardrails.evaluation import (
    EvalSet,
    evaluate_heldout,
    evaluate_injection,
    evaluate_pii,
    evaluate_public,
    render_comparison,
    render_markdown,
    target_failures,
)
from ai_platform_guardrails.pii.config import spacy_model_name

ROOT = Path(__file__).resolve().parents[1]
HELDOUT_INTRO = """## Held-out and public evaluation (report only)

Same detectors, same scoring rules, three sets:
- **Development:** the corpus above. Detectors were fixed against it, so it is optimistic.
- **Held-out:** `eval/heldout`, frozen before its first scoring run (see its README). It was
  written by the same assistant that built the detectors, so it is not independent either.
- **Public:** `deepset/prompt-injections` test split, injection only, downloaded by
  `scripts/download_public_eval.py` and never committed. The only independent check.

Only the development set gates CI. The generated miss lists give sample ids, never
text; the notes below quote a few synthetic values to explain the patterns.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="exit 1 if a target is missed")
    parser.add_argument("--eval-dir", type=Path, default=ROOT / "eval")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "guardrails-results.md")
    parser.add_argument("--heldout", action="store_true", help="add the held-out set")
    parser.add_argument("--public", action="store_true", help="add the public injection set")
    args = parser.parse_args(argv)
    configure_logging("WARNING")
    pii = evaluate_pii(args.eval_dir)
    inj = evaluate_injection(args.eval_dir)
    report = render_markdown(pii, inj, spacy_model_name())
    notes = args.eval_dir / "RESULTS_NOTES.md"
    if notes.exists():
        report += "\n" + notes.read_text(encoding="utf-8")
    if args.heldout or args.public:
        sets = [EvalSet("Development (gates CI)", pii, inj)]
        if args.heldout:
            sets.append(
                EvalSet("Held-out (report only)", *evaluate_heldout(args.eval_dir / "heldout"))
            )
        if args.public:
            try:
                public = evaluate_public(args.eval_dir / "public")
            except FileNotFoundError as exc:
                print(exc, file=sys.stderr)
                return 1
            sets.append(EvalSet("Public: deepset test (report only)", None, public))
        report += "\n" + HELDOUT_INTRO + "\n" + render_comparison(sets)
        heldout_notes = args.eval_dir / "HELDOUT_NOTES.md"
        if heldout_notes.exists():
            report += "\n" + heldout_notes.read_text(encoding="utf-8")
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
