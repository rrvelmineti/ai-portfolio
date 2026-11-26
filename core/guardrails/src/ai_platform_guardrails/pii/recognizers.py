"""Custom Presidio recognizers: healthcare/claims IDs, NPI (Luhn), DOB, street addresses,
ZIP codes, ages, and names introduced by a title or role word.

Base scores are chosen so that context-dependent entities (DOB, member/claim/policy IDs
with generic formats) only pass the threshold when a context word is nearby.
"""

from __future__ import annotations

import re
from typing import Any

from presidio_analyzer import Pattern, PatternRecognizer

CASE_SENSITIVE = re.M | re.S  # Presidio defaults to IGNORECASE (same values as `regex`)

# USPS street suffixes (Publication 28, Appendix C1), full names and common abbreviations.
STREET_SUFFIXES = (
    "Alley|Aly|Annex|Anx|Arcade|Arc|Avenue|Ave|Bayou|Byu|Beach|Bch|Bend|Bnd|Bluffs?|Blfs?|"
    "Bottom|Btm|Boulevard|Blvd|Branch|Br|Bridge|Brg|Brook|Brk|Brooks|Burgs?|Bgs?|Bypass|Byp|"
    "Camp|Cp|Canyon|Cyn|Cape|Cpe|Causeway|Cswy|Centers?|Ctrs?|Ctr|Circles?|Cirs?|Cir|Cliffs?|"
    "Clfs?|Club|Clb|Common|Cmn|Commons|Cmns|Corners?|Cors?|Course|Crse|Courts?|Cts?|Ct|"
    "Coves?|Cvs?|Creek|Crk|Crescent|Cres|Crest|Crst|Crossing|Xing|Crossroad|Xrd|Curve|Curv|"
    "Dale|Dl|Dam|Dm|Divide|Dv|Drives?|Drs?|Dr|Estates?|Ests?|Expressway|Expy|Extensions?|"
    "Exts?|Falls?|Fls?|Ferry|Fry|Fields?|Flds?|Flats?|Flts?|Fords?|Frds?|Forest|Frst|Forges?|"
    "Frgs?|Forks?|Frks?|Fort|Ft|Freeway|Fwy|Gardens?|Gdns?|Gateway|Gtwy|Glens?|Glns?|Greens?|"
    "Grns?|Groves?|Grvs?|Harbors?|Hbrs?|Haven|Hvn|Heights|Hts|Highway|Hwy|Hills?|Hls?|"
    "Hollow|Holw|Inlet|Inlt|Islands?|Iss?|Isle|Junctions?|Jcts?|Keys?|Kys?|Knolls?|Knls?|"
    "Lakes?|Lks?|Land|Landing|Lndg|Lane|Ln|Light|Lgt|Lights|Lgts|Loaf|Lf|Locks?|Lcks?|"
    "Lodge|Ldg|Loop|Mall|Manors?|Mnrs?|Meadows?|Mdws?|Mews|Mill|Ml|Mills|Mls|Mission|Msn|"
    "Motorway|Mtwy|Mount|Mt|Mountains?|Mtns?|Neck|Nck|Orchard|Orch|Oval|Overpass|Opas|Park|"
    "Parks|Parkway|Pkwy|Parkways|Pass|Passage|Psge|Path|Pike|Pines?|Pnes?|Place|Pl|Plains?|"
    "Plns?|Plaza|Plz|Points?|Pts?|Ports?|Prts?|Prairie|Pr|Radial|Radl|Ramp|Ranch|Rnch|"
    "Rapids?|Rpds?|Rest|Rst|Ridges?|Rdgs?|River|Riv|Roads?|Rds?|Rd|Route|Rte|Row|Rue|Run|"
    "Shoals?|Shls?|Shores?|Shrs?|Skyway|Skwy|Springs?|Spgs?|Spurs?|Squares?|Sqs?|Station|Sta|"
    "Stravenue|Stra|Stream|Strm|Streets?|Sts?|St|Summit|Smt|Terrace|Ter|Throughway|Trwy|"
    "Trace|Trce|Track|Trak|Trafficway|Trfy|Trail|Trl|Trailer|Trlr|Tunnel|Tunl|Turnpike|Tpke|"
    "Underpass|Upas|Unions?|Uns?|Valleys?|Vlys?|Viaduct|Via|Views?|Vws?|Villages?|Vlgs?|"
    "Ville|Vl|Vista|Vis|Walks?|Wall|Way|Ways|Wells?|Wls?"
)
_UNIT = r"(?:,?\s+(?:Apt\.?|Apartment|Suite|Ste\.?|Unit|#|Fl\.?|Floor|Rm\.?|Room)\s*[\w-]+)?"
_CITY_STATE_ZIP = r"(?:,?\s*\n?\s*[A-Z][A-Za-z.' -]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)?"
ADDRESS_REGEX = (
    r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][A-Za-z0-9'.-]*\s+){1,4}"
    rf"(?:{STREET_SUFFIXES})\.?\b(?:\s+[NSEW]{{1,2}}\b)?{_UNIT}{_CITY_STATE_ZIP}"
)
PO_BOX_REGEX = r"\b(?:P\.?\s?O\.?\s?Box|PSC\s+\d{4},\s+Box|USNS\s+\w+|USS\s+\w+)\s*\d*" + (
    r"(?:,?\s*\n?\s*[A-Z][A-Za-z.' -]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)"
)

_DATE = (
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2}|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4}|"
    r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b"
)

