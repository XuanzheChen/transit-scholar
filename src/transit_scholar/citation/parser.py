"""Citation parsers: convert 7 source formats into CSL-like structured JSON.

All parsers are deterministic and local — no network, no LLM, no PDF reading.
Each returns a CitationParseResult. ``type`` and ``title`` are the minimum
required fields; if either is missing, parse_status is ``failed``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from transit_scholar.citation.result import CitationParseResult

# --- Enumerations (frozen in Phase 1) --------------------------------------

SOURCE_FORMATS = frozenset(
    {
        "manual_structured",
        "csl_json",
        "bibtex",
        "ris",
        "apa",
        "mla",
        "gb_t_7714_2025",
    }
)

CITATION_TYPES = frozenset(
    {
        "article-journal",
        "paper-conference",
        "book",
        "chapter",
        "thesis",
        "report",
        "webpage",
        "unknown",
    }
)

DEFAULT_TYPE = "article-journal"


# --- Public entry point ----------------------------------------------------


def parse_citation(
    *,
    source_format: str,
    raw_text: str | None,
    structured_data: dict | None,
) -> CitationParseResult:
    """Dispatch to the correct parser based on ``source_format``."""
    if source_format not in SOURCE_FORMATS:
        return CitationParseResult(
            structured={},
            parse_status="failed",
            warnings=[],
            error_code="INVALID_SOURCE_FORMAT",
            error_message=f"Unknown source_format: {source_format!r}",
        )

    if raw_text is None and structured_data is None:
        return CitationParseResult(
            structured={},
            parse_status="failed",
            warnings=[],
            error_code="INVALID_CITATION_CONTENT",
            error_message="raw_text and structured_data are both empty",
        )

    if source_format == "manual_structured":
        return _parse_manual(structured_data)
    if source_format == "csl_json":
        return _parse_csl_json(raw_text, structured_data)
    if source_format == "bibtex":
        return _parse_bibtex(raw_text)
    if source_format == "ris":
        return _parse_ris(raw_text)
    if source_format == "apa":
        return _parse_formatted_text(raw_text, "apa")
    if source_format == "mla":
        return _parse_formatted_text(raw_text, "mla")
    # gb_t_7714_2025
    return _parse_formatted_text(raw_text, "gb_t_7714_2025")


# --- manual_structured -----------------------------------------------------


def _parse_manual(structured_data: dict | None) -> CitationParseResult:
    if not structured_data:
        return CitationParseResult(
            structured={},
            parse_status="failed",
            warnings=[],
            error_code="INVALID_CITATION_CONTENT",
            error_message="manual_structured requires structured_data",
        )
    return _normalise_structured(structured_data)


# --- csl_json --------------------------------------------------------------


def _parse_csl_json(
    raw_text: str | None,
    structured_data: dict | None,
) -> CitationParseResult:
    if structured_data and isinstance(structured_data, dict) and structured_data:
        return _normalise_structured(structured_data)
    if raw_text:
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            return CitationParseResult(
                structured={},
                parse_status="failed",
                warnings=[],
                error_code="PARSE_FAILED",
                error_message=f"Invalid CSL JSON: {exc}",
            )
        if isinstance(data, dict) and data:
            return _normalise_structured(data)
        if isinstance(data, dict):
            return CitationParseResult(
                structured={},
                parse_status="failed",
                warnings=["missing required field: title"],
                error_code=None,
                error_message=None,
            )
    return CitationParseResult(
        structured={},
        parse_status="failed",
        warnings=[],
        error_code="INVALID_CITATION_CONTENT",
        error_message="csl_json requires raw CSL JSON text or structured_data",
    )


# --- Shared normalisation --------------------------------------------------


def _normalise_structured(data: dict) -> CitationParseResult:
    """Validate and normalise a CSL-like dict into canonical form."""
    warnings: list[str] = []

    ctype = data.get("type") or "unknown"
    if ctype not in CITATION_TYPES:
        warnings.append(
            f"unknown citation type: {ctype!r}; falling back to 'unknown'"
        )
        ctype = "unknown"

    title = _clean_text(data.get("title"))
    if not title:
        # Minimum required field missing — failed.
        return CitationParseResult(
            structured={},
            parse_status="failed",
            warnings=["missing required field: title"],
            error_code="PARSE_FAILED",
            error_message="title is required",
        )

    author_raw = data.get("author") or []
    author = _normalise_authors(author_raw, warnings)

    structured: dict[str, Any] = {
        "type": ctype,
        "title": title,
    }
    if author:
        structured["author"] = author
    else:
        warnings.append("author list is empty")

    issued = _normalise_issued(data.get("issued"))
    if issued is not None:
        structured["issued"] = issued
    else:
        warnings.append("missing issued date")

    if data.get("container-title"):
        structured["container-title"] = _clean_text(data["container-title"])
    if data.get("volume"):
        structured["volume"] = str(data["volume"])
    if data.get("issue"):
        structured["issue"] = str(data["issue"])
    if data.get("page"):
        structured["page"] = _clean_text(data["page"])
    if data.get("publisher"):
        structured["publisher"] = _clean_text(data["publisher"])
    if data.get("publisher-place"):
        structured["publisher-place"] = _clean_text(data["publisher-place"])
    if data.get("DOI"):
        structured["DOI"] = _clean_text(data["DOI"])
    if data.get("URL"):
        structured["URL"] = _clean_text(data["URL"])
    accessed = _normalise_issued(data.get("accessed"))
    if accessed is not None:
        structured["accessed"] = accessed
    if data.get("language"):
        structured["language"] = _clean_text(data["language"])

    parse_status = "partial" if warnings else "parsed"
    return CitationParseResult(
        structured=structured,
        parse_status=parse_status,
        warnings=warnings,
        error_code=None,
        error_message=None,
    )


def _normalise_authors(raw: Any, warnings: list[str]) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        family = _clean_text(entry.get("family"))
        given = _clean_text(entry.get("given"))
        if not family and not given:
            warnings.append("author entry missing both family and given")
            continue
        person: dict[str, str] = {}
        if family:
            person["family"] = family
        if given:
            person["given"] = given
        out.append(person)
    return out


def _normalise_issued(raw: Any) -> dict[str, list[list[int]]] | None:
    """Normalise a CSL 'issued' value to ``{'date-parts': [[...]]}``."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        dp = raw.get("date-parts")
        if isinstance(dp, list) and dp and isinstance(dp[0], list):
            return {"date-parts": [[int(x) for x in dp[0]]]}
        # raw year int in a dict
        if "year" in raw:
            return {"date-parts": [[int(raw["year"])]]}
    if isinstance(raw, int):
        return {"date-parts": [[raw]]}
    if isinstance(raw, str):
        nums = [int(t) for t in re.findall(r"\d+", raw) if t]
        if nums:
            return {"date-parts": [nums]}
    return None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    # Collapse internal whitespace/newlines.
    s = re.sub(r"\s+", " ", s)
    return s


