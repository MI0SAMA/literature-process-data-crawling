"""Harvest legal arXiv OA PDFs for AlScN/ScAlN PVD papers.

Adds papers/documents/sections to data/structured/corpus.sqlite3. Stops at
TARGET successful full texts. arXiv is a preprint server; version is recorded
as preprint. No publisher paywall is touched.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.acquisition.arxiv import download_pdf, fetch_entries, is_relevant_entry
from app.acquisition.pdf import parse_pdf
from app.acquisition.sections import locate_deposition_sections
from app.acquisition.store import RawStore
from app.discovery.dedup import Deduplicator
from app.models import utc_now_iso
from app.storage import Database

CORPUS_DB = ROOT / "data/structured/corpus.sqlite3"
RAW_DIR = ROOT / "data/raw"
TARGET = 50
TERMS = [
    "AlScN",
    "ScAlN",
    "scandium aluminum nitride",
    "aluminum scandium nitride",
    "Al1-xScxN",
    "ScxAl1-xN",
]


def main() -> int:
    db = Database(CORPUS_DB)
    store = RawStore(RAW_DIR)
    try:
        existing = {
            row["doi"].lower()
            for row in db.conn.execute("SELECT doi FROM acquisition_runs WHERE status IN ('ok','partial')")
            if row["doi"]
        }
        successes = int(
            db.conn.execute(
                "SELECT COUNT(DISTINCT paper_key) FROM acquisition_runs WHERE status IN ('ok','partial')"
            ).fetchone()[0]
        )
        entries = fetch_entries(TERMS, max_results=100)
        relevant = [entry for entry in entries if is_relevant_entry(entry, broad=True)]
        print(json.dumps({"feed_entries": len(entries), "relevant": len(relevant), "already": successes}), flush=True)
        attempted = 0
        results = []
        for entry in relevant:
            if successes >= TARGET:
                break
            if entry.doi.lower() in existing:
                continue
            attempted += 1
            try:
                pdf = download_pdf(entry)
                parsed = parse_pdf(pdf)
            except Exception as error:
                record = {"arxiv_id": entry.arxiv_id, "status": "download_or_parse_failed", "error": f"{type(error).__name__}: {error}"}
                results.append(record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
                time.sleep(1)
                continue

            selected = locate_deposition_sections(parsed.sections, document_title=parsed.title)
            paper = entry.to_paper_record()
            cluster = Deduplicator().deduplicate([paper]).clusters[0]
            paper_key = db.save_cluster(cluster)
            stored = store.save(paper_key, pdf, filename="document.pdf", source_url=entry.pdf_url)
            document_id = "doc_" + hashlib.sha256(f"{paper_key}|pdf|{entry.pdf_url}".encode()).hexdigest()[:24]
            db.save_document(
                {
                    "document_id": document_id,
                    "paper_key": paper_key,
                    "doi": paper.doi,
                    "document_type": "pdf",
                    "version": "preprint",
                    "source_url": entry.pdf_url,
                    "final_url": entry.pdf_url,
                    "access_status": "open_access",
                    "http_status": 200,
                    "content_type": "application/pdf",
                    "file_path": stored.path,
                    "file_hash": stored.sha256,
                    "byte_size": stored.byte_size,
                    "fetched_at": utc_now_iso(),
                    "parser": parsed.parser,
                    "parser_version": parsed.parser_version,
                    "license": "arxiv",
                    "is_primary": 0,
                    "parent_document_id": None,
                    "error_kind": None,
                    "error_message": None,
                }
            )
            db.set_primary_document(paper_key, document_id)
            db.save_sections(document_id, [section.to_dict() for section in parsed.sections])
            deposition_id = None
            if selected:
                rows = db.get_sections(document_id=document_id)
                match = next((row for row in rows if row["locator"] == selected[0].locator), None)
                deposition_id = match["section_id"] if match else None
            status = "ok" if deposition_id else "partial"
            db.save_acquisition_run(
                {
                    "acquisition_id": f"acq_arxiv_{hashlib.sha256(entry.arxiv_id.encode()).hexdigest()[:16]}",
                    "paper_key": paper_key,
                    "doi": paper.doi,
                    "started_at": utc_now_iso(),
                    "finished_at": utc_now_iso(),
                    "status": status,
                    "primary_document_id": document_id,
                    "deposition_section_id": deposition_id,
                    "metadata": {
                        "source": "arxiv",
                        "arxiv_id": entry.arxiv_id,
                        "version": "preprint",
                        "deposition_heading": selected[0].heading if selected else None,
                        "sections": len(parsed.sections),
                    },
                }
            )
            successes += 1
            existing.add(entry.doi.lower())
            record = {
                "arxiv_id": entry.arxiv_id,
                "doi": entry.doi,
                "status": status,
                "deposition_heading": selected[0].heading if selected else None,
                "sections": len(parsed.sections),
            }
            results.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            time.sleep(1)

        evidence = {
            "source": "arxiv",
            "target": TARGET,
            "successes": successes,
            "attempted": attempted,
            "feed_entries": len(entries),
            "relevant": len(relevant),
            "results": results,
            "documents": db.count_documents(),
            "sections": db.count_sections(),
        }
        (ROOT / "docs/arxiv_manifest.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: evidence[k] for k in ("target", "successes", "attempted", "documents", "sections")}, indent=2))
        return 0 if successes >= TARGET else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
