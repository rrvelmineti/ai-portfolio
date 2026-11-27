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
_UNIT_WORDS = r"Apt\.?|Apartment|Suite|Ste\.?|Unit|#|Bldg\.?|Building|Fl\.?|Floor|Rm\.?|Room"
_UNIT = rf"(?:,?\s+(?:{_UNIT_WORDS})\s*#?[\w-]+)?"
# ", City, ST 12345" with the comma before the state optional ("Springfield MA 01103").
CITY_STATE_ZIP = r"[A-Z][A-Za-z.' -]+?,?\s+[A-Z]{2},?\s+\d{5}(?:-\d{4})?"
_CITY_STATE_ZIP = rf"(?:,?\s*\n?\s*{CITY_STATE_ZIP})?"
ADDRESS_REGEX = (
    r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][A-Za-z0-9'.-]*\s+){1,4}"
    rf"(?:{STREET_SUFFIXES})\b\.?(?:\s+[NSEW]{{1,2}}\b)?{_UNIT}{_CITY_STATE_ZIP}"
)
# PO boxes, rural/highway-contract routes and military addresses, each optionally followed
# by city/state/ZIP (APO/FPO addresses carry their own pseudo state and ZIP).
PO_BOX_REGEX = r"\b(?:P\.?\s?O\.?\s?Box|Post\s+Office\s+Box|POB)\s*#?\s*\d+" + _CITY_STATE_ZIP
RURAL_ROUTE_REGEX = (
    r"\b(?:RR|R\.R\.|Rural\s+Route|HC|Highway\s+Contract|Star\s+Route)\s*\d+,?\s+Box\s+\d+"
    + _CITY_STATE_ZIP
)
MILITARY_REGEX = (
    r"\b(?:PSC|CMR|Unit|USNS\s+\w+|USS\s+\w+)\s*\d*,?\s*(?:Box\s+\d+,?\s*)?"
    r"(?:APO|FPO|DPO)\s+(?:AA|AE|AP)\s+\d{5}(?:-\d{4})?"
)
# Text right after a street/PO-box match that still belongs to the address. Used by the
# engine's merge step so a city, state or ZIP is never left unmasked after the street.
ADDRESS_TAIL_REGEX = rf"{_UNIT}(?:,?\s*\n?\s*{CITY_STATE_ZIP})"

_DATE = (
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2}|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4}|"
    r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b"
)

_MONTHS_EN = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_MONTHS_OTHER = (  # French, German, Spanish
    r"(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|"
    r"d[ée]cembre|Januar|Februar|M[äa]rz|Juni|Juli|Oktober|Dezember|enero|febrero|marzo|"
    r"abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
)
_ORDINAL_WORDS = (
    r"(?:(?:twenty|thirty)[- ]?)?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|"
    r"ninth)|tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|"
    r"eighteenth|nineteenth|twentieth|thirtieth"
)
_DAY = rf"(?:\d{{1,2}}(?:st|nd|rd|th)?|{_ORDINAL_WORDS})"
_YEAR = r"(?:\d{4}|['\u2019]\d{2})"
WRITTEN_DATES = [
    # "the 3rd of May 1961", "twelfth of March, 1948"
    rf"\b(?:the\s+)?{_DAY}\s+(?:day\s+)?of\s+{_MONTHS_EN}\.?,?\s+{_YEAR}\b",
    # "May 3rd, '61", "March 12th 1948"
    rf"\b{_MONTHS_EN}\.?\s+{_DAY},?\s+{_YEAR}(?!\w)",
    # "3 mai 1961", "3. Mai 1961", "3 de mayo de 1961"
    rf"\b\d{{1,2}}\.?\s+(?:de\s+)?{_MONTHS_OTHER}\s+(?:de\s+)?\d{{4}}\b",
]
# Words near a date that make it a date of birth (English plus fr/de/es/hi forms).
DOB_CONTEXT = [
    "dob", "d.o.b", "birth", "birthdate", "birthday", "born", "bear",  # "bear": lemma of born
    "geb", "geboren", "geburtsdatum", "né", "née", "naissance",
    "nacido", "nacida", "nacimiento", "जन्म", "जन्मतिथि",
]  # fmt: skip

