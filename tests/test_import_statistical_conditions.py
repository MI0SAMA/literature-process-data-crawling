"""Tests for the majie Markdown import helpers."""

from __future__ import annotations

import unittest

from tools.import_statistical_conditions import parse_gas, target_power_pairs


class ImportHelperTests(unittest.TestCase):
    def test_power_range_is_one_target_not_two(self):
        pairs = target_power_pairs("Al", "100–200 W")
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["power"], "100–200")

    def test_gas_ratio_preserves_n2_and_ar(self):
        gas = parse_gas("N2:Ar=160:32 sccm")
        self.assertEqual(gas, {"N2": "160", "Ar": "32"})


if __name__ == "__main__":
    unittest.main()
