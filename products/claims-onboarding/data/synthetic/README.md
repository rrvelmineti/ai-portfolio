# Synthetic claims extracts (Spec 1 Phase 2)

These are 20 claims files from 10 **fictional** health plans: a week-1 and a week-2 file for each, every one with
answer keys. Every later phase uses them for tests, metrics and the demo.

**Everything here is invented.** There are no real people, providers, members or claims. Design:
[ADR 0010](../../../../docs/decisions/0010-synthetic-claims-data.md).

```bash
uv run python -m claims_onboarding.synthetic generate   # rewrite this folder (deterministic)
uv run python -m claims_onboarding.synthetic check      # verify every file against its keys
uv run python -m claims_onboarding.synthetic seed       # load clients + week-1 metadata (DATABASE_URL)
```

## Files

Each `client_NN_<code>/` folder holds:

| File | What it is |
|---|---|
| `week1.<ext>`, `week2.<ext>` | The client's extract, in its own format |
| `weekN.mapping.json` | **Mapping key**: every header, in file order, mapped to a target field or `"unmapped"` |
| `weekN.errors.json` | **Error key**: every planted error as `{row, line, field, rule, severity}`. `row` is the data row (1 = first record); `line` is the physical line, or the sheet row in `.xlsx`. |
| `legacy_answer_key.json` (client 1 only) | The original app's answer key, unchanged |

`manifest.json` lists every file with its SHA-256 and size, format, encoding, date format, amount style, "as of" date,
row count and error count, plus the SHA-256 of both keys. It also records the generator's seed and its Faker and
openpyxl versions.

## The clients

| # | Client | Format | Header style | Dates | Amounts | Week-1 rows / errors |
|---|---|---|---|---|---|---|
| 1 | Northwind Health Plan | CSV | original sample's (`Clm_Nbr`, `DOS_From`) | YYYY-MM-DD | plain | 2,000 / 56 |
| 2 | Cedar Valley Health | pipe `\|` | vendor upper-case (`CLM_NBR`, `PD_DT`) | MM/DD/YYYY | `$1,234.56` | 800 / 25 |
| 3 | Lakeshore Mutual | tab | camelCase | DD/MM/YYYY | plain | 500 / 18 |
| 4 | Summit Ridge Health | **.xlsx** (all cells text) | Title Case with spaces | YYYY-MM-DD | plain | 1,200 / 40 |
| 5 | Harborview Health Plan | CSV with **2 title lines and a TOTAL footer row** | abbreviations (`ClmID`, `DOS`) | MM-DD-YYYY | plain | 300 / 12 |
| 6 | Sierra Norte Salud | CSV, **latin-1** | accented Spanish (`Número_Reclamo`) | DD-MM-YYYY | plain | 650 / 30 |
| 7 | Bluewater Benefits | CSV, **UTF-8 with BOM** | mixed case (`claim_ID`, `PAID_dt`) | YYYY/MM/DD | `$` | 400 / 8 |
| 8 | Prairie Crest Health | CSV | long vendor names | MM/DD/YYYY | plain | 1,000 / 55 |
| 9 | Granite Peak Health | pipe | short codes, **ambiguous pair** `SVC_DT` / `SVC_THRU_DT` | YYYY-MM-DD | plain | 700 / 20 |
| 10 | Riverbend Health Partners | tab | renamed-but-same (`Charge Amt` = billed), no paid-date column | DD/MM/YYYY | `$` | 350 / 5 |

**Every client also has:**
- **2–5 unmapped extra columns:** member names and addresses (Faker), group, a billing NPI next to the rendering NPI,
  line counts and so on. The names and addresses give Phase 4 something to mask.
- **At least 3 headers** that differ from the dictionary's field names.

**Week 2** has the same format with three changes:
- **one header renamed,** e.g. `DIAG_CD_1` → `DIAG_CD`;
- **one new column;**
- **a different error mix,** with 5–60 errors.

**Planted errors** use the dictionary's rule types:
- missing required value;
- duplicate claim ID;
- bad date (impossible day, or another format);
- service date in the future;
- service date before date of birth;
- **paid date before service date** (rule `not before service_date` on `paid_date`);
- malformed ICD-10 code;
- procedure code not 5 characters;
- NPI not 10 digits;
- place of service not 2 digits (a warning);
- non-numeric or negative amounts;
- paid or allowed amount over billed;
- status outside the allowed list.

Each planted error breaks **exactly one** rule. The checker proves that.

## The answer keys are checked both ways

`check` reads each file in its own format and applies its mapping key. It then runs the rule checks
(`claims_onboarding/synthetic/rules.py`, which has the original app's semantics) and requires:
- **every keyed error is found;**
- **nothing else is wrong.**

It also checks the hashes, that the headers are in order, that required fields are mapped, the line numbers and, for
client 1, the original 52 errors. Phase 6's rules engine must agree with these keys on all 20 files.

## Client 1: the original sample (regression baseline)

Week 1 is the original app's 2,000-row sample (`examples/claims/client_claims.csv` in claims-onboarding-platform,
SHA-256 `f9de94d7…6134639`), with **one change: the procedure-code values.**
- The originals were real procedure-code numbers. Each distinct value now has a synthetic `Z` + 4-digit code
  (`Z0001`–`Z0008`).
- The 3 planted bad-length codes keep their bad length: they become the 4-character prefix `Z000`.
- **Every other byte is identical:** same size (251,814 bytes), same lines, same column order.
- **All 52 planted errors are unchanged.** `legacy_answer_key.json` is the original key, and the checker requires an
  exact match.

The original app never mapped the `Paid Dt` column. Now that `paid_date` is a field, 4 rows where the original planting
moved the service date past the paid date also fail `not before service_date`. These are in the error key with
`"origin": "derived_paid_date"`; the 52 originals have `"origin": "legacy"`.

## Codes and copyright

- **Diagnosis codes:** real ICD-10-CM codes, which are public (CMS). The planted invalid ones are malformed.
- **Procedure codes:** synthetic `Z` + 4 digits only, chosen so they can't collide with real CPT or HCPCS Level II
  codes. **No procedure descriptions appear anywhere,** because CPT text is AMA-copyrighted. The tests check the
  pattern; no list of real codes is committed.
- **NPIs** in clients 2–10 are synthetic and pass the NPI Luhn check (80840 prefix). Client 1 keeps the original
  sample's NPIs, which were invented too.
- **Names, addresses and member IDs:** Faker, with a fixed seed.

## Size

About 2 MB in total; the largest file is 252 KB. Everything is committed. A file over about 5 MB would be generated in
CI instead.
