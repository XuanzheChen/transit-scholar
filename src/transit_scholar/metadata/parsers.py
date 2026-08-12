"""Deterministic rule-based parsing of PDF metadata and first-pages text.

Produces candidate field values with source tracking and confidence scores.
No LLM, no network calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from transit_scholar.metadata.normalizers import (
    is_plausible_publication_year,
    is_valid_arxiv_id,
    normalize_arxiv_id,
    normalize_author_name,
    normalize_doi,
    normalize_title,
)
from transit_scholar.metadata.pdf_reader import PdfReadResult


@dataclass
class Candidate:
    """A single metadata candidate value."""

    field_name: str
    value_text: str
    source_type: str
    source_location: str
    confidence: float


# DOI: 10.<4-9 digits>/<non-empty suffix]
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s]+)")
# arXiv ID: new-style 2301.01234 or with version 2301.01234v2. Month
# validation (01-12) happens in _extract_arxiv_id via is_valid_arxiv_id.
_ARXIV_RE = re.compile(r"\b(\d{4}\.\d{4,5}(?:v\d+)?)\b")
# Year: 1900-2099; the plausible range and publication context are enforced
# in _extract_year.
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
# Publication-context keywords that make a nearby year eligible.
_YEAR_SIGNAL_KEYWORDS = (
    "published", "publication", "available online", "copyright",
    "vol.", "volume", "issue", "©",
)
# Submission-history keywords that make a nearby year ineligible.
_YEAR_REJECT_KEYWORDS = ("received", "revised", "accepted", "submitted")
_YEAR_CONTEXT_RE = re.compile(
    "|".join(_YEAR_SIGNAL_KEYWORDS + _YEAR_REJECT_KEYWORDS),
    re.IGNORECASE,
)


def _extract_doi(text: str) -> str | None:
    """Extract the first well-formed DOI from text."""
    for match in _DOI_RE.finditer(text):
        raw = match.group(1)
        normalized = normalize_doi(raw)
        if normalized and "/" in normalized:
            return normalized
    return None


def _extract_arxiv_id(text: str) -> str | None:
    """Extract the first valid arXiv ID from text.

    New-style IDs must have a month in 01..12 (``2300.01234`` and ``2313.01234``
    are rejected). The version suffix is optional.
    """
    for match in _ARXIV_RE.finditer(text):
        candidate = match.group(1)
        # Skip things that look like version numbers or years.
        prefix = candidate.split(".")[0]
        if len(prefix) == 4 and prefix.startswith(("19", "20")):
            # Could be a year; require the suffix to look arXiv-like.
            suffix = candidate.split(".")[1]
            if len(suffix) < 4:
                continue
        normalized = normalize_arxiv_id(candidate)
        if normalized and is_valid_arxiv_id(normalized):
            return normalized
    return None


def _extract_year(text: str) -> str | None:
    """Extract the first publication year supported by explicit context.

    A four-digit year is eligible only when (a) it lies in the named plausible
    range (1900 through current UTC year + 1) and (b) it appears next to an
    explicit publication signal: a published/publication/available-online/
    copyright/volume/issue keyword, or a journal volume/issue/year structure
    such as ``174 (2025)``. Years near submission-history keywords (received,
    revised, accepted, submitted) and the first arbitrary four-digit number
    are never used.
    """
    for match in _YEAR_RE.finditer(text):
        year = int(match.group(1))
        if not is_plausible_publication_year(year):
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]
        # Journal volume/issue/year structure: "174 (2025)".
        if re.search(r"\b\d{1,4}\s*\(\s*%d\s*\)" % year, line):
            return str(year)
        # The last publication-context keyword before the year decides.
        contexts = list(_YEAR_CONTEXT_RE.finditer(text[line_start:match.start()]))
        if not contexts:
            continue
        last = contexts[-1].group(0).lower()
        if last in _YEAR_SIGNAL_KEYWORDS:
            return str(year)
    return None


def _split_and_clean_authors(text: str) -> list[str]:
    """Split a raw author string into individual cleaned names.

    Removes affiliation markers: superscript digits, dagger/star symbols,
    parenthetical marks, and single-letter markers that follow a name (for
    example the ``a`` in ``Feiyu Yang a``), so marker letters never become
    authors.
    """
    # Remove parenthetical markers like (B), (corresponding author), (1).
    cleaned = re.sub(r"\([^)]*\)", " ", text)
    # Remove footnote symbols (dagger, double dagger, star, hash) and
    # corresponding-author symbols (asterisk operator, envelope, section sign,
    # paragraph sign, check mark) that may trail a name.
    cleaned = re.sub(r"[†‡*#∗✉✆§¶✓]", " ", cleaned)
    # Split on comma, semicolon, ' and ', ' & '.
    parts = re.split(r",|;|\s+and\s+|\s+&\s+", cleaned)
    names: list[str] = []
    for part in parts:
        # Strip leading digits and whitespace (superscript noise).
        p = re.sub(r"^[\d\s]+", " ", part)
        p = re.sub(r"[\d\s]+$", " ", p)
        # Strip trailing single-letter affiliation markers: "Feiyu Yang a"
        # (also "Feiyu Yang a,*", "Feiyu Yang a,b" and "Shaoquan Ni a∗").
        p = re.sub(r"\s+[a-z](?:[,*†‡#∗✉✆§¶✓0-9]+[a-z]?)*$", "", p)
        p = p.strip(" ,;:")
        # Remove academic-role suffixes.
        p = re.sub(r"\b(Senior|Member|Fellow)\b.*", "", p, flags=re.IGNORECASE).strip(" ,;:")
        p = re.sub(r"\bIEEE\b.*", "", p, flags=re.IGNORECASE).strip(" ,;:")
        p = p.strip(" ,;:")
        if p and len(p) >= 2:
            names.append(p)
    return names


def parse_pdf_metadata(read: PdfReadResult) -> list[Candidate]:
    """Extract candidates from PDF document metadata."""
    candidates: list[Candidate] = []
    meta = read.metadata

    # Title
    title = meta.get("title")
    if title and title.strip() and normalize_title(title) is not None:
        candidates.append(Candidate(
            field_name="title",
            value_text=title.strip(),
            source_type="pdf_metadata",
            source_location="metadata.title",
            confidence=0.8,
        ))

    # Author
    author = meta.get("author")
    if author and author.strip():
        for name in _split_and_clean_authors(author):
            candidates.append(Candidate(
                field_name="author",
                value_text=name,
                source_type="pdf_metadata",
                source_location="metadata.author",
                confidence=0.6,
            ))

    # Keywords
    keywords = meta.get("keywords")
    if keywords:
        doi = _extract_doi(keywords)
        if doi:
            candidates.append(Candidate(
                field_name="doi",
                value_text=doi,
                source_type="pdf_metadata",
                source_location="metadata.keywords",
                confidence=0.95,
            ))
        arxiv = _extract_arxiv_id(keywords)
        if arxiv:
            candidates.append(Candidate(
                field_name="arxiv_id",
                value_text=arxiv,
                source_type="pdf_metadata",
                source_location="metadata.keywords",
                confidence=0.95,
            ))

    return candidates


def parse_first_pages_text(read: PdfReadResult) -> list[Candidate]:
    """Extract candidates from the first pages of text."""
    candidates: list[Candidate] = []
    text = read.first_pages_text
    if not text.strip():
        return candidates

    # DOI
    doi = _extract_doi(text)
    if doi:
        candidates.append(Candidate(
            field_name="doi",
            value_text=doi,
            source_type="doi_parser",
            source_location="first_pages_text",
            confidence=0.95,
        ))

    # arXiv ID
    arxiv = _extract_arxiv_id(text)
    if arxiv:
        candidates.append(Candidate(
            field_name="arxiv_id",
            value_text=arxiv,
            source_type="arxiv_parser",
            source_location="first_pages_text",
            confidence=0.95,
        ))

    # Year
    year = _extract_year(text)
    if year:
        candidates.append(Candidate(
            field_name="publication_year",
            value_text=year,
            source_type="first_pages_text",
            source_location="first_pages_text",
            confidence=0.5,
        ))

    # Abstract: look for an "Abstract" section header and capture the body.
    abstract = _extract_abstract(text)
    if abstract:
        candidates.append(Candidate(
            field_name="abstract",
            value_text=abstract,
            source_type="first_pages_text",
            source_location="first_pages_text",
            confidence=0.4,
        ))

    return candidates


def _extract_abstract(text: str) -> str | None:
    """Extract text following an 'Abstract' header."""
    # Match "Abstract" or "ABSTRACT" followed by optional punctuation,
    # then capture text until a clear section break or double newline.
    pattern = re.compile(
        r"(?:^|\n)\s*abstract[\s.:]*\n(.+?)(?:\n\s*(?:introduction|1[.\s]|I\.|\n\n))",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(text)
    if match:
        body = match.group(1).strip()
        # Normalize whitespace.
        body = re.sub(r"\s+", " ", body)
        if len(body) > 30:
            return body
    return None


def _first_page_lines(read: PdfReadResult) -> list[str]:
    """Return cleaned lines from the first page text."""
    page_one = read.first_pages_text.split("\f")[0]
    return [line.strip() for line in page_one.splitlines()]


def _is_header_or_noise_line(line: str) -> bool:
    """Return True for page furniture that should not become metadata."""
    if len(line.strip()) < 3:
        return True
    patterns = [
        r"IEEE TRANSACTIONS",
        r"\bVOL\.",
        r"\bNO\.",
        r"\bJOURNAL\b",
        r"\bPROCEEDINGS\b",
        r"Contents lists available",
        r"journal homepage",
        r"https?://",
        r"^\d+$",
        r"^[\d\-–—]+$",
        r"^\s*doi\b",
        r"^\s*10\.\d{4,9}/",
        r"\bcopyright\b",
        r"\baccepted\b",
        r"\breceived\b",
        r"\barxiv\b",
        r"\bsciencedirect\b",
        r"\belsevier\b",
        r"^\s*article\s+info\b",
        r"^\s*keywords\b",
        # ScienceDirect journal masthead line without volume/article numbers:
        # "Transportation Research Part C" or
        # "Transportation Research Part C: Emerging Technologies".
        r"^\s*Transportation\s+Research\s+Part\s+[A-Za-z](?:\s*:.*)?\.?$",
        # Elsevier journal header forms: "174 (2025) 106310" and
        # "Transportation Research Part C: Emerging Technologies 174 (2025) 106310".
        r"^\s*\d{1,4}\s*\(\s*\d{4}\s*\)\s+\d{1,6}\s*$",
        r"^\s*[\w&:.,'\- ]+?\s+\d{1,4}\s*\(\s*\d{4}\s*\)\s+\d{1,6}\s*$",
        # Elsevier letter-spaced "A R T I C L E   I N F O" heading.
        r"^\s*A\s+R\s+T\s+I\s+C\s+L\s+E\s+I\s+N\s+F\s+O\b",
    ]
    return any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns)


def _is_abstract_line(line: str) -> bool:
    if re.search(r"^\s*abstract(?:\s|[^\w]|$)", line, re.IGNORECASE):
        return True
    # Elsevier letter-spaced "A B S T R A C T" heading.
    return bool(re.search(r"^\s*A\s+B\s+S\s+T\s+R\s+A\s+C\s+T\b", line, re.IGNORECASE))


def _is_affiliation_or_contact_line(line: str) -> bool:
    patterns = [
        r"@",
        r"\bUniversity\b",
        r"\bInstitute\b",
        r"\bCollege\b",
        r"\bSchool\b",
        r"\bDepartment\b",
        r"\bDept\b",
        r"\bLaboratory\b",
        r"\bCenter\b",
        r"\bCentre\b",
        r"\bChina\b",
        r"\bUSA\b",
        r"\bUnited States\b",
        r"\bNew Zealand\b",
        r"\bBeijing\b",
        r"\bStanford\b",
        r"\bCambridge\b",
        r"\bCorresponding\b",
        r"\bInc\.\b",
        r"\bLtd\.\b",
    ]
    return any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns)


def _looks_author_line(line: str) -> bool:
    """Heuristic: does this line look like it could contain author names?"""
    stripped = line.strip()
    if not stripped:
        return False
    if "," in stripped or ";" in stripped:
        return True
    if re.search(r"\s+and\s+", stripped, re.IGNORECASE):
        return True
    if re.search(r"[A-Za-z]\d(?:\([A-Za-z]\))?", stripped):
        return True

    words = [w for w in re.split(r"\s+", stripped) if w]
    if len(words) == 2:
        return all(re.match(r"^[A-Z][A-Za-z'.-]+(?:\d+)?(?:\([A-Za-z]\))?$", w) for w in words)
    # "Name Surname a" — two capitalized name words followed by a single-letter
    # affiliation marker (possibly with a star/digits or a second letter, e.g.
    # "Feiyu Yang a" or "Feiyu Yang a,b").
    if len(words) == 3:
        if not re.match(r"^[a-z](?:[,*†‡#0-9]+[a-z]?)*$", words[2]):
            return False
        return all(re.match(r"^[A-Z][A-Za-z'.-]+$", w) for w in words[:2])
    return False


def _passes_title_quality(text: str) -> bool:
    """Basic quality filter for first-page title candidates."""
    t = text.strip()
    if len(t) < 5 or len(t) > 500:
        return False
    if t.startswith(("http://", "https://", "file://")):
        return False
    # Every title candidate path validates via normalize_title.
    if normalize_title(t) is None:
        return False
    if _is_header_or_noise_line(t) or _is_abstract_line(t) or _is_affiliation_or_contact_line(t):
        return False
    if re.search(r"@", t):
        return False
    if re.search(r"\bdoi\b", t, re.IGNORECASE):
        return False
    if t.endswith("."):
        return False
    return True


def _passes_author_quality(text: str) -> bool:
    """Basic quality filter for first-page author candidates."""
    t = text.strip()
    if len(t) < 2 or len(t) > 200:
        return False
    if _is_header_or_noise_line(t) or _is_abstract_line(t) or _is_affiliation_or_contact_line(t):
        return False
    if re.search(r"@", t):
        return False
    if not re.search(r"[A-Za-z]", t):
        return False
    return True


def _find_first_page_title_span(lines: list[str]) -> tuple[int, int] | None:
    """Return inclusive start/end indexes for the first-page title block."""
    title_lines: list[str] = []
    start: int | None = None
    for idx, line in enumerate(lines):
        if start is None:
            if _is_header_or_noise_line(line) or _is_abstract_line(line):
                continue
            start = idx
            title_lines.append(line)
            continue

        if not line or _is_abstract_line(line) or _is_affiliation_or_contact_line(line):
            break
        if _looks_author_line(line):
            break
        if _is_header_or_noise_line(line):
            break
        title_lines.append(line)
        if len(title_lines) >= 4:
            break

    if start is None or not title_lines:
        return None
    joined = _join_title_lines(title_lines).strip(" ,;:")
    if not _passes_title_quality(joined):
        return None
    return start, start + len(title_lines) - 1


def parse_first_page_title_block(read: PdfReadResult) -> Candidate | None:
    """Extract a title candidate from the first page's title block.

    Heuristic: skip header/journal/page-number lines, then collect consecutive
    title lines until a stop signal (author, abstract, affiliation, email).
    Only consults ``first_pages_text``.
    """
    if not read.first_pages_text.strip():
        return None

    lines = _first_page_lines(read)
    span = _find_first_page_title_span(lines)
    if span is None:
        return None

    start, end = span
    joined = _join_title_lines(lines[start:end + 1])
    joined = joined.strip(" ,;:")
    if not _passes_title_quality(joined) or normalize_title(joined) is None:
        return None

    return Candidate(
        field_name="title",
        value_text=joined,
        source_type="first_pages_text",
        source_location="first_page_title_block",
        confidence=0.72,
    )


def _join_title_lines(lines: list[str]) -> str:
    """Join title lines, removing trailing line-break hyphens."""
    out = []
    for i, line in enumerate(lines):
        is_last = i == len(lines) - 1
        cur = line.strip()
        if not is_last:
            # Remove a trailing ASCII hyphen or soft hyphen used to break a word.
            if cur.endswith("-") or cur.endswith("­"):  # U+00AD soft hyphen
                cur = cur[:-1].rstrip()
            out.append(cur)
        else:
            out.append(cur)
    joined = " ".join(part for part in out if part)
    # Some embedded PDF fonts extract an en dash / em dash as a bare question
    # mark. In title blocks, a question mark surrounded by spaces is more
    # often a failed dash glyph than punctuation.
    return re.sub(r"\s+\?\s+", " – ", joined)


def parse_first_page_author_block(read: PdfReadResult) -> list[Candidate]:
    """Extract author candidates from the first page, after the title block.

    Locates the title block first, then reads the following non-empty lines
    until an affiliation/email/abstract/address stop signal. Every collected
    line is split and cleaned so affiliation markers never become authors and
    title text is never prepended to the first author.
    """
    if not read.first_pages_text.strip():
        return []

    lines = _first_page_lines(read)
    span = _find_first_page_title_span(lines)
    if span is None:
        return []

    author_lines: list[str] = []
    for line in lines[span[1] + 1:]:
        if not line:
            if author_lines:
                break
            continue
        if _is_abstract_line(line) or _is_affiliation_or_contact_line(line):
            break
        if _is_header_or_noise_line(line):
            if author_lines:
                break
            continue
        if line.endswith(".") and len(line) > 120:
            break
        author_lines.append(line)
        if len(author_lines) >= 40:
            break

    if not author_lines:
        return []

    names: list[str] = []
    for line in author_lines:
        names.extend(_split_and_clean_authors(line))

    candidates: list[Candidate] = []
    for name in names:
        cleaned = name.strip(" ,;:")
        if _passes_author_quality(cleaned):
            candidates.append(Candidate(
                field_name="author",
                value_text=cleaned,
                source_type="first_pages_text",
                source_location="first_page_author_block",
                confidence=0.68,
            ))
    return candidates


def parse_all(read: PdfReadResult) -> list[Candidate]:
    """Combine all candidate sources."""
    candidates: list[Candidate] = []
    candidates.extend(parse_pdf_metadata(read))
    candidates.extend(parse_first_pages_text(read))
    title = parse_first_page_title_block(read)
    if title is not None:
        candidates.append(title)
    candidates.extend(parse_first_page_author_block(read))
    return candidates


# --- Stage 6: strict filename arXiv ID candidate extraction ------------------
FILENAME_ARXIV_RE = re.compile(
    r"(?i)^(?:arxiv[-_ ]*)?(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)$"
)


def parse_filename_candidates(original_filename: str) -> list[Candidate]:
    """Extract a strict arXiv ID candidate from a file name.

    Only matches the stem (without extension) of new-style arXiv IDs with a
    valid month (01-12). Low-confidence (0.9) because file names may contain
    other numbers.
    """
    if not original_filename:
        return []
    stem = original_filename
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    match = FILENAME_ARXIV_RE.match(stem)
    if match is None:
        return []
    normalized = normalize_arxiv_id(match.group("id"))
    if not normalized or not is_valid_arxiv_id(normalized):
        return []
    return [Candidate(
        field_name="arxiv_id",
        value_text=normalized,
        source_type="filename_parser",
        source_location="original_filename",
        confidence=0.9,
    )]
