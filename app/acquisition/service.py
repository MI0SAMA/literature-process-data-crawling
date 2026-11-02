"""Phase 3 acquisition orchestration: DOI -> legal full text -> sections."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

from app.acquisition.fetch import DocumentFetcher
from app.acquisition.grobid import GrobidClient, GrobidError
from app.acquisition.html import HtmlBlockedError, HtmlParseError, parse_html
from app.acquisition.jats import JatsParseError, parse_jats
from app.acquisition.models import AcquisitionResult, FetchedResource, ParsedDocument
from app.acquisition.pdf import PdfParseError, parse_pdf
from app.acquisition.sections import locate_deposition_sections
from app.acquisition.store import RawStore
from app.discovery.dedup import Deduplicator
from app.identifiers import normalize_doi
from app.models import PaperRecord, utc_now_iso
from app.storage import Database


class WorkResolver(Protocol):
    def resolve(self, doi: str) -> PaperRecord | None: ...


class DoiResolver(Protocol):
    def resolve(self, doi: str) -> dict: ...


class AcquisitionService:
    def __init__(
        self,
        *,
        db: Database,
        resolver: WorkResolver,
        fetcher: DocumentFetcher,
        store: RawStore,
        doi_resolver: DoiResolver | None = None,
        grobid: GrobidClient | None = None,
        max_supplementary: int = 5,
    ) -> None:
        self.db = db
        self.resolver = resolver
        self.fetcher = fetcher
        self.store = store
        self.doi_resolver = doi_resolver
        self.grobid = grobid
        self.max_supplementary = max_supplementary

    def acquire_doi(self, doi: str) -> AcquisitionResult:
        acquisition_id = f"acq_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
        started_at = utc_now_iso()
        normalized = normalize_doi(doi)
        if not normalized:
            return self._finish(
                acquisition_id,
                started_at,
                paper_key="",
                doi=str(doi),
                status="failed",
                errors=[{"kind": "invalid_doi", "message": f"not a valid DOI: {doi}"}],
            )

        paper = self.resolver.resolve(normalized)
        if paper is None:
            return self._finish(
                acquisition_id,
                started_at,
                paper_key=f"doi:{normalized}",
                doi=normalized,
                status="metadata_only",
                errors=[{"kind": "work_not_found", "message": "DOI not found in OpenAlex"}],
            )

        cluster = Deduplicator().deduplicate([paper]).clusters[0]
        paper_key = self.db.save_cluster(cluster)
        doi_resolution = None
        if self.doi_resolver is not None:
            doi_resolution = self.doi_resolver.resolve(normalized)

        candidates = _oa_candidates(paper)
        if not candidates and not paper.pmcid:
            return self._finish(
                acquisition_id,
                started_at,
                paper_key=paper_key,
                doi=normalized,
                status="metadata_only",
                metadata={"doi_resolution": doi_resolution, "oa_candidates": []},
                errors=[{"kind": "not_open_access", "message": "no legal open-access location in metadata"}],
            )

        documents: list[dict[str, Any]] = []
        parsed_documents: list[tuple[dict[str, Any], ParsedDocument]] = []
        errors: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []

        # 1. PMC/JATS is the most structured and is attempted first.
        pmcid = _normalize_pmcid(paper.pmcid)
        if pmcid:
            jats_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
            resource = self.fetcher.fetch(jats_url, accept="application/xml,text/xml")
            attempts.append({"source": "pmc_jats", "url": jats_url, "status": resource.status, "access": resource.access_status})
            if resource.ok:
                document, parsed, error = self._parse_jats(resource, paper, paper_key)
                if parsed is not None and document is not None:
                    documents.append(document)
                    parsed_documents.append((document, parsed))
                else:
                    errors.append(error or {"kind": "jats_parse_error", "message": "JATS parse failed"})
                    documents.append(self._record_failed_document(paper_key, normalized, "jats", resource, error))
            else:
                errors.append(_resource_error("pmc_jats", resource))
                documents.append(self._record_failed_document(paper_key, normalized, "jats", resource, _resource_error("pmc_jats", resource)))

        # 2. OA HTML landing pages.
        if not parsed_documents:
            for candidate in candidates:
                if candidate["kind"] == "pdf":
                    continue
                resource = self.fetcher.fetch(candidate["url"], accept="text/html,application/xhtml+xml")
                attempts.append({"source": "html", "url": candidate["url"], "status": resource.status, "access": resource.access_status})
                if not resource.ok:
                    error = _resource_error("html", resource)
                    errors.append(error)
                    documents.append(self._record_failed_document(paper_key, normalized, "html", resource, error, version=candidate.get("version")))
                    continue
                if _looks_like_pdf(resource):
                    document, parsed, error = self._parse_pdf(resource, paper, paper_key)
                else:
                    document, parsed, error = self._parse_html(resource, paper, paper_key)
                if parsed is not None and document is not None:
                    documents.append(document)
                    parsed_documents.append((document, parsed))
                    break
                errors.append(error or {"kind": "html_parse_error", "message": "HTML parse failed"})
                documents.append(self._record_failed_document(paper_key, normalized, "html", resource, error, version=candidate.get("version")))

        # 3. OA PDF fallback.
        if not parsed_documents:
            for candidate in candidates:
                if candidate["kind"] != "pdf":
                    continue
                resource = self.fetcher.fetch(candidate["url"], accept="application/pdf")
                attempts.append({"source": "pdf", "url": candidate["url"], "status": resource.status, "access": resource.access_status})
                if not resource.ok:
                    error = _resource_error("pdf", resource)
                    errors.append(error)
                    documents.append(self._record_failed_document(paper_key, normalized, "pdf", resource, error, version=candidate.get("version")))
                    continue
                document, parsed, error = self._parse_pdf(resource, paper, paper_key)
                if parsed is not None and document is not None:
                    documents.append(document)
                    parsed_documents.append((document, parsed))
                    break
                errors.append(error or {"kind": "pdf_parse_error", "message": "PDF parse failed"})
                documents.append(self._record_failed_document(paper_key, normalized, "pdf", resource, error, version=candidate.get("version")))

        if not parsed_documents:
            return self._finish(
                acquisition_id,
                started_at,
                paper_key=paper_key,
                doi=normalized,
                status="fulltext_unavailable",
                documents=documents,
                errors=errors or [{"kind": "fulltext_unavailable", "message": "no legal full text could be parsed"}],
                metadata={"doi_resolution": doi_resolution, "oa_candidates": candidates, "attempts": attempts},
            )

        primary_document, primary_parsed = parsed_documents[0]
        primary_id = primary_document["document_id"]
        self.db.set_primary_document(paper_key, primary_id)

        # 4. Supplementary files linked from the primary document.
        supplementary_count = 0
        si_base = _supplementary_base(primary_document, pmcid, paper)
        for raw_si_url in primary_parsed.supplementary_urls:
            if supplementary_count >= self.max_supplementary:
                break
            si_url = urljoin(si_base, raw_si_url)
            if urlparse(si_url).scheme not in {"http", "https"}:
                errors.append({"kind": "invalid_supplementary_url", "message": raw_si_url, "url": raw_si_url})
                continue
            resource = self.fetcher.fetch(si_url)
            attempts.append({"source": "supplementary", "url": si_url, "status": resource.status, "access": resource.access_status})
            if not resource.ok:
                error = _resource_error("supplementary", resource)
                errors.append(error)
                documents.append(
                    self._record_failed_document(
                        paper_key,
                        normalized,
                        "supplementary",
                        resource,
                        error,
                        parent_document_id=primary_id,
                    )
                )
                continue
            document, parsed, error = self._parse_supplementary(resource, paper, paper_key, primary_id)
            if document is not None:
                documents.append(document)
                if parsed is not None:
                    parsed_documents.append((document, parsed))
                    supplementary_count += 1
            if error:
                errors.append(error)

        # 5. Locate the deposition section across primary + SI.
        best_section = None
        best_score = -1.0
        for document, parsed in parsed_documents:
            selected = locate_deposition_sections(parsed.sections, document_title=parsed.title)
            if selected:
                section = selected[0]
                if section.score > best_score:
                    best_score = section.score
                    best_section = (document, section)
            self.db.save_sections(
                document["document_id"],
                [section.to_dict() for section in parsed.sections],
            )

        deposition_section_id = None
        deposition_section = None
        if best_section is not None:
            document, section = best_section
            rows = self.db.get_sections(document_id=document["document_id"])
            match = next(
                (row for row in rows if row["locator"] == section.locator),
                None,
            )
            if match:
                deposition_section_id = match["section_id"]
                deposition_section = match

        status = "ok" if deposition_section_id else "partial"
        return self._finish(
            acquisition_id,
            started_at,
            paper_key=paper_key,
            doi=normalized,
            status=status,
            primary_document_id=primary_id,
            deposition_section_id=deposition_section_id,
            deposition_section=deposition_section,
            documents=documents,
            errors=errors,
            metadata={
                "doi_resolution": doi_resolution,
                "oa_candidates": candidates,
                "attempts": attempts,
                "parser": primary_document.get("parser"),
                "supplementary_count": supplementary_count,
            },
        )

    def _parse_jats(self, resource: FetchedResource, paper: PaperRecord, paper_key: str):
        try:
            parsed = parse_jats(resource.body)
        except JatsParseError as error:
            return None, None, {"kind": "jats_parse_error", "message": str(error), "url": resource.url}
        document = self._save_parsed_document(paper_key, paper, "jats", resource, parsed)
        return document, parsed, None

    def _parse_html(self, resource: FetchedResource, paper: PaperRecord, paper_key: str):
        try:
            parsed = parse_html(resource.body, base_url=resource.final_url or resource.url)
        except HtmlBlockedError as error:
            return None, None, {"kind": "blocked", "message": str(error), "url": resource.url}
        except HtmlParseError as error:
            return None, None, {"kind": "html_parse_error", "message": str(error), "url": resource.url}
        if parsed.metadata.get("content_scope") != "fulltext":
            return None, None, {
                "kind": "abstract_only",
                "message": "HTML page contains only an abstract/landing page, not full text",
                "url": resource.url,
            }
        document = self._save_parsed_document(paper_key, paper, "html", resource, parsed)
        return document, parsed, None

    def _parse_pdf(self, resource: FetchedResource, paper: PaperRecord, paper_key: str):
        if not _looks_like_pdf(resource):
            return None, None, {"kind": "not_pdf", "message": "response is not a PDF", "url": resource.url}
        parsed = None
        grobid_error = None
        if self.grobid is not None:
            try:
                parsed = self.grobid.process_pdf(resource.body)
            except GrobidError as error:
                grobid_error = str(error)
        if parsed is None:
            try:
                parsed = parse_pdf(resource.body)
            except PdfParseError as error:
                return None, None, {
                    "kind": "pdf_parse_error",
                    "message": f"{error}; grobid={grobid_error or 'not_configured'}",
                    "url": resource.url,
                }
        document = self._save_parsed_document(paper_key, paper, "pdf", resource, parsed)
        if grobid_error:
            document["grobid_error"] = grobid_error
        return document, parsed, None

    def _parse_supplementary(self, resource: FetchedResource, paper: PaperRecord, paper_key: str, parent_id: str):
        if _looks_like_pdf(resource):
            try:
                parsed = parse_pdf(resource.body)
            except PdfParseError as error:
                document = self._save_raw_supplementary(paper_key, paper, resource, parent_id)
                return document, None, {"kind": "supplementary_pdf_parse_error", "message": str(error), "url": resource.url}
            document = self._save_parsed_document(
                paper_key, paper, "supplementary", resource, parsed, parent_document_id=parent_id
            )
            return document, parsed, None
        if _looks_like_html(resource):
            try:
                parsed = parse_html(resource.body, base_url=resource.final_url or resource.url)
            except HtmlParseError as error:
                document = self._save_raw_supplementary(paper_key, paper, resource, parent_id)
                return document, None, {"kind": "supplementary_html_parse_error", "message": str(error), "url": resource.url}
            document = self._save_parsed_document(
                paper_key, paper, "supplementary", resource, parsed, parent_document_id=parent_id
            )
            return document, parsed, None
        # ZIP/XLSX/DOCX supplementary files are preserved even though Phase 3
        # does not parse them; Phase 4 can consume them later.
        document = self._save_raw_supplementary(paper_key, paper, resource, parent_id)
        return document, None, None

    def _save_raw_supplementary(
        self,
        paper_key: str,
        paper: PaperRecord,
        resource: FetchedResource,
        parent_id: str,
    ) -> dict[str, Any]:
        stored = self.store.save(paper_key, resource.body, filename=_filename(resource), source_url=resource.final_url or resource.url)
        document = {
            "document_id": _document_id(paper_key, "supplementary", resource.final_url or resource.url),
            "paper_key": paper_key,
            "doi": paper.doi,
            "document_type": "supplementary",
            "version": "unknown",
            "source_url": resource.url,
            "final_url": resource.final_url,
            "access_status": resource.access_status,
            "http_status": resource.status,
            "content_type": resource.content_type,
            "file_path": stored.path,
            "file_hash": stored.sha256,
            "byte_size": stored.byte_size,
            "fetched_at": resource.fetched_at,
            "parser": None,
            "parser_version": None,
            "license": paper.license,
            "is_primary": 0,
            "parent_document_id": parent_id,
            "error_kind": None,
            "error_message": None,
        }
        self.db.save_document(document)
        return document

    def _save_parsed_document(
        self,
        paper_key: str,
        paper: PaperRecord,
        document_type: str,
        resource: FetchedResource,
        parsed: ParsedDocument,
        *,
        parent_document_id: str | None = None,
    ) -> dict[str, Any]:
        stored = self.store.save(paper_key, resource.body, filename=_filename(resource), source_url=resource.final_url or resource.url)
        document_id = _document_id(paper_key, document_type, resource.final_url or resource.url)
        document = {
            "document_id": document_id,
            "paper_key": paper_key,
            "doi": paper.doi,
            "document_type": document_type,
            "version": _version(paper),
            "source_url": resource.url,
            "final_url": resource.final_url,
            "access_status": resource.access_status,
            "http_status": resource.status,
            "content_type": resource.content_type,
            "file_path": stored.path,
            "file_hash": stored.sha256,
            "byte_size": stored.byte_size,
            "fetched_at": resource.fetched_at,
            "parser": parsed.parser,
            "parser_version": parsed.parser_version,
            "license": paper.license,
            "is_primary": 0,
            "parent_document_id": parent_document_id,
            "error_kind": None,
            "error_message": None,
        }
        self.db.save_document(document)
        self.db.save_tables(
            document_id,
            [{**table.to_dict(), "order": index} for index, table in enumerate(parsed.tables)],
        )
        return document

    def _record_failed_document(
        self,
        paper_key: str,
        doi: str,
        document_type: str,
        resource: FetchedResource,
        error: dict,
        *,
        version: str | None = None,
        parent_document_id: str | None = None,
    ) -> dict[str, Any]:
        document_id = _document_id(paper_key, document_type, resource.final_url or resource.url)
        document = {
            "document_id": document_id,
            "paper_key": paper_key,
            "doi": doi,
            "document_type": document_type,
            "version": version or "unknown",
            "source_url": resource.url,
            "final_url": resource.final_url,
            "access_status": "blocked" if error.get("kind") == "blocked" else resource.access_status,
            "http_status": resource.status,
            "content_type": resource.content_type,
            "file_path": None,
            "file_hash": None,
            "byte_size": 0,
            "fetched_at": resource.fetched_at,
            "parser": None,
            "parser_version": None,
            "license": None,
            "is_primary": 0,
            "parent_document_id": parent_document_id,
            "error_kind": error.get("kind"),
            "error_message": error.get("message"),
        }
        self.db.save_document(document)
        return document

    def _finish(
        self,
        acquisition_id: str,
        started_at: str,
        *,
        paper_key: str,
        doi: str,
        status: str,
        primary_document_id: str | None = None,
        deposition_section_id: str | None = None,
        deposition_section: dict | None = None,
        documents: list[dict] | None = None,
        errors: list[dict] | None = None,
        metadata: dict | None = None,
    ) -> AcquisitionResult:
        errors = errors or []
        metadata = dict(metadata or {})
        metadata["errors"] = errors
        primary_error = errors[0] if status != "ok" and errors else None
        self.db.save_acquisition_run(
            {
                "acquisition_id": acquisition_id,
                "paper_key": paper_key,
                "doi": doi,
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "status": status,
                "primary_document_id": primary_document_id,
                "deposition_section_id": deposition_section_id,
                "error_kind": primary_error.get("kind") if primary_error else None,
                "error_message": primary_error.get("message") if primary_error else None,
                "metadata": metadata,
            }
        )
        sections = self.db.get_sections(paper_key=paper_key) if paper_key else []
        return AcquisitionResult(
            paper_key=paper_key,
            doi=doi,
            status=status,
            primary_document_id=primary_document_id,
            deposition_section_id=deposition_section_id,
            deposition_section=deposition_section,
            documents=documents or (self.db.list_documents(paper_key) if paper_key else []),
            sections=sections,
            errors=errors,
            metadata=metadata,
        )


def _oa_candidates(paper: PaperRecord) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(url: str | None, kind: str, version: str | None = None, license_value: str | None = None) -> None:
        if not url or not str(url).startswith("http"):
            return
        if url in seen:
            return
        seen.add(url)
        candidates.append({"url": url, "kind": kind, "version": version, "license": license_value})

    raw = paper.raw or {}
    oa = raw.get("open_access") or {}
    if oa.get("oa_url"):
        add(oa["oa_url"], "pdf" if _url_looks_pdf(oa["oa_url"]) else "html", license_value=paper.license)
    best = raw.get("best_oa_location") or {}
    if best.get("is_oa"):
        add(best.get("pdf_url"), "pdf", best.get("version"), best.get("license"))
        add(best.get("landing_page_url"), "html", best.get("version"), best.get("license"))
    for location in raw.get("locations") or []:
        if not location.get("is_oa"):
            continue
        add(location.get("pdf_url"), "pdf", location.get("version"), location.get("license"))
        add(location.get("landing_page_url"), "html", location.get("version"), location.get("license"))
    if paper.is_oa:
        add(paper.pdf_url, "pdf", license_value=paper.license)
        add(paper.landing_url, "html", license_value=paper.license)
    return candidates


def _looks_like_pdf(resource: FetchedResource) -> bool:
    return "pdf" in resource.content_type or resource.body[:5] == b"%PDF-"


def _looks_like_html(resource: FetchedResource) -> bool:
    if "html" in resource.content_type or "xml" in resource.content_type:
        return True
    head = resource.body[:200].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or head.startswith(b"<?xml")


def _resource_error(source: str, resource: FetchedResource) -> dict[str, Any]:
    return {
        "kind": resource.error_kind or f"{source}_error",
        "message": resource.error_message or f"{source} fetch failed",
        "url": resource.url,
        "status": resource.status,
        "access_status": resource.access_status,
    }


def _document_id(paper_key: str, document_type: str, url: str) -> str:
    return "doc_" + hashlib.sha256(f"{paper_key}|{document_type}|{url}".encode("utf-8")).hexdigest()[:24]


def _filename(resource: FetchedResource) -> str:
    if _looks_like_pdf(resource):
        return "document.pdf"
    if "xml" in resource.content_type:
        return "document.xml"
    lowered = (resource.final_url or resource.url).casefold().split("?")[0]
    for suffix in (".zip", ".xlsx", ".docx", ".csv", ".txt"):
        if lowered.endswith(suffix):
            return "supplementary" + suffix
    return "document.html"


def _version(paper: PaperRecord) -> str:
    raw = paper.raw or {}
    for location in (raw.get("best_oa_location") or {}, raw.get("primary_location") or {}):
        value = location.get("version")
        if value:
            return {
                "publishedVersion": "published",
                "acceptedVersion": "accepted",
                "submittedVersion": "preprint",
            }.get(value, str(value))
    return "unknown"


def _url_looks_pdf(url: str) -> bool:
    return ".pdf" in url.casefold() or "pdf" in url.casefold().split("?")[0].rsplit("/", 1)[-1]


def _normalize_pmcid(value: object) -> str | None:
    if not value:
        return None
    text = str(value)
    match = re.search(r"PMC\d+", text, re.IGNORECASE)
    if match:
        return match.group(0).upper()
    match = re.search(r"/(?:articles/)?(\d+)", text)
    if match:
        return f"PMC{match.group(1)}"
    match = re.search(r"\b(\d{5,})\b", text)
    if match:
        return f"PMC{match.group(1)}"
    return None


def _supplementary_base(primary_document: dict, pmcid: str | None, paper: PaperRecord) -> str:
    if primary_document.get("document_type") == "jats" and pmcid:
        return f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/bin/"
    return primary_document.get("final_url") or paper.landing_url or paper.oa_url or ""
