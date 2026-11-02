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


if __name__ == "__main__":
    unittest.main()
