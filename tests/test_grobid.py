"""Tests for optional GROBID TEI parsing."""

from __future__ import annotations

import unittest

from app.acquisition.grobid import parse_tei


TEI = b"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader><fileDesc><titleStmt><title>AlScN sputtering study</title></titleStmt></fileDesc></teiHeader>
  <text>
    <body>
      <div>
        <head>Experimental</head>
        <p>AlScN films were deposited by reactive magnetron sputtering at 200 W and 0.5 Pa.</p>
      </div>
      <div>
        <head>Results</head>
        <p>The films were ferroelectric.</p>
      </div>
    </body>
  </text>
</TEI>
"""


class GrobidTeiTests(unittest.TestCase):
    def test_parses_tei_sections(self):
        doc = parse_tei(TEI)
        self.assertIn("AlScN", doc.title)
        headings = [section.heading for section in doc.sections]
        self.assertIn("Experimental", headings)
        self.assertEqual(doc.parser, "grobid")

    def test_invalid_tei_raises(self):
        with self.assertRaises(Exception):
            parse_tei(b"not xml")

    def test_table_keeps_empty_cell_and_caption(self):
        tei = b"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text><body><div><head>Experimental</head>
    <p>AlScN films.</p>
    <table><head>Table 1</head>
      <row><cell>Sample</cell><cell>Al power</cell><cell>Sc power</cell></row>
      <row><cell>S1</cell><cell></cell><cell>60</cell></row>
    </table>
  </div></body></text>
</TEI>
"""
        doc = parse_tei(tei)
        table = doc.tables[0]
        self.assertEqual(table.caption, "Table 1")
        self.assertIn("S1 |  | 60", table.text)


if __name__ == "__main__":
    unittest.main()
