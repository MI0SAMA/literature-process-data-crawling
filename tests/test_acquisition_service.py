"""Tests for Phase 3 acquisition orchestration (offline fakes)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.acquisition.fetch import DocumentFetcher
from app.acquisition.models import FetchedResource
from app.acquisition.service import AcquisitionService
from app.acquisition.store import RawStore
from app.config import load_app_config
from app.discovery.dedup import Deduplicator
from app.models import Author, PaperRecord
from app.storage import Database

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResolver:
    def __init__(self, paper):
        self.paper = paper
        self.calls = []

    def resolve(self, doi):
        self.calls.append(doi)
        return self.paper


class FakeDoiResolver:
    def __init__(self, payload=None):
        self.payload = payload or {"status": 200, "final_url": "https://example.org/paper", "error": None}
        self.calls = []

    def resolve(self, doi):
        self.calls.append(doi)
        return {"doi": doi, **self.payload}


class FakeFetcher:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def fetch(self, url, *, accept=""):
        self.calls.append(url)
        if url not in self.mapping:
            return FetchedResource(
                url=url,
                final_url=url,
                status=404,
                content_type="text/html",
                body=b"",
                fetched_at="2026-10-29T00:00:00+00:00",
                access_status="not_found",
                error_kind="not_found",
                error_message="HTTP 404",
            )
        return self.mapping[url]


def response(url, body, content_type="text/html", status=200, access="open_access"):
    return FetchedResource(
        url=url,
        final_url=url,
        status=status,
        content_type=content_type,
        body=body,
        fetched_at="2026-10-29T00:00:00+00:00",
        access_status=access,
        error_kind=None if status == 200 else "http_error",
        error_message=None if status == 200 else f"HTTP {status}",
    )


def paper(*, pmcid=None, is_oa=True, raw=None, doi="10.1063/1.5084945"):
    default_raw = {
        "open_access": {"is_oa": is_oa, "oa_url": "https://example.org/paper" if is_oa else None},
        "best_oa_location": {
            "is_oa": is_oa,
            "landing_page_url": "https://example.org/paper" if is_oa else None,
            "pdf_url": "https://example.org/paper.pdf" if is_oa else None,
            "version": "publishedVersion",
            "license": "cc-by",
        },
        "primary_location": {"landing_page_url": "https://example.org/paper", "version": "publishedVersion"},
    }
    return PaperRecord(
        source="openalex",
        source_id="W1",
        title="Ferroelectric AlScN thin films by reactive sputtering",
        doi=doi,
        abstract="AlScN films were deposited by reactive magnetron sputtering.",
        authors=[Author(name="Test Author", family="Author")],
        publication_year=2026,
        venue="Test Journal",
        item_type="article",
        landing_url="https://example.org/paper" if is_oa else "https://doi.org/" + doi,
        pdf_url="https://example.org/paper.pdf" if is_oa else None,
        is_oa=is_oa,
        oa_status="gold" if is_oa else "closed",
        oa_url="https://example.org/paper" if is_oa else None,
        pmcid=pmcid,
        license="cc-by" if is_oa else None,
        raw=raw if raw is not None else default_raw,
    )


class AcquisitionServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tempdir.name) / "test.sqlite3")
        self.store = RawStore(Path(self.tempdir.name) / "raw")

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def service(self, paper_obj, mapping, doi_resolver=None):
        return AcquisitionService(
            db=self.db,
            resolver=FakeResolver(paper_obj),
            fetcher=FakeFetcher(mapping),
            store=self.store,
            doi_resolver=doi_resolver,
        )

    def test_jats_path_records_document_and_deposition_section(self):
        source = paper(pmcid="PMC12345")
        jats_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC12345/fullTextXML"
        service = self.service(source, {jats_url: response(jats_url, (FIXTURES / "jats_sample.xml").read_bytes(), "application/xml")})
        result = service.acquire_doi(source.doi)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.documents[0]["document_type"], "jats")
        self.assertIsNotNone(result.deposition_section)
        self.assertIn("Experimental", result.deposition_section["heading"])
        self.assertGreaterEqual(self.db.count_documents(), 1)
        self.assertGreaterEqual(self.db.count_sections(), 3)
        self.assertGreaterEqual(self.db.count_tables(), 1)
        document = self.db.list_documents(result.paper_key)[0]
        self.assertTrue(Path(document["file_path"]).is_file())
        self.assertEqual(len(document["file_hash"]), 64)

    def test_html_path_and_supplementary(self):
        source = paper()
        html = (FIXTURES / "html_sample.html").read_bytes()
        si_url = "https://example.org/supporting/si.pdf"
        si_html = b"<html><body><article><h1>Supplementary</h1><h2>Supplementary Methods</h2><p>AlScN was sputtered at 150 W and 0.4 Pa.</p></article></body></html>"
        service = self.service(
            source,
            {
                "https://example.org/paper": response("https://example.org/paper", html),
                si_url: response(si_url, si_html),
            },
        )
        result = service.acquire_doi(source.doi)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.primary_document_id, result.documents[0]["document_id"])
        types = {document["document_type"] for document in result.documents}
        self.assertIn("supplementary", types)

    def test_not_open_access_is_metadata_only_and_never_fetches(self):
        source = paper(is_oa=False, raw={"open_access": {"is_oa": False, "oa_url": None}})
        fetcher = FakeFetcher({})
        service = AcquisitionService(db=self.db, resolver=FakeResolver(source), fetcher=fetcher, store=self.store)
        result = service.acquire_doi(source.doi)
        self.assertEqual(result.status, "metadata_only")
        self.assertEqual(fetcher.calls, [])
        self.assertEqual(result.errors[0]["kind"], "not_open_access")

    def test_forbidden_fetch_is_fulltext_unavailable(self):
        source = paper()
        forbidden = FetchedResource(
            url="https://example.org/paper",
            final_url="https://example.org/paper",
            status=403,
            content_type="text/html",
            body=b"",
            fetched_at="2026-10-29T00:00:00+00:00",
            access_status="forbidden",
            error_kind="forbidden",
            error_message="HTTP 403",
        )
        service = self.service(source, {"https://example.org/paper": forbidden})
        result = service.acquire_doi(source.doi)
        self.assertEqual(result.status, "fulltext_unavailable")
        self.assertTrue(any(document["access_status"] == "forbidden" for document in result.documents))

    def test_fulltext_without_deposition_section_is_partial(self):
        source = paper(pmcid="PMC12345")
        jats_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC12345/fullTextXML"
        body = b"""<article xmlns="http://jats.nlm.nih.gov/1.1/"><front><article-title>AlScN study</article-title></front><body><sec><title>Introduction</title><p>This paper studies ferroelectricity in AlScN.</p></sec></body></article>"""
        service = self.service(source, {jats_url: response(jats_url, body, "application/xml")})
        result = service.acquire_doi(source.doi)
        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.deposition_section_id)

    def test_doi_resolution_is_recorded(self):
        source = paper(pmcid="PMC12345")
        jats_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC12345/fullTextXML"
        doi_resolver = FakeDoiResolver({"status": 302, "final_url": "https://example.org/paper", "error": None})
        service = self.service(
            source,
            {jats_url: response(jats_url, (FIXTURES / "jats_sample.xml").read_bytes(), "application/xml")},
            doi_resolver=doi_resolver,
        )
        result = service.acquire_doi(source.doi)
        self.assertEqual(result.metadata["doi_resolution"]["final_url"], "https://example.org/paper")

    def test_work_not_found_is_metadata_only(self):
        class MissingResolver:
            def resolve(self, doi):
                return None

        service = AcquisitionService(db=self.db, resolver=MissingResolver(), fetcher=FakeFetcher({}), store=self.store)
        result = service.acquire_doi("10.1234/not-found")
        self.assertEqual(result.status, "metadata_only")
        self.assertEqual(result.errors[0]["kind"], "work_not_found")


if __name__ == "__main__":
    unittest.main()
