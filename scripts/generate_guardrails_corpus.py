"""Generate the synthetic PII evaluation corpus (eval/pii/*.jsonl) with Faker.

Deterministic (fixed seed). Every value is synthetic but valid-format: Faker SSNs avoid
000/666/9xx areas, card numbers pass Luhn, NPIs carry a correct check digit. Gold labels
are recorded as character spans while each value is inserted, so labels are exact.

    uv run python scripts/generate_guardrails_corpus.py
"""

from __future__ import annotations

import json
import random
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from faker import Faker

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "pii"
SEED = 20261127

fake = Faker("en_US")
Faker.seed(SEED)
rng = random.Random(SEED)  # noqa: S311 - reproducible synthetic data, not security

# Given names that are also common English words (tricky for NER).
WORD_NAMES = ["Will", "Rose", "Grant", "Hope", "Faith", "Mark", "Bill", "Summer", "Joy",
              "Dawn", "Grace", "Daisy", "Pat", "Sandy", "Hunter", "Ivy", "April", "Chase",
              "Bob", "Art", "Rich", "Gene", "Carol", "Frank", "Jack"]  # fmt: skip
WORD_SURNAMES = ["Stone", "Young", "King", "Brown", "Black", "Price", "Long", "Little",
                 "Bush", "Hill", "Ford", "Rice", "Wise", "Moody", "Banks", "Lane"]  # fmt: skip


def luhn_check_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def npi() -> str:
    body = rng.choice("12") + "".join(rng.choice("0123456789") for _ in range(8))
    return body + luhn_check_digit("80840" + body)


def member_id() -> str:
    if rng.random() < 0.7:
        return "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(3)) + str(
            rng.randint(10**8, 10**9 - 1))  # fmt: skip
    return f"M{rng.randint(10**7, 10**8 - 1)}"


def claim_number() -> str:
    return f"CLM-{rng.randint(2019, 2026)}-{rng.randint(10**6, 10**7 - 1):07d}"


def policy_number() -> str:
    return f"POL-{rng.randint(10**7, 10**8 - 1):08d}"


def dob() -> str:
    d = fake.date_of_birth(minimum_age=1, maximum_age=95)
    return rng.choice([d.strftime("%m/%d/%Y"), d.isoformat(), d.strftime("%B %d, %Y")])


def service_date() -> str:
    d = fake.date_between(start_date="-2y", end_date="today")
    return rng.choice([d.strftime("%m/%d/%Y"), d.isoformat(), d.strftime("%b %d, %Y")])


def person() -> str:
    return f"{fake.first_name()} {fake.last_name()}"


def word_person() -> str:
    return f"{rng.choice(WORD_NAMES)} {rng.choice(WORD_SURNAMES)}"


def address() -> str:
    return f"{fake.street_address()}, {fake.city()}, {fake.state_abbr()} {fake.zipcode()}"


GENERATORS: dict[str, Callable[[], str]] = {
    "PERSON": person, "WORD_PERSON": word_person, "EMAIL": fake.email,
    "PHONE": fake.phone_number, "US_SSN": fake.ssn, "DATE_OF_BIRTH": dob,
    "ADDRESS": address, "CREDIT_CARD": fake.credit_card_number, "IP_ADDRESS": fake.ipv4,
    "MEMBER_ID": member_id, "CLAIM_NUMBER": claim_number, "POLICY_NUMBER": policy_number,
    "NPI": npi, "SERVICE_DATE": service_date,
    "US_BANK_NUMBER": lambda: str(rng.randint(10**9, 10**12 - 1)),
}  # fmt: skip
# Slots that are not PII labels (they exist to test precision).
UNLABELLED = {"SERVICE_DATE"}


def render(template: str, kind: str, sid: str) -> dict[str, Any]:
    """Fill ``{SLOT}`` markers left to right, recording exact gold spans."""
    text, entities, pos = "", [], 0
    while True:
        start = template.find("{", pos)
        if start < 0:
            text += template[pos:]
            break
        end = template.index("}", start)
        text += template[pos:start]
        slot = template[start + 1 : end]
        value = GENERATORS[slot]()
        if slot not in UNLABELLED:
            label = "PERSON" if slot == "WORD_PERSON" else slot
            entities.append({"type": label, "start": len(text), "end": len(text) + len(value)})
        text += value
        pos = end + 1
    return {"id": sid, "kind": kind, "text": text, "entities": entities}


