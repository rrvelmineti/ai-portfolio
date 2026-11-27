"""Precision/recall trade-off for the injection thresholds, on TUNING data only.

    uv run python scripts/download_public_eval.py --split train
    uv run python scripts/tune_injection_thresholds.py

Reads held-out v1 (seen, tuning data since guardrails-v2) and the deepset TRAIN split.
Never reads held-out v2 or the deepset test split. Writes docs/injection-thresholds.md
and docs/injection-thresholds.svg.

Score = max(rules score, classifier probability), the approved combination (ADR 0007).
Caveat: guard-small was trained on deepset train, so train-split numbers are inflated (†).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from ai_platform_common.logging import configure_logging
from ai_platform_guardrails.evaluation import load_corpus
from ai_platform_guardrails.injection.ml import OnnxPromptClassifier
from ai_platform_guardrails.injection.scanner import scan

ROOT = Path(__file__).resolve().parents[1]
GRID = [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.97, 0.99]


def is_evaluation_only(directory: Path) -> bool:
    """Held-out v2 and the deepset test split must never be used for tuning."""
    posix = directory.as_posix()
    return "heldout_v2" in posix or posix.rstrip("/").endswith("public/injection")


@dataclass(frozen=True)
class Row:
    sid: str
    source: str  # "v1" or "train"
    context: str
    label: int
    rules: float
    clf: float

    @property
    def combined(self) -> float:
        return max(self.rules, self.clf)


def load_rows(eval_dir: Path) -> list[Row]:
    sets = [
        ("v1", eval_dir / "heldout" / "injection"),
        ("train", eval_dir / "public" / "train" / "injection"),
    ]
    clf = OnnxPromptClassifier()
    rows: list[Row] = []
    for source, directory in sets:
        if is_evaluation_only(directory):
            raise SystemExit(f"refusing evaluation-only data: {directory}")
        for label, stem in ((1, "attacks"), (0, "benign")):
            samples = load_corpus(directory, stem)
            if not samples:
                raise SystemExit(f"no {stem} in {directory} (download the train split first)")
            for s in samples:
                rows.append(Row(s["id"], source, s.get("context", "chat"), label,
                                scan(s["text"]).score, clf.score(s["text"]).score))  # fmt: skip
    return rows


def _rate(rows: list[Row], t: float, *, label: int, use: str = "combined") -> tuple[int, int]:
    sel = [r for r in rows if r.label == label]
    hit = sum(1 for r in sel if getattr(r, use) >= t)
    return hit, len(sel)


def _fmt(k: int, n: int) -> str:
    return f"{k}/{n} ({k / n:.0%})" if n else "–"


def table(rows: list[Row]) -> list[str]:
    v1 = [r for r in rows if r.source == "v1"]
    v1_chat = [r for r in v1 if r.context == "chat"]
    tr = [r for r in rows if r.source == "train"]
    out = [
        "| Threshold | v1 attacks ≥ t | v1 benign ≥ t (all 40) | v1 chat benign ≥ t (20) | "
        "train attacks ≥ t † | train benign ≥ t † | precision (v1 + train) |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for t in GRID:
        tp = sum(1 for r in rows if r.label == 1 and r.combined >= t)
        fp = sum(1 for r in rows if r.label == 0 and r.combined >= t)
        prec = f"{tp / (tp + fp):.1%}" if tp + fp else "–"
        out.append(
            f"| {t:.2f} | {_fmt(*_rate(v1, t, label=1))} | {_fmt(*_rate(v1, t, label=0))} | "
            f"{_fmt(*_rate(v1_chat, t, label=0))} | {_fmt(*_rate(tr, t, label=1))} | "
            f"{_fmt(*_rate(tr, t, label=0))} | {prec} |"
        )
    return out


RULES_BLOCK, RULES_FLAG = 0.7, 0.4  # unchanged rules thresholds (Phase 0.5)


def action(r: Row, clf_block: float, clf_flag: float) -> str:
    """Per-layer decision: chat may be blocked; documents are only ever flagged."""
    if r.context == "chat" and (r.rules >= RULES_BLOCK or r.clf >= clf_block):
        return "block"
    return "flag" if r.rules >= RULES_FLAG or r.clf >= clf_flag else "none"


_CAUGHT = {True: {"block"}, False: {"block", "flag"}}  # chat must be blocked; docs flagged


def per_layer_table(rows: list[Row]) -> list[str]:
    out = [
        "| Classifier block | Classifier flag | v1 attacks caught | v1 benign **blocked** | "
        "v1 benign flagged or blocked | train attacks caught † | train benign flagged † |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cb in (0.7, 0.75, 0.8, 0.85, 0.9, 0.95):
        for cf in (0.5, 0.6, 0.7):
            cells = []
            for source in ("v1", "train"):
                sel = [r for r in rows if r.source == source]
                atk = [r for r in sel if r.label == 1]
                caught = sum(1 for r in atk if action(r, cb, cf) in _CAUGHT[r.context == "chat"])
                ben = [r for r in sel if r.label == 0]
                blocked = sum(1 for r in ben if action(r, cb, cf) == "block")
                flagged = sum(1 for r in ben if action(r, cb, cf) != "none")
                cells.append((caught, len(atk), blocked, flagged, len(ben)))
            (va, vn, vb, vf, vbn), (ta, tn, _tb, tf, tbn) = cells
            out.append(f"| {cb:.2f} | {cf:.2f} | {_fmt(va, vn)} | {_fmt(vb, vbn)} | "
                       f"{_fmt(vf, vbn)} | {_fmt(ta, tn)} | {_fmt(tf, tbn)} |")  # fmt: skip
    return out


def floor_rows(rows: list[Row]) -> list[str]:
    """Benign v1 samples the rules alone score at or above the current block level."""
    return [f"- `{r.sid}` ({r.context}): rules {r.rules:.2f}, classifier {r.clf:.2f}"
            for r in rows if r.source == "v1" and r.label == 0 and r.rules >= 0.4]  # fmt: skip


def svg(rows: list[Row], path: Path) -> None:
    """Recall and false-positive rate versus threshold (v1 only), plain SVG."""
    w, h, pad = 640, 360, 50
    v1 = [r for r in rows if r.source == "v1"]
    ts = [i / 100 for i in range(1, 100)]

    def series(label: int) -> list[tuple[float, float]]:
        n = sum(1 for r in v1 if r.label == label)
        return [(t, sum(1 for r in v1 if r.label == label and r.combined >= t) / n) for t in ts]

    def pts(data: list[tuple[float, float]]) -> str:
        return " ".join(f"{pad + x * (w - 2 * pad):.1f},{h - pad - y * (h - 2 * pad):.1f}"
                        for x, y in data)  # fmt: skip

    five = h - pad - 0.05 * (h - 2 * pad)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="sans-serif" '
        'font-size="12"><rect width="100%" height="100%" fill="white"/>',
        f'<line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}" stroke="#444"/>',
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{h - pad}" stroke="#444"/>',
        f'<line x1="{pad}" y1="{five:.1f}" x2="{w - pad}" y2="{five:.1f}" stroke="#999" '
        'stroke-dasharray="4 4"/>',
        f'<text x="{w - pad - 4}" y="{five - 4:.1f}" text-anchor="end" fill="#666">5% line</text>',
        f'<polyline fill="none" stroke="#1f6feb" stroke-width="2" points="{pts(series(1))}"/>',
        f'<polyline fill="none" stroke="#d1242f" stroke-width="2" points="{pts(series(0))}"/>',
        f'<text x="{pad + 8}" y="{pad + 14}" fill="#1f6feb">v1 attacks caught (recall)</text>',
        f'<text x="{pad + 8}" y="{pad + 30}" fill="#d1242f">v1 benign flagged (FP rate)</text>',
        f'<text x="{w / 2}" y="{h - 12}" text-anchor="middle">threshold on max(rules, '
        "classifier)</text>",
    ]
    for t in (0, 0.25, 0.5, 0.75, 1):
        x = pad + t * (w - 2 * pad)
        parts.append(f'<text x="{x:.1f}" y="{h - pad + 16}" text-anchor="middle">{t}</text>')
        y = h - pad - t * (h - 2 * pad)
        parts.append(f'<text x="{pad - 6}" y="{y + 4:.1f}" text-anchor="end">{t:.0%}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--eval-dir", type=Path, default=ROOT / "eval")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "injection-thresholds.md")
    args = parser.parse_args(argv)
    configure_logging("WARNING")
    rows = load_rows(args.eval_dir)
    svg_path = args.out.with_suffix(".svg")
    svg(rows, svg_path)
    lines = [
        "# Injection thresholds: trade-off on tuning data",
        "",
        "Generated by `scripts/tune_injection_thresholds.py` from held-out v1 (seen) and the "
        "deepset train split only. Score = max(rules, guard-small). † guard-small was trained "
        "on deepset train, so those columns are inflated.",
        "",
        f"![recall and false positives vs threshold]({svg_path.name})",
        "",
        *table(rows),
        "",
        "Benign v1 samples the **rules alone** already score at 0.4 or above (a floor no "
        "classifier threshold can remove):",
        "",
        *floor_rows(rows),
        "",
        "## Per-layer thresholds (rules keep 0.7 block / 0.4 flag)",
        "",
        "A single threshold on max(rules, classifier) above 0.85 would also raise the rules' "
        "block level, so two development attacks the rules score 0.80 would only be flagged. "
        "Keeping the rules' own thresholds and giving the classifier separate ones avoids that. "
        "Chat input is blocked at the block level and flagged between flag and block; content "
        'inside `wrap_untrusted` fences is only ever flagged. "Caught" = chat attack blocked, '
        "document attack flagged.",
        "",
        *per_layer_table(rows),
    ]
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
