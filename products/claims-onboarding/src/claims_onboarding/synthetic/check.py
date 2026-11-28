"""The two-way answer-key checker: the ground truth for every later phase (ADR 0010).

For every file in the manifest it confirms:

1. the file's bytes match the manifest (SHA-256 and size), and so do both keys;
2. the mapping key lists exactly the file's headers, in order; targets are dictionary
   fields or ``unmapped``; no target is used twice; every required field is mapped;
3. **every keyed error is real** and **nothing else is wrong**: the rule checks
   (``rules.py``) on the file, read in its own format with its mapping key, find exactly
   the error key's ``(row, field, rule, severity)`` set, and each ``line`` matches the
   file's layout;
4. for client 1, the legacy subset equals the original 52-error answer key exactly.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from claims_onboarding.synthetic.dictionary import SYNTHETIC_DIR, load_dictionary
from claims_onboarding.synthetic.formats import FileFormat, read
from claims_onboarding.synthetic.rules import Finding, check_rows


def _format(spec: dict[str, Any]) -> FileFormat:
    return FileFormat(kind=spec["kind"], delimiter=spec["delimiter"], encoding=spec["encoding"],
                      bom=spec["bom"], preamble=tuple(spec["preamble"]),
                      footer_total=spec["footer_total"], extension=spec["extension"])  # fmt: skip


def check_file(entry: dict[str, Any], root: Path = SYNTHETIC_DIR) -> list[str]:
    """Problems with one manifest entry (empty when the file and its keys agree)."""
    where = entry["path"]
    data = (root / entry["path"]).read_bytes()
    problems: list[str] = []
    if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["bytes"]:
        problems.append(f"{where}: bytes don't match the manifest")
    keys: dict[str, Any] = {}
    for kind in ("mapping_key", "error_key"):
        raw = (root / entry[kind]["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry[kind]["sha256"]:
            problems.append(f"{where}: {kind} doesn't match the manifest")
        keys[kind] = json.loads(raw)

    fmt = _format(entry["format"])
    headers, rows = read(fmt, data)
    columns = keys["mapping_key"]["columns"]
    dictionary = load_dictionary()
    if [c["header"] for c in columns] != headers:
        problems.append(f"{where}: mapping key headers differ from the file's headers")
    targets = [c["target"] for c in columns if c["target"] != "unmapped"]
    if unknown := sorted(set(targets) - set(dictionary.names)):
        problems.append(f"{where}: unknown targets {unknown}")
    if len(targets) != len(set(targets)):
        problems.append(f"{where}: a target is mapped twice")
    if missing := [f.name for f in dictionary.fields if f.required and f.name not in targets]:
        problems.append(f"{where}: required fields not mapped: {missing}")
    if len(rows) != entry["rows"]:
        problems.append(f"{where}: {len(rows)} rows, manifest says {entry['rows']}")
    if bad := [i + 1 for i, r in enumerate(rows) if len(r) != len(headers)]:
        problems.append(f"{where}: rows with the wrong number of fields: {bad[:5]}")
        return problems

    index = {c["header"]: i for i, c in enumerate(columns)}
    data_rows = [{c["target"]: r[index[c["header"]]] for c in columns if c["target"] != "unmapped"}
                 for r in rows]  # fmt: skip
    found = set(check_rows(data_rows, dictionary.for_date_format(entry["date_format"]),
                           amount_style=entry["amount_style"],
                           as_of=date.fromisoformat(entry["as_of"])))  # fmt: skip
    errors = keys["error_key"]["errors"]
    keyed = {Finding(e["row"], e["field"], e["rule"], e["severity"]) for e in errors}
    if missed := sorted(keyed - found):
        problems.append(f"{where}: keyed errors the checks don't find: {missed[:5]}")
    if unkeyed := sorted(found - keyed):
        problems.append(f"{where}: errors not in the key: {unkeyed[:5]}")
    if len(keyed) != len(errors) or len(errors) != entry["planted_errors"]:
        problems.append(f"{where}: error key has duplicates or a wrong count")
    if wrong := [e for e in errors if e["line"] != fmt.header_line + e["row"]]:
        problems.append(f"{where}: wrong line numbers, e.g. {wrong[0]}")

    if entry.get("legacy"):
        legacy = json.loads((root / Path(entry["path"]).parent / "legacy_answer_key.json")
                            .read_text(encoding="utf-8"))  # fmt: skip
        ours = {(e["line"], e["field"], e["rule"]) for e in errors if e.get("origin") == "legacy"}
        theirs = {(e["line"], e["field"], e["rule"]) for e in legacy["planted_errors"]}
        if ours != theirs or len(theirs) != 52:
            problems.append(f"{where}: legacy errors differ from the original answer key")
    return problems


def check_all(root: Path = SYNTHETIC_DIR) -> list[str]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return [p for entry in manifest["files"] for p in check_file(entry, root)]
