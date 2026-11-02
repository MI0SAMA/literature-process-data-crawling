"""Build a small offline cache manifest from the recorded OpenAlex fixture.

This is a convenience for reproducing the Phase 2 CLI without network:
  python tools/build_offline_fixture.py
  python -m app.cli cache-import --manifest tests/fixtures/live/cache_manifest.json
  python -m app.cli search --query "AlScN sputtering" --databases openalex \
      --max-results 25 --page-size 25 --offline --db data/structured/offline_demo.sqlite3
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
from app.discovery.query_plan import build_query_plan
from app.http import CachedHttpClient, HttpResponse, MemoryResponseCache

FIXTURE = ROOT / "tests/fixtures/live/recorded_openalex.json"
OUT_DIR = ROOT / "tests/fixtures/live"
QUERIES = ("AlScN sputtering", "AlScN thin films", "AlScN")


class RecordingBodyTransport:
    def __init__(self, body: bytes):
        self.body = body
        self.url = ""

    def request(self, method, url, headers, timeout, body=None):
        self.url = url
        return HttpResponse(200, {}, self.body, url)


def main() -> int:
    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))["queries"]
    config = load_app_config(ROOT / "config/search_queries.yaml")
    entries = []
    for query in QUERIES:
        slug = "".join(ch if ch.isalnum() else "_" for ch in query.lower())
        result = recorded[query]
        records = [
            {
                "id": f"https://openalex.org/recorded/{slug}_{index}",
                "doi": f"https://doi.org/{item['doi']}" if item.get("doi") else None,
                "title": item["title"],
                "publication_year": item.get("year"),
                "type": item.get("type"),
            }
            for index, item in enumerate(result, start=1)
        ]
        body = json.dumps({"meta": {"count": len(records), "per_page": 25}, "results": records}).encode("utf-8")
        transport = RecordingBodyTransport(body)
        http = CachedHttpClient(transport, cache=MemoryResponseCache(), sleep=lambda _: None)
        spec = build_query_plan(
            config,
            query=query,
            databases=("openalex",),
            max_results=25,
            from_year=2005,
            page_size=25,
        ).specs[0]
        OpenAlexClient(http).search(spec)
        response_file = OUT_DIR / f"openalex_{slug}_p1.json"
        response_file.write_bytes(body)
        entries.append(
            {
                "source": "openalex",
                "url": transport.url,
                "file": response_file.name,
                "status": 200,
                "expires_at": "2099-01-01T00:00:00+00:00",
            }
        )
    manifest = OUT_DIR / "cache_manifest.json"
    manifest.write_text(json.dumps({"entries": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {manifest} ({len(entries)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
