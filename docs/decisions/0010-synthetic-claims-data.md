# 0010. Synthetic claims data: client profiles, two-way-checked answer keys, synthetic codes

- Status: Accepted
- Date: 2026-11-28

## Context

Every later Spec 1 phase needs realistic claims extracts with known right answers:
- ingestion (Phase 3), masking (4), mapping accuracy (5) and the rules engine (6);
- recurring feeds (8) and the demo (9).

The data must be fully synthetic, small enough to commit, and identical on every run.
The old app's 2,000-row sample and its 52-error answer key are our regression baseline.
That sample used real procedure-code numbers (CPT/HCPCS), which we may not keep.

## Decision

### One dictionary, many client profiles

- **`data/dictionary/claims_v1.json` is the target dictionary:** the old app's 12 fields
  plus an optional **`paid_date`** with `not before service_date`. Each field has its
  type, format, rules and severity, in the old `rules.txt` wording. The seed loads it as
  `data_dictionaries` v1, the tenant default.
- **Each client sends its own formats.** A client profile declares:
  - its **date format** (one of the six the old engine supported);
  - an **amount style**: `plain`, or `currency`, which also accepts `$` and thousands
    separators;
  - its ID patterns.

  The rules are the dictionary's, with the client's date format substituted. So "bad
  date" means "not a valid date in the format this client declared", which is exactly
  what Phase 3 must normalise.
- **10 fictional clients** cover the brief:
  - delimiters: comma, pipe and tab;
  - one `.xlsx`, one latin-1 file, one UTF-8 file with a BOM;
  - one file with title lines and a TOTAL footer;
  - abbreviated, vendor-style, mixed-case and accented headers;
  - an ambiguous pair of date columns and a renamed-but-same field;
  - extra unmapped columns, including Faker names and addresses for Phase 4.
- **A week-2 file for every client:** one header renamed, one new column, a different
  error mix.

### The answer keys are the ground truth, checked both ways

- **Two keys per file:**
  - a **mapping key**: every header, in order, mapped to a field or `"unmapped"`;
  - an **error key**: `{row, line, field, rule, severity}`, where `line` is the physical
    line or sheet row, so header offsets are explicit.
- **The error key is the planting record.** Each error is written down when it is planted,
  never derived from the checks, and each one is planted to break **exactly one** rule.
- **The checker** (`claims_onboarding.synthetic.check`) reads each file in its own format
  and runs rule checks with the **old engine's semantics**:
  - blanks fail only `required`;
  - cross-field and "not in the future" rules run only on values that parse;
  - `unique` reports later repeats.

  It requires **every keyed error to be found and nothing else** to be wrong.
- So a cascade, or a planted value that accidentally breaks a second rule, fails the build.
  Phase 6's production engine must agree with these keys on all 20 files.
- **Determinism:**
  - fixed seeds per client and week, and seeded Faker;
  - `.xlsx` files are written with fixed workbook properties, a pinned `dcterms:modified`
    and fixed zip timestamps;
  - the manifest records every SHA-256 and the Faker and openpyxl versions;
  - a test regenerates everything and compares bytes.
- **The files are byte-exact in git:** `-text` in `.gitattributes` and excluded from the
  whitespace pre-commit hooks, which otherwise "fix" a TSV row that ends in an empty cell.

### Client 1: the original sample, with synthetic procedure codes

- **Only the procedure-code values change.**
  - Each distinct original code becomes a synthetic `Z` + 4-digit code (`Z0001`–`Z0008`).
  - The 3 planted bad-length codes keep their bad length, as the 4-character prefix `Z000`.
  - Every other byte is identical: same size, same lines, same columns.
- **All 52 errors are unchanged,** and a test compares them with the original key, which
  is committed unchanged as `legacy_answer_key.json`.
- **4 derived errors.** The old app never mapped `Paid Dt`. Mapping it to `paid_date` adds
  4 rows where the original planting put the service date after the paid date. They are in
  the key as `origin: derived_paid_date`, separate from the 52 `legacy` ones.
- **Fixed "as of" date** of 2026-11-15, the day the original was measured, so "not in the
  future" never drifts.

### Codes and copyright

- **Procedure codes are `Z` + 4 digits, everywhere.**
  - `P`+digits or `X`+letters could collide with real HCPCS Level II codes, which use
    letters A–V. `Z` codes can't collide with CPT (5 digits, or 4 digits plus F/T/U) or
    with HCPCS.
  - No procedure descriptions appear anywhere: CPT text is AMA-copyrighted.
  - Tests check the pattern (`^Z\d{4}$`, or a planted bad-length `Z` value), and **no list
    of real codes is committed.**
- **ICD-10-CM codes are real.** They're public (CMS); the planted invalid ones are malformed.
- **NPIs are synthetic and Luhn-valid,** using the NPI check over the `80840` prefix.
  Client 1 keeps the sample's invented NPIs.
- **Names, addresses and member IDs come from seeded Faker.** Nothing here is real.

### Seeding and the demo

- **`python -m claims_onboarding.synthetic seed`** loads into the existing `demo` tenant:
  - the dictionary;
  - the 10 clients;
  - each week-1 file's **metadata and headers** (contents are Phase 3).
- **It acts as `system:seed`,** a demo-tenant account that can never log in: it's inactive,
  and its password hash `!` can't verify. So every audit row names a clear, non-human
  actor. It's idempotent.
- **The nightly demo reset** (owner role):
  - deletes every claims row of the demo tenant, child tables first, after dropping
    resubmission links;
  - keeps `system:seed` (the core reset takes `keep_usernames`);
  - re-runs the seed;
  - skips the claims part on a database where the claims schema isn't migrated yet.
- **Deploys:**
  - migrate the claims schema;
  - grant the app role **exactly** `CLAIMS_GRANTS`: no DELETE anywhere, and sign-offs and
    file columns are insert-only. They're passed in as `extra_grants`, so core never
    imports a product.
  - run the seed.

## Consequences

- **One source of truth** for "what's in each file and what's wrong with it". Phase 3 must
  read every format; Phase 5 scores mapping against the mapping keys; Phase 6 must reproduce
  the error keys exactly.
- **The checker repeats some rule logic** that Phase 6 will own. The duplication is
  deliberate: it's the independent oracle Phase 6 is tested against.
- **Regenerating changes hashes** whenever the generator, the profiles or the pinned
  Faker/openpyxl versions change. The determinism test flags that, and the manifest must
  then be regenerated and reviewed in the same PR.
- **Client 1's file differs from the original** in one column. Its provenance (the original
  SHA-256) and the change are recorded in the data README.
- **The demo tenant gains a non-login account** (`system:seed`) that survives the nightly
  reset.
