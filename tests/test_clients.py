"""Tests for OpenAlex and Crossref response adapters."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.clients.crossref import CrossrefClient
from app.clients.openalex import OpenAlexClient
from app.http import CachedHttpClient, HttpResponse, MemoryResponseCache, RetryPolicy
from app.models import QuerySpec

FIXTURES = Path(__file__).parent / "fixtures"


class FixtureTransport:
    def __init__(self, path: Path):
        self.path = path
        self.calls = []

    def request(self, method, url, headers, timeout, body=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return HttpResponse(200, {}, self.path.read_bytes(), url)


def make_http(path: Path):
    transport = FixtureTransport(path)
    http = CachedHttpClient(
        transport,
        cache=MemoryResponseCache(),
        retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0, max_delay_seconds=0),
        sleep=lambda _: None,
    )
    return http, transport


SPEC = QuerySpec(
    query_id="q01",
    query="AlScN sputtering",
    databases=("openalex",),
    max_results=10,
    from_year=2019,
    to_year=2020,
    page_size=2,
)


class OpenAlexClientTests(unittest.TestCase):
    def setUp(self):
        self.http, self.transport = make_http(FIXTURES / "openalex_works.json")
        self.client = OpenAlexClient(self.http, mailto="research@example.org")

    def test_parses_records(self):
        page = self.client.search(SPEC)
        self.assertEqual(page.total, 10)
        self.assertEqual(page.page, 1)
        self.assertEqual(page.next_page, 2)
        self.assertEqual(len(page.records), 2)
        first = page.records[0]
        self.assertEqual(first.source, "openalex")
        self.assertEqual(first.doi, "10.1063/1.5084945")
        self.assertEqual(first.title, "AlScN: A III-V semiconductor based ferroelectric")
        self.assertEqual(first.abstract, "AlScN films are ferroelectric.")
        self.assertEqual(first.authors[0].family, "Fichtner")
        self.assertEqual(first.venue, "Journal of Applied Physics")
        self.assertFalse(first.is_oa)
        self.assertEqual(first.item_type, "article")

    def test_second_record_without_doi_is_kept(self):
        page = self.client.search(SPEC)
        second = page.records[1]
        self.assertIsNone(second.doi)
        self.assertTrue(second.is_oa)
        self.assertEqual(second.pdf_url, "https://example.org/scaln.pdf")

    def test_url_contains_auditable_parameters(self):
        self.client.search(SPEC)
        url = self.transport.calls[0]["url"]
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        self.assertEqual(params["search"], ["AlScN sputtering"])
        self.assertEqual(params["per-page"], ["2"])
        self.assertEqual(params["page"], ["1"])
        self.assertEqual(params["mailto"], ["research@example.org"])
        self.assertIn("from_publication_date:2019-01-01", params["filter"][0])
        self.assertIn("to_publication_date:2020-12-31", params["filter"][0])
        self.assertIn("abstract_inverted_index", params["select"][0])

    def test_pagination_page_two(self):
        page = self.client.search(SPEC, page=2)
        self.assertEqual(page.next_page, 3)

    def test_crossref_accepts_uppercase_doi(self):
        self.assertEqual(self.client.parse_record({"doi": "10.1063/1.5084945"}).doi, "10.1063/1.5084945")


class CrossrefClientTests(unittest.TestCase):
    def setUp(self):
        self.http, self.transport = make_http(FIXTURES / "crossref_works.json")
        self.client = CrossrefClient(self.http, mailto="research@example.org")

    def test_parses_article_and_filters_components(self):
        page = self.client.search(SPEC)
        self.assertEqual(len(page.records), 1)
        first = page.records[0]
        self.assertEqual(first.source, "crossref")
        self.assertEqual(first.doi, "10.1063/1.5084945")
        self.assertEqual(first.abstract, "AlScN films are ferroelectric.")
        self.assertEqual(first.authors[0].family, "Fichtner")
        self.assertEqual(first.publication_year, 2019)
        self.assertEqual(first.venue, "Journal of Applied Physics")
        self.assertEqual(first.pdf_url, "https://example.org/paper.pdf")

    def test_url_contains_query_and_date_filter(self):
        self.client.search(SPEC)
        params = parse_qs(urlparse(self.transport.calls[0]["url"]).query)
        self.assertEqual(params["query.bibliographic"], ["AlScN sputtering"])
        self.assertEqual(params["rows"], ["2"])
        self.assertEqual(params["offset"], ["0"])
        self.assertIn("from-pub-date:2019-01-01", params["filter"][0])
        self.assertIn("until-pub-date:2020-12-31", params["filter"][0])

    def test_abstract_tags_are_removed(self):
        item = json.loads((FIXTURES / "crossref_works.json").read_text(encoding="utf-8"))["message"]["items"][0]
        record = self.client.parse_record(item)
        self.assertEqual(record.abstract, "AlScN films are ferroelectric.")

    def test_invalid_date_parts_do_not_crash(self):
        record = self.client.parse_record({"DOI": "10.1234/x", "title": ["T"], "issued": {"date-parts": [[None]]}})
        self.assertIsNone(record.publication_year)


if __name__ == "__main__":
    unittest.main()
