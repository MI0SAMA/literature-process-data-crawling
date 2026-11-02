"""Tests for the static review page."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.review.html import generate_review_html
from app.storage import Database


class ReviewHtmlTests(unittest.TestCase):
    def test_generates_evidence_page(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db = Database(Path(tempdir) / "test.sqlite3")
            db.save_recipe(
                "doi:10.1234/x:R1",
                "doi:10.1234/x",
                "10.1234/x",
                "R1",
                "S1",
                {"paper_title": "AlScN study", "paper_doi": "10.1234/x", "target1_power_w": 200},
            )
            db.save_evidence(
                {
                    "evidence_id": "doi:10.1234/x:R1:target1_power_w",
                    "record_id": "doi:10.1234/x:R1",
                    "entity_type": "recipe",
                    "entity_id": "doi:10.1234/x:R1",
                    "field_key": "target1_power_w",
                    "paper_doi": "10.1234/x",
                    "evidence_text": "The target was sputtered at 200 W.",
                    "raw_value": "200",
                    "raw_unit": "W",
                    "normalized_value": 200,
                    "normalized_unit": "W",
                    "evidence_status": "reported",
                }
            )
            output = Path(tempdir) / "review.html"
            generate_review_html(db, output)
            text = output.read_text(encoding="utf-8")
            self.assertIn("AlScN study", text)
            self.assertIn("target1_power_w", text)
            self.assertIn("sputtered", text)
            db.close()


if __name__ == "__main__":
    unittest.main()
