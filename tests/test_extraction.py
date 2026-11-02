"""Tests for unit parsing and the rule-based recipe extractor."""

from __future__ import annotations

import unittest

from app.extraction.extractor import RuleBasedExtractor
from app.extraction.units import convert, normalize_temperature, parse_numeric


class UnitTests(unittest.TestCase):
    def test_scientific_notation(self):
        self.assertAlmostEqual(parse_numeric("1.0 × 10−6"), 1e-6)
        self.assertAlmostEqual(parse_numeric("10−6"), 1e-6)

    def test_pressure_conversion(self):
        value, unit = convert(0.5, "Pa", "pressure")
        self.assertAlmostEqual(value, 0.005)
        self.assertEqual(unit, "mbar")
        value, unit = convert(5.0, "mTorr", "pressure")
        self.assertAlmostEqual(value, 0.006666, places=5)

    def test_temperature_conversion(self):
        value, unit = normalize_temperature(300, "K")
        self.assertAlmostEqual(value, 26.85)
        self.assertEqual(unit, "°C")

    def test_length_conversion(self):
        value, unit = convert(1, "cm", "length")
        self.assertEqual(value, 10_000_000)
        self.assertEqual(unit, "nm")


def section(text: str, heading: str = "Experimental") -> dict:
    return {
        "section_type": "deposition_methods",
        "heading": heading,
        "text": text,
        "locator": "jats:sec:2",
        "section_order": 0,
        "document_id": "doc1",
    }