# --- BibTeX ---------------------------------------------------------------

_BIBTEX_FIELD_MAP = {
    "title": "title",
    "journal": "container-title",
    "booktitle": "container-title",
    "volume": "volume",
    "number": "issue",
    "pages": "page",
    "publisher": "publisher",
    "address": "publisher-place",
    "doi": "DOI",
    "url": "URL",
    "language": "language",
    # NOTE: "year" is intentionally absent from this map. BibTeX year is
    # emitted only as canonical issued date-parts below, so no internal
    # "_year" key leaks into public structured data (AC-CITE-002).
}

_BIBTEX_TYPE_MAP = {
    "article": "article-journal",
    "inproceedings": "paper-conference",
    "incollection": "paper-conference",
    "conference": "paper-conference",
    "proceedings": "paper-conference",
    "book": "book",
    "inbook": "chapter",
    "phdthesis": "thesis",
    "mastersthesis": "thesis",
    "techreport": "report",
    "misc": "unknown",
}


def _parse_bibtex(raw_text: str | None) -> CitationParseResult:
    if not raw_text:
        return CitationParseResult(
            structured={},
            parse_status="failed",
            warnings=[],
            error_code="INVALID_CITATION_CONTENT",
            error_message="bibtex requires raw_text",
        )
    warnings: list[str] = []
    text = raw_text

    # Extract entry type.
    m = re.search(r"@\s*(\w+)\s*\{", raw_text)
    entry_type = m.group(1).lower() if m else "misc"
    ctype = _BIBTEX_TYPE_MAP.get(entry_type, "unknown")

    # Extract fields via regex — handles simple {..} and ".." values.
    fields: dict[str, str] = {}
    for fm in re.finditer(
        r"(\w+)\s*=\s*(?:\{((?:[^{}]|\{[^{}]*\})*)\}|"  # { ... }
        r'"((?:[^"\\]|\\.)*)"|(\d+))',
        text,
        re.IGNORECASE,
    ):
        key = fm.group(1).lower()
        val = fm.group(2) if fm.group(2) is not None else (
            fm.group(3) if fm.group(3) is not None else fm.group(4)
        )
        # Collapse whitespace.
        val = re.sub(r"\s+", " ", val).strip()
        fields[key] = val

    # Authors.
    authors: list[dict[str, str]] = []
    raw_author = fields.get("author", "")
    if raw_author:
        for part in re.split(r"\s+and\s+", raw_author, flags=re.IGNORECASE):
            part = part.strip()
            if not part:
                continue
            if "," in part:
                family, given = part.split(",", 1)
                authors.append({
                    "family": family.strip(),
                    "given": given.strip(),
                })
            else:
                tokens = part.split()
                if len(tokens) == 1:
                    authors.append({"family": tokens[0]})
                else:
                    authors.append({
                        "family": tokens[-1],
                        "given": " ".join(tokens[:-1]),
                    })
    if not authors:
        warnings.append("no authors parsed from bibtex")

    structured: dict[str, Any] = {"type": ctype}
    if authors:
        structured["author"] = authors

    title = fields.get("title", "")
    if not title:
        return CitationParseResult(
            structured={},
            parse_status="failed",
            warnings=["missing title in bibtex"],
            error_code="PARSE_FAILED",
            error_message="title is required",
        )
    structured["title"] = title

    for bibkey, cslkey in _BIBTEX_FIELD_MAP.items():
        if bibkey in fields and fields[bibkey]:
            structured[cslkey] = fields[bibkey]

    year = fields.get("year", "")
    if year:
        nums = re.findall(r"\d{4}", year)
        if nums:
            structured["issued"] = {"date-parts": [[int(nums[0])]]}
        else:
            warnings.append("could not parse bibtex year")
    else:
        warnings.append("missing issued year in bibtex")

    parse_status = "partial" if warnings else "parsed"
    return CitationParseResult(
        structured=structured,
        parse_status=parse_status,
        warnings=warnings,
        error_code=None,
        error_message=None,
    )


