"""Tests for the SQLite Phase 2 schema and repositories."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.discovery.query_plan import build_query_plan
from app.config import load_app_config
from app.models import QueryExecution, ScreeningDecision
from app.storage import Database
from tests.helpers import make_record
from app.discovery.dedup import Deduplicator


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tempdir.name) / "test.sqlite3")
        self.db.init_schema()
        self.config = load_app_config("config/search_queries.yaml")
        self.plan = build_query_plan(self.config, query="AlScN sputtering", databases=("openalex",))
        self.run_id = self.db.start_run(self.plan)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def _cluster(self):
        records = [
            make_record(source="openalex", source_id="W1", doi="10.1063/1.5084945"),
            make_record(source="crossref", source_id="cr1", doi="10.1063/1.5084945"),
        ]
        return Deduplicator().deduplicate(records).clusters[0]

    def test_start_run_and_query_execution_are_persisted(self):
        execution = QueryExecution(
            execution_id="exec1",
            run_id=self.run_id,
            query_id="custom_001",
            database="openalex",
            query_text="AlScN sputtering",
            from_year=2005,
            to_year=None,
            requested_max=10,
            page_size=5,
            started_at="2026-10-29T00:00:00+00:00",
            finished_at="2026-10-29T00:00:01+00:00",
            pages_fetched=2,
            result_count=9,
            unique_count=8,
            status="ok",
            request_count=2,
            cache_hits=1,
            retry_count=0,
        )
        self.db.save_query_execution(execution)
        rows = self.db.list_query_executions(self.run_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result_count"], 9)
        self.assertEqual(rows[0]["status"], "ok")

    def test_saving_cluster_and_screening(self):
        cluster = self._cluster()
        key = self.db.save_cluster(cluster, self.run_id)
        self.assertEqual(key, cluster.paper_key)
        self.assertEqual(self.db.count_papers(), 1)
        self.assertEqual(self.db.count_sources(), 2)
        decision = ScreeningDecision(
            paper_key=key,
            rule_label="likely_AlScN_PVD",
            final_label="likely_AlScN_PVD",
            confidence=0.9,
            matched_material=["AlScN"],
            matched_method=["sputtering"],
            flags=[],
            reasons=["test"],
            review_required=False,
            eligible_for_confirmation=True,
            classifier="rule",
            classifier_version="phase2-rules-v1",
        )
        self.db.save_screening(decision)
        stored = self.db.get_screening(key)
        self.assertEqual(stored["final_label"], "likely_AlScN_PVD")
        self.assertEqual(stored["matched_material"], ["AlScN"])

    def test_saving_same_sources_twice_is_idempotent(self):
        cluster = self._cluster()
        self.db.save_cluster(cluster, self.run_id)
        self.db.save_cluster(cluster, self.run_id)
        self.assertEqual(self.db.count_papers(), 1)
        self.assertEqual(self.db.count_sources(), 2)

    def test_second_cluster_with_same_doi_reuses_existing_paper(self):
        cluster = self._cluster()
        key = self.db.save_cluster(cluster, self.run_id)
        other = self._cluster()
        other.paper_key = "doi:different-key"
        other_key = self.db.save_cluster(other, self.run_id)
        self.assertEqual(other_key, key)
        self.assertEqual(self.db.count_papers(), 1)

    def test_documents_sections_and_acquisition_run(self):
        document = {
            "document_id": "doc1",
            "paper_key": "doi:10.1063/1.5084945",
            "doi": "10.1063/1.5084945",
            "document_type": "jats",
            "version": "published",
            "source_url": "https://example.org/jats",
            "final_url": "https://example.org/jats",
            "access_status": "open_access",
            "http_status": 200,
            "content_type": "application/xml",
            "file_path": "data/raw/x.xml",
            "file_hash": "a" * 64,
            "byte_size": 123,
            "fetched_at": "2026-10-29T00:00:00+00:00",
            "parser": "jats",
            "parser_version": "1",
            "license": "cc-by",
            "is_primary": 0,
        }
        self.db.save_document(document)
        self.db.save_sections(
            "doc1",
            [
                {"section_type": "methods", "heading": "Experimental", "text": "AlScN was sputtered.", "order": 0, "locator": "jats:sec:0"},
                {"section_type": "references", "heading": "References", "text": "Ref.", "order": 1, "locator": "jats:sec:1", "is_reference": True},
            ],
        )
        self.db.save_tables(
            "doc1",
            [{"label": "Table 1", "caption": "Conditions", "text": "200 W | 0.5 Pa", "locator": "jats:table:1", "order": 0}],
        )
        self.db.set_primary_document("doi:10.1063/1.5084945", "doc1")
        self.assertEqual(self.db.count_documents(), 1)
        self.assertEqual(self.db.count_sections(), 2)
        self.assertEqual(self.db.count_tables(), 1)
        self.assertEqual(self.db.get_tables(document_id="doc1")[0]["caption"], "Conditions")
        self.assertTrue(self.db.list_documents("doi:10.1063/1.5084945")[0]["is_primary"])
        self.assertEqual(self.db.get_sections(document_id="doc1")[0]["heading"], "Experimental")
        self.db.save_acquisition_run(
            {
                "acquisition_id": "acq1",
                "paper_key": "doi:10.1063/1.5084945",
                "doi": "10.1063/1.5084945",
                "started_at": "2026-10-29T00:00:00+00:00",
                "finished_at": "2026-10-29T00:00:01+00:00",
                "status": "ok",
                "primary_document_id": "doc1",
                "deposition_section_id": "doc1:s0",
                "metadata": {"parser": "jats"},
            }
        )
        run = self.db.latest_acquisition("doi:10.1063/1.5084945")
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["metadata"], {"parser": "jats"})

    def test_transaction_rollback_restores_recipe(self):
        before = len(self.db.list_recipes())
        self.db.begin()
        self.db.save_recipe(
            "doi:10.1234/tx:R1",
            "doi:10.1234/tx",
            "10.1234/tx",
            "R1",
            "doi:10.1234/tx:S1",
            {"paper_doi": "10.1234/tx", "paper_title": "TX"},
        )
        self.assertEqual(len(self.db.list_recipes()), before + 1)
        self.db.rollback()
        self.assertEqual(len(self.db.list_recipes()), before)

    def test_review_action_updates_status_and_audit(self):
        self.db.save_recipe(
            "doi:10.1234/x:R1",
            "doi:10.1234/x",
            "10.1234/x",
            "R1",
            "doi:10.1234/x:S1",
            {"paper_doi": "10.1234/x", "paper_title": "T"},
        )
        result = self.db.record_review_action("doi:10.1234/x:R1", "verified", "checked evidence")
        self.assertEqual(result["old_status"], "new")
        self.assertEqual(result["new_status"], "verified")
        actions = self.db.list_review_actions("doi:10.1234/x:R1")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["note"], "checked evidence")
        self.assertEqual(self.db.list_recipes()[0]["data"]["review_status"], "verified")

    def test_resolve_review_item_records_action(self):
        self.db.add_review_item(
            {
                "review_id": "rev_test",
                "paper_key": "doi:10.1234/x",
                "record_id": "doi:10.1234/x:R1",
                "field_key": "substrate_temperature_c",
                "reason": "manual check",
                "severity": "high",
                "evidence_id": None,
                "status": "open",
            }
        )
        result = self.db.resolve_review_item("rev_test", "checked")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(self.db.conn.execute("SELECT status FROM review_queue WHERE review_id='rev_test'").fetchone()[0], "resolved")
        self.assertEqual(self.db.list_review_actions("doi:10.1234/x:R1")[0]["action"], "resolve_review")

    def test_derived_field_review_has_no_evidence_but_keeps_history(self):
        self.db.save_recipe(
            "doi:10.1234/derived:R1",
            "doi:10.1234/derived",
            "10.1234/derived",
            "R1",
            "doi:10.1234/derived:S1",
            {"paper_doi": "10.1234/derived", "paper_title": "T", "deposition_method": "sputtering"},
        )
        first = self.db.record_field_review("doi:10.1234/derived:R1", "deposition_method", "rejected", "wrong method")
        self.assertIsNone(first["evidence_id"])
        self.db.record_field_review("doi:10.1234/derived:R1", "deposition_method", "verified", "checked text")
        actions = self.db.list_review_actions("doi:10.1234/derived:R1")
        self.assertEqual([item["new_status"] for item in actions], ["rejected", "verified"])
        self.assertEqual({item["field_key"] for item in actions}, {"deposition_method"})
        result = self.db.record_review_action("doi:10.1234/derived:R1", "verified", "derived field checked")
        self.assertEqual(result["new_status"], "verified")

    def test_verification_requires_field_level_review(self):
        self.db.save_recipe(
            "doi:10.1234/z:R1",
            "doi:10.1234/z",
            "10.1234/z",
            "R1",
            "doi:10.1234/z:S1",
            {"paper_doi": "10.1234/z", "paper_title": "T", "target1_power_w": 200},
        )
        self.db.save_evidence(
            {
                "evidence_id": "doi:10.1234/z:R1:target1_power_w",
                "record_id": "doi:10.1234/z:R1",
                "entity_type": "recipe",
                "entity_id": "doi:10.1234/z:R1",
                "field_key": "target1_power_w",
                "paper_doi": "10.1234/z",
                "source_url": None,
                "source_section": "Methods",
                "page_table_figure": "p1",
                "evidence_text": "200 W",
                "raw_value": "200",
                "raw_unit": "W",
                "normalized_value": 200,
                "normalized_unit": "W",
                "evidence_status": "reported",
                "derivation_note": None,
            }
        )
        with self.assertRaises(ValueError):
            self.db.record_review_action("doi:10.1234/z:R1", "verified", "no field checks")
        self.db.record_field_review("doi:10.1234/z:R1", "target1_power_w", "verified", "checked")
        result = self.db.record_review_action("doi:10.1234/z:R1", "verified", "field checked")
        self.assertEqual(result["new_status"], "verified")

    def test_verification_blocks_missing_evidence(self):
        self.db.save_recipe(
            "doi:10.1234/y:R1",
            "doi:10.1234/y",
            "10.1234/y",
            "R1",
            "doi:10.1234/y:S1",
            {"paper_doi": "10.1234/y", "paper_title": "T", "target1_power_w": 200},
        )
        with self.assertRaises(ValueError):
            self.db.record_review_action("doi:10.1234/y:R1", "verified", "blind")

    def test_run_papers_filter(self):
        cluster = self._cluster()
        self.db.save_cluster(cluster, self.run_id)
        other_run = self.db.start_run(self.plan)
        self.assertEqual(len(self.db.list_papers(self.run_id)), 1)
        self.assertEqual(len(self.db.list_papers(other_run)), 0)
        self.assertEqual(len(self.db.list_papers()), 1)

    def test_duplicate_doi_report_is_empty(self):
        self.db.save_cluster(self._cluster(), self.run_id)
        self.assertEqual(self.db.list_duplicate_dois(), [])

    def test_cache_roundtrip(self):
        self.db.cache_put(
            "key1",
            source="openalex",
            url="https://api.test/works",
            status=200,
            body=b'{"ok": true}',
            fetched_at="2026-10-29T00:00:00+00:00",
            expires_at="2026-10-30T00:00:00+00:00",
        )
        cached = self.db.cache_get("key1")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.body, b'{"ok": true}')
        self.assertEqual(cached.status, 200)

    def test_finish_run_records_counts(self):
        self.db.finish_run(self.run_id, status="ok", record_count=4, paper_count=3)
        row = self.db.get_run(self.run_id)
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["record_count"], 4)
        self.assertEqual(row["paper_count"], 3)
        self.assertIsNotNone(row["finished_at"])


if __name__ == "__main__":
    unittest.main()
