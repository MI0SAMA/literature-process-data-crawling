"""Batch-acquire legal OA full texts into the Phase 3/4 corpus.

Selects AlScN-PVD candidates already screened as likely + OA in live14.sqlite3,
prefers arXiv/PMC/MDPI/Nature/Springer, and stops at TARGET successful full texts.
No paywall/login bypass. Network proxy is optional and read from ALSCN_PROXY.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.acquisition.fetch import DocumentFetcher
from app.acquisition.resolve import OpenAlexDoiResolver
from app.acquisition.service import AcquisitionService
from app.acquisition.store import RawStore
from app.clients.openalex import OpenAlexClient
from app.config import load_app_config
from app.http import CachedHttpClient, RateLimiter, RequestsTransport, RetryPolicy
from app.storage import Database

SOURCE_DB = ROOT / "data/structured/live14.sqlite3"
CORPUS_DB = ROOT / "data/structured/corpus.sqlite3"
RAW_DIR = ROOT / "data/raw"
TARGET = 50
MAX_ATTEMPTS = 90

PRIORITY_DOIS = [
    "10.3390/biomimetics11080519",
    "10.3390/mi13101698",
    "10.1038/s41467-025-62904-6",
    "10.1063/5.0267904",
    "10.1557/s43579-026-00960-9",
    "10.3390/mi14051065",
    "10.5772/intechopen.1015616",
    "10.1557/s43579-025-00709-w",
    "10.48550/arxiv.2512.24455",
    "10.48550/arxiv.2501.02101",
]


def candidates() -> list[str]:
    connection = sqlite3.connect(SOURCE_DB)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT p.doi, p.pdf_url, p.landing_url
        FROM papers p
        JOIN screenings s ON s.paper_key = p.paper_key
        WHERE p.is_oa = 1 AND p.doi IS NOT NULL
          AND s.final_label IN ('likely_AlScN_PVD', 'unclear')
        ORDER BY
          CASE
            WHEN p.doi LIKE '10.48550/arxiv%' THEN 0
            WHEN p.doi LIKE '10.3390%' THEN 1
            WHEN p.doi LIKE '10.1038%' THEN 2
            WHEN p.doi LIKE '10.1063%' THEN 3
            WHEN p.doi LIKE '10.1557%' THEN 4
            WHEN p.doi LIKE '10.1088%' THEN 5
            WHEN p.doi LIKE '10.1103%' THEN 6
            ELSE 7
          END,
          p.publication_year DESC
        """
    ).fetchall()
    connection.close()
    result = list(dict.fromkeys(PRIORITY_DOIS + [row["doi"] for row in rows if row["doi"]]))
    return result


def already_acquired(db: Database) -> set[str]:
    rows = db.conn.execute(
        "SELECT doi FROM acquisition_runs WHERE status IN ('ok','partial')"
    ).fetchall()
    return {row["doi"] for row in rows if row["doi"]}


def main() -> int:
    if not SOURCE_DB.is_file():
        raise SystemExit(f"source DB not found: {SOURCE_DB}")
    config = load_app_config(ROOT / "config/search_queries.yaml")
    proxy = os.environ.get("ALSCN_PROXY") or None
    db = Database(CORPUS_DB)
    try:
        transport = RequestsTransport(proxy=proxy)
        retry = RetryPolicy(max_attempts=2, base_delay_seconds=1.0, max_delay_seconds=4.0)
        api_http = CachedHttpClient(
            transport,
            cache=db,
            retry_policy=retry,
            rate_limiter=RateLimiter(0.15),
            timeout=30,
            user_agent="AlScN-PVD-Literature/0.4 (batch OA acquisition)",
            cache_ttl_seconds=config.defaults.cache_ttl_hours * 3600,
        )
        service = AcquisitionService(
            db=db,
            resolver=OpenAlexDoiResolver(OpenAlexClient(api_http)),
            fetcher=DocumentFetcher(
                transport,
                timeout=35,
                retry_policy=retry,
                rate_limiter=RateLimiter(0.25),
            ),
            store=RawStore(RAW_DIR),
            doi_resolver=None,
            max_supplementary=1,
        )
        done = already_acquired(db)
        successes = len(done)
        attempts = 0
        results = []
        for doi in candidates():
            if successes >= TARGET or attempts >= MAX_ATTEMPTS:
                break
            if doi in done:
                continue
            attempts += 1
            try:
                result = service.acquire_doi(doi)
                record = {
                    "doi": doi,
                    "status": result.status,
                    "deposition_heading": (result.deposition_section or {}).get("heading"),
                    "document_types": [doc.get("document_type") for doc in result.documents],
                    "error_kinds": [error.get("kind") for error in result.errors],
                }
                if result.status in {"ok", "partial"}:
                    successes += 1
                results.append(record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
            except Exception as error:
                results.append({"doi": doi, "status": "exception", "error": f"{type(error).__name__}: {error}"})
                print(json.dumps(results[-1], ensure_ascii=False), flush=True)
        evidence = {
            "target": TARGET,
            "successes": successes,
            "attempts": attempts,
            "results": results,
            "documents": db.count_documents(),
            "sections": db.count_sections(),
            "tables": db.count_tables(),
        }
        (ROOT / "docs/corpus_manifest.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps({k: evidence[k] for k in ("target", "successes", "attempts", "documents", "sections", "tables")}, indent=2))
        return 0 if successes >= TARGET else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