# --- RIS -------------------------------------------------------------------

_RIS_TAG_MAP = {
    "TY": "_type",
    "TI": "title",
    "T1": "title",
    "CT": "title",
    "JO": "container-title",
    "JF": "container-title",
    "T2": "container-title",
    "VL": "volume",
    "IS": "issue",
    "SP": "_sp",
    "EP": "_ep",
    "PB": "publisher",
    "CY": "publisher-place",
    "DO": "DOI",
    "UR": "URL",
    "L1": "URL",
    "LA": "language",
    "PY": "_year",
    "Y1": "_year",
    "DA": "_year",
}

_RIS_TYPE_MAP = {
    "JOUR": "article-journal",
    "CPAPER": "paper-conference",
    "CONF": "paper-conference",
    "BOOK": "book",
    "CHAP": "chapter",
    "THES": "thesis",
    "RPRT": "report",
    "ELEC": "webpage",
}


def _parse_ris(raw_text: str | None) -> CitationParseResult:
    if not raw_text:
        return CitationParseResult(
            structured={},
            parse_status="failed",
            warnings=[],
            error_code="INVALID_CITATION_CONTENT",
            error_message="ris requires raw_text",
        )
    warnings: list[str] = []
    tags: dict[str, list[str]] = {}
    for m in re.finditer(r"^([A-Z][A-Z0-9])\s*-\s*(.*)$", raw_text, re.MULTILINE):
        tag = m.group(1)
        val = m.group(2).strip()
        tags.setdefault(tag, []).append(val)

    ctype = "unknown"
    ty = tags.get("TY")
    if ty:
        ctype = _RIS_TYPE_MAP.get(ty[0].upper(), "unknown")

    title_parts = tags.get("TI") or tags.get("T1") or tags.get("CT") or []
    title = " ".join(title_parts).strip()
    if not title:
        return CitationParseResult(
            structured={},
            parse_status="failed",
            warnings=["missing title in ris"],
            error_code="PARSE_FAILED",
            error_message="title is required",
        )

    authors: list[dict[str, str]] = []
    for au in tags.get("AU", []):
        if "," in au:
            family, given = au.split(",", 1)
            authors.append({"family": family.strip(), "given": given.strip()})
        else:
            tokens = au.strip().split()
            if len(tokens) == 1:
                authors.append({"family": tokens[0]})
            elif tokens:
                authors.append({"family": tokens[-1], "given": " ".join(tokens[:-1])})
    if not authors:
        warnings.append("no authors parsed from ris")

    structured: dict[str, Any] = {"type": ctype, "title": title}
    if authors:
        structured["author"] = authors

    for riskey, cslkey in _RIS_TAG_MAP.items():
        vals = tags.get(riskey, [])
        if not vals:
            continue
        val = vals[0]
        if cslkey == "_sp":
            structured.setdefault("_sp", val)
        elif cslkey == "_ep":
            structured.setdefault("_ep", val)
        elif cslkey == "_year":
            nums = re.findall(r"\d{4}", val)
            if nums:
                structured.setdefault("issued", {"date-parts": [[int(nums[0])]]})
        elif cslkey == "_type":
            pass
        else:
            structured[cslkey] = val

    sp = structured.pop("_sp", None)
    ep = structured.pop("_ep", None)
    if sp or ep:
        structured["page"] = f"{sp or ''}-{ep or ''}".strip("-")

    if "issued" not in structured:
        warnings.append("missing issued year in ris")

    parse_status = "partial" if warnings else "parsed"
    return CitationParseResult(
        structured=structured,
        parse_status=parse_status,
        warnings=warnings,
        error_code=None,
        error_message=None,
    )