TITLE_WORDS = (
    r"(?:Mr|Mrs|Ms|Mx|Dr|Prof)\.?|Patient|Member|Claimant|Insured|Subscriber|Beneficiary|"
    r"Policyholder|Dear|Attn:?|Name:|Contact:?|Signed,?|Sincerely,?|Regards,?|From:|To:|"
    r"Spouse|Dependent|Provider|Physician|Nurse|Agent|Adjuster|Mother|Father"
)
_NAME = r"[A-Z][a-z]+(?:['-][A-Z]?[a-z]+)?"
PERSON_AFTER_TITLE = (
    rf"(?<=\b(?:{TITLE_WORDS})\s{{1,3}}){_NAME}(?:\s+[A-Z]\.)?(?:\s+{_NAME}){{1,2}}"
)


def luhn_valid(digits: str) -> bool:
    """Standard Luhn (mod 10) check over a digit string."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def npi_valid(npi: str) -> bool:
    """NPI check digit: Luhn over '80840' + the 10 digits (CMS NPI standard)."""
    digits = re.sub(r"\D", "", npi)
    return len(digits) == 10 and digits[0] in "12" and luhn_valid("80840" + digits)


class NpiRecognizer(PatternRecognizer):
    """10-digit National Provider Identifier, validated with the Luhn check."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="NPI",
            name="NpiRecognizer",
            patterns=[Pattern("npi", r"\b[12]\d{9}\b", 0.6)],
            context=["npi", "provider", "prescriber", "physician", "rendering", "billing"],
        )

    def validate_result(self, pattern_text: str) -> bool | None:
        return npi_valid(pattern_text)


def _pattern(entity: str, name: str, regexes: list[tuple[str, float]], context: list[str],
             *, flags: int | None = None) -> PatternRecognizer:  # fmt: skip
    kwargs: dict[str, Any] = {}
    if flags is not None:
        kwargs["global_regex_flags"] = flags
    return PatternRecognizer(
        supported_entity=entity,
        name=name,
        patterns=[Pattern(f"{name}_{i}", rx, score) for i, (rx, score) in enumerate(regexes)],
        context=context,
        **kwargs,
    )


def default_recognizers() -> list[PatternRecognizer]:
    """Recognizers added to every analyzer. Formats are documented in the README."""
    return [
        NpiRecognizer(),
        _pattern(
            "MEMBER_ID",
            "MemberIdRecognizer",
            [
                (r"\b[A-Z]{3}\d{9}\b", 0.6),  # e.g. XGH123456789 (payer style)
                (r"\bM\d{8,10}\b", 0.6),  # e.g. M12345678
                (r"\b[A-Z]\d{2}[A-Z]\d{5,7}\b", 0.3),
            ],  # weak, needs context
            ["member", "member id", "mbr", "subscriber", "enrollee", "id card"],
            flags=CASE_SENSITIVE,
        ),  # fmt: skip
        _pattern(
            "CLAIM_NUMBER",
            "ClaimNumberRecognizer",
            [
                (r"\bCLM-?\d{4}-?\d{6,8}\b", 0.7),  # e.g. CLM-2024-0012345
                (r"\b\d{2}[A-Z]\d{9,11}\b", 0.3),
            ],  # weak, needs context
            ["claim", "claim number", "claim no", "claim #", "icn"],
            flags=CASE_SENSITIVE,
        ),  # fmt: skip
        _pattern(
            "POLICY_NUMBER",
            "PolicyNumberRecognizer",
            [
                (r"\bPOL-?\d{6,10}\b", 0.7),  # e.g. POL-00123456
                (r"\b[A-Z]{2}\d{8}\b", 0.3),
            ],  # weak, needs context
            ["policy", "policy number", "policy no", "pol #", "group policy"],
            flags=CASE_SENSITIVE,
        ),  # fmt: skip
        _pattern(
            "DATE_OF_BIRTH",
            "DateOfBirthRecognizer",
            [(_DATE, 0.3)],
            ["dob", "d.o.b", "date of birth", "birth date", "birthdate", "born", "birthday"],
        ),
        _pattern(
            "ADDRESS",
            "StreetAddressRecognizer",
            [(ADDRESS_REGEX, 0.7), (PO_BOX_REGEX, 0.7)],
            ["address", "street", "lives at", "mail to", "residence"],
            flags=CASE_SENSITIVE,
        ),  # fmt: skip
        _pattern(
            "PERSON",
            "PersonAfterTitleRecognizer",
            [(PERSON_AFTER_TITLE, 0.7)],
            [],
            flags=CASE_SENSITIVE,
        ),  # fmt: skip
        # Used only in HIPAA Safe Harbor mode (generalised, not masked).
        _pattern(
            "ZIP_CODE",
            "ZipCodeRecognizer",
            [(r"(?<=\b[A-Z]{2}\s{1,2})\d{5}(?:-\d{4})?\b", 0.6), (r"\b\d{5}(?:-\d{4})?\b", 0.2)],
            ["zip", "zip code", "postal", "postcode"],
            flags=CASE_SENSITIVE,
        ),  # fmt: skip
        _pattern(
            "AGE",
            "AgeRecognizer",
            [
                (r"\b(?:age[d:]?\s*)\d{1,3}\b", 0.6),
                (r"\b\d{1,3}(?:-|\s)(?:year|yr)s?(?:-|\s)old\b", 0.7),
                (r"\b\d{1,3}\s*(?:y/o|yo|yrs?)\b", 0.6),
            ],
            [],
        ),  # fmt: skip
    ]
