"""Score PII detection and injection defence on the synthetic corpus in eval/.

Prints recall/precision per entity type, injection detection rate and false-positive
rate, and writes docs/guardrails-results.md. With --check, exits 1 below the targets
(development corpus only).

--heldout adds held-out v1 (tuning data since guardrails-v2), --heldout-v2 the unseen
held-out v2, and --public the downloaded deepset test split, side by side. Those columns
are report-only: they never change the exit code.

    uv run python scripts/guardrails_report.py --check
    uv run python scripts/guardrails_report.py --check --heldout --heldout-v2 --public
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

Same detectors, same scoring rules, up to four sets:
- **Development:** the corpus above. Detectors were fixed against it, so it is optimistic.
- **Held-out v1:** `eval/heldout`. Frozen before its first scoring; **seen** since
  guardrails-v2 (used to choose thresholds and check PII fixes), so it is tuning data now.
- **Held-out v2:** `eval/heldout_v2`. Frozen before any v2 change and scored once, at the
  end of guardrails-v2: the honest number. Same author as the detectors, so not independent.
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
    parser.add_argument("--heldout", action="store_true", help="add held-out v1 (tuning data)")
    parser.add_argument("--heldout-v2", action="store_true", help="add held-out v2 (unseen)")
    parser.add_argument("--public", action="store_true", help="add the public injection set")
    args = parser.parse_args(argv)
    configure_logging("WARNING")
    pii = evaluate_pii(args.eval_dir)
    inj = evaluate_injection(args.eval_dir)
    report = render_markdown(pii, inj, spacy_model_name())
    notes = args.eval_dir / "RESULTS_NOTES.md"
    if notes.exists():
        report += "\n" + notes.read_text(encoding="utf-8")
    if args.heldout or args.heldout_v2 or args.public:
        sets = [EvalSet("Development (gates CI)", pii, inj)]
        if args.heldout:
            v1 = evaluate_heldout(args.eval_dir / "heldout")
            sets.append(EvalSet("Held-out v1 (tuning data)", *v1))
        if args.heldout_v2:
            v2 = evaluate_heldout(args.eval_dir / "heldout_v2")
            sets.append(EvalSet("Held-out v2 (unseen)", *v2))
        if args.public:
            try:
                public = evaluate_public(args.eval_dir / "public")
            except FileNotFoundError as exc:
                print(exc, file=sys.stderr)
                return 1
            sets.append(EvalSet("Public: deepset test", None, public))
        report += "\n" + HELDOUT_INTRO + "\n" + render_comparison(sets)
        for extra in ("V2_NOTES.md", "CLASSIFIER_COMPARISON.md"):
            path = args.eval_dir / extra
            if args.heldout_v2 and path.exists():
                report += "\n" + path.read_text(encoding="utf-8")
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
