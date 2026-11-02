"""Tests for configuration loading and QueryPlan construction."""

from __future__ import annotations

import unittest

from app.config import load_app_config
from app.discovery.query_plan import build_query_plan


class QueryPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_app_config("config/search_queries.yaml")

    def test_default_plan_covers_material_name_variants(self):
        plan = build_query_plan(self.config)
        queries = " ".join(spec.query.lower() for spec in plan.specs)
        for term in ("alscn", "scaln", "sc-doped aln", "al1-xscxn", "scxal1-xn", "scandium aluminum nitride"):
            with self.subTest(term=term):
                self.assertIn(term, queries)

    def test_default_plan_covers_sputtering_modes(self):
        plan = build_query_plan(self.config)
        queries = " ".join(spec.query.lower() for spec in plan.specs)
        for term in ("sputtering", "magnetron", "reactive", "co-sputtering", "pulsed dc", "rf"):
            with self.subTest(term=term):
                self.assertIn(term, queries)

    def test_specs_record_database_range_and_pagination(self):
        plan = build_query_plan(self.config)
        spec = plan.specs[0]
        self.assertEqual(spec.databases, ("openalex", "crossref"))
        self.assertEqual(spec.max_results, 100)
        self.assertEqual(spec.page_size, 50)
        self.assertEqual(spec.from_year, 2005)
        self.assertIsNone(spec.to_year)

    def test_custom_query_replaces_default_plan(self):
        plan = build_query_plan(self.config, query="AlScN sputtering", max_results=25)
        self.assertEqual(len(plan.specs), 1)
        self.assertEqual(plan.specs[0].query, "AlScN sputtering")
        self.assertEqual(plan.specs[0].max_results, 25)

    def test_duplicate_queries_are_collapsed(self):
        plan = build_query_plan(self.config, query="AlScN sputtering")
        self.assertEqual(len({spec.query.casefold() for spec in plan.specs}), len(plan.specs))

    def test_plan_serializes_auditable_fields(self):
        plan = build_query_plan(self.config, query="AlScN sputtering", databases=("openalex",))
        payload = plan.to_dict()
        self.assertTrue(payload["plan_id"])
        self.assertTrue(payload["generated_at"])
        self.assertEqual(len(payload["specs"]), 1)
        spec = payload["specs"][0]
        for key in ("query_id", "query", "databases", "from_year", "to_year", "max_results", "page_size"):
            self.assertIn(key, spec)

    def test_invalid_year_range_is_rejected(self):
        with self.assertRaises(ValueError):
            build_query_plan(self.config, from_year=2025, to_year=2020)

    def test_unknown_database_is_rejected(self):
        with self.assertRaises(ValueError):
            build_query_plan(self.config, databases=("openalex", "unknown"))


if __name__ == "__main__":
    unittest.main()
