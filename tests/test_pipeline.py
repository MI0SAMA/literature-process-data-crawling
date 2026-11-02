"""Tests for the end-to-end discovery pipeline."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from app.config import load_app_config
from app.discovery.pipeline import DiscoveryPipeline
from app.discovery.query_plan import build_query_plan
from app.http import ApiError
from app.models import Author, SearchPage
from app.storage import Database
from tests.helpers import make_record


class FakeClient:
    def __init__(self, source: str, pages):
        self.source = source
        self.pages = pages
        self.calls = []

    def search(self, spec, *, page=1):
        self.calls.append((spec.query, page))
        if page > len(self.pages):
            return SearchPage([], 0, page, None)
        return self.pages[page - 1]


class FailingClient:
    def __init__(self, source: str):
        self.source = source
        self.calls = 0

    def search(self, spec, *, page=1):
        self.calls += 1
        raise ApiError("proxy blocked", kind="network", url="https://api.test", retryable=True)


def page(records, next_page=None, total=None):
    return SearchPage(records, total if total is not None else len(records), 1, next_page)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tempdir.name) / "test.sqlite3")
        self.config = load_app_config("config/search_queries.yaml")
        self.plan = build_query_plan(
            self.config,
            query="AlScN sputtering",
            databases=("openalex", "crossref"),
            max_results=5,
            page_size=5,
        )
        self.output = Path(self.tempdir.name) / "out"

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_deduplicates_screens_and_writes_outputs(self):
        shared_doi = "10.1063/1.5084945"
        openalex = FakeClient(
            "openalex",
            [
                page(
                    [
                        make_record(source="openalex", source_id="W1", doi=shared_doi),
                        make_record(
                            source="openalex",
                            source_id="W2",
                            doi=None,
                            title="BaTiO3 thin films by magnetron sputtering",
                            abstract="BaTiO3 films were sputtered.",
                            authors=[Author(name="A. One", family="One")],
                        ),
                    ],
                    next_page=2,
                ),
                page(
                    [
                        make_record(
                            source="openalex",
                            source_id="W3",
                            doi="10.1234/unique",
                            title="AlScN films by pulsed DC sputtering",
                            abstract="AlScN films were deposited by pulsed DC magnetron sputtering.",
                        )
                    ],
                    next_page=None,
                ),
            ],
        )
        crossref = FakeClient(
            "crossref",
            [
                page(
                    [
                        make_record(
                            source="crossref",
                            source_id="cr1",
                            doi=shared_doi,
                            title="AlScN: A III-V semiconductor based ferroelectric",
                        )
                    ],
                    next_page=None,
                )
            ],
        )
        pipeline = DiscoveryPipeline(
            config=self.config,
            db=self.db,
            clients={"openalex": openalex, "crossref": crossref},
            output_dir=self.output,
        )
        summary = pipeline.run(self.plan)

        self.assertEqual(summary.status, "ok")
        self.assertEqual(summary.records_in, 4)
        self.assertEqual(summary.papers_out, 3)
        self.assertEqual(summary.duplicates_removed, 1)
        self.assertEqual(summary.likely, 2)
        self.assertEqual(summary.not_relevant, 1)
        self.assertEqual(self.db.count_papers(), 3)
        self.assertEqual(self.db.count_sources(), 4)
        for filename in ("candidates.csv", "candidates.json", "review_queue.csv", "search_log.json", "run_summary.json"):
            self.assertTrue((self.output / summary.run_id / filename).is_file(), filename)

        with (self.output / summary.run_id / "candidates.csv").open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)
        self.assertIn("likely_AlScN_PVD", {row["final_label"] for row in rows})

    def test_rerun_is_idempotent_for_papers_and_sources(self):
        records = [
            make_record(source="openalex", source_id="W1", doi="10.1063/1.5084945"),
            make_record(source="openalex", source_id="W2", doi="10.1234/other", title="ScAlN sputtering", abstract="ScAlN was sputtered."),
        ]
        clients = {
            "openalex": FakeClient("openalex", [page(records)]),
            "crossref": FakeClient("crossref", [page([], None)]),
        }
        pipeline = DiscoveryPipeline(config=self.config, db=self.db, clients=clients, output_dir=self.output)
        first = pipeline.run(self.plan)
        second = pipeline.run(self.plan)
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(self.db.count_papers(), 2)
        self.assertEqual(self.db.count_sources(), 2)

    def test_one_failing_source_produces_partial_run(self):
        clients = {
            "openalex": FailingClient("openalex"),
            "crossref": FakeClient(
                "crossref",
                [page([make_record(source="crossref", source_id="cr1", doi="10.1063/1.5084945")])],
            ),
        }
        pipeline = DiscoveryPipeline(config=self.config, db=self.db, clients=clients, output_dir=self.output)
        summary = pipeline.run(self.plan)
        self.assertEqual(summary.status, "partial")
        self.assertEqual(len(summary.errors), 1)
        self.assertEqual(summary.errors[0]["kind"], "network")
        self.assertEqual(summary.papers_out, 1)
        executions = self.db.list_query_executions(summary.run_id)
        self.assertEqual({row["status"] for row in executions}, {"error", "ok"})

    def test_all_sources_failing_is_failed(self):
        clients = {
            "openalex": FailingClient("openalex"),
            "crossref": FailingClient("crossref"),
        }
        pipeline = DiscoveryPipeline(config=self.config, db=self.db, clients=clients, output_dir=self.output)
        summary = pipeline.run(self.plan)
        self.assertEqual(summary.status, "failed")
        self.assertEqual(summary.papers_out, 0)
        self.assertEqual(self.db.count_papers(), 0)

    def test_pagination_respects_max_results(self):
        first = [
            make_record(source="openalex", source_id=f"W{i}", doi=f"10.1234/{i}", title=f"AlScN sputtering {i}")
            for i in range(1, 4)
        ]
        second = [
            make_record(source="openalex", source_id="W4", doi="10.1234/4", title="AlScN sputtering 4")
        ]
        clients = {
            "openalex": FakeClient("openalex", [page(first, next_page=2), page(second)]),
            "crossref": FakeClient("crossref", [page([], None)]),
        }
        pipeline = DiscoveryPipeline(config=self.config, db=self.db, clients=clients, output_dir=self.output)
        summary = pipeline.run(self.plan)
        self.assertEqual(summary.records_in, 4)
        self.assertEqual(len(clients["openalex"].calls), 2)
        self.assertEqual(len(clients["crossref"].calls), 1)


if __name__ == "__main__":
    unittest.main()
