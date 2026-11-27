"""Download the pinned prompt-injection classifier and verify every file.

    uv run python scripts/download_guard_model.py            # into $GUARD_MODEL_DIR
    uv run python scripts/download_guard_model.py --check    # verify only, no network

Fetches only the files listed in ``ai_platform_guardrails.injection.ml.GUARD_SMALL``
(ONNX model, tokenizer.json, config.json, LICENSE, NOTICE) at the pinned Hub revision,
checks each against its pinned size and SHA-256, and moves it into place only if it
matches. Files already present and intact are not downloaded again (CI cache hits).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import urllib.request
from pathlib import Path

from ai_platform_guardrails.injection.ml import (
    GUARD_SMALL,
    ModelSpec,
    ModelUnavailable,
    model_dir,
    sha256_file,
    verify_files,
)

TIMEOUT_S = 120


def _fetch(url: str, dest: Path) -> None:
    if not url.startswith("https://huggingface.co/"):  # pragma: no cover - constants only
        raise ValueError(f"refusing unexpected URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "guardrails-model-fetch/1.0"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp, dest.open("wb") as out:  # noqa: S310
        while chunk := resp.read(1 << 20):
            out.write(chunk)


def download(spec: ModelSpec, target: Path) -> list[str]:
    """Fetch missing or bad files; returns the paths that were downloaded."""
    fetched = []
    for f in spec.files:
        dest = target / f.path
        if dest.is_file() and dest.stat().st_size == f.size and sha256_file(dest) == f.sha256:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            _fetch(spec.url_base + f.path, tmp_path)
            if tmp_path.stat().st_size != f.size or sha256_file(tmp_path) != f.sha256:
                raise ModelUnavailable(f"{f.path}: downloaded file does not match its pin")
            tmp_path.replace(dest)
            fetched.append(f.path)
        finally:
            tmp_path.unlink(missing_ok=True)
    verify_files(spec, target)
    return fetched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download the pinned injection classifier.")
    parser.add_argument("--dir", type=Path, help="target directory (default: GUARD_MODEL_DIR)")
    parser.add_argument("--check", action="store_true", help="verify only, never download")
    args = parser.parse_args(argv)
    target = args.dir or model_dir(GUARD_SMALL)
    try:
        if args.check:
            verify_files(GUARD_SMALL, target)
            fetched: list[str] = []
        else:
            fetched = download(GUARD_SMALL, target)
    except (OSError, ModelUnavailable) as exc:
        print(f"model not ready: {exc}", file=sys.stderr)
        return 1
    state = f"downloaded {', '.join(fetched)}" if fetched else "already present"
    print(f"{GUARD_SMALL.repo}@{GUARD_SMALL.revision[:7]} verified in {target} ({state})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