# --- Formatted text (APA / MLA / GB/T) ------------------------------------
# Basic heuristic reverse-parsing for journal articles only. Unreliable input
# yields partial or failed — never fabricated complete structured data.


def _parse_formatted_text(
    raw_text: str | None, style: str
) -> CitationParseResult:
    if not raw_text:
        return CitationParseResult(
            structured={},
            parse_status="failed",
            warnings=[],
            error_code="INVALID_CITATION_CONTENT",
            error_message=f"{style} requires raw_text",
        )
    warnings: list[str] = [f"{style} reverse-parse is heuristic; fields may be incomplete"]

    if style == "apa":
        return _parse_apa_text(raw_text, warnings)
    if style == "mla":
        return _parse_mla_text(raw_text, warnings)
    return _parse_gb_t_text(raw_text, warnings)


def _parse_apa_text(raw_text: str, warnings: list[str]) -> CitationParseResult:
    """Heuristic APA 7 journal-article reverse parse.

    Expected shape: Author, A. A., & Author, B. B. (Year). Title.
    *Journal*, *Volume*(Issue), pages. https://doi.org/...
    """
    text = raw_text.strip()
    structured: dict[str, Any] = {"type": "article-journal"}

    # Year: first (YYYY) group.
    year_match = re.search(r"\((\d{4})\)", text)
    if year_match:
        structured["issued"] = {"date-parts": [[int(year_match.group(1))]]}
    else:
        warnings.append("could not parse year from apa text")

    # Authors: everything before the first '('.
    author_section = text[:year_match.start()].strip() if year_match else text
    authors: list[dict[str, str]] = []
    # Split on ", &" or " &" or "," between author groups.
    parts = re.split(r",\s*&|\s*&\s*|;\s*", author_section)
    for part in parts:
        part = part.strip().rstrip(",")
        if not part:
            continue
        if "," in part:
            family, given = part.split(",", 1)
            authors.append({"family": family.strip(), "given": _apa_initials(given)})
        else:
            tokens = part.strip().split()
            if len(tokens) >= 2:
                authors.append({"family": tokens[-1], "given": " ".join(tokens[:-1])})
            elif tokens:
                authors.append({"family": tokens[0]})
    if authors:
        structured["author"] = authors
    else:
        warnings.append("no authors parsed from apa text")

    # Title + container: between year and the next '.' after year.
    if year_match:
        after_year = text[year_match.end():].lstrip(" .")
        # Title ends at first period.
        title_match = re.match(r"([^.]+)\.\s*", after_year)
        if title_match:
            title = title_match.group(1).strip()
            structured["title"] = title
            remainder = after_year[title_match.end():]
        else:
            warnings.append("could not parse title from apa text")
            remainder = after_year
    else:
        warnings.append("could not parse title from apa text")
        remainder = ""

    # Container, volume(issue), page — italicised in real APA; here we look
    # for a comma-separated chunk before the DOI.
    if remainder:
        # Strip DOI/url tail.
        container_part = re.split(r"\s*https?://", remainder, maxsplit=1)[0].rstrip(" .")
        # Last comma-separated piece may be pages; the one before is container+vol.
        segments = [s.strip() for s in container_part.split(",") if s.strip()]
        if segments:
            # First segment is typically: *Journal*, *Volume*(Issue), pages
            first = segments[0]
            # Volume(issue) pattern.
            vol_match = re.search(r"(\d+)\s*\((\d+)\)", first)
            if vol_match:
                structured["volume"] = vol_match.group(1)
                structured["issue"] = vol_match.group(2)
                container_name = first[:vol_match.start()].strip(" *\t")
            else:
                container_name = first.strip(" *\t")
            if container_name:
                structured["container-title"] = container_name
            # Pages: last numeric-ish segment.
            if len(segments) > 1:
                page_seg = segments[-1].strip()
                page_match = re.search(r"([\d]+[\-–][\d]+|[\d]+)", page_seg)
                if page_match:
                    structured["page"] = page_match.group(1)

    # DOI
    doi_match = re.search(r"https://doi\.org/(\S+)", text)
    if doi_match:
        structured["DOI"] = doi_match.group(1).rstrip(".")

    if "title" not in structured:
        return CitationParseResult(
            structured={},
            parse_status="failed",
            warnings=warnings + ["missing title in apa text"],
            error_code="PARSE_FAILED",
            error_message="title is required",
        )

    return CitationParseResult(
        structured=structured,
        parse_status="partial",
        warnings=warnings,
        error_code=None,
        error_message=None,
    )


