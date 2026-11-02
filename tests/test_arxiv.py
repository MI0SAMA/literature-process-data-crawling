"""Tests for arXiv feed parsing and relevance filtering."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.acquisition.arxiv import is_relevant_entry, parse_arxiv_feed

FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_feed.xml"


class ArxivTests(unittest.TestCase):
    def test_parses_entries(self):
        entries = parse_arxiv_feed(FIXTURE.read_bytes())
        self.assertEqual(len(entries), 2)
        first = entries[0]
        self.assertEqual(first.base_id, "2512.24455")
        self.assertEqual(first.doi, "10.1234/example")
        self.assertEqual(first.year, 2025)
        self.assertEqual(first.authors[0], "Alice Example")
        self.assertIn("sputtering", first.abstract)

    def test_relevance_filter(self):
        entries = parse_arxiv_feed(FIXTURE.read_bytes())
        self.assertTrue(is_relevant_entry(entries[0]))
        self.assertFalse(is_relevant_entry(entries[1]))

    def test_doi_falls_back_to_arxiv_doi(self):
        entry = parse_arxiv_feed(FIXTURE.read_bytes())[1]
        self.assertTrue(entry.doi.startswith("10.48550/arXiv.2501.00001"))


if __name__ == "__main__":
    unittest.main()
