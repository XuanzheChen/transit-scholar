"""Basic citation renderers: GB/T 7714-2025, APA 7, MLA 9.

Each renderer reads ONLY the structured_json dict — never the Paper ORM —
so that the structured intermediate data can be validated on its own terms.
Missing fields produce warnings and incomplete-but-structured output; the
renderer never fabricates data.
"""

from __future__ import annotations

from typing import Any

RENDERER_VERSION = "stage5-basic-v1"

STYLES = frozenset({"gb_t_7714_2025", "apa_7", "mla_9"})


def render(
    structured: dict[str, Any],
    *,
    style: str,
) -> tuple[str, list[str]]:
    """Render a structured citation dict in the given style.

    Returns (rendered_text, warnings). On invalid style returns ("", [warning]).
    """
    if style not in STYLES:
        return "", [f"unknown style: {style!r}"]
    if style == "gb_t_7714_2025":
        return _render_gb_t(structured)
    if style == "apa_7":
        return _render_apa(structured)
    return _render_mla(structured)


# --- Helpers ----------------------------------------------------------------


def _year_from_issued(issued: Any) -> str:
    if isinstance(issued, dict):
        dp = issued.get("date-parts")
        if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0]:
            return str(dp[0][0])
    return ""


def _format_authors_gb_t(authors: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for a in authors[:3]:
        family = a.get("family", "")
        given = a.get("given", "")
        if family and given:
            parts.append(f"{family} {given}")
        elif family:
            parts.append(family)
        elif given:
            parts.append(given)
    suffix = "等" if len(authors) > 3 else ""
    return ", ".join(parts) + suffix


def _format_authors_apa(authors: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for a in authors[:20]:
        family = a.get("family", "")
        given = a.get("given", "")
        initials = _apa_initials_from_given(given)
        if family and initials:
            parts.append(f"{family}, {initials}")
        elif family:
            parts.append(family)
        elif initials:
            parts.append(initials)
    if not parts:
        return ""
    if len(parts) == 1:
        # Single author: plain "Family, I." — no multi-author connector and
        # no empty ", & " prefix (AC-CITE-001).
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]}, & {parts[1]}"
    if len(parts) == 3:
        return f"{parts[0]}, {parts[1]}, & {parts[2]}"
    if len(parts) > 20:
        return ", ".join(parts[:20]) + ", . . ."
    return ", ".join(parts[:-1]) + f", & {parts[-1]}"


def _apa_initials_from_given(given: str) -> str:
    """Format given names as APA initials without fabricating missing names."""
    if not given:
        return ""
    tokens = [t for t in given.replace(".", " ").split() if t]
    initials: list[str] = []
    for token in tokens:
        initials.append(token[0].upper() + ".")
    return " ".join(initials)


def _format_authors_mla(authors: list[dict[str, str]]) -> str:
    if not authors:
        return ""
    first = authors[0]
    family = first.get("family", "")
    given = first.get("given", "")
    first_str = f"{family}, {given}" if family and given else (family or given)
    if len(authors) == 1:
        return first_str + "."
    if len(authors) == 2:
        second = authors[1]
        sf = second.get("family", "")
        sg = second.get("given", "")
        second_str = f"{sg} {sf}" if sf and sg else (sf or sg)
        return f"{first_str}, and {second_str}."
    return first_str + ", et al."


def _container_label(ctype: str) -> str:
    return {
        "article-journal": "J",
        "paper-conference": "C",
        "book": "M",
        "chapter": "M",
        "thesis": "D",
        "report": "R",
        "webpage": "EB",
        "unknown": "Z",
    }.get(ctype, "Z")


# --- GB/T 7714-2025 -------------------------------------------------------


def _render_gb_t(structured: dict[str, Any]) -> tuple[str, list[str]]:
    warnings: list[str] = []
    title = structured.get("title", "")
    if not title:
        warnings.append("missing title")
    authors = structured.get("author") or []
    if not authors:
        warnings.append("missing author")
    year = _year_from_issued(structured.get("issued"))
    if not year:
        warnings.append("missing issued year")
    container = structured.get("container-title", "")
    volume = structured.get("volume", "")
    issue = structured.get("issue", "")
    page = structured.get("page", "")
    doi = structured.get("DOI", "")
    ctype = structured.get("type", "unknown")

    parts: list[str] = []
    parts.append(_format_authors_gb_t(authors))
    parts.append(f"{title}[{_container_label(ctype)}].")
    if container:
        tail = container
        if volume:
            tail += f", {volume}"
            if issue:
                tail += f"({issue})"
        if page:
            tail += f": {page}"
        parts.append(tail + ".")
    else:
        if not structured.get("publisher"):
            warnings.append("missing container-title/publisher")
    if year:
        parts.append(f"{year}.")
    if doi:
        parts.append(f"doi: {doi}.")

    rendered = " ".join(p for p in parts if p)
    return rendered, warnings


# --- APA 7 -----------------------------------------------------------------


def _render_apa(structured: dict[str, Any]) -> tuple[str, list[str]]:
    warnings: list[str] = []
    title = structured.get("title", "")
    if not title:
        warnings.append("missing title")
    authors = structured.get("author") or []
    if not authors:
        warnings.append("missing author")
    year = _year_from_issued(structured.get("issued"))
    if not year:
        warnings.append("missing issued year")
    container = structured.get("container-title", "")
    volume = structured.get("volume", "")
    issue = structured.get("issue", "")
    page = structured.get("page", "")
    doi = structured.get("DOI", "")
    url = structured.get("URL", "")

    parts: list[str] = []
    parts.append(_format_authors_apa(authors) + " " if authors else "")
    parts.append(f"({year})." if year else "(n.d.).")
    parts.append(f"{title}.")
    if container:
        tail = f"*{container}*"
        if volume:
            tail += f", *{volume}*"
            if issue:
                tail += f"({issue})"
        if page:
            tail += f", {page}"
        parts.append(tail + ".")
    else:
        if not structured.get("publisher"):
            warnings.append("missing container-title/publisher")
    if doi:
        parts.append(f"https://doi.org/{doi}")
    elif url:
        parts.append(url)

    rendered = " ".join(p for p in parts if p)
    return rendered, warnings


# --- MLA 9 -----------------------------------------------------------------


def _render_mla(structured: dict[str, Any]) -> tuple[str, list[str]]:
    warnings: list[str] = []
    title = structured.get("title", "")
    if not title:
        warnings.append("missing title")
    authors = structured.get("author") or []
    if not authors:
        warnings.append("missing author")
    year = _year_from_issued(structured.get("issued"))
    if not year:
        warnings.append("missing issued year")
    container = structured.get("container-title", "")
    volume = structured.get("volume", "")
    issue = structured.get("issue", "")
    page = structured.get("page", "")
    doi = structured.get("DOI", "")
    url = structured.get("URL", "")

    parts: list[str] = []
    parts.append(_format_authors_mla(authors))
    parts.append(f'"{title}."')
    if container:
        tail = f"*{container}*"
        if volume:
            tail += f", vol. {volume}"
            if issue:
                tail += f", no. {issue}"
        if year:
            tail += f", {year}"
        if page:
            tail += f", pp. {page}"
        parts.append(tail + ".")
    else:
        if not structured.get("publisher"):
            warnings.append("missing container-title/publisher")
    if doi:
        parts.append(f"doi:{doi}.")
    elif url:
        parts.append(f"{url}.")

    rendered = " ".join(p for p in parts if p)
    return rendered, warnings