def _apa_initials(text: str) -> str:
    """Convert 'A. B.' style initials to a normalised form."""
    letters = re.findall(r"([A-Z])\.", text)
    if letters:
        return ". ".join(letters) + "."
    return text.strip()


def _parse_mla_text(raw_text: str, warnings: list[str]) -> CitationParseResult:
    """Heuristic MLA 9 journal-article reverse parse.

    Expected shape: Author. "Title." *Journal*, vol. #, no. #, Year, pp. ...
    """
    text = raw_text.strip()
    structured: dict[str, Any] = {"type": "article-journal"}

    # Title is in double quotes.
    title_match = re.search(r'"([^"]+)"', text)
    if title_match:
        structured["title"] = title_match.group(1).strip().rstrip(".")
    else:
        warnings.append("could not parse title from mla text")

    # Year: a 4-digit number.
    year_match = re.search(r"\b(\d{4})\b", text)
    if year_match:
        structured["issued"] = {"date-parts": [[int(year_match.group(1))]]}
    else:
        warnings.append("could not parse year from mla text")

    # Authors: everything before the first '.' that precedes the title.
    before_title = text[:title_match.start()] if title_match else text
    author_section = before_title.split(".")[0] if "." in before_title else before_title
    authors: list[dict[str, str]] = []
    for part in re.split(r",\s*&\s*|\s*&\s*|;\s*", author_section):
        part = part.strip().rstrip(",").strip()
        if not part:
            continue
        if "," in part:
            family, given = part.split(",", 1)
            authors.append({"family": family.strip(), "given": given.strip()})
        else:
            tokens = part.strip().split()
            if len(tokens) >= 2:
                authors.append({"family": tokens[-1], "given": " ".join(tokens[:-1])})
            elif tokens:
                authors.append({"family": tokens[0]})
    if authors:
        structured["author"] = authors
    else:
        warnings.append("no authors parsed from mla text")

    # Volume / issue / pages.
    vol_match = re.search(r"vol\.\s*(\d+)", text, re.IGNORECASE)
    if vol_match:
        structured["volume"] = vol_match.group(1)
    no_match = re.search(r"no\.\s*(\d+)", text, re.IGNORECASE)
    if no_match:
        structured["issue"] = no_match.group(1)
    pp_match = re.search(r"pp\.\s*([\d\-–]+)", text, re.IGNORECASE)
    if pp_match:
        structured["page"] = pp_match.group(1)

    # Container: between the closing quote and the next comma.
    if title_match:
        after_title = text[title_match.end():]
        container_match = re.match(r"\.\s*([^,]+),", after_title)
        if container_match:
            structured["container-title"] = container_match.group(1).strip(" *\t")

    if "title" not in structured:
        return CitationParseResult(
            structured={},
            parse_status="failed",
            warnings=warnings + ["missing title in mla text"],
            error_code="PARSE_FAILED",
            error_message="title is required",
        )

    return CitationParseResult(
        structured=structured,
        parse_status="partial",
        warnings=warnings,
        error_code=None,
        error_message=None,
    )