# Obfuscated emails: "kim dot park at example dot com", "dana.ruiz(at)example(dot)org".
_AT = r"(?:\s*[\(\[\{<]\s*at\s*[\)\]\}>]\s*|\s+at\s+|\s*@\s*)"
_DOT = r"(?:\s*[\(\[\{<]\s*dot\s*[\)\]\}>]\s*|\s+dot\s+|\.)"
_TLDS = r"(?:com|org|net|edu|gov|mil|int|io|co|us|uk|ca|au|de|fr|es|it|nl|in|info|biz|me|app|dev)"
OBFUSCATED_EMAIL_REGEX = (
    rf"\b[a-z0-9_%+-]+(?:{_DOT}[a-z0-9_%+-]+){{0,3}}{_AT}"
    rf"[a-z0-9-]+(?:{_DOT}[a-z0-9-]+){{0,3}}{_DOT}{_TLDS}\b"
)
_PLAIN_EMAIL = re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)+")
_NOT_DOMAINS = {"the", "a", "an", "this", "that", "my", "your", "our", "their", "his", "her",
                "its", "home", "work", "least", "last", "first", "once", "all"}  # fmt: skip


class ObfuscatedEmailRecognizer(PatternRecognizer):
    """Emails written to dodge scrapers. Plain ones are left to Presidio's recognizer."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="EMAIL_ADDRESS",
            name="ObfuscatedEmailRecognizer",
            patterns=[Pattern("obfuscated_email", OBFUSCATED_EMAIL_REGEX, 0.8)],
            context=["email", "mail", "contact", "reach", "reply", "write"],
        )

    def validate_result(self, pattern_text: str) -> bool | None:
        if _PLAIN_EMAIL.fullmatch(pattern_text.strip()):
            return False  # an ordinary email: the built-in recognizer handles it
        domain = re.split(r"\s+at\s+|[\(\[\{<]\s*at\s*[\)\]\}>]|@", pattern_text,
                          maxsplit=1, flags=re.I)[-1]  # fmt: skip
        first_label = re.split(r"\s+dot\s+|[\(\[\{<]|\.", domain.strip(), flags=re.I)[0]
        return None if first_label.lower() not in _NOT_DOMAINS else False


TITLE_WORDS = (
    r"(?:Mr|Mrs|Ms|Mx|Dr|Prof)\.?|Patient|Member|Claimant|Insured|Subscriber|Beneficiary|"
    r"Policyholder|Dear|Attn:?|Name:|Contact:?|Signed,?|Sincerely,?|Regards,?|From:|To:|"
    r"Spouse|Dependent|Provider|Physician|Nurse|Agent|Adjuster|Mother|Father"
)
_NAME = r"[A-Z][a-z]+(?:['-][A-Z]?[a-z]+)?"
PERSON_AFTER_TITLE = (
    rf"(?<=\b(?:{TITLE_WORDS})\s{{1,3}}){_NAME}(?:\s+[A-Z]\.)?(?:\s+{_NAME}){{1,2}}"
)


_PHONE_PREFIX = r"(?:\+1[-.\s]?|001[-.\s]?)?"
_PHONE_EXT = r"(?:\s?(?:x|ext\.?)\s?\d{1,6})?\b"


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


class LuhnCardRecognizer(PatternRecognizer):
    """12-19 digit card numbers (optionally grouped) that pass Luhn, near card context.

    Presidio's built-in recognizer knows common brand prefixes/lengths; this catches the
    rest (e.g. 12-digit Maestro, 19-digit Visa) without flagging random digit runs.
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entity="CREDIT_CARD",
            name="LuhnCardRecognizer",
            patterns=[Pattern("luhn_card", r"\b\d(?:[ -]?\d){11,18}\b", 0.3)],
            context=["card", "credit", "debit", "visa", "mastercard", "amex", "maestro",
                     "statement", "charged", "payment", "ending"],
        )  # fmt: skip

    def validate_result(self, pattern_text: str) -> bool | None:
        digits = re.sub(r"\D", "", pattern_text)
        # None keeps the (context-dependent) score; True would force 1.0 for any Luhn hit
        return None if 12 <= len(digits) <= 19 and luhn_valid(digits) else False


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
        # Unambiguous US phone formats score on their own; bare 10 digits need context.
        _pattern(
            "PHONE_NUMBER",
            "UsPhoneFormatRecognizer",
            [
                (
                    rf"(?:(?<=\s)|^|(?<=[(:;,])){_PHONE_PREFIX}\(\d{{3}}\)\s?\d{{3}}[-.\s]\d{{4}}"
                    rf"{_PHONE_EXT}",
                    0.7,
                ),
                (rf"(?<![\w+]){_PHONE_PREFIX}\d{{3}}[-.]\d{{3}}[-.]\d{{4}}{_PHONE_EXT}", 0.65),
                (r"\b[2-9]\d{2}[2-9]\d{6}\b", 0.5),  # NANP area+exchange; masks on the safe side
            ],
            ["phone", "tel", "telephone", "mobile", "cell", "call", "fax", "contact"],
        ),  # fmt: skip
        LuhnCardRecognizer(),
        ObfuscatedEmailRecognizer(),
        # Bank account numbers: 8-17 digits, only next to account/bank context.
        _pattern(
            "US_BANK_NUMBER",
            "BankAccountRecognizer",
            [(r"\b\d{8,17}\b", 0.3)],
            ["account", "acct", "bank", "routing", "checking", "savings", "iban"],
        ),  # fmt: skip
        _pattern(
            "MEMBER_ID",
            "MemberIdRecognizer",
            [
                (r"\b[A-Z]{3}\d{9}\b", 0.6),  # e.g. XGH123456789 (payer style)
                (r"\bM\d{8,10}\b", 0.6),  # e.g. M12345678
                (r"\b[A-Z]\d{2}[A-Z]\d{5,7}\b", 0.3),
            ],  # weak, needs context
            ["member", "mbr", "subscriber", "enrollee", "id", "card"],
            flags=CASE_SENSITIVE,
        ),  # fmt: skip
        # House formats written with spaces or dashes, in either case (ADR 0007).
        _pattern(
            "MEMBER_ID",
            "MemberIdSeparatedRecognizer",
            [
                (r"\b[A-Z]{3}[-\s]?\d{3}[-\s]?\d{3}[-\s]?\d{3}\b", 0.5),
                (r"\bM[-\s]?\d{4}[-\s]?\d{4,6}\b", 0.5),
            ],
            ["member", "mbr", "subscriber", "enrollee", "id", "card"],
        ),  # fmt: skip
        _pattern(
            "POLICY_NUMBER",
            "PolicyNumberSeparatedRecognizer",
            [(r"\bPOL[-\s#]?\d{6,10}\b", 0.7)],
            ["policy", "pol", "number", "no"],
        ),
        _pattern(
            "CLAIM_NUMBER",
            "ClaimNumberSeparatedRecognizer",
            [(r"\bCLM[-\s]?\d{4}[-\s]?\d{6,8}\b", 0.7)],
            ["claim", "icn", "number", "no"],
        ),
        _pattern(
            "CLAIM_NUMBER",
            "ClaimNumberRecognizer",
            [
                (r"\bCLM-?\d{4}-?\d{6,8}\b", 0.7),  # e.g. CLM-2024-0012345
                (r"\b\d{2}[A-Z]\d{9,11}\b", 0.3),
            ],  # weak, needs context
            ["claim", "icn", "number", "no"],
            flags=CASE_SENSITIVE,
        ),  # fmt: skip
        _pattern(
            "POLICY_NUMBER",
            "PolicyNumberRecognizer",
            [
                (r"\bPOL-?\d{6,10}\b", 0.7),  # e.g. POL-00123456
                (r"\b[A-Z]{2}\d{8}\b", 0.3),
            ],  # weak, needs context
            ["policy", "pol", "number", "no"],
            flags=CASE_SENSITIVE,
        ),  # fmt: skip
        _pattern(
            "DATE_OF_BIRTH",
            "DateOfBirthRecognizer",
            [(_DATE, 0.3), *((rx, 0.3) for rx in WRITTEN_DATES)],
            DOB_CONTEXT,
        ),
        _pattern(
            "ADDRESS",
            "StreetAddressRecognizer",
            [
                (ADDRESS_REGEX, 0.7),
                (PO_BOX_REGEX, 0.7),
                (RURAL_ROUTE_REGEX, 0.7),
                (MILITARY_REGEX, 0.7),
            ],  # fmt: skip
            ["address", "street", "live", "mail", "residence"],
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
            ["zip", "postal", "postcode"],
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