FREE_TEXT = [
    "Claimant {PERSON} called about claim {CLAIM_NUMBER}; callback number {PHONE}.",
    "Member {PERSON} (member ID {MEMBER_ID}, DOB {DATE_OF_BIRTH}) reported water damage.",
    "Rendering provider NPI {NPI} billed for the visit on {SERVICE_DATE}.",
    "Policyholder {PERSON} lives at {ADDRESS} and can be reached at {EMAIL}.",
    "SSN on file is {US_SSN}; please verify before releasing payment for {CLAIM_NUMBER}.",
    "Payment card {CREDIT_CARD} was declined for policy {POLICY_NUMBER}.",
    "Login from IP address {IP_ADDRESS} by member {PERSON} was flagged.",
    "Spoke with {PERSON} at {PHONE} regarding policy number {POLICY_NUMBER}.",
    "Date of birth {DATE_OF_BIRTH} does not match the ID card for member {MEMBER_ID}.",
    "Refund to bank account {US_BANK_NUMBER} requested by {PERSON}.",
    "Adjuster {PERSON} approved claim {CLAIM_NUMBER} on {SERVICE_DATE}.",
    "Mail the EOB to {ADDRESS}. Contact email: {EMAIL}.",
    "{PERSON} disputes the charge; card ending in {CREDIT_CARD} per the statement.",
    "The patient, {PERSON}, was born on {DATE_OF_BIRTH} and has NPI {NPI} as PCP.",
    "Subscriber {PERSON}, SSN {US_SSN}, phone {PHONE}, email {EMAIL}.",
]
EMAILS = [
    "From: {EMAIL}\nTo: claims@example.com\nSubject: Claim {CLAIM_NUMBER}\n\n"
    "Hi, this is {PERSON}. My member ID is {MEMBER_ID} and my phone is {PHONE}.\n"
    "Please send updates to {ADDRESS}.\nThanks,\n{PERSON}",
    "Subject: Address change\n\nDear team,\nPlease update my address to {ADDRESS}. "
    "My policy number is {POLICY_NUMBER} and DOB {DATE_OF_BIRTH}.\nRegards,\n{PERSON}\n{PHONE}",
    "Hello,\nI was charged twice on card {CREDIT_CARD} for claim {CLAIM_NUMBER}. "
    "You can call me on {PHONE} or reply to {EMAIL}.\n{PERSON}",
]
LETTERS = [
    "{ADDRESS}\n\nDear {PERSON},\n\nWe received your claim {CLAIM_NUMBER} for services on "
    "{SERVICE_DATE}. Your member ID is {MEMBER_ID}. If you have questions call {PHONE}.\n\n"
    "Sincerely,\n{PERSON}\nClaims Department",
    "RE: Policy {POLICY_NUMBER}\n\nDear {PERSON},\n\nOur records show a date of birth of "
    "{DATE_OF_BIRTH} and SSN {US_SSN}. Please confirm by email to {EMAIL}.\n\nRegards,\n{PERSON}",
]
TRICKY = [
    "{WORD_PERSON} called about claim {CLAIM_NUMBER}. Will we grant the appeal? Hope so.",
    "Member {WORD_PERSON} visited on {SERVICE_DATE}; the rose garden claim is unrelated.",
    "Please mark the file for {WORD_PERSON}; summer schedule applies from {SERVICE_DATE}.",
    "Note from {WORD_PERSON}: the hunter green form was faxed to {PHONE}.",
    "ID table row: {MEMBER_ID} | {CLAIM_NUMBER} | {NPI} | {SERVICE_DATE}",
    "| member_id | claim | npi |\n| {MEMBER_ID} | {CLAIM_NUMBER} | {NPI} |",
    "Dates of service {SERVICE_DATE} and {SERVICE_DATE}; admission on {SERVICE_DATE}.",
    "Contact {WORD_PERSON} at {EMAIL} or {PHONE} about policy {POLICY_NUMBER}.",
]
CSV_COLUMNS: list[tuple[str, str | None]] = [
    ("member_name", "PERSON"), ("member_id", "MEMBER_ID"), ("dob", "DATE_OF_BIRTH"),
    ("ssn", "US_SSN"), ("claim_no", "CLAIM_NUMBER"), ("rendering_npi", "NPI"),
    ("phone", "PHONE"), ("email", "EMAIL"), ("date_of_service", None), ("billed_amount", None),
]  # fmt: skip


def csv_rows(n: int) -> list[dict[str, Any]]:
    """Claims-style table rows: gold label per column (evaluated with mask_records)."""
    gen = {"PERSON": person, "MEMBER_ID": member_id, "DATE_OF_BIRTH": dob,
           "US_SSN": fake.ssn, "CLAIM_NUMBER": claim_number, "NPI": npi,
           "PHONE": fake.phone_number, "EMAIL": fake.email}  # fmt: skip
    rows = []
    for _ in range(n):
        row: dict[str, str] = {}
        for col, ent in CSV_COLUMNS:
            if ent:
                row[col] = gen[ent]()
            elif col == "date_of_service":
                row[col] = service_date()
            else:
                row[col] = f"{rng.randint(20, 5000)}.{rng.randint(0, 99):02d}"
        rows.append(row)
    return rows


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    plan: list[tuple[str, list[str], int]] = [
        ("free_text", FREE_TEXT, 90), ("email", EMAILS, 30), ("letter", LETTERS, 30),
        ("tricky", TRICKY, 40),
    ]  # fmt: skip
    for kind, templates, n in plan:
        for i in range(n):
            samples.append(render(templates[i % len(templates)], kind, f"{kind}-{i:03d}"))
    with (OUT / "texts.jsonl").open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    table = {"id": "claims-table-001", "kind": "csv",
             "columns": {c: e for c, e in CSV_COLUMNS}, "rows": csv_rows(40)}  # fmt: skip
    (OUT / "claims_table.json").write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
    n_ent = sum(len(s["entities"]) for s in samples)
    print(f"wrote {len(samples)} text samples ({n_ent} labelled entities) and a "
          f"{len(table['rows'])}-row claims table to {OUT.relative_to(ROOT)}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
