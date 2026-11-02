"""Data objects for Phase 3 full-text acquisition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedSection:
    section_type: str
    heading: str
    text: str
    order: int
    locator: str
    page_start: int | None = None
    page_end: int | None = None
    is_reference: bool = False
    score: float = 0.0
    char_start: int | None = None
    char_end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_type": self.section_type,
            "heading": self.heading,
            "text": self.text,
            "order": self.order,
            "locator": self.locator,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "is_reference": self.is_reference,
            "score": round(self.score, 4),
            "char_start": self.char_start,
            "char_end": self.char_end,
        }


@dataclass
class ParsedTable:
    label: str
    caption: str
    text: str
    locator: str
    page: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "caption": self.caption,
            "text": self.text,
            "locator": self.locator,
            "page": self.page,
        }


@dataclass
class ParsedDocument:
    title: str
    sections: list[ParsedSection]
    tables: list[ParsedTable] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    supplementary_urls: list[str] = field(default_factory=list)
    parser: str = "unknown"
    parser_version: str = "1"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "sections": [section.to_dict() for section in self.sections],
            "tables": [table.to_dict() for table in self.tables],
            "references": self.references,
            "supplementary_urls": self.supplementary_urls,
            "parser": self.parser,
            "parser_version": self.parser_version,
            "metadata": self.metadata,
        }


@dataclass
class FetchedResource:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)
    fetched_at: str = ""
    access_status: str = "unknown"
    error_kind: str | None = None
    error_message: str | None = None
    attempts: int = 1
    retry_count: int = 0

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error_kind is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "status": self.status,
            "content_type": self.content_type,
            "byte_size": len(self.body),
            "access_status": self.access_status,
            "error_kind": self.error_kind,
            "error_message": self.error_message,
            "attempts": self.attempts,
            "retry_count": self.retry_count,
        }


@dataclass
class AcquisitionResult:
    paper_key: str
    doi: str
    status: str
    primary_document_id: str | None = None
    deposition_section_id: str | None = None
    deposition_section: dict[str, Any] | None = None
    documents: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_key": self.paper_key,
            "doi": self.doi,
            "status": self.status,
            "primary_document_id": self.primary_document_id,
            "deposition_section_id": self.deposition_section_id,
            "deposition_section": self.deposition_section,
            "documents": self.documents,
            "sections": self.sections,
            "errors": self.errors,
            "metadata": self.metadata,
        }
