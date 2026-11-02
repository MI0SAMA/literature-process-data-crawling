"""Optional GROBID client for structured PDF section extraction."""

from __future__ import annotations

from lxml import etree

from app.acquisition.models import ParsedDocument, ParsedSection, ParsedTable
from app.acquisition.sections import classify_heading
from app.acquisition.text import element_text, local_name, normalize_text

TEI_NS = "http://www.tei-c.org/ns/1.0"


class GrobidError(RuntimeError):
    pass


class GrobidClient:
    def __init__(self, base_url: str, *, timeout: float = 90.0, proxy: str | None = None) -> None:
        import requests

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})

    def process_pdf(self, pdf_bytes: bytes) -> ParsedDocument:
        url = f"{self.base_url}/api/processFulltextDocument"
        try:
            response = self.session.post(
                url,
                files={"input": ("document.pdf", pdf_bytes, "application/pdf")},
                data={"ConsolidateHeader": "1", "teiCoordinates": "1"},
                timeout=self.timeout,
            )
        except Exception as error:
            raise GrobidError(f"GROBID request failed: {error}") from error
        if response.status_code != 200:
            raise GrobidError(f"GROBID returned HTTP {response.status_code}: {response.text[:200]}")
        return parse_tei(response.content)


def parse_tei(tei_bytes: bytes) -> ParsedDocument:
    try:
        root = etree.fromstring(tei_bytes)
    except etree.XMLSyntaxError as error:
        raise GrobidError(f"invalid TEI XML: {error}") from error

    title = "Untitled"
    for element in root.iter():
        if local_name(element) == "title" and element_text(element):
            title = element_text(element)
            break

    sections: list[ParsedSection] = []
    order = 0

    def walk(div, depth: int = 0) -> None:
        nonlocal order
        heading = ""
        for child in div:
            if local_name(child) == "head":
                heading = element_text(child)
                break
        paragraphs = []
        for child in div:
            if local_name(child) in {"p", "list", "quote", "note"}:
                paragraphs.append(element_text(child))
        text = normalize_text(" ".join(paragraphs))
        if text:
            sections.append(
                ParsedSection(
                    section_type=classify_heading(heading, text),
                    heading=heading or ("Section" if depth else "Body"),
                    text=text,
                    order=order,
                    locator=f"tei:div:{order}",
                )
            )
            order += 1
        for child in div:
            if local_name(child) == "div":
                walk(child, depth + 1)

    for body in [element for element in root.iter() if local_name(element) == "body"]:
        for div in body:
            if local_name(div) == "div":
                walk(div)
        break

    tables: list[ParsedTable] = []
    for index, table in enumerate([element for element in root.iter() if local_name(element) == "table"], start=1):
        rows = []
        for row in table.iter():
            if local_name(row) != "row":
                continue
            cells = [element_text(cell) for cell in row if local_name(cell) == "cell"]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(" | ".join(cells))
        tables.append(
            ParsedTable(
                label=f"Table {index}",
                caption="",
                text="\n".join(rows),
                locator=f"tei:table:{index}",
            )
        )

    references = []
    for element in root.iter():
        if local_name(element) in {"biblStruct", "bibl"}:
            text = element_text(element)
            if text:
                references.append(text)

    return ParsedDocument(
        title=title,
        sections=sections,
        tables=tables,
        references=references,
        supplementary_urls=[],
        parser="grobid",
        parser_version="tei",
        metadata={"section_count": len(sections), "reference_count": len(references)},
    )
