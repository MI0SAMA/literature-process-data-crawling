"""JATS XML parser for PubMed Central / publisher full text."""

from __future__ import annotations

from lxml import etree

from app.acquisition.models import ParsedDocument, ParsedSection, ParsedTable
from app.acquisition.sections import classify_heading
from app.acquisition.text import element_text, first_text, join_blocks, local_name, normalize_text

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


class JatsParseError(ValueError):
    pass


def parse_jats(xml_bytes: bytes) -> ParsedDocument:
    try:
        root = etree.fromstring(xml_bytes)
    except (etree.XMLSyntaxError, ValueError) as error:
        raise JatsParseError(f"invalid JATS XML: {error}") from error

    title = first_text(root, "article-title") or first_text(root, "title") or "Untitled"
    sections: list[ParsedSection] = []
    order = 0

    abstract = _first_by_local(root, "abstract")
    if abstract is not None:
        text = element_text(abstract)
        if text:
            sections.append(
                ParsedSection(
                    section_type="abstract",
                    heading="Abstract",
                    text=text,
                    order=order,
                    locator="jats:abstract",
                )
            )
            order += 1

    body = _first_by_local(root, "body")
    if body is not None:
        direct_paragraphs = [
            element_text(child)
            for child in body
            if local_name(child) in {"p", "list", "disp-quote", "statement", "boxed-text"}
        ]
        direct_text = join_blocks(direct_paragraphs)
        if direct_text:
            sections.append(
                ParsedSection(
                    section_type="body",
                    heading="Body",
                    text=direct_text,
                    order=order,
                    locator="jats:body",
                )
            )
            order += 1
        for sec in body:
            if local_name(sec) == "sec":
                order = _walk_sec(sec, sections, order)

    back = _first_by_local(root, "back")
    if back is not None:
        for sec in back:
            if local_name(sec) == "sec":
                order = _walk_sec(sec, sections, order)

    references = [
        element_text(ref)
        for ref_list in _all_by_local(root, "ref-list")
        for ref in ref_list
        if local_name(ref) == "ref"
    ]
    references = [ref for ref in references if ref]

    tables: list[ParsedTable] = []
    for index, table_wrap in enumerate(_all_by_local(root, "table-wrap"), start=1):
        label = normalize_text(_child_text(table_wrap, "label"))
        caption = _child_text(table_wrap, "caption")
        table = _first_by_local(table_wrap, "table")
        table_text = element_text(table)
        tables.append(
            ParsedTable(
                label=label or f"Table {index}",
                caption=caption,
                text=table_text,
                locator=f"jats:table:{index}",
            )
        )

    supplementary_urls: list[str] = []
    for element in root.iter():
        name = local_name(element)
        if name not in {"supplementary-material", "media", "ext-link", "uri"}:
            continue
        href = element.get(_XLINK_HREF) or element.get("href")
        if not href:
            continue
        text = element_text(element)
        if name in {"supplementary-material", "media"} or "supp" in (text + href).casefold():
            if href not in supplementary_urls:
                supplementary_urls.append(href)

    return ParsedDocument(
        title=title,
        sections=sections,
        tables=tables,
        references=references,
        supplementary_urls=supplementary_urls,
        parser="jats",
        parser_version="1",
        metadata={"section_count": len(sections), "reference_count": len(references)},
    )


def _walk_sec(sec: etree._Element, sections: list[ParsedSection], order: int) -> int:
    heading = _child_text(sec, "title")
    blocks = []
    for child in sec:
        name = local_name(child)
        if name == "sec":
            continue
        if name in {"p", "list", "disp-quote", "statement", "boxed-text", "def-list"}:
            blocks.append(element_text(child))
    text = join_blocks(blocks)
    section_type = classify_heading(heading, text)
    sections.append(
        ParsedSection(
            section_type=section_type,
            heading=heading or "Section",
            text=text,
            order=order,
            locator=f"jats:sec:{order}",
        )
    )
    order += 1
    for child in sec:
        if local_name(child) == "sec":
            order = _walk_sec(child, sections, order)
    return order


def _child_text(element: etree._Element, local: str) -> str:
    for child in element:
        if local_name(child) == local:
            return element_text(child)
    return ""


def _first_by_local(root: etree._Element, local: str) -> etree._Element | None:
    for element in root.iter():
        if local_name(element) == local:
            return element
    return None


def _all_by_local(root: etree._Element, local: str) -> list[etree._Element]:
    return [element for element in root.iter() if local_name(element) == local]