def _parse_gb_t_text(raw_text: str, warnings: list[str]) -> CitationParseResult:
    """Heuristic GB/T 7714-2025 journal-article reverse parse.

    Expected shape: 作者. 题名[J]. 刊名, 年, 卷(期): 页码. DOI
    """
    text = raw_text.strip()
    structured: dict[str, Any] = {"type": "article-journal"}

    # Type marker [J] confirms journal.
    if "[J]" not in text and "[M]" not in text:
        warnings.append("no [J]/[M] type marker in gb/t text")

    # Title: between first period and [J]/[M].
    title_match = re.search(r"\.([^\.]+?)\s*\[J\]", text)
    if title_match:
        structured["title"] = title_match.group(1).strip()
    else:
        warnings.append("could not parse title from gb/t text")

    # Authors: everything before the first '.'.
    author_section = text.split(".")[0] if "." in text else text
    authors: list[dict[str, str]] = []
    for part in re.split(r"[，,]\s*", author_section):
        part = part.strip()
        if not part:
            continue
        # GB/T authors are typically "Family Given" or just "Family".
        tokens = part.split()
        if len(tokens) >= 2 and not re.search(r"[A-Za-z]", part):
            authors.append({"family": tokens[0], "given": "".join(tokens[1:])})
        else:
            authors.append({"family": part})
    if authors:
        structured["author"] = authors
    else:
        warnings.append("no authors parsed from gb/t text")

    # Year / volume(issue): page.
    year_match = re.search(r",\s*(\d{4})\s*,", text)
    if year_match:
        structured["issued"] = {"date-parts": [[int(year_match.group(1))]]}
    else:
        warnings.append("could not parse year from gb/t text")

    vol_match = re.search(r"(\d+)\((\d+)\)\s*:", text)
    if vol_match:
        structured["volume"] = vol_match.group(1)
        structured["issue"] = vol_match.group(2)
    else:
        vol_simple = re.search(r",\s*(\d+)\s*,", text)
        if vol_simple:
            structured["volume"] = vol_simple.group(1)

    page_match = re.search(r":\s*([\d\-–]+)\s*\.?", text)
    if page_match:
        structured["page"] = page_match.group(1).rstrip(".")

    # Container: between [J] and the next comma.
    container_match = re.search(r"\[J?\]\s*\.?\s*([^,，]+)", text)
    if container_match:
        structured["container-title"] = container_match.group(1).strip()

    # DOI
    doi_match = re.search(r"(10\.\d{4,}/\S+)", text)
    if doi_match:
        structured["DOI"] = doi_match.group(1).rstrip(".")

    if "title" not in structured:
        return CitationParseResult(
            structured={},
            parse_status="failed",
            warnings=warnings + ["missing title in gb/t text"],
            error_code="PARSE_FAILED",
            error_message="title is required",
        )

    return CitationParseResult(
        structured=structured,
        parse_status="partial",
        warnings=warnings,
        error_code=None,
        error_message=None,
    )
