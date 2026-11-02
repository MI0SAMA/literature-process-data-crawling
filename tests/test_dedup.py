"""Tests for DOI-first and title/author/year deduplication."""

from __future__ import annotations

import unittest

from app.discovery.dedup import Deduplicator
from app.models import Author
from tests.helpers import make_record


class DedupTests(unittest.TestCase):
    def setUp(self):
        self.dedup = Deduplicator(
            fuzzy_auto_merge_threshold=0.90,
            fuzzy_review_threshold=0.45,
        )

    def test_same_doi_across_sources_is_one_cluster(self):
        a = make_record(source="openalex", source_id="W1", doi="10.1063/1.5084945")
        b = make_record(
            source="crossref",
            source_id="10.1063/1.5084945",
            doi="https://doi.org/10.1063/1.5084945",
            title="AlScN: A III-V semiconductor based ferroelectric",
        )
        result = self.dedup.deduplicate([a, b])
        self.assertEqual(len(result.clusters), 1)
        self.assertEqual(result.clusters[0].method, "doi")
        self.assertFalse(result.clusters[0].review_required)
        self.assertEqual(len(result.clusters[0].records), 2)
        self.assertEqual(result.review_items, [])

    def test_same_doi_with_url_prefix_and_case_is_one_cluster(self):
        a = make_record(doi="10.1002/ADMA.200802611", source="openalex")
        b = make_record(doi="https://doi.org/10.1002/adma.200802611", source="crossref")
        result = self.dedup.deduplicate([a, b])
        self.assertEqual(len(result.clusters), 1)
        self.assertEqual(result.stats["duplicates_removed"], 1)

    def test_no_doi_exact_title_author_year_is_merged(self):
        a = make_record(source="openalex", source_id="W2", doi=None)
        b = make_record(
            source="crossref",
            source_id="cr2",
            doi=None,
            title="AlScN thin films by reactive magnetron sputtering",
        )
        result = self.dedup.deduplicate([a, b])
        self.assertEqual(len(result.clusters), 1)
        self.assertEqual(result.clusters[0].method, "title_author_year")
        self.assertFalse(result.clusters[0].review_required)

    def test_no_doi_variant_title_is_merged_into_review_cluster(self):
        a = make_record(source="openalex", source_id="W3", doi=None)
        b = make_record(
            source="crossref",
            source_id="cr3",
            doi=None,
            title="AlScN thin films by reactive magnetron sputter deposition",
        )
        result = self.dedup.deduplicate([a, b])
        self.assertEqual(len(result.clusters), 1)
        self.assertTrue(result.clusters[0].review_required)
        self.assertEqual(result.clusters[0].method, "fuzzy_title_author_year")
        self.assertEqual(len(result.review_items), 0)

    def test_uncertain_pair_is_not_merged_and_enters_review_queue(self):
        a = make_record(source="openalex", source_id="W4", doi=None, year=2019)
        b = make_record(
            source="crossref",
            source_id="cr4",
            doi=None,
            title="Reactive sputtering of AlScN thin films for piezoelectric MEMS",
            year=2019,
        )
        result = self.dedup.deduplicate([a, b])
        self.assertEqual(len(result.clusters), 2)
        self.assertEqual(len(result.review_items), 1)
        self.assertGreaterEqual(result.review_items[0].similarity, 0.45)

    def test_exact_title_missing_author_is_merged_with_review_flag(self):
        a = make_record(source="openalex", source_id="W10", doi=None, authors=[], title="AlScN thin films")
        b = make_record(source="crossref", source_id="cr10", doi=None, authors=[], title="AlScN thin films")
        result = self.dedup.deduplicate([a, b])
        self.assertEqual(len(result.clusters), 1)
        self.assertTrue(result.clusters[0].review_required)
        self.assertIn("author metadata is missing", result.clusters[0].review_reason)

    def test_same_title_different_author_is_not_merged(self):
        a = make_record(source="openalex", source_id="W5", doi=None, authors=[Author(name="A. One", family="One")])
        b = make_record(
            source="crossref",
            source_id="cr5",
            doi=None,
            authors=[Author(name="B. Two", family="Two")],
        )
        result = self.dedup.deduplicate([a, b])
        self.assertEqual(len(result.clusters), 2)
        self.assertEqual(result.review_items, [])

    def test_doi_record_can_absorb_no_doi_duplicate(self):
        a = make_record(source="openalex", source_id="W6", doi="10.1063/1.5084945")
        b = make_record(
            source="crossref",
            source_id="cr6",
            doi=None,
            title="AlScN thin films by reactive magnetron sputtering",
        )
        result = self.dedup.deduplicate([a, b])
        self.assertEqual(len(result.clusters), 1)
        self.assertEqual(result.clusters[0].doi, "10.1063/1.5084945")
        self.assertEqual(len(result.clusters[0].records), 2)

    def test_merges_title_and_keeps_richest_abstract(self):
        a = make_record(source="openalex", source_id="W7", abstract="short")
        b = make_record(source="crossref", source_id="cr7", abstract="a much longer abstract about reactive sputtering")
        cluster = self.dedup.deduplicate([a, b]).clusters[0]
        self.assertEqual(cluster.abstract, "a much longer abstract about reactive sputtering")

    def test_stats_are_reported(self):
        records = [
            make_record(source="openalex", source_id="W8", doi="10.1234/a"),
            make_record(source="crossref", source_id="cr8", doi="10.1234/a"),
            make_record(source="openalex", source_id="W9", doi=None, title="Different paper", authors=[Author(name="C. Three", family="Three")]),
        ]
        result = self.dedup.deduplicate(records)
        self.assertEqual(result.stats["records_in"], 3)
        self.assertEqual(result.stats["clusters_out"], 2)
        self.assertEqual(result.stats["duplicates_removed"], 1)


if __name__ == "__main__":
    unittest.main()
