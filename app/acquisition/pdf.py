"""PDF text extraction and heading-based section parsing.

GROBID is preferred when a GROBID service is configured. This module provides
the deterministic fallback used when GROBID is unavailable.
"""

from __future__ import annotations

import io
import logging
import re

from app.acquisition.models import ParsedDocument, ParsedSection
from app.acquisition.sections import classify_heading
from app.acquisition.text import normalize_text

logging.getLogger("pypdf").setLevel(logging.ERROR)

_HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s*)?"
    r"(abstract|introduction|background|experimental(?:\s+section)?|experiments?|methods?(?:/results)?|"
    r"materials\s+and\s+methods|methodology|(?:thin\s+film\s+)?deposition(?:\s+of\s+[A-Za-z0-9 \-]+)?|"
    r"film\s+growth|growth|fabrication|device\s+fabrication|"
    r"results(?:\s+and\s+discussion)?|discussion|conclusions?|"
    r"references|bibliography|acknowledg\w*|supplementary(?:\s+methods?)?)\s*$",
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(r"^(?:\d+\.?\s*)?(references|bibliography|literature cited)\s*$", re.IGNORECASE)
_HEADING_KEYWORD_RE = re.compile(
    r"\b(experimental|experiment|method|methods|deposition|fabrication|growth|sputter|synthesis|sample\s+preparation)\b",
    re.IGNORECASE,
)
_NUMBERED_RE = re.compile(r"^(?:[IVXLC]+|\d+(?:\.\d+)*)[.)]?\s+\S")


class PdfParseError(ValueError):
    pass


def parse_pdf(pdf_bytes: bytes) -> ParsedDocument:
    pages = _extract_pages(pdf_bytes)
    if not pages:
        raise PdfParseError("PDF contains no extractable pages")

    sections: list[ParsedSection] = []
    current_heading = ""
    current_lines: list[str] = []
    current_start_page: int | None = None
    current_end_page: int | None = None
    in_references = False
    order = 0

    def flush() -> None:
        nonlocal order
        text = normalize_text(" ".join(current_lines))
        if not text:
            return
        heading = current_heading or f"Page {current_start_page or 1}"
        is_reference = in_references or bool(_REFERENCE_RE.match(heading.strip(" .")))
        sections.append(
            ParsedSection(
                section_type="references" if is_reference else classify_heading(heading, text),
                heading=heading,
                text=text,
                order=order,
                locator=f"pdf:p{current_start_page or 1}",
                page_start=current_start_page,
                page_end=current_end_page,
                is_reference=is_reference,
            )
        )
        order += 1

    for page_number, page_text in enumerate(pages, start=1):
        lines = [line.strip() for line in (page_text or "").splitlines()]
        for line in lines:
            if not line:
                continue
            if in_references:
                current_lines.append(line)
                current_end_page = page_number
                continue
            if _REFERENCE_RE.match(line.strip(" .")):
                flush()
                in_references = True
                current_heading = "References"
                current_lines = []
                current_start_page = page_number
                current_end_page = page_number
                continue
            if _is_heading(line):
                flush()
                current_heading = line
                current_lines = []
                current_start_page = page_number
                current_end_page = page_number
            else:
                if current_start_page is None:
                    current_start_page = page_number
                current_end_page = page_number
                current_lines.append(line)
    flush()

    if not sections:
        raise PdfParseError("PDF text did not produce any sections")

    return ParsedDocument(
        title=sections[0].heading if sections else "Untitled",
        sections=sections,
        tables=[],
        references=[],
        supplementary_urls=[],
        parser="pypdf_heading_heuristic",
        parser_version="1",
        metadata={"pdf_pages": len(pages), "table_extraction": "not_available_in_pdf_fallback"},
    )


def _is_heading(line: str) -> bool:
    stripped = line.strip(" .")
    if not stripped or len(stripped) > 140:
        return False
    if re.search(r"\bet\s+al\.|\bdoi\s*:|\bvol\.|\bpp\.|University|Institute|Department", stripped, re.IGNORECASE):
        return False
    if _HEADING_RE.match(stripped):
        return True
    if not _HEADING_KEYWORD_RE.search(stripped):
        return False
    numbered = _NUMBERED_RE.match(stripped)
    if numbered:
        remainder = stripped[numbered.end() :].lstrip()
        if remainder and remainder[0].islower():
            return False
    letters = [char for char in stripped if char.isalpha()]
    uppercase_ratio = sum(1 for char in letters if char.isupper()) / len(letters) if letters else 0.0
    return len(stripped) <= 110 and (uppercase_ratio >= 0.6 or bool(numbered))


def _extract_pages(pdf_bytes: bytes) -> list[str]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        return [page.extract_text() or "" for page in reader.pages]
    except Exception as first_error:
        try:
            import fitz

            document = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages = [page.get_text() for page in document]
            document.close()
            return pages
        except Exception as second_error:
            raise PdfParseError(f"PDF text extraction failed: {first_error}; fallback: {second_error}") from second_error
