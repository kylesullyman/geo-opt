"""Normalization shared by NAP checks and mention detection. Pure functions."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")

LEGAL_SUFFIXES = {"inc", "llc", "llp", "ltd", "co", "corp", "corporation", "pc", "pllc", "dds", "dmd", "md", "pa"}

STREET_ABBR = {
    "street": "st", "avenue": "ave", "av": "ave", "boulevard": "blvd", "road": "rd", "drive": "dr",
    "lane": "ln", "court": "ct", "place": "pl", "parkway": "pkwy", "highway": "hwy", "suite": "ste",
    "north": "n", "south": "s", "east": "e", "west": "w", "circle": "cir", "terrace": "ter",
    "way": "way", "square": "sq", "unit": "unit", "building": "bldg", "floor": "fl", "#": "ste",
}

US_STATES = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca", "colorado": "co",
    "connecticut": "ct", "delaware": "de", "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
    "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks", "kentucky": "ky", "louisiana": "la",
    "maine": "me", "maryland": "md", "massachusetts": "ma", "michigan": "mi", "minnesota": "mn",
    "mississippi": "ms", "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
    "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny", "north carolina": "nc",
    "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or", "pennsylvania": "pa",
    "rhode island": "ri", "south carolina": "sc", "south dakota": "sd", "tennessee": "tn", "texas": "tx",
    "utah": "ut", "vermont": "vt", "virginia": "va", "washington": "wa", "west virginia": "wv",
    "wisconsin": "wi", "wyoming": "wy",
}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("&", " and ").replace("’", "'")
    s = re.sub(r"'s\b", "", s, flags=re.I)  # possessive: "Smiles Dental's" -> "Smiles Dental"
    s = s.replace("'", "")
    return _WS.sub(" ", _PUNCT.sub(" ", s.lower())).strip()


def norm_name(s: str) -> str:
    words = fold(s).split()
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    if words and words[0] == "the":
        words = words[1:]
    return " ".join(words)


def norm_phone(s: str) -> str | None:
    digits = re.sub(r"\D", "", s or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\b(\d{3})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})\b")


def find_phones(text: str) -> set[str]:
    return {a + b + c for a, b, c in PHONE_RE.findall(text or "")}


def norm_street(s: str) -> str:
    words = fold(s.replace("#", " # ")).split()
    return " ".join(STREET_ABBR.get(w, w) for w in words)


def street_core(s: str) -> str:
    """Number + street name, without suite/unit: '123 main st'."""
    n = norm_street(s)
    n = re.split(r"\b(ste|unit|apt|bldg|fl)\b", n)[0]
    return n.strip()


def norm_region(s: str) -> str:
    f = fold(s)
    return US_STATES.get(f, f)


def norm_postal(s: str) -> str:
    m = re.match(r"\s*(\d{5})", s or "")
    return m.group(1) if m else fold(s or "")


def norm_domain(url_or_host: str) -> str:
    s = url_or_host.strip().lower()
    if "://" not in s:
        s = "http://" + s
    host = urlsplit(s).hostname or ""
    return host.removeprefix("www.")


def domain_matches(url: str, domain: str) -> bool:
    """True if url's host is domain or a subdomain of it."""
    h = norm_domain(url)
    d = norm_domain(domain)
    return bool(h) and (h == d or h.endswith("." + d))
