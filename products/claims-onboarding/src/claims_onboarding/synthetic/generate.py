"""Generate every synthetic extract, its mapping key and error key, and the manifest.

    uv run python -m claims_onboarding.synthetic generate     # rewrites data/synthetic/

Deterministic: each file's random stream is seeded from ``SEED_BASE``, the client number
and the week, and Faker is seeded the same way, so the same versions produce the same
bytes (the manifest records the library versions and every file's SHA-256).

The error key is the *planting record*: each planted error is written down when it is
planted, never derived from the checks, so the two-way checker is a real test.
Client 1's week-1 file is a fixed asset (the ported original sample; see ``port_legacy``).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

from faker import Faker

from claims_onboarding.synthetic.dictionary import DATA_DIR, load_dictionary
from claims_onboarding.synthetic.formats import FileFormat, write
from claims_onboarding.synthetic.profiles import CLIENTS, Client, ErrorPlan
from claims_onboarding.synthetic.rules import DATE_FORMATS, check_rows, format_date

SYNTHETIC_DIR = DATA_DIR / "synthetic"
SEED_BASE = 2026
AS_OF = {1: date(2026, 11, 1), 2: date(2026, 11, 8)}
LEGACY_AS_OF = date(2026, 11, 15)  # the original sample was measured on 15 Nov 2026
# Real, public ICD-10-CM codes (CMS). Procedure codes are synthetic: Z + 4 digits.
ICD10_CODES = ("E11.9", "I10", "J06.9", "M54.50", "Z00.00", "R51.9", "K21.9", "F41.1",
               "N39.0", "E78.5", "J45.909", "M25.561", "R10.9", "Z23")  # fmt: skip
BAD_ICD10 = ("119.0", "E1", "DIABETES", "E11.95X7Q", "1E1.9")
PLACES = ("11", "21", "22", "23", "81", "02")
STATUSES = ("paid",) * 7 + ("denied", "pending", "adjusted")
BAD_STATUSES = ("approved", "closed", "in review")
ALLOWED_STATUS_RULE = "one of paid / denied / pending / adjusted"

RULE_FOR_KIND: dict[str, Callable[[str], str]] = {
    "required": lambda _f: "required",
    "unique": lambda _f: "unique",
    "bad_date": lambda fmt: f"date {fmt}",
    "future": lambda _f: "not in the future",
    "before_dob": lambda _f: "not before member_dob",
    "paid_before_service": lambda _f: "not before service_date",
    "icd": lambda _f: "ICD-10 format",
    "proc_len": lambda _f: "exactly 5 characters",
    "npi_len": lambda _f: "exactly 10 digits",
    "pos": lambda _f: "exactly 2 digits",
    "non_numeric": lambda _f: "number",
    "negative": lambda _f: "not negative",
    "paid_gt": lambda _f: "not greater than billed_amount",
    "allowed_gt": lambda _f: "not greater than billed_amount",
    "status": lambda _f: ALLOWED_STATUS_RULE,
}
# Every (field, kind) a client can receive, if it has the columns involved.
ALL_ERRORS: tuple[tuple[str, str], ...] = (
    ("claim_id", "required"), ("claim_id", "unique"), ("member_id", "required"),
    ("member_dob", "required"), ("member_dob", "bad_date"), ("provider_npi", "npi_len"),
    ("service_date", "bad_date"), ("service_date", "future"), ("service_date", "before_dob"),
    ("paid_date", "paid_before_service"), ("paid_date", "bad_date"),
    ("diagnosis_code", "required"), ("diagnosis_code", "icd"), ("procedure_code", "proc_len"),
    ("place_of_service", "pos"), ("billed_amount", "required"),
    ("billed_amount", "non_numeric"), ("allowed_amount", "negative"),
    ("allowed_amount", "allowed_gt"), ("paid_amount", "negative"),
    ("paid_amount", "non_numeric"), ("paid_amount", "paid_gt"), ("claim_status", "status"),
)  # fmt: skip
NEEDS: dict[str, tuple[str, ...]] = {
    "before_dob": ("member_dob",),
    "paid_before_service": ("service_date",),
    "paid_gt": ("billed_amount",),
    "allowed_gt": ("billed_amount",),
}


# ---------------------------------------------------------------- value helpers


def npi_check_digit(base9: str) -> str:
    """Luhn check digit for an NPI (computed over the ``80840`` prefix + 9 digits)."""
    total = 0
    for i, ch in enumerate(reversed("80840" + base9)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return str((10 - total % 10) % 10)


def npi_is_valid(npi: str) -> bool:
    return len(npi) == 10 and npi.isdigit() and npi_check_digit(npi[:9]) == npi[9]


def synthetic_npi(rng: random.Random) -> str:
    base = str(rng.choice((1, 2))) + f"{rng.randrange(10**8):08d}"
    return base + npi_check_digit(base)


def money(cents: int, style: str) -> str:
    sign, cents = ("-", -cents) if cents < 0 else ("", cents)
    if style == "currency":
        return f"{sign}${cents // 100:,}.{cents % 100:02d}"
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def cased(value: str, case: str) -> str:
    return {"upper": value.upper(), "title": value.title()}.get(case, value)


def _impossible_date(rng: random.Random, label: str) -> str:
    y, m, d = rng.choice(((2025, 2, 30), (2025, 4, 31), (2026, 2, 29), (2025, 6, 31)))
    order = {"Y": f"{y:04d}", "M": f"{m:02d}", "D": f"{d:02d}"}
    sep = "-" if "-" in label else "/"
    return sep.join(order[part[0]] for part in label.split(sep))


def _wrong_format(rng: random.Random, value: date, label: str) -> str:
    others = [lb for lb, (_, pat) in DATE_FORMATS.items() if pat != DATE_FORMATS[label][1]]
    return format_date(value, rng.choice(sorted(others)))


# ---------------------------------------------------------------- one file


@dataclass
class Built:
    data: bytes
    headers: list[str]
    mapping: list[dict[str, str]]
    errors: list[dict[str, Any]]
    rows: int


def _columns(client: Client, week: int) -> list[tuple[str, str]]:
    cols = list(client.columns)
    if week == 2:
        old, new = client.week2_rename
        cols = [(new if h == old else h, t) for h, t in cols]
        cols.append(client.week2_new_column)
    return cols


def _plan(client: Client, week: int, rng: random.Random, mapped: set[str]) -> ErrorPlan:
    if week == 1:
        return dict(client.errors_week1)
    options = [(f, k) for f, k in ALL_ERRORS
               if f in mapped and all(n in mapped for n in NEEDS.get(k, ()))]  # fmt: skip
    chosen = rng.sample(options, rng.randint(4, min(7, len(options))))
    plan = {pair: rng.randint(1, 8) for pair in chosen}
    if sum(plan.values()) < 5:
        plan[chosen[0]] += 5 - sum(plan.values())
    return plan


def build_file(client: Client, week: int) -> Built:
    seed = SEED_BASE * 1000 + client.number * 10 + week
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not security
    fake = Faker(client.faker_locale)
    fake.seed_instance(seed)
    as_of = AS_OF[week]
    columns = _columns(client, week)
    mapped = {t for _, t in columns if not t.startswith("extra:")}
    n = client.rows_week1 if week == 1 else client.rows_week2
    fmt, style = client.date_format, client.amount_style
    procedures = [f"Z{rng.randrange(10_000):04d}" for _ in range(12)]
    first_id = 1 if week == 1 else 500_000

    values: list[dict[str, str]] = []
    canon: list[dict[str, Any]] = []
    for i in range(n):
        dob = date(1940, 1, 1) + timedelta(days=rng.randrange(365 * 75))
        service = date(2025, 1, 1) + timedelta(days=rng.randrange(560))
        paid_on = service + timedelta(days=rng.randrange(7, 45))
        billed = rng.randrange(4_000, 400_000)
        allowed = billed * rng.randrange(50, 91) // 100
        status = rng.choice(STATUSES)
        paid = 0 if status == "denied" else allowed * rng.randrange(60, 101) // 100
        row = {
            "claim_id": client.claim_id.format(n=first_id + i),
            "member_id": client.member_id.format(n=rng.randrange(10**6, 10**7)),
            "member_dob": format_date(dob, fmt), "provider_npi": synthetic_npi(rng),
            "service_date": format_date(service, fmt), "paid_date": format_date(paid_on, fmt),
            "diagnosis_code": rng.choice(ICD10_CODES), "procedure_code": rng.choice(procedures),
            "place_of_service": rng.choice(PLACES), "billed_amount": money(billed, style),
            "allowed_amount": money(allowed, style), "paid_amount": money(paid, style),
            "claim_status": cased(status, client.status_case),
        }  # fmt: skip
        for _, target in columns:
            if target.startswith("extra:"):
                row[target] = _extra(target.removeprefix("extra:"), rng, fake, service, fmt)
        values.append(row)
        canon.append({"service": service, "billed": billed})

    plan = _plan(client, week, rng, mapped)
    start = max(1, n // 4)  # duplicates copy from rows before this; planted rows come after
    targets = rng.sample(range(start, n), sum(plan.values()))
    errors: list[dict[str, Any]] = []
    dictionary = load_dictionary()
    k = 0
    for (field, kind), count in plan.items():
        for _ in range(count):
            i = targets[k]
            k += 1
            _break(values[i], canon[i], field, kind, rng, values, start, client, as_of, mapped)
            rule = RULE_FOR_KIND[kind](fmt)
            spec = next(r for r in dictionary.for_date_format(fmt).field(field).rules
                        if r.text == rule)  # fmt: skip
            errors.append({"row": i + 1, "line": client.fmt.header_line + i + 1,
                           "field": field, "rule": rule, "severity": spec.severity})  # fmt: skip
    errors.sort(key=lambda e: (e["row"], e["field"], e["rule"]))

    headers = [h for h, _ in columns]
    table = [[row[t] for _, t in columns] for row in values]
    footer = None
    if client.fmt.footer_total:
        total = sum(c["billed"] for c in canon)
        footer = ["TOTAL" if j == 0 else (money(total, "plain") if t == "billed_amount" else "")
                  for j, (_, t) in enumerate(columns)]  # fmt: skip
    mapping = [{"header": h, "target": "unmapped" if t.startswith("extra:") else t}
               for h, t in columns]  # fmt: skip
    return Built(write(client.fmt, headers, table, footer), headers, mapping, errors, n)


def _extra(kind: str, rng: random.Random, fake: Faker, service: date, fmt: str) -> str:
    makers: dict[str, Callable[[], str]] = {
        "member_name": lambda: str(fake.name()),
        "address": lambda: f"{fake.street_address()}, {fake.city()}".replace("\n", " "),
        "group": lambda: f"G{rng.randrange(100, 999)}",
        "billing_npi": lambda: synthetic_npi(rng),
        "line_count": lambda: str(rng.randint(1, 6)),
        "plan_code": lambda: rng.choice(("HMO", "PPO", "EPO")),
        "member_zip": lambda: str(fake.postcode()),
        "payer_ref": lambda: f"PR{rng.randrange(10**6, 10**7)}",
        "thru_date": lambda: format_date(service + timedelta(days=rng.randrange(4)), fmt),
        "adjustment_reason": lambda: rng.choice(("", "CO-45", "PR-1", "OA-23")),
        "network": lambda: rng.choice(("In", "Out")),
        "channel": lambda: rng.choice(("EDI", "Portal", "Paper")),
        "frequency": lambda: rng.choice(("1", "7", "8")),
        "revenue_code": lambda: f"0{rng.randrange(100, 999)}",
        "referral_flag": lambda: rng.choice(("Y", "N")),
    }
    return makers[kind]()


def _break(row: dict[str, str], canon: dict[str, Any], field: str, kind: str,
           rng: random.Random, rows: list[dict[str, str]], start: int, client: Client,
           as_of: date, mapped: set[str]) -> None:  # fmt: skip
    """Make exactly one rule fail on this row (and nothing else)."""
    fmt, style = client.date_format, client.amount_style
    if kind == "required":
        row[field] = ""
    elif kind == "unique":
        row[field] = rows[rng.randrange(start)][field]
    elif kind == "bad_date":
        row[field] = (_impossible_date(rng, fmt) if rng.random() < 0.5
                      else _wrong_format(rng, canon["service"], fmt))  # fmt: skip
    elif kind == "future":
        row[field] = format_date(as_of + timedelta(days=rng.randrange(20, 400)), fmt)
        if "paid_date" in mapped:
            row["paid_date"] = ""  # optional; otherwise "paid before service" would also fire
    elif kind == "before_dob":
        service = date(2025, 1, 1) + timedelta(days=rng.randrange(300))
        row["service_date"] = format_date(service, fmt)
        row["member_dob"] = format_date(service + timedelta(days=rng.randrange(30, 300)), fmt)
        if "paid_date" in mapped:
            row["paid_date"] = format_date(service + timedelta(days=10), fmt)
    elif kind == "paid_before_service":
        row[field] = format_date(canon["service"] - timedelta(days=rng.randrange(1, 30)), fmt)
    elif kind == "icd":
        row[field] = rng.choice(BAD_ICD10)
    elif kind == "proc_len":
        row[field] = rng.choice(("Z123", f"Z{rng.randrange(10_000, 100_000)}"))
    elif kind == "npi_len":
        row[field] = row[field][:9]
    elif kind == "pos":
        row[field] = rng.choice(("110", "1", "A1"))
    elif kind == "non_numeric":
        row[field] = rng.choice(("N/A", "TBD", "12O.50"))
    elif kind == "negative":
        row[field] = money(-rng.randrange(100, 5_000), style)
    elif kind == "paid_gt":
        row[field] = money(canon["billed"] + 10_000, style)
        if "allowed_amount" in mapped:
            row["allowed_amount"] = money(canon["billed"], style)
    elif kind == "allowed_gt":
        row[field] = money(canon["billed"] + 5_000, style)  # paid <= allowed <= billed already
    elif kind == "status":
        row[field] = cased(rng.choice(BAD_STATUSES), client.status_case)
    else:  # pragma: no cover - ALL_ERRORS lists every kind
        raise ValueError(kind)


# ---------------------------------------------------------------- client 1 (legacy port)


def port_legacy(original: bytes, legacy_key: dict[str, Any], client: Client
                ) -> tuple[bytes, list[dict[str, str]], list[dict[str, Any]]]:  # fmt: skip
    """Port the original sample: replace only procedure-code values with synthetic
    ``Z`` + 4-digit codes. Every other byte stays identical; planted bad-length codes stay
    bad-length (the prefix of their replacement). Returns (bytes, mapping key, error key).
    """
    text = original.decode("utf-8")
    if '"' in text:
        raise ValueError("the legacy sample has no quoted fields; refusing to guess")
    lines = text.split("\n")
    header = lines[0].split(",")
    col = header.index(client.mapped()["procedure_code"])
    cells = [line.split(",") for line in lines[1:] if line]
    valid = sorted({c[col] for c in cells if len(c[col]) == 5})
    replacement = {code: f"Z{i:04d}" for i, code in enumerate(valid, start=1)}
    for c in cells:
        value = c[col]
        if value not in replacement:
            prefix_of = sorted(code for code in valid if code.startswith(value))
            if not prefix_of:
                raise ValueError(f"unexpected procedure code {value!r}")
            replacement[value] = replacement[prefix_of[0]][: len(value)]
        c[col] = replacement[value]
    out = "\n".join([lines[0], *(",".join(c) for c in cells)]) + (
        "\n" if text.endswith("\n") else ""
    )
    data = out.encode("utf-8")

    mapping = [{"header": h, "target": "unmapped" if t.startswith("extra:") else t}
               for h, t in client.columns]  # fmt: skip
    dictionary = load_dictionary()
    severity = {(f.name, r.text): r.severity for f in dictionary.fields for r in f.rules}
    errors = [{"row": e["line"] - 1, "line": e["line"], "field": e["field"], "rule": e["rule"],
               "severity": severity[(e["field"], e["rule"])], "origin": "legacy"}
              for e in legacy_key["planted_errors"]]  # fmt: skip
    # The original app didn't map "Paid Dt"; now that paid_date is a field, the rows where
    # the original planting moved the service date past the paid date fail one more rule.
    rows = list(csv.DictReader(io.StringIO(out)))
    by_target = [{m["target"]: r[m["header"]] for m in mapping if m["target"] != "unmapped"}
                 for r in rows]  # fmt: skip
    legacy_cells = {(e["row"], e["field"], e["rule"]) for e in errors}
    for f in check_rows(by_target, dictionary, amount_style="plain", as_of=LEGACY_AS_OF):
        if (f.row, f.field, f.rule) not in legacy_cells:
            if f.field != "paid_date":
                raise ValueError(f"unexpected legacy finding {f}")
            errors.append({"row": f.row, "line": f.row + 1, "field": f.field, "rule": f.rule,
                           "severity": f.severity, "origin": "derived_paid_date"})  # fmt: skip
    errors.sort(key=lambda e: (e["row"], e["field"], e["rule"]))
    return data, mapping, errors


# ---------------------------------------------------------------- everything


def _dump(obj: Any) -> bytes:  # noqa: ANN401 - JSON
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def generate_all(out_dir: Path = SYNTHETIC_DIR, fixed_dir: Path = SYNTHETIC_DIR) -> dict[str, Any]:
    """Write every file, key and the manifest to ``out_dir``. Client 1's week-1 assets
    are copied from ``fixed_dir`` (they're ported once, not generated)."""
    files: list[dict[str, Any]] = []
    for client in CLIENTS:
        folder = out_dir / client.slug
        folder.mkdir(parents=True, exist_ok=True)
        for week in (1, 2):
            stem = f"week{week}"
            name = stem + client.fmt.extension
            if client.legacy and week == 1:
                src = fixed_dir / client.slug
                data = (src / name).read_bytes()
                mapping_bytes = (src / f"{stem}.mapping.json").read_bytes()
                errors_bytes = (src / f"{stem}.errors.json").read_bytes()
                if out_dir != fixed_dir:
                    (folder / "legacy_answer_key.json").write_bytes(
                        (src / "legacy_answer_key.json").read_bytes()
                    )
                rows, n_errors = client.rows_week1, len(json.loads(errors_bytes)["errors"])
                as_of = LEGACY_AS_OF
            else:
                built = build_file(client, week)
                data, rows, n_errors, as_of = built.data, built.rows, len(built.errors), AS_OF[week]
                mapping_bytes = _dump({"file": name, "columns": built.mapping})
                errors_bytes = _dump({"file": name, "as_of": as_of.isoformat(),
                                      "errors": built.errors})  # fmt: skip
            for suffix, content in ((name, data), (f"{stem}.mapping.json", mapping_bytes),
                                    (f"{stem}.errors.json", errors_bytes)):  # fmt: skip
                (folder / suffix).write_bytes(content)
            files.append(_entry(client, week, name, data, rows, n_errors, as_of,
                                mapping_bytes, errors_bytes))  # fmt: skip
    manifest = {
        "note": "Synthetic claims extracts for 10 fictional health plans. Every value is "
                "invented; procedure codes are synthetic (Z + 4 digits). See README.md.",
        "generator": {"module": "claims_onboarding.synthetic.generate", "seed_base": SEED_BASE,
                      "faker": version("faker"), "openpyxl": version("openpyxl")},
        "dictionary": "../dictionary/claims_v1.json",
        "files": files,
    }  # fmt: skip
    (out_dir / "manifest.json").write_bytes(_dump(manifest))
    return manifest


def _entry(client: Client, week: int, name: str, data: bytes, rows: int, n_errors: int,
           as_of: date, mapping_bytes: bytes, errors_bytes: bytes) -> dict[str, Any]:  # fmt: skip
    base = f"{client.slug}/week{week}"
    fmt: FileFormat = client.fmt
    return {
        "client": client.number, "code": client.code, "name": client.name, "week": week,
        "path": f"{client.slug}/{name}", "sha256": _sha(data), "bytes": len(data),
        "format": fmt.describe(), "date_format": client.date_format,
        "amount_style": client.amount_style, "as_of": as_of.isoformat(), "rows": rows,
        "planted_errors": n_errors,
        "mapping_key": {"path": f"{base}.mapping.json", "sha256": _sha(mapping_bytes)},
        "error_key": {"path": f"{base}.errors.json", "sha256": _sha(errors_bytes)},
        "legacy": client.legacy and week == 1,
    }  # fmt: skip
