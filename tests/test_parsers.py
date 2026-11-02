"""Tests for JATS, HTML and PDF document parsers."""

from __future__ import annotations

import io
import unittest
from pathlib import Path

from app.acquisition.html import parse_html
from app.acquisition.jats import parse_jats
from app.acquisition.pdf import parse_pdf
from app.acquisition.sections import locate_deposition_sections

FIXTURES = Path(__file__).parent / "fixtures"


class JatsParserTests(unittest.TestCase):
    def setUp(self):
        self.doc = parse_jats((FIXTURES / "jats_sample.xml").read_bytes())

    def test_title_and_sections(self):
        self.assertIn("Ferroelectric AlScN", self.doc.title)
        headings = [section.heading for section in self.doc.sections]
        self.assertIn("Experimental", headings)
        self.assertIn("Results and Discussion", headings)
        self.assertIn("Supplementary Methods", headings)

    def test_methods_text_and_tables(self):
        methods = next(section for section in self.doc.sections if section.heading == "Experimental")
        self.assertIn("200 W", methods.text)
        self.assertEqual(len(self.doc.tables), 1)
        self.assertIn("0.5 Pa", self.doc.tables[0].text)

    def test_references_and_supplementary(self):
        self.assertEqual(len(self.doc.references), 1)
        self.assertIn("Fichtner", self.doc.references[0])
        self.assertIn("https://example.org/jats-si.pdf", self.doc.supplementary_urls)

    def test_deposition_locator(self):
        selected = locate_deposition_sections(self.doc.sections)
        self.assertGreaterEqual(len(selected), 1)
        self.assertIn("Experimental", {item.heading for item in selected})


class HtmlParserTests(unittest.TestCase):
    def setUp(self):
        self.doc = parse_html(
            (FIXTURES / "html_sample.html").read_bytes(),
            base_url="https://example.org/paper",
        )

    def test_title_and_sections(self):
        self.assertIn("Ferroelectric AlScN", self.doc.title)
        headings = [section.heading for section in self.doc.sections]
        self.assertIn("Experimental", headings)
        self.assertIn("Results and Discussion", headings)

    def test_methods_text_and_table(self):
        methods = next(section for section in self.doc.sections if section.heading == "Experimental")
        self.assertIn("200 W", methods.text)
        self.assertEqual(len(self.doc.tables), 1)
        self.assertIn("0.5 Pa", self.doc.tables[0].text)

    def test_references_are_separated(self):
        self.assertGreaterEqual(len(self.doc.references), 1)
        self.assertIn("Fichtner", self.doc.references[0])

    def test_supplementary_link_is_absolute(self):
        self.assertIn("https://example.org/supporting/si.pdf", self.doc.supplementary_urls)

    def test_deposition_locator(self):
        selected = locate_deposition_sections(self.doc.sections)
        self.assertEqual(selected[0].heading, "Experimental")

    def test_abstract_only_landing_page_is_marked(self):
        page = b"<html><body><article><h1>AlScN sputtering</h1><h2>Abstract</h2><p>Short abstract.</p><h2>Fingerprint</h2><p>Keywords and metadata.</p></article></body></html>"
        doc = parse_html(page, base_url="https://repository.example.org/item")
        self.assertEqual(doc.metadata["content_scope"], "abstract_only")


class PdfParserTests(unittest.TestCase):
    def test_pdf_heading_heuristic(self):
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer)
        pdf.drawString(72, 760, "Ferroelectric AlScN thin films")
        pdf.drawString(72, 720, "Experimental")
        pdf.drawString(72, 700, "AlScN films were deposited by reactive magnetron sputtering.")
        pdf.drawString(72, 680, "The target power was 200 W and the pressure was 0.5 Pa.")
        pdf.showPage()
        pdf.drawString(72, 760, "Results and Discussion")
        pdf.drawString(72, 740, "The films showed ferroelectric switching.")
        pdf.save()

        doc = parse_pdf(buffer.getvalue())
        headings = [section.heading for section in doc.sections]
        self.assertIn("Experimental", headings)
        self.assertIn("Results and Discussion", headings)
        experimental = next(section for section in doc.sections if section.heading == "Experimental")
        self.assertEqual(experimental.page_start, 1)
        selected = locate_deposition_sections(doc.sections)
        self.assertEqual(selected[0].heading, "Experimental")

    def test_invalid_pdf_raises(self):
        with self.assertRaises(Exception):
            parse_pdf(b"not a pdf")

    def test_references_do_not_create_deposition_sections(self):
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer)
        pdf.drawString(72, 760, "Experimental")
        pdf.drawString(72, 740, "AlScN was deposited by magnetron sputtering at 200 W and 0.5 Pa.")
        pdf.drawString(72, 700, "References")
        pdf.drawString(72, 680, "Deposition of aluminum scandium nitride films, J. Appl. Phys. 2020.")
        pdf.save()

        doc = parse_pdf(buffer.getvalue())
        self.assertTrue(any(section.section_type == "references" for section in doc.sections))
        selected = locate_deposition_sections(doc.sections, document_title="AlScN study")
        self.assertEqual(selected[0].heading, "Experimental")


if __name__ == "__main__":
    unittest.main()