class ExtractorTests(unittest.TestCase):
    def setUp(self):
        self.extractor = RuleBasedExtractor()
        self.text = (
            "AlScN films were deposited on Si(100) by reactive magnetron sputtering. "
            "The AlSc target containing 30 at.% Sc was sputtered at 200 W under Ar flow of 20 sccm "
            "and N2 flow of 5 sccm. The working pressure was 0.5 Pa, substrate temperature 300 C, "
            "film thickness 200 nm, deposition time 30 min, and target-substrate distance 10 cm."
        )

    def extract(self, text=None):
        return self.extractor.extract(
            paper_key="doi:10.1234/test",
            doi="10.1234/test",
            title="AlScN test",
            year=2026,
            sections=[section(text or self.text)],
            tables=[],
            source_url="https://example.org/paper",
            source_document_id="doc1",
        )

    def test_core_fields_and_evidence(self):
        result = self.extract()
        self.assertEqual(result.status, "extracted")
        data = result.recipes[0].data
        self.assertEqual(data["deposition_method"], "sputtering")
        self.assertEqual(data["target1_material"], "AlSc alloy")
        self.assertEqual(data["target1_sc_at_pct"], 30)
        self.assertEqual(data["target1_power_w"], 200)
        self.assertEqual(data["ar_flow_sccm"], 20)
        self.assertEqual(data["n2_flow_sccm"], 5)
        self.assertAlmostEqual(data["working_pressure_mbar"], 0.005)
        self.assertEqual(data["substrate_temperature_c"], 300)
        self.assertEqual(data["alscn_thickness_nm"], 200)
        self.assertEqual(data["deposition_time_min"], 30)
        self.assertEqual(data["target_substrate_distance_mm"], 100)
        self.assertEqual(data["substrate_material"], "Si(100)")
        self.assertGreaterEqual(len(result.evidence), 8)
        self.assertTrue(all(item["evidence_text"] for item in result.evidence))
        self.assertGreaterEqual(len(result.recipes[0].targets), 1)

    def test_base_pressure_scientific_notation(self):
        text = self.text + " The base pressure was 1.0 × 10−6 Torr."
        result = self.extract(text)
        self.assertAlmostEqual(result.recipes[0].data["base_pressure_mbar"], 1.333e-6, places=8)

    def test_electrode_power_is_not_used_as_target_power(self):
        text = "The TiN top electrode was deposited at 150 W. The AlSc target was sputtered at 200 W under Ar 20 sccm."
        result = self.extract(text)
        self.assertEqual(result.recipes[0].data["target1_power_w"], 200)

    def test_no_deposition_section_returns_status(self):
        result = self.extractor.extract(
            paper_key="doi:10.1234/none",
            doi="10.1234/none",
            title="None",
            year=2026,
            sections=[{"section_type": "results", "heading": "Results", "text": "No methods.", "locator": "x", "section_order": 0}],
            tables=[],
            source_url=None,
        )
        self.assertEqual(result.status, "no_deposition_section")
        self.assertEqual(result.recipes, [])

    def test_missing_fields_stay_null_and_review(self):
        text = "AlScN films were deposited by magnetron sputtering."
        result = self.extract(text)
        data = result.recipes[0].data
        self.assertIsNone(data.get("target1_power_w"))
        self.assertIsNone(data.get("working_pressure_mbar"))
        self.assertGreaterEqual(len(result.recipes[0].review_items), 1)

    def test_gas_ratio_is_split_into_n2_and_ar(self):
        text = (
            "AlScN films were deposited by reactive sputtering. "
            "Bulk AlScN was deposited under N2:Ar = 160:32 sccm at 200 C using 5 kW DC power."
        )
        result = self.extract(text)
        data = result.recipes[0].data
        self.assertEqual(data["n2_flow_sccm"], 160)
        self.assertEqual(data["ar_flow_sccm"], 32)

    def test_gas_value_for_gas_and_microbar(self):
        text = (
            "AlScN films were deposited by sputtering at a working pressure of 5 µbar. "
            "The gas flow was 20 sccm for Ar and 12 sccm for N2."
        )
        result = self.extract(text)
        data = result.recipes[0].data
        self.assertEqual(data["ar_flow_sccm"], 20)
        self.assertEqual(data["n2_flow_sccm"], 12)
        self.assertAlmostEqual(data["working_pressure_mbar"], 0.005)

    def test_stack_thickness_is_not_misassigned(self):
        text = (
            "The stack was 250 nm-Mo/20 nm-AlN/Si. "
            "The AlScN nitride layer thickness was 365 nm."
        )
        result = self.extract(text)
        self.assertEqual(result.recipes[0].data["alscn_thickness_nm"], 365)

    def test_adhesion_and_electrode_thickness_are_not_alscn(self):
        text = (
            "A 10 nm Ti adhesion layer and a 100 nm Pt bottom electrode were deposited, "
            "followed by AlScN deposition."
        )
        result = self.extract(text)
        self.assertIsNone(result.recipes[0].data.get("alscn_thickness_nm"))

    def test_multiple_targets_not_single_target(self):
        text = (
            "AlScN films were deposited in a single uninterrupted vacuum cycle using "
            "five independent targets (AlSc, Al and Pt)."
        )
        result = self.extract(text)
        self.assertEqual(result.recipes[0].data["target_configuration"], "multiple targets")

    def test_ac_excitation_is_not_deposition_mode(self):
        text = (
            "AlScN was deposited using 5 kW DC power in a magnetron sputter system. "
            "C-V measurements used a 100 kHz AC excitation."
        )
        result = self.extract(text)
        self.assertEqual(result.recipes[0].data["sputtering_mode"], "DC")

    def test_aln_superlattice_is_not_substrate(self):
        text = (
            "AlScN/AlN superlattices were deposited on Pt top electrodes. "
            "The film stack included AlN layers."
        )
        result = self.extract(text)
        self.assertIsNone(result.recipes[0].data.get("substrate_material"))

    def test_per_target_power_is_not_assigned_to_first_target(self):
        text = (
            "Al and Sc targets were used for co-sputtering. "
            "A thin seed layer was grown using 250 W on the Sc target and 875 W on the Al target."
        )
        result = self.extract(text)
        data = result.recipes[0].data
        self.assertEqual(data["target1_material"], "Al")
        self.assertEqual(data["target2_material"], "Sc")
        self.assertEqual(data["target1_power_w"], 875)
        self.assertEqual(data["target2_power_w"], 250)
        targets = result.recipes[0].targets
        self.assertEqual(targets[0]["evidence_id"], f"{result.paper_key}:R1:target1_power_w")
        self.assertEqual(targets[1]["evidence_id"], f"{result.paper_key}:R1:target2_power_w")
        target_evidence = {(item["field_key"], item["entity_id"]) for item in result.evidence if item["entity_type"] == "target"}
        self.assertIn(("target1_power_w", f"{result.paper_key}:R1:T1"), target_evidence)
        self.assertIn(("target2_power_w", f"{result.paper_key}:R1:T2"), target_evidence)

    def test_electrode_temperature_does_not_override_alscn_temperature(self):
        text = (
            "A 54 nm Al bottom electrode was deposited at 150 °C on sapphire. "
            "The AlBScN layer was co-sputtered at 300 °C using 900 W."
        )
        result = self.extract(text)
        self.assertEqual(result.recipes[0].data["substrate_temperature_c"], 300)

    def test_probe_station_pressure_is_not_working_pressure(self):
        text = (
            "AlScN was deposited by sputtering. "
            "Temperature-dependent measurements were conducted in a vacuum probe station at ~10^-3 Torr."
        )
        result = self.extract(text)
        self.assertIsNone(result.recipes[0].data.get("working_pressure_mbar"))

    def test_room_temperature_is_not_converted_to_25c(self):
        text = "AlScN films were deposited by sputtering. Electrical measurements were performed at room temperature."
        result = self.extract(text)
        self.assertIsNone(result.recipes[0].data.get("substrate_temperature_c"))
        self.assertEqual(result.recipes[0].data.get("temperature_basis"), "room_temp")

    def test_method_and_power_semantics_are_exported(self):
        text = (
            "AlScN films were deposited by reactive co-sputtering using a pulsed DC source. "
            "The target power was 5 kW."
        )
        result = self.extract(text)
        data = result.recipes[0].data
        self.assertEqual(data["target1_power_basis"], "target_power")
        self.assertIn("reactive", data["deposition_method_detail"])
        self.assertIn("co-sputtering", data["deposition_method_detail"])
        self.assertEqual(data["method_category"], "pulsed DC")

    def test_sputter_power_is_not_labeled_target_power(self):
        text = "AlScN films were deposited by magnetron sputtering. Sputter power of 6 kW was used for the film deposition."
        result = self.extract(text)
        self.assertEqual(result.recipes[0].data["target1_power_basis"], "sputter_power")
        self.assertEqual(result.recipes[0].data["target1_power_statistic"], "unknown")

    def test_target_diameter_and_nominal_power_density(self):
        text = "AlScN films were deposited from a 2-inch Al target at 875 W."
        result = self.extract(text)
        target = result.recipes[0].targets[0]
        self.assertAlmostEqual(target["target_diameter_inch"], 2.0)
        self.assertAlmostEqual(target["power_density_w_cm2"], 43.17, places=1)
        self.assertEqual(target["power_basis"], "target_power")

    def test_bias_and_power_density_are_not_target_power(self):
        bias = self.extract("AlScN films were deposited by sputtering. The deposition bias was 8 W.")
        self.assertIsNone(bias.recipes[0].data.get("target1_power_w"))
        density = self.extract("AlScN films were deposited by sputtering at a power density of 12.7 W/cm2.")
        self.assertIsNone(density.recipes[0].data.get("target1_power_w"))

    def test_target_distance_and_thickness_are_not_diameter(self):
        text = (
            "300 W was supplied to the 5 cm diameter AlScN target. "
            "The substrate was positioned 16.5 cm from the target. "
            "Each target has a thickness of 6 mm and a diameter of 200 mm."
        )
        result = self.extract(text)
        target = result.recipes[0].targets[0]
        self.assertEqual(target["target_diameter_mm"], 50.0)

        result2 = self.extract("Each target has a thickness of 6 mm and a diameter of 200 mm. The target power was 200 W.")
        self.assertEqual(result2.recipes[0].targets[0]["target_diameter_mm"], 200.0)


if __name__ == "__main__":
    unittest.main()
