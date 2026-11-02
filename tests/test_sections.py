"""Tests for section classification and deposition-section location."""

from __future__ import annotations

import unittest

from app.acquisition.models import ParsedSection
from app.acquisition.sections import classify_heading, locate_deposition_sections, score_deposition_section


def section(heading: str, text: str, order: int = 0, section_type: str = "other") -> ParsedSection:
    return ParsedSection(
        section_type=section_type,
        heading=heading,
        text=text,
        order=order,
        locator=f"test:{order}",
    )


class ClassifyHeadingTests(unittest.TestCase):
    def test_methods_headings(self):
        for heading in ("Experimental", "Methods", "Materials and Methods", "Experimental section"):
            with self.subTest(heading=heading):
                self.assertEqual(classify_heading(heading), "methods")

    def test_deposition_headings(self):
        for heading in ("Deposition of AlScN films", "Film growth", "Fabrication", "Sputtering conditions"):
            with self.subTest(heading=heading):
                self.assertEqual(classify_heading(heading), "methods")

    def test_supplementary_methods(self):
        self.assertEqual(classify_heading("Supplementary Methods"), "supplementary_methods")

    def test_references(self):
        self.assertEqual(classify_heading("References"), "references")
        self.assertEqual(classify_heading("Bibliography"), "references")

    def test_abstract_results_conclusion(self):
        self.assertEqual(classify_heading("Abstract"), "abstract")
        self.assertEqual(classify_heading("Results and Discussion"), "results")
        self.assertEqual(classify_heading("Conclusions"), "conclusion")


class DepositionScoreTests(unittest.TestCase):
    def test_experimental_section_with_process_text_scores_high(self):
        s = section(
            "Experimental",
            "AlScN films were deposited by reactive magnetron sputtering. "
            "The target power was 200 W, Ar flow 20 sccm, N2 flow 5 sccm, "
            "working pressure 0.5 Pa, substrate temperature 300 C.",
        )
        self.assertGreaterEqual(score_deposition_section(s), 5.0)

    def test_results_only_section_scores_lower_than_methods(self):
        methods = section(
            "Experimental",
            "AlScN was sputtered at 200 W and 0.5 Pa.",
            order=1,
        )
        results = section(
            "Results",
            "The AlScN film showed a XRD peak; sputtering power was 200 W.",
            order=2,
        )
        self.assertGreater(score_deposition_section(methods), score_deposition_section(results))

    def test_references_are_excluded(self):
        refs = section("References", "AlScN sputtering power 200 W, pressure 0.5 Pa", section_type="references")
        self.assertLess(score_deposition_section(refs), 0)

    def test_unrelated_methods_are_not_selected(self):
        s = section("Experimental", "Samples were measured by X-ray diffraction and electron microscopy.")
        self.assertLess(score_deposition_section(s), 3.0)


class LocateDepositionTests(unittest.TestCase):
    def test_selects_best_methods_section(self):
        sections = [
            section("Introduction", "AlScN is a ferroelectric material.", 0),
            section("Experimental", "AlScN films were deposited by reactive magnetron sputtering at 200 W and 0.5 Pa.", 1),
            section("Results", "The film was piezoelectric.", 2),
        ]
        selected = locate_deposition_sections(sections)
        self.assertGreaterEqual(len(selected), 1)
        self.assertEqual(selected[0].heading, "Experimental")
        self.assertEqual(selected[0].section_type, "deposition_methods")

    def test_supplementary_methods_can_be_selected_when_body_lacks_deposition(self):
        sections = [
            section("Experimental", "Device fabrication and electrical measurements are described.", 0),
            section("Supplementary Methods", "AlScN was deposited by RF sputtering at 150 W, 0.4 Pa, 20 sccm Ar.", 1, "supplementary_methods"),
        ]
        selected = locate_deposition_sections(sections)
        self.assertEqual(selected[0].section_type, "supplementary_methods")

    def test_no_deposition_section_returns_empty(self):
        sections = [
            section("Introduction", "This paper studies ferroelectricity.", 0),
            section("Methods", "Polarization was measured by PUND.", 1),
        ]
        self.assertEqual(locate_deposition_sections(sections), [])

    def test_conclusion_is_not_selected_as_methods(self):
        sections = [
            section("Experimental", "AlScN was sputtered at 200 W and 0.5 Pa.", 0),
            section("Conclusion", "AlScN films were sputtered and showed ferroelectricity at 200 W.", 1),
        ]
        selected = locate_deposition_sections(sections)
        self.assertEqual(selected[0].heading, "Experimental")
        self.assertNotIn("Conclusion", [item.heading for item in selected])

    def test_title_heading_is_not_selected(self):
        sections = [
            section("Title: AlScN sputtered thin films", "AlScN was sputtered at 200 W and 0.5 Pa.", 0, "methods"),
        ]
        self.assertEqual(locate_deposition_sections(sections, document_title="AlScN sputtered thin films"), [])


if __name__ == "__main__":
    unittest.main()
