"""Tests for Phase 5 validation and review queue."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.normalize.validator import RecipeValidator, validate_all
from app.storage import Database


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tempdir.name) / "test.sqlite3")
        self.record_id = "doi:10.1234/x:R1"
        self.db.save_recipe(
            self.record_id,
            "doi:10.1234/x",
            "10.1234/x",
            "R1",
            "S1",
            {
                "paper_doi": "10.1234/x",
                "paper_title": "AlScN study",
                "target1_power_w": 200,
                "working_pressure_mbar": 0.005,
                "substrate_temperature_c": 300,
                "ar_flow_sccm": 20,
                "n2_flow_sccm": 5,
            },
        )
        for field, value, unit in (
            ("target1_power_w", 200, "W"),
            ("working_pressure_mbar", 0.005, "mbar"),
            ("substrate_temperature_c", 300, "°C"),
            ("ar_flow_sccm", 20, "sccm"),
            ("n2_flow_sccm", 5, "sccm"),
        ):
            self.db.save_evidence(
                {
                    "evidence_id": f"{self.record_id}:{field}",
                    "record_id": self.record_id,
                    "entity_type": "recipe",
                    "entity_id": self.record_id,
                    "field_key": field,
                    "paper_doi": "10.1234/x",
                    "evidence_text": f"{field} = {value} {unit}",
                    "raw_value": str(value),
                    "raw_unit": unit,
                    "normalized_value": value,
                    "normalized_unit": unit,
                    "evidence_status": "reported",
                }
            )

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_valid_recipe_has_no_issues(self):
        issues = RecipeValidator().validate(self.db.list_recipes()[0], self.db.list_evidence(self.record_id))
        self.assertEqual(issues, [])

    def test_out_of_range_and_missing_evidence(self):
        recipe = self.db.list_recipes()[0]
        recipe["data"]["target1_power_w"] = 99999
        recipe["data"]["ar_flow_sccm"] = 0
        issues = RecipeValidator().validate(recipe, self.db.list_evidence(self.record_id))
        keys = {item["field_key"] for item in issues}
        self.assertIn("target1_power_w", keys)
        self.assertIn("ar_flow_sccm", keys)

    def test_validate_all_sets_needs_review(self):
        recipe = self.db.list_recipes()[0]
        recipe["data"]["target1_power_w"] = 99999
        self.db.save_recipe(
            recipe["record_id"],
            recipe["paper_key"],
            recipe["paper_doi"],
            recipe["recipe_id"],
            recipe["sample_id"],
            recipe["data"],
        )
        summary = validate_all(self.db)
        self.assertGreater(summary["issues"], 0)
        self.assertEqual(self.db.list_recipes()[0]["review_status"], "needs_review")

    def test_orphan_measurement_is_flagged(self):
        self.db.save_measurement(
            "m_orphan",
            {
                "measurement_id": "m_orphan",
                "record_id": self.record_id,
                "sample_id": "missing-sample",
                "property_key": "Pr",
                "property_value": 20,
                "property_unit": "µC/cm²",
            },
        )
        self.assertEqual(len(self.db.orphan_measurements()), 1)
        summary = validate_all(self.db)
        self.assertGreaterEqual(summary["issues"], 1)

    def test_semantic_audit_flags(self):
        recipe = self.db.list_recipes()[0]
        recipe["data"].update(
            {
                "target_configuration": "single target",
                "substrate_material": "AlN",
                "alscn_thickness_nm": 250,
                "sputtering_mode": "DC + AC",
                "target1_sc_at_pct": 9.6,
                "substrate_temperature_c": 150,
                "working_pressure_mbar": 0.001333,
            }
        )
        evidence = self.db.list_evidence(self.record_id)
        evidence.append(
            {
                "field_key": "target_configuration",
                "evidence_text": "five independent targets",
            }
        )
        evidence.append(
            {
                "field_key": "substrate_material",
                "evidence_text": "AlScN/AlN superlattices",
            }
        )
        evidence.append(
            {
                "field_key": "alscn_thickness_nm",
                "evidence_text": "250 nm-Mo/20 nm-AlN/Si stack",
            }
        )
        evidence.append(
            {
                "field_key": "sputtering_mode",
                "evidence_text": "C-V measurement used 100 kHz AC excitation",
            }
        )
        evidence.append(
            {
                "field_key": "target1_sc_at_pct",
                "evidence_text": "the film contained 9.6 at.% Sc",
            }
        )
        evidence.append(
            {
                "field_key": "substrate_temperature_c",
                "evidence_text": "A bottom electrode was deposited at 150 °C.",
            }
        )
        evidence.append(
            {
                "field_key": "working_pressure_mbar",
                "evidence_text": "Temperature-dependent measurements were conducted in a vacuum probe station at ~10^-3 Torr.",
            }
        )
        issues = RecipeValidator().validate(recipe, evidence)
        keys = {item["field_key"] for item in issues}
        for key in (
            "target_configuration",
            "substrate_material",
            "alscn_thickness_nm",
            "sputtering_mode",
            "target1_sc_at_pct",
            "substrate_temperature_c",
            "working_pressure_mbar",
        ):
            self.assertIn(key, keys)


if __name__ == "__main__":
    unittest.main()
