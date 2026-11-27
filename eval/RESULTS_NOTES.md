## How these numbers were reached (honest history)

The corpus was committed before the first scoring run and has not been edited since.

**First run** (same corpus, before any detector changes):

| Metric | First run | Now |
|---|---:|---:|
| CREDIT_CARD recall | 81.8% (4 missed) | see table above |
| PHONE recall | 98.2% (2 missed) | see table above |
| DATE_OF_BIRTH recall | 67.5% | see table above |
| US_BANK_NUMBER recall | 0% | see table above |
| Injection detection | 31/35 (88.6%) | see above |
| Benign false positives | 1/35 (2.9%) | see above |

**What changed in the detectors (general fixes, not per-sample tuning):**

- **Context words were silently ignored.** Presidio matches context per token (lemma), so
  multi-word entries such as "date of birth" or "claim number" never matched, and "born"
  lemmatises to "bear". All context lists now use single words (plus "bear").
- **Card numbers:** Presidio's recognizer knows common brand prefixes/lengths only. Added a
  12-19 digit Luhn-validated recognizer that still needs card context ("card", "charged",
  "statement", ...), so random digit runs are not flagged.
- **Phones:** bare 10-digit numbers with valid NANP area and exchange codes are treated as
  phones without needing a context word. Trade-off: some other 10-digit numbers will be
  masked as phones (safe direction for privacy, lower precision on real data).
- **Bank accounts:** added an 8-17 digit recognizer that only fires next to account/bank words
  (Presidio's built-in scores too low to pass the threshold).
- **Injection:** letter-spaced text with multi-space word gaps is re-joined before scanning;
  role-play rule window widened; new signals for *asking to skip oversight* ("no confirmation
  needed", "do not log it") and *harvesting other people's data*; "developer mode" now only
  counts when someone enables it or addresses the assistant (fixed the product-guide false
  positive).

**Known limits:**

- **PERSON:** names that are also ordinary words ("Will Stone", "Grace Lane") are still missed
  in some sentences (see Misses). Recall is above the 95% target, not 100%.
- **EMAIL precision:** the false positives are `claims@example.com`, an organisational address
  in the email template that the generator does not label. It is a real email address, so
  this is a gold-label gap, not a detector error; we left the corpus unchanged.
- **No detector catches everything.** 100% on 35 hand-written attacks does not mean 100% on
  attacks we have not imagined; see ADR 0005 for the layered design that limits the damage
  when a detector misses.
