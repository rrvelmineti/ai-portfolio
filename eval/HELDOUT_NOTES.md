### What the gap means

The first scoring run on the held-out and public sets is the run shown above. The
held-out set was frozen in commit `c918e1a`, before any scoring, and no detector has
changed since. Nothing has been tuned to these numbers.

**Headline: the development numbers overstate real-world accuracy, a lot for injection.**
100% on the development attacks says the rules recognise the phrasings they were written
for. On new phrasings they catch 17/40 of the held-out attacks and 6/60 of the public ones.
The low false-positive rates hold up (2/40, 0/56), so the rules are precise but narrow.

#### Prompt injection

- **No near misses.** Every missed attack scored below the 0.4 flag threshold, so the
  optional LLM classifier, which only sees borderline inputs, would not have been asked
  either. Lowering thresholds would not help; the rules simply don't fire.
- **What the rules miss (held-out):** requests that sound like ordinary work, with no
  override or jailbreak vocabulary:
  - system-prompt extraction phrased as an audit or a question (0/3);
  - harvesting other members' data (0/3);
  - skipping approval "because the reviewer already agreed" (0/2);
  - task hijack, social engineering, multi-turn and split payloads (0/6 combined);
  - overrides worded differently from the rules: a developer "priority update", a
    French and a Spanish override (both rule languages) and a Chinese one (not a rule
    language);
  - instructions hidden in an HTML comment or a JSON field (2 of the 4 hidden
    instructions);
  - a "grandmother" role-play and a rot13 payload.
- **What they catch:** fake delimiters, exfiltration links, character spacing, most
  encodings and explicit role-play personas.
- **Public set (`deepset/prompt-injections`, test split):**
  - **German:** 20 of the 60 attacks are German, and all 20 were missed. The rules cover
    German override phrasing only, and the NER model is English.
  - **English:** 6/40 (15%) caught. Most are task hijacks ("forget that, now write
    about …") or role-play setups that our rules do not treat as attacks.
  - **Labels:** some labels are debatable for our use. For example, `deepset-test-000`, an
    "act as an interviewer" role-play prompt, is labelled an injection. The number is
    reported as is; we did not relabel.
- **Why it matters less than 10% suggests, and why it still matters:** the rule scanner
  is one layer. The canary, output link blocking, the tool allowlist and the approval
  gate do not depend on recognising the attack text. But the scanner is the only layer
  that stops an attack *before* the model reads it, and on independent data it mostly
  doesn't.

#### PII

- **Strong on fixed formats:** every SSN, card number, NPI, IP address and claim number
  was found (18/18), including a claim number missing one hyphen.
- **Dates of birth: 2/8.** The misses are formats the recognizer doesn't parse
  ("3rd of May 1961", "Jan 5 '72", "19570823", "22.10.1975"), and dates with no DOB word
  nearby. Dates without a DOB word are left alone by design (dates of service must stay
  readable), so 2 of the 6 misses are a labelling choice in this set rather than a
  detector bug. With Safe Harbor on, any date the date recognizer does find is
  generalised to its year anyway.
- **Off-format IDs:** `M-4471-2290`, `POL 00873321`, a hyphenated bank account and a
  bare bank account with only "checking" as context were missed. Tenant-specific
  recognizers are the intended fix for house formats.
- **Names: 25/28.** Missed: a name in ALL CAPS, a lone first name that is also a word
  ("Will") and "April May". False positives: an OCR-damaged label ("Narne"), a Spanish
  phrase ("La paciente"), a place name ("Cody") and an obfuscated email.
- **Type-strict scoring understates masking.** An obfuscated email
  ("kim dot park at example dot com") was partly masked as a PERSON, and the rural-route
  address lost only its city. These count as misses because the type is wrong; the value
  is partly hidden, not fully exposed.
- **Expected trade-offs, seen again:** a 10-digit order number masked as a phone and a
  bank routing number masked as an account (the safe direction, documented in ADR 0005).

#### NPI vs phone

`core/guardrails/tests/test_npi_phone_overlap.py` pins the overlap. NPIs win over phones
when both match. That is right for real NPIs, but about 1 in 10 bare 10-digit phone
numbers starting with 2 also pass the NPI check and are then labelled NPI even next to
"call" (an `xfail(strict=True)` test). The number is masked either way.

#### What would change these numbers (not done here)

Detector fixes belong in a separate branch, and that branch must say which held-out
samples it was tuned against. Those samples then stop counting as held out.
1. Turn on the LLM classifier for *all* chat input, not only borderline, and measure its
   cost and latency.
2. Add intent rules for extraction-as-audit, bulk data requests and approval skipping.
3. Add DOB date formats, plus tenant recognizers for house ID formats.
4. Score a public PII benchmark (future work, ADR 0006).
