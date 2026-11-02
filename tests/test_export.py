"""Tests for six-sheet Excel export."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from app.export.excel import ExcelExporter
from app.storage import Database


class ExcelExportTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tempdir.name) / "test.sqlite3")
        self.db.save_recipe(
            "doi:10.1234/x:R1",
            "doi:10.1234/x",
            "10.1234/x",
            "R1",
            "S1",
            {
                "record_id": "doi:10.1234/x:R1",
                "paper_doi": "10.1234/x",
                "paper_title": "AlScN test",
                "publication_year": 2026,
                "recipe_id": "R1",
                "sample_id": "S1",
                "record_scope": "actual_recipe",
                "review_status": "new",
                "deposition_method": "sputtering",
                "sputtering_mode": "RF",
                "target1_material": "AlSc alloy",
                "target1_power_w": 200,
                "ar_flow_sccm": 20,
                "n2_flow_sccm": 5,
                "working_pressure_mbar": 0.005,
                "substrate_temperature_c": 300,
                "alscn_thickness_nm": 200,
            },
        )
        self.db.save_target(
            "doi:10.1234/x:R1:T1",
            "doi:10.1234/x:R1",
            {
                "target_record_id": "doi:10.1234/x:R1:T1",
                "record_id": "doi:10.1234/x:R1",
                "target_index": 1,
                "layer_role": "AlScN",
                "target_material": "AlSc alloy",
                "target_sc_at_pct": 30,
                "target_power_w": 200,
                "power_mode": "RF",
                "evidence_id": "doi:10.1234/x:R1:target1_power_w",
            },
        )
        self.db.save_evidence(
            {
                "evidence_id": "doi:10.1234/x:R1:target1_power_w",
                "record_id": "doi:10.1234/x:R1",
                "entity_type": "target",
                "entity_id": "doi:10.1234/x:R1:T1",
                "field_key": "target1_power_w",
                "paper_doi": "10.1234/x",
                "source_url": "https://example.org",
                "source_section": "Experimental",
                "page_table_figure": "jats:sec:2",
                "evidence_text": "The AlSc target was sputtered at 200 W.",
                "raw_value": "200",
                "raw_unit": "W",
                "normalized_value": 200,
                "normalized_unit": "W",
                "evidence_status": "reported",
                "derivation_note": None,
            }
        )

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_six_sheets_and_key_values(self):
        output = Path(self.tempdir.name) / "literature.xlsx"
        ExcelExporter().export(self.db, output)
        workbook = load_workbook(output, data_only=True)
        self.assertEqual(
            workbook.sheetnames,
            ["工艺记录", "靶材明细", "性能记录", "证据记录", "字段字典", "原表头映射"],
        )
        recipes = workbook["工艺记录"]
        headers = [cell.value for cell in recipes[1]]
        self.assertIn("论文DOI", headers)
        self.assertIn("靶材1功率(W)", headers)
        row = {headers[index]: cell.value for index, cell in enumerate(recipes[2])}
        self.assertEqual(row["论文DOI"], "10.1234/x")
        self.assertEqual(row["靶材1功率(W)"], 200)
        self.assertEqual(row["靶材1标称Sc(at.%)"], 30)
        self.assertEqual(row["工作气压(mbar)"], 0.005)
        self.assertEqual(row["审核状态"], "new")

        self.db.set_recipe_review_status("doi:10.1234/x:R1", "verified")
        ExcelExporter().export(self.db, output)
        workbook = load_workbook(output, data_only=True)
        recipes = workbook["工艺记录"]
        headers = [cell.value for cell in recipes[1]]
        row = {headers[index]: cell.value for index, cell in enumerate(recipes[2])}
        self.assertEqual(row["审核状态"], "verified")

        evidence = workbook["证据记录"]
        evidence_headers = [cell.value for cell in evidence[1]]
        self.assertIn("证据原文", evidence_headers)
        self.assertIn("The AlSc target", str(evidence.cell(row=2, column=evidence_headers.index("证据原文") + 1).value))

        dictionary = workbook["字段字典"]
        self.assertGreater(dictionary.max_row, 100)
        mapping = workbook["原表头映射"]
        self.assertGreater(mapping.max_row, 20)


if __name__ == "__main__":
    unittest.main()
