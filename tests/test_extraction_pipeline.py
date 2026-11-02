"""Tests for the Phase 4 extraction pipeline and DB persistence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.discovery.dedup import Deduplicator
from app.extraction.pipeline import ExtractionPipeline
from app.storage import Database
from tests.helpers import make_record


class ExtractionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tempdir.name) / "test.sqlite3")
        cluster = Deduplicator().deduplicate(
            [
                make_record(
                    source="openalex",
                    source_id="W1",
                    doi="10.1234/extract",
                    title="AlScN sputtering study",
                )
            ]
        ).clusters[0]
        self.paper_key = self.db.save_cluster(cluster)
        self.db.save_document(
            {
                "document_id": "doc1",
                "paper_key": self.paper_key,
                "doi": "10.1234/extract",
                "document_type": "jats",
                "source_url": "https://example.org/paper",
                "final_url": "https://example.org/paper",
                "access_status": "open_access",
                "http_status": 200,
                "parser": "jats",
                "parser_version": "1",
                "is_primary": 1,
                "file_hash": "a" * 64,
            }
        )
        self.db.save_sections(
            "doc1",
            [
                {
                    "section_type": "deposition_methods",
                    "heading": "Experimental",
                    "text": (
                        "AlScN films were deposited on Si(100) by reactive magnetron sputtering. "
                        "The AlSc target containing 30 at.% Sc was sputtered at 200 W under Ar flow 20 sccm "
                        "and N2 flow 5 sccm at a working pressure of 0.5 Pa. The substrate temperature was 300 C. "
                        "The film thickness was 200 nm and deposition time 30 min."
                    ),
                    "order": 0,
                    "locator": "jats:sec:2",
                }
            ],
        )
        self.db.save_tables(
            "doc1",
            [{"label": "Table 1", "caption": "Deposition conditions", "text": "Power | 200 W", "locator": "jats:table:1", "order": 0}],
        )

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_extract_paper_persists_recipe_target_evidence_and_run(self):
        pipeline = ExtractionPipeline(self.db, output_dir=Path(self.tempdir.name) / "extractions")
        result = pipeline.extract_paper(self.paper_key)
        self.assertEqual(result.status, "extracted")
        self.assertEqual(len(result.recipes), 1)
        self.assertGreaterEqual(len(result.evidence), 8)
        self.assertEqual(self.db.count_recipes(), 1)
        self.assertEqual(self.db.count_evidence(), len(result.evidence))
        self.assertEqual(self.db.count_samples(), 1)
        self.assertTrue(result.recipes[0].sample_id.startswith(self.paper_key))
        targets = self.db.list_targets(f"{self.paper_key}:R1")
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["target_power_w"], 200)
        runs = self.db.list_extraction_runs()
        self.assertEqual(runs[0]["status"], "extracted")
        self.assertEqual(runs[0]["model"], "rule-based")

    def test_batch_is_idempotent_for_extracted_papers(self):
        pipeline = ExtractionPipeline(self.db, output_dir=Path(self.tempdir.name) / "extractions")
        first = pipeline.extract_batch()
        self.assertEqual(first["extracted"], 1)
        second = pipeline.extract_batch()
        self.assertEqual(second["attempted"], 0)
        self.assertEqual(self.db.count_recipes(), 1)

    def test_force_failure_keeps_old_data_and_logs_rollback(self):
        output = Path(self.tempdir.name) / "extractions"
        pipeline = ExtractionPipeline(self.db, output_dir=output)
        pipeline.extract_paper(self.paper_key)
        old_json = next(output.glob("*.json"))
        old_text = old_json.read_text(encoding="utf-8")
        self.db.conn.execute("DELETE FROM document_sections")
        self.db.conn.commit()
        result = pipeline.extract_paper(self.paper_key, force=True)
        self.assertEqual(result.status, "no_deposition_section")
        self.assertEqual(self.db.count_recipes(), 1)
        self.assertTrue(old_json.exists())
        self.assertEqual(old_json.read_text(encoding="utf-8"), old_text)
        self.assertFalse(list(output.glob("*.json.tmp")))
        statuses = [run["status"] for run in self.db.list_extraction_runs()]
        self.assertIn("rolled_back", statuses)


if __name__ == "__main__":
    unittest.main()
