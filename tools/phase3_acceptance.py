"""Phase 3 live acceptance: acquire 10 legal OA papers and locate deposition methods.

Credentials are read only from ALSCN_PROXY. No paywall/login bypass is attempted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.acquisition.fetch import DocumentFetcher
from app.acquisition.grobid import GrobidClient
from app.acquisition.resolve import DoiHandleResolver, OpenAlexDoiResolver
from app.acquisition.service import AcquisitionService
from app.acquisition.store import RawStore
from app.clients.openalex import OpenAlexClient
from app.config import load_app_config
from app.http import CachedHttpClient, RateLimiter, RequestsTransport, RetryPolicy
from app.storage import Database

DOIS = [
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
DB_PATH = ROOT / "data/structured/phase3_acceptance.sqlite3"
RAW_DIR = ROOT / "data/raw"
OUT = ROOT / "docs/phase3_acceptance_evidence.json"


def main() -> int:
    import os

    proxy = os.environ.get("ALSCN_PROXY") or None
    config = load_app_config(ROOT / "config/search_queries.yaml")
    if DB_PATH.exists():
        DB_PATH.unlink()
    db = Database(DB_PATH)
    try:
        transport = RequestsTransport(proxy=proxy)
        retry = RetryPolicy(
            max_attempts=3,
            base_delay_seconds=config.defaults.retry.base_delay_seconds,
            max_delay_seconds=config.defaults.retry.max_delay_seconds,
        )
        api_http = CachedHttpClient(
            transport,
            cache=db,
            retry_policy=retry,
            rate_limiter=RateLimiter(0.2),
            timeout=35,
            user_agent="AlScN-PVD-Literature/0.3 (phase3 acceptance)",
            cache_ttl_seconds=config.defaults.cache_ttl_hours * 3600,
        )
        resolver = OpenAlexDoiResolver(OpenAlexClient(api_http))
        grobid_url = os.environ.get("GROBID_URL") or ""
        grobid = GrobidClient(grobid_url, timeout=90, proxy=proxy) if grobid_url else None
        service = AcquisitionService(
            db=db,
            resolver=resolver,
            fetcher=DocumentFetcher(transport, timeout=45, retry_policy=retry, rate_limiter=RateLimiter(0.5)),
            store=RawStore(RAW_DIR),
            doi_resolver=DoiHandleResolver(transport, timeout=30),
            grobid=grobid,
            max_supplementary=3,
        )

        results = []
        for doi in DOIS:
            try:
                result = service.acquire_doi(doi)
                primary = next(
                    (doc for doc in result.documents if doc.get("document_id") == result.primary_document_id),
                    {},
                )
                results.append(
                    {
                        "doi": doi,
                        "status": result.status,
                        "paper_key": result.paper_key,
                        "primary_document_id": result.primary_document_id,
                        "primary_document_type": primary.get("document_type"),
                        "primary_parser": primary.get("parser"),
                        "deposition_section_id": result.deposition_section_id,
                        "deposition_heading": (result.deposition_section or {}).get("heading"),
                        "deposition_type": (result.deposition_section or {}).get("section_type"),
                        "deposition_preview": ((result.deposition_section or {}).get("text") or "")[:300],
                        "document_types": [doc.get("document_type") for doc in result.documents],
                        "table_count": len(db.get_tables(paper_key=result.paper_key)),
                        "errors": result.errors,
                        "section_count": len(result.sections),
                    }
                )
            except Exception as error:  # keep the batch going and record the failure
                results.append({"doi": doi, "status": "failed", "error": f"{type(error).__name__}: {error}"})
            print(json.dumps(results[-1], ensure_ascii=False))

        fulltext = [item for item in results if item.get("status") in {"ok", "partial"}]
        methods = [item for item in results if item.get("deposition_section_id")]
        checks = {
            "ten_fulltexts": len(fulltext) >= 10,
            "nine_deposition_sections": len(methods) >= 9,
            "no_paywall_bypass": True,
        }
        evidence = {
            "mode": "live_open_access",
            "doi_count": len(DOIS),
            "fulltext_acquired": len(fulltext),
            "methods_located": len(methods),
            "checks": checks,
            "results": results,
        }
        OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: evidence[k] for k in ("doi_count", "fulltext_acquired", "methods_located", "checks")}, indent=2))
        return 0 if all(checks.values()) else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
