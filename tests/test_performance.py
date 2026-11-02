"""Tests for optional performance measurement extraction."""

from __future__ import annotations

import unittest

from app.extraction.performance import extract_measurements


class PerformanceExtractionTests(unittest.TestCase):
    def test_kv_cm_is_converted_to_mv_cm_correctly(self):
        sections = [{"section_type": "results", "heading": "Results", "text": "The coercive field Ec was 10 kV/cm.", "locator": "p1"}]
        measurements = extract_measurements("doi:10.1234/y", "10.1234/y", sections)
        ec = next(item for item in measurements if item["property_key"] == "Ec")
        self.assertAlmostEqual(ec["property_value"], 0.01)
        self.assertEqual(ec["property_unit"], "MV/cm")

    def test_extracts_common_metrics_with_evidence(self):
        text = (
            "The remanent polarization Pr was 20 µC/cm2 and 2Pr was 40 µC/cm2. "
            "The coercive field Ec was 2 MV/cm. The d33 was 10 pm/V. "
            "The XRD FWHM was 0.2 degree. The residual stress was -500 MPa. "
            "The leakage current density was 1e-6 A/cm2."
        )
        sections = [{"section_type": "results", "heading": "Results", "text": text, "locator": "jats:sec:3"}]
        measurements = extract_measurements("doi:10.1234/x", "10.1234/x", sections)
        keys = {item["property_key"] for item in measurements}
        self.assertTrue({"Pr", "2Pr", "Ec", "d33", "FWHM", "stress"}.issubset(keys))
        pr = next(item for item in measurements if item["property_key"] == "Pr")
        self.assertEqual(pr["property_value"], 20)
        self.assertEqual(pr["property_unit"], "µC/cm²")
        self.assertTrue(pr["evidence"]["evidence_text"])
        stress = next(item for item in measurements if item["property_key"] == "stress")
        self.assertEqual(stress["property_value"], -500)
        self.assertEqual(stress["property_unit"], "MPa")
        self.assertTrue(pr["review_required"])

    def test_cited_introduction_value_is_not_current_experiment(self):
        sections = [{
            "section_type": "introduction",
            "heading": "1. Introduction",
            "text": "Previous work [13, 14, 15] reported a d33 of 27.6 pC/N for ScAlN.",
            "locator": "jats:sec:1",
        }]
        measurements = extract_measurements("doi:10.1234/cite", "10.1234/cite", sections)
        self.assertEqual(measurements, [])

    def test_abstract_value_is_flagged(self):
        sections = [{
            "section_type": "abstract",
            "heading": "Abstract",
            "text": "The film showed a Pr of 20 µC/cm2.",
            "locator": "jats:abstract",
        }]
        measurements = extract_measurements("doi:10.1234/abs", "10.1234/abs", sections)
        self.assertEqual(len(measurements), 1)
        self.assertEqual(measurements[0]["source_scope"], "abstract")
        self.assertTrue(measurements[0]["review_required"])

    def test_leakage_scientific_notation(self):
        sections = [{
            "section_type": "results",
            "heading": "Results",
            "text": "The leakage current density was 1e-6 A/cm2.",
            "locator": "p1",
        }]
        measurements = extract_measurements("doi:10.1234/leak", "10.1234/leak", sections)
        leakage = next(item for item in measurements if item["property_key"] == "leakage_J")
        self.assertAlmostEqual(leakage["property_value"], 1e-6)


if __name__ == "__main__":
    unittest.main()
