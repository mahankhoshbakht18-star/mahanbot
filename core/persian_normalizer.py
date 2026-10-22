"""
core/persian_normalizer.py — Persian text normalization, cleaning, and location/bank matching.
Handles Arabic/Persian letter variations (ی/ي, ک/ك), half-spaces (ZWNJ), digits, whitespace,
and distinct handling of compound names (e.g., Chabahar vs Free Zone, Sib & Suran vs Suran).
"""
import re
from typing import Any, List, Optional, Set, Tuple


# Character translation table for Persian normalization
_CHAR_MAP = {
    ord("ي"): "ی",
    ord("ى"): "ی",
    ord("ئ"): "ی",
    ord("ك"): "ک",
    ord("ة"): "ه",
    ord("ۀ"): "ه",
    ord("أ"): "ا",
    ord("إ"): "ا",
    ord("آ"): "ا",
    ord("\u200c"): " ",  # ZWNJ (half-space) converted to space for robust word tokenization
    ord("\u200b"): "",   # Zero-width space stripped
    ord("\ufeff"): "",   # BOM stripped
    ord("\xa0"): " ",    # Non-breaking space
    ord("\t"): " ",
    ord("\r"): " ",
    ord("\n"): " ",
}

# Digits mapping (Persian and Arabic numerals -> standard ASCII digits)
_DIGIT_MAP = {
    ord("۰"): "0", ord("۱"): "1", ord("۲"): "2", ord("۳"): "3", ord("۴"): "4",
    ord("۵"): "5", ord("۶"): "6", ord("۷"): "7", ord("۸"): "8", ord("۹"): "9",
    ord("٠"): "0", ord("١"): "1", ord("٢"): "2", ord("٣"): "3", ord("٤"): "4",
    ord("٥"): "5", ord("٦"): "6", ord("٧"): "7", ord("٨"): "8", ord("٩"): "9",
}

_FULL_TRANS = {**_CHAR_MAP, **_DIGIT_MAP}

# Arabic diacritics (harakat)
_HARAKAT_RE = re.compile(r"[\u064B-\u0652\u0670]")

# Location prefixes to strip when computing matching keys
_LOCATION_PREFIX_RE = re.compile(r"^\s*(استان|شهرستان|شهر|منطقه|بخش)\s+", re.UNICODE)


def normalize_persian_text(text: Any) -> str:
    """
    Standardize Persian text:
    - Arabic Yeh/Kaf to Persian
    - Persian/Arabic digits to ASCII
    - Strip diacritics
    - Normalize spaces
    """
    if text is None:
        return ""
    s = str(text)
    s = _HARAKAT_RE.sub("", s)
    s = s.translate(_FULL_TRANS)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_location_key(text: Any) -> str:
    """
    Normalized key for province or city matching:
    Strips leading 'استان', 'شهرستان', 'شهر', collapses spaces, and cleans punctuation.
    """
    norm = normalize_persian_text(text)
    norm = _LOCATION_PREFIX_RE.sub("", norm).strip()
    # Normalize common Persian conjunctions
    norm = re.sub(r"\s*و\s*", " و ", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm


def normalize_compact_key(text: Any) -> str:
    """Remove all spaces and punctuation for strict fuzzy-resistant key matching."""
    norm = normalize_location_key(text)
    return re.sub(r"[\s\-_/\\()]+", "", norm)


def normalize_national_id(nid: Any) -> str:
    """
    Normalize national ID: clean non-digits and preserve 10 digits with leading zeros.
    Returns 10-character string or empty string if invalid.
    """
    if nid is None:
        return ""
    s = str(nid).strip()
    s = s.translate(_DIGIT_MAP)
    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""
    if len(digits) < 10:
        digits = digits.zfill(10)
    return digits


def normalize_postal_code(postal: Any) -> str:
    """
    Normalize postal code: clean non-digits and preserve 10 digits with leading zeros.
    Returns 10-character string or empty string if invalid.
    """
    if postal is None:
        return ""
    s = str(postal).strip()
    s = s.translate(_DIGIT_MAP)
    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""
    if len(digits) < 10:
        digits = digits.zfill(10)
    return digits


def is_special_location_case(loc1: str, loc2: str) -> Optional[bool]:
    """
    Handle special sensitive location pairs where naive substring/token matching fails:
    - 'چابهار' vs 'چابهار / مناطق آزاد'
    - 'سوران' vs 'سیب و سوران'
    - 'مهرستان' vs 'زابلی' (historical alias: they are the same city)
    """
    k1 = normalize_location_key(loc1)
    k2 = normalize_location_key(loc2)

    # Chabahar vs Free Zone distinction
    has_fz1 = "مناطق ازاد" in k1 or "منطقه ازاد" in k1
    has_fz2 = "مناطق ازاد" in k2 or "منطقه ازاد" in k2
    if ("چابهار" in k1 and "چابهار" in k2) and (has_fz1 != has_fz2):
        # One is Free Zone, the other is regular Chabahar -> distinct!
        return False

    # Sib & Suran vs Suran distinction
    has_sib1 = "سیب" in k1
    has_sib2 = "سیب" in k2
    if ("سوران" in k1 and "سوران" in k2) and (has_sib1 != has_sib2):
        # If user explicitly specified 'سیب و سوران', do not conflate with standalone 'سوران' unless configured
        return False

    # Mehrestan & Zaboli are the same administrative city in Sistan & Baluchestan
    if (k1 in ("مهرستان", "زابلی") and k2 in ("مهرستان", "زابلی")):
        return True

    return None


def match_location_name(input_text: Any, canonical_name: str, aliases: Optional[List[str]] = None) -> bool:
    """
    Controlled matching of an input province or city against a canonical name and its aliases.
    Strictly checks for false positives.
    """
    if not input_text:
        return False

    raw_in = str(input_text)
    k_in = normalize_location_key(raw_in)
    c_in = normalize_compact_key(raw_in)

    candidates = [canonical_name] + (aliases or [])
    for cand in candidates:
        special = is_special_location_case(raw_in, cand)
        if special is False:
            continue
        if special is True:
            return True

        k_cand = normalize_location_key(cand)
        c_cand = normalize_compact_key(cand)

        if k_in == k_cand or c_in == c_cand:
            return True

    return False
