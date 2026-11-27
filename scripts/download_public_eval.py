"""Download the public prompt-injection test set used as an independent check.

    uv run python scripts/download_public_eval.py
    uv run python scripts/download_public_eval.py --split train

Dataset: deepset/prompt-injections on Hugging Face (label 1 = injection, 0 = benign; mostly
English and German). Licence on the dataset card: Apache-2.0.

- ``--split test`` (default, 116 rows) -> eval/public/injection/: **reporting only**.
- ``--split train`` (546 rows) -> eval/public/train/injection/: the only public data that
  may be used for tuning rules and thresholds (guardrails-v2 onwards).

Nothing from it is committed: rows are written to eval/public/ (gitignored). The script
refuses to continue if the dataset has moved past the pinned revision, or if the rows'
SHA-256 differs from the one recorded here, so every score is on the same data. It uses
the datasets-server JSON API (stdlib only, no parquet reader needed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATASET = "deepset/prompt-injections"
PINNED_REVISION = "4f61ecb038e9c3fb77e21034b22511b523772cdd"
# split -> (rows, sha256 of json.dumps(rows, ensure_ascii=False, sort_keys=True,
# separators=(",", ":")) where rows = [{"text": ..., "label": ...}] in row order).
EXPECTED: dict[str, tuple[int, str]] = {
    "test": (116, "15927a81d0bac6659c08d55fe30085dcfd50485a840ec11f2d024ea44c670c80"),
    "train": (546, "58264230040d84c379d3cefd9e3082aae9712ed1217e410ee971710301663ce0"),
}
# Where each split is written, relative to --out-dir. Test stays where Phase 0.5 put it.
SUBDIR = {"test": ".", "train": "train"}
HUB_API = "https://huggingface.co/api/datasets/"
ROWS_API = "https://datasets-server.huggingface.co/rows"
PAGE = 100
TIMEOUT_S = 30


class DownloadError(RuntimeError):
    pass


def _get_json(url: str) -> dict[str, Any]:
    if not url.startswith("https://"):  # pragma: no cover - constants only
        raise DownloadError(f"refusing non-HTTPS URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "guardrails-eval/1.0"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 - https only
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise DownloadError(f"unexpected response from {url}")
    return data


def rows_sha256(rows: list[dict[str, Any]]) -> str:
    blob = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def fetch_rows(split: str = "test") -> list[dict[str, Any]]:
    info = _get_json(HUB_API + DATASET)
    if info.get("sha") != PINNED_REVISION:
        raise DownloadError(
            f"{DATASET} is at revision {info.get('sha')}, not the pinned {PINNED_REVISION}; "
            "scores would not be comparable. Review the change before updating the pin."
        )
    rows: list[dict[str, Any]] = []
    while True:
        query = urllib.parse.urlencode({"dataset": DATASET, "config": "default", "split": split,
                                        "offset": len(rows), "length": PAGE})  # fmt: skip
        page = _get_json(f"{ROWS_API}?{query}")["rows"]
        rows += [{"text": r["row"]["text"], "label": int(r["row"]["label"])} for r in page]
        if len(page) < PAGE:
            break
    return rows


def to_eval_files(
    rows: list[dict[str, Any]], split: str = "test"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into the eval attack/benign JSONL shapes (all inputs are user chat prompts)."""
    attacks, benign = [], []
    for i, r in enumerate(rows):
        sample = {"id": f"deepset-{split}-{i:03d}", "context": "chat", "text": r["text"]}
        if r["label"] == 1:
            attacks.append({**sample, "category": "deepset"})
        else:
            benign.append(sample)
    return attacks, benign


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download the public injection test set.")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "eval" / "public")
    parser.add_argument("--split", choices=sorted(EXPECTED), default="test")
    args = parser.parse_args(argv)
    try:
        rows = fetch_rows(args.split)
    except (OSError, DownloadError, KeyError, ValueError) as exc:
        print(f"download failed: {exc}", file=sys.stderr)
        return 1
    digest = rows_sha256(rows)
    n_expected, sha_expected = EXPECTED[args.split]
    if len(rows) != n_expected or digest != sha_expected:
        print(f"content check failed: {len(rows)} rows, sha256 {digest}", file=sys.stderr)
        return 1
    attacks, benign = to_eval_files(rows, args.split)
    out = args.out_dir / SUBDIR[args.split] / "injection"
    out.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.split == "test" else f"_{args.split}"
    for name, items in (
        (f"attacks_deepset{suffix}.jsonl", attacks),
        (f"benign_deepset{suffix}.jsonl", benign),
    ):
        body = "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items)
        (out / name).write_text(body, encoding="utf-8")
    print(f"{DATASET}@{PINNED_REVISION[:7]} {args.split}: {len(attacks)} attacks, "
          f"{len(benign)} benign -> {out}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
