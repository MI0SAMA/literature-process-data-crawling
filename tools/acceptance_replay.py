"""Offline acceptance replay for Phase 2.

The local network proxy returns HTTP 407 for direct API calls. The three
OpenAlex result pages in tests/fixtures/live/recorded_openalex.json were
captured through the harness web_fetch path (public metadata only). This
script runs the real OpenAlexClient, pipeline, SQLite cache and screening
code against those recorded responses, then re-runs fully offline from the
cache and evaluates the 20-paper gold set.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.clients.openalex import OpenAlexClient
from app.config import load_app_config
from app.discovery.pipeline import DiscoveryPipeline
from app.discovery.query_plan import build_query_plan
from app.http import CachedHttpClient, HttpResponse, MemoryResponseCache
from app.models import QueryPlan, QuerySpec
from app.reporting import evaluate_gold, evaluate_screening_labels, write_run_outputs
from app.storage import Database

FIXTURE = ROOT / "tests/fixtures/live/recorded_openalex.json"
GOLD = ROOT / "tests/gold_standard/known_alscn_pvd_papers.csv"
SCREENING_LABELS = ROOT / "tests/gold_standard/screening_labels.csv"
DB_PATH = ROOT / "data/structured/acceptance_phase2.sqlite3"
OUT_DIR = ROOT / "data/structured/acceptance_phase2"
QUERIES = ("AlScN sputtering", "AlScN thin films", "AlScN")


class RecordedOpenAlexTransport:
    def __init__(self, fixture_path: Path):
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.queries = payload["queries"]
        self.calls = []

    def request(self, method, url, headers, timeout, body=None):
        from urllib.parse import parse_qs, urlparse

        params = parse_qs(urlparse(url).query)
        query = params.get("search", [""])[0]
        page = int(params.get("page", ["1"])[0])
        self.calls.append((query, page))
        if query not in self.queries or page != 1:
            records = []
        else:
            slug = "".join(ch if ch.isalnum() else "_" for ch in query.lower())
            records = [
                {
                    "id": f"https://openalex.org/recorded/{slug}_{index}",
                    "doi": f"https://doi.org/{item['doi']}" if item.get("doi") else None,
                    "title": item["title"],
                    "publication_year": item.get("year"),
                    "type": item.get("type"),
                }
                for index, item in enumerate(self.queries[query], start=1)
            ]
        payload = {
            "meta": {"count": len(records), "page": page, "per_page": 25},
            "results": records,
        }
        return HttpResponse(200, {}, json.dumps(payload).encode("utf-8"), url)


class ForbiddenTransport:
    def request(self, method, url, headers, timeout, body=None):
        raise AssertionError(f"network was attempted in offline mode: {url}")


def build_acceptance_plan(config) -> QueryPlan:
    specs = []
    for index, query in enumerate(QUERIES, start=1):
        plan = build_query_plan(
            config,
            query=query,
            databases=("openalex",),
            max_results=25,
            from_year=2005,
            page_size=25,
        )
        spec = plan.specs[0]
        spec = QuerySpec(
            query_id=f"accept_{index:02d}",
            query=spec.query,
            databases=spec.databases,
            max_results=spec.max_results,
            from_year=spec.from_year,
            to_year=spec.to_year,
            page_size=spec.page_size,
        )
        specs.append(spec)
    return QueryPlan(plan_id="acceptance_phase2_recorded_v1", generated_at="2026-10-29T00:00:00+00:00", specs=specs)


def main() -> int:
    config = load_app_config(ROOT / "config/search_queries.yaml")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    db = Database(DB_PATH)
    try:
        plan = build_acceptance_plan(config)
        transport = RecordedOpenAlexTransport(FIXTURE)
        http = CachedHttpClient(
            transport,
            cache=db,
            cache_ttl_seconds=365 * 24 * 3600,
            sleep=lambda _: None,
        )
        client = OpenAlexClient(http)
        pipeline = DiscoveryPipeline(
            config=config,
            db=db,
            clients={"openalex": client},
            output_dir=OUT_DIR,
        )
        first = pipeline.run(plan)

        offline_http = CachedHttpClient(
            ForbiddenTransport(),
            cache=db,
            offline=True,
            sleep=lambda _: None,
        )
        offline_client = OpenAlexClient(offline_http)
        offline_pipeline = DiscoveryPipeline(
            config=config,
            db=db,
            clients={"openalex": offline_client},
            output_dir=OUT_DIR,
        )
        second = offline_pipeline.run(plan)
        gold = evaluate_gold(db, GOLD)
        screening = evaluate_screening_labels(db, SCREENING_LABELS)
        outputs = write_run_outputs(db, second.run_id, OUT_DIR / second.run_id)
        checks = {
            "search_run_ok": first.status == "ok" and second.status == "ok",
            "recorded_queries_executed": len(transport.calls) == 3,
            "cache_entries_written": db.count_cache() == 3,
            "offline_replay_completed": True,
            "papers_out_match_db": second.papers_out == db.count_papers(),
            "source_records_preserved": db.count_sources() == 75,
            "gold_recall_at_least_18_of_20": gold["found_count"] >= 18,
            "no_duplicate_doi": len(db.list_duplicate_dois()) == 0,
            "screening_labels_accuracy_at_least_0_9": screening["accuracy"] >= 0.9,
        }

        result = {
            "mode": "recorded_openalex_metadata_replay",
            "live_api_status": "blocked_by_local_proxy_http_407",
            "recorded_queries": list(QUERIES),
            "recorded_records": sum(len(v) for v in transport.queries.values()),
            "network_calls_first_run": len(transport.calls),
            "cache_entries": db.count_cache(),
            "first_run": first.to_dict(),
            "offline_run": second.to_dict(),
            "gold": gold,
            "screening_labels": screening,
            "duplicate_doi_count": len(db.list_duplicate_dois()),
            "papers": db.count_papers(),
            "sources": db.count_sources(),
            "checks": checks,
            "outputs": outputs,
        }
        output_path = ROOT / "docs/phase2_acceptance_evidence.json"
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all(checks.values()) else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
