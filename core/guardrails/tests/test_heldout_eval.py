"""Held-out and public evaluation: freeze check, scoring plumbing, report rendering.

Report-only: nothing here asserts accuracy on the held-out or public sets.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from ai_platform_guardrails.evaluation import (
    EntityScore,
    EvalSet,
    InjectionScore,
    evaluate_heldout,
    evaluate_public,
    load_corpus,
    render_comparison,
)

ROOT = Path(__file__).resolve().parents[3]
HELDOUT = ROOT / "eval" / "heldout"


def _load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ freeze


def test_heldout_files_match_the_freeze_manifest() -> None:
    lines = (HELDOUT / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    for line in lines:
        digest, name = line.split(maxsplit=1)
        actual = hashlib.sha256((HELDOUT / name).read_bytes()).hexdigest()
        assert actual == digest, f"{name} changed after the freeze (see eval/heldout/README.md)"


HELDOUT_V2 = ROOT / "eval" / "heldout_v2"
V2_SOURCE_SHA256 = "b0649dec9fe63ca8f19ce68359f1e1ff8ef57272469f2bcf1ed542db9eebe3c3"


def test_heldout_v2_matches_its_freeze_manifest_without_scoring() -> None:
    """v2 is checked for integrity only; it must not be scored before the end of guardrails-v2."""
    src = (HELDOUT_V2 / "claude_v2_samples.txt").read_bytes()
    assert hashlib.sha256(src).hexdigest() == V2_SOURCE_SHA256
    lines = (HELDOUT_V2 / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    for line in lines:
        digest, name = line.split(maxsplit=1)
        actual = hashlib.sha256((HELDOUT_V2 / name).read_bytes()).hexdigest()
        assert actual == digest, f"{name} changed after the v2 freeze"


def test_heldout_v2_jsonl_is_generated_from_the_source(capsys: pytest.CaptureFixture[str]) -> None:
    convert = _load_script("convert_tagged_samples")
    assert convert.main([str(HELDOUT_V2 / "claude_v2_samples.txt"), "--check",
                         "--out-dir", str(HELDOUT_V2)]) == 0  # fmt: skip
    assert "up to date" in capsys.readouterr().out


def test_heldout_jsonl_is_generated_from_the_source(capsys: pytest.CaptureFixture[str]) -> None:
    convert = _load_script("convert_tagged_samples")
    assert convert.main([str(HELDOUT / "claude_samples.txt"), "--check"]) == 0
    assert "up to date" in capsys.readouterr().out


def test_converter_writes_files_and_rejects_dev_overlap(tmp_path: Path) -> None:
    convert = _load_script("convert_tagged_samples")
    src = tmp_path / "t_samples.txt"
    src.write_text("ATTACK: zzz unique attack\nCall [PERSON: Ann Lee]\nBENIGN: hi\n", "utf-8")
    out = tmp_path / "out"
    assert convert.main([str(src), "--out-dir", str(out), "--check"]) == 1  # not written yet
    assert convert.main([str(src), "--out-dir", str(out)]) == 0
    assert load_corpus(out / "injection", "attacks")[0]["id"] == "t-atk-000"
    dev = tmp_path / "dev" / "injection"
    dev.mkdir(parents=True)
    (dev / "attacks.jsonl").write_text(json.dumps({"text": "ZZZ unique attack"}) + "\n", "utf-8")
    args = [str(src), "--out-dir", str(out), "--dev-dir", str(tmp_path / "dev")]
    assert convert.main(args) == 1
    src.write_text("Call [PHON: 1]\n", "utf-8")
    assert convert.main([str(src), "--out-dir", str(out)]) == 1


# ------------------------------------------------------------------ scoring plumbing


def test_heldout_scoring_counts_every_sample() -> None:
    pii, inj = evaluate_heldout(HELDOUT)
    gold = sum(len(s["entities"]) for s in load_corpus(HELDOUT / "pii", "texts"))
    assert sum(s.tp + s.fn for s in pii.values()) == gold == 83
    assert (inj.attacks, inj.benign) == (40, 40)
    assert 0 <= inj.detected <= inj.attacks


def test_public_scoring_needs_downloaded_data(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="download_public_eval"):
        evaluate_public(tmp_path)
    inj_dir = tmp_path / "injection"
    inj_dir.mkdir()
    row = {"id": "p-0", "context": "chat", "category": "deepset", "text": "What is a deductible?"}
    (inj_dir / "attacks_x.jsonl").write_text(json.dumps(row) + "\n", "utf-8")
    (inj_dir / "benign_x.jsonl").write_text(json.dumps({**row, "id": "p-1"}) + "\n", "utf-8")
    score = evaluate_public(tmp_path)
    assert (score.attacks, score.benign) == (1, 1)


def test_render_comparison_side_by_side() -> None:
    dev = EvalSet(
        "Dev",
        {"PHONE": EntityScore(tp=10), "DATE_TIME": EntityScore(fp=2, fp_ids=["d-1", "d-2"])},
        InjectionScore(10, 10, 10, 0, [], [], {"direct": (10, 10)}),
    )
    held = EvalSet(
        "Held",
        {"PHONE": EntityScore(tp=3, fn=1, fp=1, missed_ids=["h-2"], fp_ids=["h-9"])},
        InjectionScore(4, 3, 4, 1, ["h-atk-1"], ["h-ben-0"], {"task_hijack": (0, 1)}),
    )
    public = EvalSet("Public", None, InjectionScore(2, 1, 0, 0, ["p-1"], [], {}))
    md = render_comparison([dev, held, public])
    assert "| Entity | Dev | Held | Public | Dev target |" in md
    assert "| PHONE | 10/10 (100.0%) | 3/4 (75.0%), 1 false pos. | not measured | 100% |" in md
    assert "| DATE_TIME | no gold; 2 false pos. | – | not measured |  |" in md
    assert "| Attacks detected | 10/10 (100.0%) | 3/4 (75.0%) | 1/2 (50.0%) | 100% |" in md
    assert "| Benign false positives | 0/10 (0.0%) | 1/4 (25.0%) | – | < 10% |" in md
    assert "| task_hijack | 0/1 |" in md
    assert "- PHONE missed: h-2" in md and "- PHONE false pos.: h-9" in md
    assert "- injection missed: h-atk-1" in md and "- injection false positives: h-ben-0" in md
    assert "- injection missed: p-1" in md
    assert "Dev: misses" not in md  # the development set's misses are listed elsewhere


def test_report_script_adds_comparison(tmp_path: Path) -> None:
    eval_dir = tmp_path / "eval"
    for sub in ("pii", "injection", "heldout"):
        src = ROOT / "eval" / sub
        for f in src.rglob("*"):
            if f.is_file():
                dest = eval_dir / sub / f.relative_to(src)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(f.read_bytes())
    report = _load_script("guardrails_report")
    out = tmp_path / "r.md"
    assert report.main(["--eval-dir", str(eval_dir), "--out", str(out), "--public"]) == 1
    assert (
        report.main(["--check", "--heldout", "--eval-dir", str(eval_dir), "--out", str(out)]) == 0
    )
    text = out.read_text(encoding="utf-8")
    assert "## Held-out and public evaluation (report only)" in text
    assert "Held-out (report only)" in text and "Public: deepset" not in text


# ------------------------------------------------------------------ public download (offline)


def _fake_api(rows: list[dict[str, Any]], sha: str) -> Any:  # noqa: ANN401 - test stub
    def get_json(url: str) -> dict[str, Any]:
        if url.startswith("https://huggingface.co/api/datasets/"):
            return {"sha": sha}
        offset = int(url.split("offset=")[1].split("&")[0])
        length = int(url.split("length=")[1].split("&")[0])
        page = rows[offset : offset + length]
        return {"rows": [{"row_idx": offset + i, "row": r} for i, r in enumerate(page)]}

    return get_json


def test_download_paginates_checks_and_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dl = _load_script("download_public_eval")
    rows = [{"text": f"t{i}", "label": i % 2} for i in range(150)]
    monkeypatch.setattr(dl, "_get_json", _fake_api(rows, dl.PINNED_REVISION))
    assert dl.fetch_rows() == rows
    monkeypatch.setitem(dl.EXPECTED, "test", (150, dl.rows_sha256(rows)))
    monkeypatch.setitem(dl.EXPECTED, "train", (150, dl.rows_sha256(rows)))
    assert dl.main(["--out-dir", str(tmp_path), "--split", "train"]) == 0
    train = load_corpus(tmp_path / "train" / "injection", "attacks")
    assert train[0]["id"] == "deepset-train-001"
    assert evaluate_public_paths_untouched(tmp_path)
    assert dl.main(["--out-dir", str(tmp_path)]) == 0
    attacks = load_corpus(tmp_path / "injection", "attacks")
    benign = load_corpus(tmp_path / "injection", "benign")
    assert len(attacks) == len(benign) == 75
    assert attacks[0] == {"id": "deepset-test-001", "context": "chat", "text": "t1",
                          "category": "deepset"}  # fmt: skip
    assert "category" not in benign[0]


def evaluate_public_paths_untouched(root: Path) -> bool:
    """Train rows must never land where the test-split scorer reads."""
    return not list((root / "injection").glob("*.jsonl")) if (root / "injection").exists() else True


def test_download_refuses_changed_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dl = _load_script("download_public_eval")
    rows = [{"text": "a", "label": 1}]
    monkeypatch.setattr(dl, "_get_json", _fake_api(rows, "f" * 40))
    assert dl.main(["--out-dir", str(tmp_path)]) == 1  # moved past the pinned revision
    monkeypatch.setattr(dl, "_get_json", _fake_api(rows, dl.PINNED_REVISION))
    assert dl.main(["--out-dir", str(tmp_path)]) == 1  # content hash mismatch
    assert not (tmp_path / "injection").exists()


def test_download_rejects_non_object_response(monkeypatch: pytest.MonkeyPatch) -> None:
    dl = _load_script("download_public_eval")

    class Resp:
        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[1, 2]"

    monkeypatch.setattr(dl.urllib.request, "urlopen", lambda *a, **k: Resp())
    with pytest.raises(dl.DownloadError, match="unexpected response"):
        dl._get_json("https://huggingface.co/api/datasets/x")
