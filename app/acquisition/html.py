"""HTML article parser for open-access publisher pages."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from lxml import etree, html

from app.acquisition.models import ParsedDocument, ParsedSection, ParsedTable
from app.acquisition.sections import classify_heading
from app.acquisition.text import normalize_text

_BLOCK_TAGS = {"p", "blockquote", "dd", "dt", "pre", "li"}
_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg"}
_SUPPLEMENT_RE = re.compile(r"supplement|supporting|esi|additional\s+file|media", re.IGNORECASE)
_REFERENCE_HEADING_RE = re.compile(r"^(references?|bibliography|works cited|literature cited)\b", re.IGNORECASE)


class HtmlParseError(ValueError):
    pass


class HtmlBlockedError(HtmlParseError):
    pass


def parse_html(html_bytes: bytes, *, base_url: str = "") -> ParsedDocument:
    try:
        root = html.document_fromstring(html_bytes)
    except (etree.ParserError, ValueError) as error:
        raise HtmlParseError(f"invalid HTML: {error}") from error

    for element in root.xpath("//script|//style|//nav|//header|//footer|//aside|//form|//noscript|//svg"):
        _drop(element)

    title = _first_heading_text(root) or normalize_text(root.findtext(".//title")) or "Untitled"
    page_text = normalize_text(" ".join(root.itertext()))
    lowered = page_text.casefold()
    if any(
        marker in lowered
        for marker in (
            "checking your browser",
            "just a moment",
            "enable javascript and cookies",
            "recaptcha",
            "access denied",
            "request blocked",
        )
    ) and len(page_text) < 4000:
        raise HtmlBlockedError("publisher returned a browser challenge or access-denied page")

    container = _article_container(root)
    headings = [element for element in container.iter() if _is_heading(element)]

    sections: list[ParsedSection] = []
    references: list[str] = []
    order = 0
    char_cursor = 0

    if not headings:
        text = _container_text(container)
        if text:
            sections.append(
                ParsedSection(
                    section_type="body",
                    heading=title,
                    text=text,
                    order=0,
                    locator="html:body",
                    char_start=0,
                    char_end=len(text),
                )
            )
    else:
        all_elements = list(container.iter())
        heading_positions = [all_elements.index(heading) for heading in headings]
        for index, heading in enumerate(headings):
            start = heading_positions[index] + 1
            end = heading_positions[index + 1] if index + 1 < len(headings) else len(all_elements)
            heading_text = _heading_text(heading)
            blocks = _blocks_between(all_elements[start:end], container)
            text = normalize_text(" ".join(blocks))
            if not text:
                continue
            section_type = classify_heading(heading_text, text)
            is_reference = bool(_REFERENCE_HEADING_RE.search(heading_text or ""))
            if is_reference:
                section_type = "references"
                references.extend(blocks)
            section = ParsedSection(
                section_type=section_type,
                heading=heading_text or "Section",
                text=text,
                order=order,
                locator=f"html:{order}",
                is_reference=is_reference,
                char_start=char_cursor,
                char_end=char_cursor + len(text),
            )
            sections.append(section)
            char_cursor += len(text) + 1
            order += 1

    if not sections:
        raise HtmlParseError("HTML page contains no article sections")

    body_text_length = sum(
        len(section.text or "")
        for section in sections
        if section.section_type not in {"abstract", "references"}
    )
    structural_sections = [
        section
        for section in sections
        if section.section_type in {"introduction", "methods", "results", "conclusion", "supplementary_methods"}
        and len(section.text or "") >= 200
        and not any(
            term in (section.heading or "").casefold()
            for term in ("title", "search", "fingerprint", "cite", "access", "project", "funding")
        )
    ]
    content_scope = "fulltext" if structural_sections and body_text_length >= 200 else "abstract_only"

    tables: list[ParsedTable] = []
    for index, table in enumerate(container.xpath(".//table"), start=1):
        caption = ""
        caption_element = table.find(".//caption")
        if caption_element is not None:
            caption = normalize_text(" ".join(caption_element.itertext()))
        if not caption:
            parent = table.getparent()
            if parent is not None:
                figure = parent.find(".//figcaption")
                if figure is not None:
                    caption = normalize_text(" ".join(figure.itertext()))
        rows = []
        for row in table.xpath(".//tr"):
            cells = [normalize_text(" ".join(cell.itertext())) for cell in row]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(" | ".join(cells))
        tables.append(
            ParsedTable(
                label=f"Table {index}",
                caption=caption,
                text="\n".join(rows),
                locator=f"html:table:{index}",
            )
        )

    supplementary_urls: list[str] = []
    for anchor in root.xpath("//a[@href]"):
        href = anchor.get("href") or ""
        text = normalize_text(" ".join(anchor.itertext()))
        if href and _SUPPLEMENT_RE.search(text + " " + href):
            absolute = urljoin(base_url, href) if base_url else href
            if absolute not in supplementary_urls:
                supplementary_urls.append(absolute)

    return ParsedDocument(
        title=title,
        sections=sections,
        tables=tables,
        references=references,
        supplementary_urls=supplementary_urls,
        parser="html",
        parser_version="1",
        metadata={
            "section_count": len(sections),
            "reference_count": len(references),
            "content_scope": content_scope,
        },
    )


def _article_container(root: etree._Element) -> etree._Element:
    for xpath in ("//article", "//main", "//*[@role='main']", "//div[contains(@class,'article-body')]"):
        found = root.xpath(xpath)
        if found:
            return found[0]
    body = root.find(".//body")
    return body if body is not None else root


def _is_heading(element: etree._Element) -> bool:
    tag = element.tag
    return isinstance(tag, str) and tag.casefold() in {"h1", "h2", "h3", "h4", "h5", "h6"}


def _heading_text(element: etree._Element) -> str:
    return normalize_text(" ".join(element.itertext()))


def _blocks_between(elements: list[etree._Element], container: etree._Element) -> list[str]:
    blocks: list[str] = []
    for element in elements:
        tag = element.tag
        if not isinstance(tag, str) or tag.casefold() not in _BLOCK_TAGS:
            continue
        if _inside_table_or_figure(element, container):
            continue
        if tag.casefold() == "p" and element.getparent() is not None and element.getparent().tag == "li":
            continue
        text = normalize_text(" ".join(element.itertext()))
        if text:
            blocks.append(text)
    return blocks


def _inside_table_or_figure(element: etree._Element, container: etree._Element) -> bool:
    parent = element.getparent()
    while parent is not None and parent is not container:
        tag = parent.tag
        if isinstance(tag, str) and tag.casefold() in {"table", "figure"}:
            return True
        parent = parent.getparent()
    return False


def _container_text(container: etree._Element) -> str:
    parts = []
    for element in container.iter():
        tag = element.tag
        if not isinstance(tag, str) or tag.casefold() not in _BLOCK_TAGS:
            continue
        if _inside_table_or_figure(element, container):
            continue
        text = normalize_text(" ".join(element.itertext()))
        if text:
            parts.append(text)
    return normalize_text(" ".join(parts))


def _first_heading_text(root: etree._Element) -> str:
    found = root.xpath("//h1")
    if found:
        return _heading_text(found[0])
    return ""


def _drop(element: etree._Element) -> None:
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)
