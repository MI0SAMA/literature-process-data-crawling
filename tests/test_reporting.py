"""Tests for candidate exports and gold-standard evaluation."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from app.config import load_app_config
from app.discovery.dedup import Deduplicator
from app.discovery.query_plan import build_query_plan
from app.models import ScreeningDecision
from app.reporting import evaluate_gold, evaluate_screening_labels, read_gold_csv, write_run_outputs
from app.storage import Database
from tests.helpers import make_record


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db = Database(self.root / "test.sqlite3")
        self.config = load_app_config("config/search_queries.yaml")
        self.plan = build_query_plan(self.config, query="AlScN sputtering", databases=("openalex",))
        self.run_id = self.db.start_run(self.plan)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def _add_paper(self, doi: str, title: str, label: str = "likely_AlScN_PVD"):
        cluster = Deduplicator().deduplicate(
            [make_record(source="openalex", source_id=f"W-{doi}", doi=doi, title=title, abstract="AlScN was sputtered.")]
        ).clusters[0]
        key = self.db.save_cluster(cluster, self.run_id)
        self.db.save_screening(
            ScreeningDecision(
                paper_key=key,
                rule_label=label,
                final_label=label,
                confidence=0.9,
                matched_material=["AlScN"],
                matched_method=["sputtering"],
                flags=[],
                reasons=["test"],
                review_required=label == "unclear",
                eligible_for_confirmation=label == "likely_AlScN_PVD",
                classifier="rule",
                classifier_version="phase2-rules-v1",
            )
        )
        return key

    def test_write_run_outputs(self):
        self._add_paper("10.1063/1.5084945", "AlScN sputtering")
        out = write_run_outputs(self.db, self.run_id, self.root / "out")
        for key in ("candidates_csv", "candidates_json", "review_queue_csv", "search_log_json", "run_summary_json"):
            self.assertTrue(Path(out[key]).is_file(), key)
        with Path(out["candidates_csv"]).open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["doi"], "10.1063/1.5084945")
        payload = json.loads(Path(out["candidates_json"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["count"], 1)

    def test_gold_evaluation_finds_doi_and_reports_missing(self):
        self._add_paper("10.1063/1.5084945", "AlScN sputtering")
        gold = self.root / "gold.csv"
        gold.write_text(
            "doi,title,year,category,notes\n"
            "10.1063/1.5084945,AlScN sputtering,2019,single,\n"
            "10.1002/adma.200802611,ScAlN cosputtering,2008,single,\n",
            encoding="utf-8",
        )
        result = evaluate_gold(self.db, gold)
        self.assertEqual(result["gold_count"], 2)
        self.assertEqual(result["found_count"], 1)
        self.assertEqual(result["recall"], 0.5)
        self.assertEqual(len(result["missing"]), 1)
        self.assertEqual(result["duplicate_doi_count"], 0)

    def test_gold_reader_skips_invalid_doi(self):
        gold = self.root / "gold.csv"
        gold.write_text("doi,title,year\nnot-a-doi,T,2020\n10.1063/1.5084945,Good,2019\n", encoding="utf-8")
        rows = read_gold_csv(gold)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["doi"], "10.1063/1.5084945")

    def test_screening_label_evaluation(self):
        self._add_paper("10.1063/1.5084945", "AlScN sputtering", "likely_AlScN_PVD")
        self._add_paper("10.1234/mocvd", "AlScN by MOCVD", "unclear")
        labels = self.root / "labels.csv"
        labels.write_text(
            "doi,title,expected_label\n"
            "10.1063/1.5084945,AlScN sputtering,likely_AlScN_PVD\n"
            "10.1234/mocvd,AlScN by MOCVD,unclear\n",
            encoding="utf-8",
        )
        result = evaluate_screening_labels(self.db, labels)
        self.assertEqual(result["evaluated"], 2)
        self.assertEqual(result["correct"], 2)
        self.assertEqual(result["accuracy"], 1.0)
        self.assertEqual(result["precision_likely"], 1.0)
        self.assertEqual(result["recall_likely"], 1.0)


if __name__ == "__main__":
    unittest.main()
