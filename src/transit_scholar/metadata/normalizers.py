"""Normalization helpers for metadata fields.

All functions are deterministic and safe to call repeatedly. They return
``None`` when the input is ``None`` or empty after stripping.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone


def normalize_title(value: str | None) -> str | None:
    """Normalize a title string for matching/comparison.

    Applies: Unicode NFKC normalization, lowercase, collapse newlines,
    collapse consecutive whitespace, replace common hyphens with spaces,
    strip leading/trailing punctuation and whitespace.
    """
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", value)
    text = text.lower()
    text = text.replace("\n", " ").replace("\r", " ")
    # Replace common hyphens/dashes with a space (ASCII hyphen, U+2010 hyphen,
    # U+2011 non-breaking hyphen, U+2012 figure dash, U+2013 en dash,
    # U+2014 em dash, U+2015 horizontal bar, U+2212 minus sign).
    text = re.sub(r"[-‐‑‒–—―−]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(".,;:!?\"'()[]{}")
    return text or None


def normalize_doi(value: str | None) -> str | None:
    """Normalize a DOI string.

    Applies: lowercase, strip common URL prefixes (https://doi.org/,
    http://dx.doi.org/, doi:), strip trailing punctuation and whitespace.
    """
    if value is None:
        return None
    text = value.strip().lower()
    # Remove common URL prefixes.
    for prefix in (
        "https://doi.org/",
        "http://dx.doi.org/",
        "https://dx.doi.org/",
        "doi:",
    ):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.strip().rstrip(".,;:")
    return text or None


def normalize_arxiv_id(value: str | None) -> str | None:
    """Normalize an arXiv ID.

    Applies: strip arXiv: prefix, lowercase, keep version suffix (vN).
    This is a pure string normalizer; validity (e.g. the 01-12 month rule of
    new-style IDs) is enforced by :func:`is_valid_arxiv_id` at the extraction
    paths, never by normalization alone.
    """
    if value is None:
        return None
    text = value.strip().lower()
    if text.startswith("arxiv:"):
        text = text[len("arxiv:") :].strip()
    return text or None


# --- arXiv ID validity (new-style YYMM.sequence with a valid month) ----------
_ARXIV_NEW_STYLE_RE = re.compile(r"^(\d{2})(\d{2})\.(\d{4,5})(?:v\d+)?$")


def is_valid_arxiv_id(value: str | None) -> bool:
    """Return True when ``value`` is an acceptable new-style arXiv ID.

    New-style IDs are ``YYMM.sequence``: the month part must be ``01`` through
    ``12`` (``00`` and ``13``-``99`` are rejected), the sequence must be 4 or 5
    digits, and an optional version suffix (``vN``) is allowed. Old-style IDs
    are out of scope for extraction.
    """
    if value is None:
        return False
    text = value.strip().lower()
    if text.startswith("arxiv:"):
        text = text[len("arxiv:") :].strip()
    match = _ARXIV_NEW_STYLE_RE.match(text)
    if match is None:
        return False
    month = int(match.group(2))
    return 1 <= month <= 12


# --- Publication year range (named plausible range) --------------------------
MIN_PUBLICATION_YEAR = 1900


def plausible_publication_year_upper() -> int:
    """Upper bound of the plausible publication year range.

    The named range is ``1900`` through the current UTC year plus one, so a
    paper published at the very start of a new year is never rejected.
    """
    return datetime.now(timezone.utc).year + 1


def is_plausible_publication_year(year: int | str | None) -> bool:
    """Return True when ``year`` lies in the plausible publication range.

    Every year materialization path (heuristic, structured metadata and
    provider candidates alike) must use this same validator before writing a
    value, so an out-of-range year is never accepted from any source.
    """
    if year is None:
        return False
    try:
        value = int(str(year).strip())
    except (TypeError, ValueError):
        return False
    return MIN_PUBLICATION_YEAR <= value <= plausible_publication_year_upper()


def normalize_author_name(value: str | None) -> str | None:
    """Normalize an author name for matching.

    Applies: Unicode NFKC, lowercase, collapse whitespace, strip leading/
    trailing punctuation. Does NOT attempt Chinese/abbreviation mapping.
    """
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", value)
    text = text.lower()
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(".,;:!?\"'()[]{}")
    return text or None
