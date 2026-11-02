"""Smoke tests for the CLI surface."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from app.cli import main


class CliTests(unittest.TestCase):
    def test_help_exits_zero(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as context:
                main(["--help"])
        self.assertEqual(context.exception.code, 0)
        self.assertIn("search", output.getvalue())

    def test_init_db_creates_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db = Path(tempdir) / "phase2.sqlite3"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["init-db", "--db", str(db)])
            self.assertEqual(code, 0)
            self.assertTrue(db.is_file())
            self.assertIn("papers", output.getvalue())

    def test_report_without_run_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db = Path(tempdir) / "phase2.sqlite3"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(["report", "--db", str(db)])
            self.assertEqual(code, 1)
            self.assertIn("no search run", stderr.getvalue())

    def test_cache_stats(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db = Path(tempdir) / "phase2.sqlite3"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["cache-stats", "--db", str(db)])
            self.assertEqual(code, 0)
            self.assertIn("cache_entries", output.getvalue())

    def test_ingest_help(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as context:
                main(["ingest", "--help"])
        self.assertEqual(context.exception.code, 0)
        self.assertIn("--doi", output.getvalue())


if __name__ == "__main__":
    unittest.main()
