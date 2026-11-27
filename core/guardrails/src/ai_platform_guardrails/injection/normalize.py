"""Text normalisation and payload decoding before rule matching.

Attackers hide instructions with Unicode tricks (zero-width characters, look-alike
letters, full-width forms) and encodings (base64, hex, ``\\u`` escapes, URL encoding).
We normalise the text and also return decoded variants so every layer is scanned.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote

ZERO_WIDTH = re.compile("[​-‏‪-‮⁠-⁤﻿­]")
# Common Cyrillic/Greek look-alikes of Latin letters (NFKC does not fold these).
CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i",
    "ј": "j", "ѕ": "s", "ԁ": "d", "ɡ": "g", "һ": "h", "ӏ": "l", "ո": "n", "ν": "v",
    "Α": "A", "Β": "B", "Ε": "E", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O",
    "Ρ": "P", "Τ": "T", "Χ": "X", "Υ": "Y", "ο": "o", "ι": "i", "α": "a", "ε": "e",
})  # fmt: skip
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a",
                      "$": "s"})  # fmt: skip

B64_TOKEN = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{16,}={0,2}(?![A-Za-z0-9+/=])")
HEX_TOKEN = re.compile(r"\b(?:0x)?((?:[0-9a-fA-F]{2}){8,})\b")
U_ESCAPE = re.compile(r"(?:\\u[0-9a-fA-F]{4}){4,}")
PCT_ESCAPE = re.compile(r"(?:%[0-9a-fA-F]{2}){4,}")
SPACED_LETTERS = re.compile(r"\b(?:[A-Za-z][ .\-_]){5,}[A-Za-z]\b")
# "I g n o r e   a l l": single letters separated by one space, words by 2+ spaces
SPACED_WORDS = re.compile(r"(?:\b[A-Za-z]\b(?: {1,3}|$)){6,}")


@dataclass
class Normalised:
    text: str  # normalised, lower-cased
    variants: list[tuple[str, str]] = field(default_factory=list)  # (kind, decoded text)
    obfuscated: bool = False  # zero-width / confusables / spacing tricks were present


def _printable_ratio(s: str) -> float:
    return sum(ch.isprintable() or ch in "\n\t" for ch in s) / max(1, len(s))


def _clean(text: str) -> tuple[str, bool]:
    nfkc = unicodedata.normalize("NFKC", text)
    stripped = ZERO_WIDTH.sub("", nfkc)
    folded = stripped.translate(CONFUSABLES)
    changed = stripped != nfkc or folded != stripped or nfkc != text
    return folded, changed


def _try_b64(token: str) -> str | None:
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), validate=True)
        out = raw.decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    return out if len(out) >= 8 and _printable_ratio(out) > 0.9 and " " in out else None


def _try_hex(token: str) -> str | None:
    try:
        out = bytes.fromhex(token).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return out if _printable_ratio(out) > 0.9 and " " in out else None


def _despace(text: str) -> str:
    """Re-join letter-spaced text: 2+ spaces separate words, single separators vanish."""

    def join(m: re.Match[str]) -> str:
        words = re.split(r" {2,}", m.group(0).strip())
        return " " + " ".join(re.sub(r"[ .\-_]", "", w) for w in words) + " "

    text = SPACED_WORDS.sub(join, text)
    return SPACED_LETTERS.sub(lambda m: re.sub(r"[ .\-_]", "", m.group(0)), text)


def normalise(text: str) -> Normalised:
    """Normalise ``text`` and collect decoded variants (one level deep)."""
    cleaned, obfuscated = _clean(text)
    variants: list[tuple[str, str]] = []
    for m in B64_TOKEN.finditer(cleaned):
        decoded = _try_b64(m.group(0))
        if decoded:
            variants.append(("base64", _clean(decoded)[0].lower()))
    for m in HEX_TOKEN.finditer(cleaned):
        decoded = _try_hex(m.group(1))
        if decoded:
            variants.append(("hex", _clean(decoded)[0].lower()))
    for m in U_ESCAPE.finditer(cleaned):
        try:
            decoded = codecs.decode(m.group(0), "unicode_escape")
        except UnicodeDecodeError:
            continue
        variants.append(("unicode_escape", _clean(decoded)[0].lower()))
    for m in PCT_ESCAPE.finditer(cleaned):
        variants.append(("url_encoding", _clean(unquote(m.group(0)))[0].lower()))
    spaced = SPACED_LETTERS.findall(cleaned) or SPACED_WORDS.search(cleaned)
    if spaced:
        variants.append(("spaced_letters", _despace(cleaned).lower()))
        obfuscated = True
    leet = cleaned.lower().translate(LEET)
    if leet != cleaned.lower():
        variants.append(("leetspeak", leet))
    rot = codecs.encode(cleaned, "rot13").lower()
    variants.append(("rot13", rot))
    return Normalised(re.sub(r"\s+", " ", cleaned).lower(), variants, obfuscated)
