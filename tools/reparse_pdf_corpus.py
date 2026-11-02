"""Reparse stored PDFs and update status in both directions (ok <-> partial)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.acquisition.pdf import parse_pdf
from app.acquisition.sections import locate_deposition_sections
from app.storage import Database

DB_PATH = ROOT / "data/structured/corpus.sqlite3"


def main() -> int:
    db = Database(DB_PATH)
    try:
        rows = db.conn.execute(
            """
            SELECT a.acquisition_id, a.paper_key, a.doi, a.status, d.document_id, d.file_path
            FROM acquisition_runs a
            JOIN documents d ON d.document_id = a.primary_document_id
            WHERE d.document_type = 'pdf' AND d.file_path IS NOT NULL
            """
        ).fetchall()
        changed = 0
        upgraded = 0
        downgraded = 0
        results = []
        for row in rows:
            path = Path(row["file_path"])
            if not path.is_file():
                continue
            try:
                parsed = parse_pdf(path.read_bytes())
            except Exception as error:
                results.append({"doi": row["doi"], "status": "parse_failed", "error": str(error)})
                continue
            selected = locate_deposition_sections(parsed.sections, document_title=parsed.title)
            db.save_sections(row["document_id"], [section.to_dict() for section in parsed.sections])
            section_id = None
            if selected:
                sections = db.get_sections(document_id=row["document_id"])
                match = next((section for section in sections if section["locator"] == selected[0].locator), None)
                section_id = match["section_id"] if match else None
            new_status = "ok" if section_id else "partial"
            if new_status != row["status"]:
                changed += 1
                if new_status == "ok":
                    upgraded += 1
                else:
                    downgraded += 1
            db.conn.execute(
                "UPDATE acquisition_runs SET status=?, deposition_section_id=? WHERE acquisition_id=?",
                (new_status, section_id, row["acquisition_id"]),
            )
            db.conn.commit()
            result = {
                "doi": row["doi"],
                "old": row["status"],
                "new": new_status,
                "heading": selected[0].heading if selected else None,
                "sections": len(parsed.sections),
            }
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)

        summary = {
            "candidates": len(rows),
            "changed": changed,
            "upgraded": upgraded,
            "downgraded": downgraded,
            "with_deposition": int(
                db.conn.execute(
                    "SELECT COUNT(DISTINCT paper_key) FROM acquisition_runs WHERE deposition_section_id IS NOT NULL"
                ).fetchone()[0]
            ),
            "results": results,
        }
        (ROOT / "docs/pdf_reparse_manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: summary[k] for k in ("candidates", "changed", "upgraded", "downgraded", "with_deposition")}, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
