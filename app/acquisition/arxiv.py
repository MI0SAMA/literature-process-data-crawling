"""arXiv Atom feed and PDF harvesting helpers (legal open access)."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import requests

from app.identifiers import normalize_title
from app.models import Author, PaperRecord
from app.acquisition.text import normalize_text

_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_QUERY_BASE = "https://export.arxiv.org/api/query"
_PDF_BASE = "https://export.arxiv.org/pdf"


@dataclass
class ArxivEntry:
    arxiv_id: str
    title: str
    abstract: str
    authors: list[str]
    published: str
    year: int | None
    categories: list[str] = field(default_factory=list)
    journal_doi: str | None = None

    @property
    def base_id(self) -> str:
        return re.sub(r"v\d+$", "", self.arxiv_id)

    @property
    def doi(self) -> str:
        if self.journal_doi:
            return self.journal_doi
        return f"10.48550/arXiv.{self.base_id}"

    @property
    def abs_url(self) -> str:
        return f"https://arxiv.org/abs/{self.arxiv_id}"

    @property
    def pdf_url(self) -> str:
        return f"{_PDF_BASE}/{self.arxiv_id}"

    def to_paper_record(self) -> PaperRecord:
        authors = []
        for name in self.authors:
            parts = name.split()
            family = parts[-1] if parts else None
            given = " ".join(parts[:-1]) if len(parts) > 1 else None
            authors.append(Author(name=name, family=family, given=given))
        return PaperRecord(
            source="arxiv",
            source_id=self.arxiv_id,
            title=self.title,
            doi=self.doi,
            abstract=self.abstract,
            authors=authors,
            publication_year=self.year,
            publication_date=self.published,
            venue="arXiv",
            item_type="preprint",
            landing_url=self.abs_url,
            pdf_url=self.pdf_url,
            is_oa=True,
            oa_status="green",
            oa_url=self.pdf_url,
            license="arxiv",
            raw={
                "open_access": {"is_oa": True, "oa_url": self.pdf_url, "oa_status": "green"},
                "best_oa_location": {
                    "is_oa": True,
                    "pdf_url": self.pdf_url,
                    "landing_page_url": self.abs_url,
                    "version": "submittedVersion",
                },
                "primary_location": {"landing_page_url": self.abs_url, "version": "submittedVersion"},
            },
        )


def fetch_entries(terms: list[str], *, max_results: int = 100, timeout: float = 30.0) -> list[ArxivEntry]:
    entries: list[ArxivEntry] = []
    seen: set[str] = set()
    session = requests.Session()
    for term in terms:
        query = term.replace(" ", "+")
        url = f"{_QUERY_BASE}?search_query=all:{query}&start=0&max_results={max_results}&sortBy=relevance"
        response = session.get(url, timeout=timeout, headers={"User-Agent": "AlScN-PVD-Literature/0.4"})
        response.raise_for_status()
        for entry in parse_arxiv_feed(response.content):
            if entry.base_id in seen:
                continue
            seen.add(entry.base_id)
            entries.append(entry)
        time.sleep(3)
    return entries


def parse_arxiv_feed(xml_bytes: bytes) -> list[ArxivEntry]:
    root = ET.fromstring(xml_bytes)
    entries = []
    for node in root.findall("atom:entry", _NS):
        entry_id = normalize_text(node.findtext("atom:id", default="", namespaces=_NS))
        match = re.search(r"/abs/([^/]+)$", entry_id)
        if not match:
            continue
        arxiv_id = match.group(1)
        title = normalize_text(node.findtext("atom:title", default="", namespaces=_NS))
        abstract = normalize_text(node.findtext("atom:summary", default="", namespaces=_NS))
        published = normalize_text(node.findtext("atom:published", default="", namespaces=_NS))
        authors = [
            normalize_text(author.findtext("atom:name", default="", namespaces=_NS))
            for author in node.findall("atom:author", _NS)
        ]
        categories = [category.attrib.get("term", "") for category in node.findall("atom:category", _NS)]
        journal_doi = normalize_text(node.findtext("arxiv:doi", default="", namespaces=_NS)) or None
        year_match = re.match(r"(\d{4})", published)
        entries.append(
            ArxivEntry(
                arxiv_id=arxiv_id,
                title=title,
                abstract=abstract,
                authors=[author for author in authors if author],
                published=published,
                year=int(year_match.group(1)) if year_match else None,
                categories=[category for category in categories if category],
                journal_doi=journal_doi,
            )
        )
    return entries


def is_relevant_entry(entry: ArxivEntry, *, broad: bool = False) -> bool:
    text = f"{entry.title} {entry.abstract}".casefold()
    material = any(
        term in text
        for term in ("alscn", "scaln", "al1-xscxn", "scxal1-xn", "scandium aluminum nitride", "aluminum scandium nitride", "sc-doped aln")
    )
    pvd = any(
        term in text
        for term in ("sputter", "magnetron", "pvd", "physical vapor", "hipims", "pld", "pulsed laser")
    )
    film = any(term in text for term in ("thin film", "film", "epitax", "deposition", "grown"))
    method = pvd or (broad and film)
    non_pvd_only = any(term in text for term in ("mocvd", "metalorganic chemical vapor", "molecular beam epitaxy", "atomic layer deposition")) and not pvd
    return material and method and not non_pvd_only


def download_pdf(entry: ArxivEntry, *, timeout: float = 90.0, session: requests.Session | None = None) -> bytes:
    client = session or requests.Session()
    response = client.get(entry.pdf_url, timeout=timeout, headers={"User-Agent": "AlScN-PVD-Literature/0.4"})
    response.raise_for_status()
    if response.content[:5] != b"%PDF-":
        raise ValueError(f"arXiv PDF response is not a PDF: {entry.arxiv_id}")
    return response.content
