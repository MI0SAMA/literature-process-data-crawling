"""Tests for DOI/title normalization (written before implementation)."""

from __future__ import annotations

import unittest

from app.identifiers import normalize_doi, normalize_title, title_similarity


class NormalizeDoiTests(unittest.TestCase):
    def test_bare_doi_is_lowercased(self):
        self.assertEqual(normalize_doi("10.1002/ADMA.200802611"), "10.1002/adma.200802611")

    def test_url_prefix_is_removed(self):
        self.assertEqual(
            normalize_doi("https://doi.org/10.1063/1.5084945"),
            "10.1063/1.5084945",
        )

    def test_doi_prefix_and_trailing_punctuation_are_removed(self):
        self.assertEqual(normalize_doi("DOI: 10.1063/1.5084945."), "10.1063/1.5084945")
        self.assertEqual(normalize_doi("doi:10.1038/s41467-025-62904-6)"), "10.1038/s41467-025-62904-6")

    def test_percent_encoded_slash_is_decoded(self):
        self.assertEqual(
            normalize_doi("https://doi.org/10.1002%2Fadma.200802611"),
            "10.1002/adma.200802611",
        )

    def test_invalid_values_return_none(self):
        for value in (None, "", "   ", "not-a-doi", "https://example.org/paper/1"):
            with self.subTest(value=value):
                self.assertIsNone(normalize_doi(value))

    def test_doi_with_suffix_punctuation_inside_parentheses_is_preserved(self):
        doi = "10.1016/j.apsusc.2026.168566"
        self.assertEqual(normalize_doi(f"({doi})"), doi)


class NormalizeTitleTests(unittest.TestCase):
    def test_punctuation_and_case_are_removed(self):
        self.assertEqual(
            normalize_title("AlScN: A III–V Semiconductor‐Based Ferroelectric"),
            "alscn a iii v semiconductor based ferroelectric",
        )

    def test_whitespace_is_collapsed(self):
        self.assertEqual(normalize_title("  AlScN   thin\nfilms  "), "alscn thin films")

    def test_leading_article_is_removed(self):
        self.assertEqual(normalize_title("The AlScN ferroelectric"), "alscn ferroelectric")

    def test_similarity_is_high_for_formatting_variants(self):
        a = "AlScN: A III-V semiconductor based ferroelectric"
        b = "AlScN — a III V semiconductor-based ferroelectric"
        self.assertGreaterEqual(title_similarity(a, b), 0.95)

    def test_similarity_is_low_for_different_titles(self):
        self.assertLess(
            title_similarity("AlScN ferroelectric films", "ScAlN piezoelectric resonators"),
            0.70,
        )


if __name__ == "__main__":
    unittest.main()
