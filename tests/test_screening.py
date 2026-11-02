"""Tests for deterministic title/abstract screening and AI combination."""

from __future__ import annotations

import unittest

from app.config import load_app_config
from app.discovery.screening import RuleScreener, combine_rule_and_ai
from app.models import Author
from tests.helpers import make_record


def paper(title: str, abstract: str | None = None, item_type: str = "article"):
    return make_record(
        title=title,
        abstract=abstract,
        item_type=item_type,
        authors=[Author(name="Test Author", family="Author")],
    )


class RuleScreenerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.screener = RuleScreener(load_app_config("config/search_queries.yaml").screening)

    def test_material_and_sputtering_is_likely(self):
        result = self.screener.screen(paper("AlScN thin films grown by reactive magnetron sputtering"))
        self.assertEqual(result.label, "likely_AlScN_PVD")
        self.assertTrue(result.eligible_for_confirmation)
        self.assertGreaterEqual(result.confidence, 0.8)
        self.assertIn("alscn", " ".join(result.matched_material).lower())

    def test_sc_al_n_notation_is_recognized(self):
        result = self.screener.screen(paper("ScxAl1-xN thin films deposited by RF sputtering"))
        self.assertEqual(result.label, "likely_AlScN_PVD")

    def test_scandium_aluminum_nitride_spelling_is_recognized(self):
        result = self.screener.screen(paper("Scandium aluminum nitride films prepared by magnetron sputtering"))
        self.assertEqual(result.label, "likely_AlScN_PVD")

    def test_sc_doped_aln_is_recognized(self):
        result = self.screener.screen(paper("Sc-doped AlN thin films grown by reactive sputtering"))
        self.assertEqual(result.label, "likely_AlScN_PVD")

    def test_non_alscn_is_not_relevant(self):
        result = self.screener.screen(paper("BaTiO3 thin films grown by magnetron sputtering"))
        self.assertEqual(result.label, "not_relevant")
        self.assertFalse(result.eligible_for_confirmation)

    def test_alscn_without_deposition_method_is_unclear(self):
        result = self.screener.screen(paper("AlScN thin films for piezoelectric MEMS"))
        self.assertEqual(result.label, "unclear")
        self.assertIn("deposition_method_not_found", result.flags)

    def test_alscn_mocvd_only_is_not_promoted(self):
        result = self.screener.screen(paper("AlScN thin films grown by MOCVD"))
        self.assertEqual(result.label, "unclear")
        self.assertIn("non_pvd_method", result.flags)

    def test_review_is_reference_only(self):
        result = self.screener.screen(paper("Ferroelectric AlScN: a review", item_type="review"))
        self.assertEqual(result.label, "not_relevant")
        self.assertIn("review_article", result.flags)

    def test_review_title_without_review_type_is_caught(self):
        result = self.screener.screen(paper("Review of ferroelectric AlScN thin films", item_type="journal-article"))
        self.assertEqual(result.label, "not_relevant")
        self.assertIn("review_article", result.flags)

    def test_possible_electrode_only_is_not_confirmed(self):
        result = self.screener.screen(paper("Sputtered AlScN electrodes for HfO2 capacitors"))
        self.assertEqual(result.label, "unclear")
        self.assertIn("possible_electrode_only", result.flags)
        self.assertFalse(result.eligible_for_confirmation)

    def test_pld_is_flagged_as_other_pvd(self):
        result = self.screener.screen(paper("AlScN thin films deposited by pulsed laser deposition"))
        self.assertEqual(result.label, "likely_AlScN_PVD")
        self.assertIn("other_pvd", result.flags)
        self.assertFalse(result.eligible_for_confirmation)

    def test_title_only_is_flagged(self):
        result = self.screener.screen(paper("AlScN sputtering", abstract=None))
        self.assertIn("title_only", result.flags)


class CombineWithAiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.screener = RuleScreener(load_app_config("config/search_queries.yaml").screening)

    def rule(self, title: str, item_type: str = "article"):
        return self.screener.screen(paper(title, item_type=item_type))

    def test_ai_likely_promotes_rule_unclear_when_material_present(self):
        rule = self.rule("AlScN thin films for piezoelectric MEMS")
        combined = combine_rule_and_ai(rule, "likely_AlScN_PVD", confidence=0.8)
        self.assertEqual(combined.label, "likely_AlScN_PVD")
        self.assertIn("ai_promoted", combined.flags)

    def test_ai_cannot_promote_missing_material(self):
        rule = self.rule("BaTiO3 thin films by sputtering")
        combined = combine_rule_and_ai(rule, "likely_AlScN_PVD", confidence=0.9)
        self.assertEqual(combined.label, "not_relevant")

    def test_ai_rejection_of_rule_likely_becomes_review(self):
        rule = self.rule("AlScN sputtering")
        combined = combine_rule_and_ai(rule, "not_relevant", confidence=0.9)
        self.assertEqual(combined.label, "unclear")
        self.assertTrue(combined.review_required)
        self.assertIn("ai_conflict", combined.flags)

    def test_ai_likely_cannot_override_electrode_only_flag(self):
        rule = self.rule("Sputtered AlScN electrodes for HfO2 capacitors")
        combined = combine_rule_and_ai(rule, "likely_AlScN_PVD", confidence=0.95)
        self.assertEqual(combined.label, "unclear")

    def test_ai_unclear_keeps_unclear(self):
        rule = self.rule("AlScN sputtering")
        combined = combine_rule_and_ai(rule, "unclear", confidence=0.4)
        self.assertEqual(combined.label, "unclear")


if __name__ == "__main__":
    unittest.main()
