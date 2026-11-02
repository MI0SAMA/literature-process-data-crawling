"""Core data models shared by discovery, storage and reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Author:
    name: str
    family: str | None = None
    given: str | None = None
    orcid: str | None = None


@dataclass
class PaperRecord:
    """One record as returned by one source, before cross-source deduplication."""

    source: str
    source_id: str
    title: str
    doi: str | None = None
    abstract: str | None = None
    authors: list[Author] = field(default_factory=list)
    publication_year: int | None = None
    publication_date: str | None = None
    venue: str | None = None
    item_type: str | None = None
    landing_url: str | None = None
    pdf_url: str | None = None
    is_oa: bool | None = None
    oa_status: str | None = None
    oa_url: str | None = None
    pmcid: str | None = None
    license: str | None = None
    language: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def first_author_family(self) -> str | None:
        if not self.authors:
            return None
        author = self.authors[0]
        if author.family:
            return author.family
        parts = author.name.replace(",", " ").split()
        return parts[-1] if parts else None


@dataclass
class PaperCluster:
    """Canonical paper produced by deduplication and its source records."""

    paper_key: str
    title: str
    normalized_title: str
    doi: str | None
    abstract: str | None
    authors: list[Author]
    publication_year: int | None
    venue: str | None
    item_type: str | None
    landing_url: str | None
    records: list[PaperRecord]
    method: str
    review_required: bool = False
    review_reason: str | None = None

    @property
    def first_author_family(self) -> str | None:
        if not self.authors:
            return None
        author = self.authors[0]
        if author.family:
            return author.family
        parts = author.name.replace(",", " ").split()
        return parts[-1] if parts else None

    @property
    def sources(self) -> list[str]:
        return sorted({record.source for record in self.records})

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_key": self.paper_key,
            "doi": self.doi,
            "title": self.title,
            "publication_year": self.publication_year,
            "venue": self.venue,
            "item_type": self.item_type,
            "authors": [author.name for author in self.authors],
            "sources": self.sources,
            "dedup_method": self.method,
            "review_required": self.review_required,
            "review_reason": self.review_reason,
            "records": [
                {
                    "source": record.source,
                    "source_id": record.source_id,
                    "doi": record.doi,
                    "title": record.title,
                }
                for record in self.records
            ],
        }


@dataclass
class ReviewItem:
    left_source: str
    left_source_id: str
    right_source: str
    right_source_id: str
    left_title: str
    right_title: str
    similarity: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_source": self.left_source,
            "left_source_id": self.left_source_id,
            "right_source": self.right_source,
            "right_source_id": self.right_source_id,
            "left_title": self.left_title,
            "right_title": self.right_title,
            "similarity": round(self.similarity, 4),
            "reason": self.reason,
        }


@dataclass
class DedupResult:
    clusters: list[PaperCluster]
    review_items: list[ReviewItem]
    stats: dict[str, int]


@dataclass(frozen=True)
class QuerySpec:
    query_id: str
    query: str
    databases: tuple[str, ...] = ("openalex", "crossref")
    max_results: int = 100
    from_year: int | None = None
    to_year: int | None = None
    page_size: int = 50

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "query": self.query,
            "databases": list(self.databases),
            "from_year": self.from_year,
            "to_year": self.to_year,
            "max_results": self.max_results,
            "page_size": self.page_size,
        }


@dataclass
class QueryPlan:
    plan_id: str
    generated_at: str
    specs: list[QuerySpec]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "generated_at": self.generated_at,
            "specs": [spec.to_dict() for spec in self.specs],
        }


@dataclass
class SearchPage:
    records: list[PaperRecord]
    total: int | None
    page: int
    next_page: int | None
    raw: dict[str, Any] = field(default_factory=dict)
    cache_hit: bool = False
    retries: int = 0


@dataclass
class QueryExecution:
    execution_id: str
    run_id: str
    query_id: str
    database: str
    query_text: str
    from_year: int | None
    to_year: int | None
    requested_max: int
    page_size: int
    started_at: str
    finished_at: str | None = None
    pages_fetched: int = 0
    result_count: int = 0
    unique_count: int = 0
    status: str = "running"
    error_kind: str | None = None
    error_message: str | None = None
    request_count: int = 0
    cache_hits: int = 0
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "run_id": self.run_id,
            "query_id": self.query_id,
            "database": self.database,
            "query_text": self.query_text,
            "from_year": self.from_year,
            "to_year": self.to_year,
            "requested_max": self.requested_max,
            "page_size": self.page_size,
            "pages_fetched": self.pages_fetched,
            "result_count": self.result_count,
            "unique_count": self.unique_count,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "error_kind": self.error_kind,
            "error_message": self.error_message,
            "request_count": self.request_count,
            "cache_hits": self.cache_hits,
            "retry_count": self.retry_count,
        }


@dataclass
class RuleResult:
    label: str
    confidence: float
    matched_material: list[str]
    matched_method: list[str]
    flags: list[str]
    reasons: list[str]
    review_required: bool
    eligible_for_confirmation: bool
    classifier: str = "rule"
    classifier_version: str = "phase2-rules-v1"


@dataclass
class ScreeningDecision:
    paper_key: str
    rule_label: str
    final_label: str
    confidence: float
    matched_material: list[str]
    matched_method: list[str]
    flags: list[str]
    reasons: list[str]
    review_required: bool
    eligible_for_confirmation: bool
    classifier: str
    classifier_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_key": self.paper_key,
            "rule_label": self.rule_label,
            "final_label": self.final_label,
            "confidence": round(self.confidence, 4),
            "matched_material": self.matched_material,
            "matched_method": self.matched_method,
            "flags": self.flags,
            "reasons": self.reasons,
            "review_required": self.review_required,
            "eligible_for_confirmation": self.eligible_for_confirmation,
            "classifier": self.classifier,
            "classifier_version": self.classifier_version,
        }
