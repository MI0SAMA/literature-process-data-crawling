"""Command-line interface for Phase 2 discovery."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from app import __version__
from app.acquisition.fetch import DocumentFetcher
from app.acquisition.grobid import GrobidClient
from app.acquisition.resolve import DoiHandleResolver, OpenAlexDoiResolver
from app.acquisition.service import AcquisitionService
from app.acquisition.store import RawStore
from app.clients.crossref import CrossrefClient
from app.clients.openalex import OpenAlexClient
from app.config import load_app_config
from app.discovery.llm import LLMClassifier
from app.discovery.pipeline import DiscoveryPipeline
from app.discovery.query_plan import build_query_plan
from app.export.excel import ExcelExporter
from app.evaluation.metrics import extraction_metrics, metrics_markdown
from app.extraction.pipeline import ExtractionPipeline
from app.http import (
    CachedHttpClient,
    RateLimiter,
    RequestsTransport,
    RetryPolicy,
    UrllibTransport,
    cache_key,
)
from app.identifiers import normalize_doi
from app.normalize.validator import RecipeValidator, validate_all
from app.review.html import generate_review_html
from app.models import utc_now_iso
from app.reporting import evaluate_gold, evaluate_screening_labels, write_run_outputs
from app.storage import Database

DEFAULT_DB = "data/structured/alscn_pvd.sqlite3"
DEFAULT_OUT = "data/structured"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="AlScN-PVD Phase 2 literature discovery, deduplication and screening.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="run discovery queries and screen candidates")
    search.add_argument("--query", help="override the configured query plan with one query")
    search.add_argument("--max-results", type=int, default=None, help="maximum results per query per database")
    search.add_argument("--from-year", type=int, default=None)
    search.add_argument("--to-year", type=int, default=None)
    search.add_argument("--databases", default=None, help="comma-separated: openalex,crossref")
    search.add_argument("--page-size", type=int, default=None, help="API page size (1-200)")
    search.add_argument("--config", default="config/search_queries.yaml")
    search.add_argument("--db", default=DEFAULT_DB)
    search.add_argument("--out", default=DEFAULT_OUT)
    search.add_argument("--classifier", choices=("heuristic", "llm"), default="heuristic")
    search.add_argument("--offline", action="store_true", help="read only from the local API cache")
    search.add_argument("--no-cache", action="store_true", help="disable API response cache")
    search.add_argument("--cache-ttl-hours", type=float, default=None)
    search.add_argument("--timeout", type=float, default=None)
    search.add_argument("--mailto", default=None, help="contact email for OpenAlex/Crossref polite pools")
    search.add_argument("--openalex-api-key", default=None)
    search.add_argument("--proxy", default=None, help="HTTP(S) proxy URL; prefer ALSCN_PROXY env to avoid shell history")
    search.add_argument("--quiet", action="store_true")
    search.set_defaults(func=_cmd_search)

    report = subparsers.add_parser("report", help="export candidates and logs for a run")
    report.add_argument("--db", default=DEFAULT_DB)
    report.add_argument("--run-id", default=None)
    report.add_argument("--out", default=DEFAULT_OUT)
    report.set_defaults(func=_cmd_report)

    gold = subparsers.add_parser("gold-eval", help="evaluate retrieval recall against a DOI gold set")
    gold.add_argument("--db", default=DEFAULT_DB)
    gold.add_argument("--gold", default="tests/gold_standard/known_alscn_pvd_papers.csv")
    gold.add_argument("--labels", default=None, help="optional screening label CSV")
    gold.add_argument("--output", default=None)
    gold.add_argument("--min-recall", type=float, default=0.0)
    gold.set_defaults(func=_cmd_gold_eval)

    init = subparsers.add_parser("init-db", help="create the SQLite schema")
    init.add_argument("--db", default=DEFAULT_DB)
    init.set_defaults(func=_cmd_init_db)

    cache_import = subparsers.add_parser("cache-import", help="import recorded API JSON into the offline cache")
    cache_import.add_argument("--db", default=DEFAULT_DB)
    cache_import.add_argument("--manifest", required=True)
    cache_import.set_defaults(func=_cmd_cache_import)

    cache_stats = subparsers.add_parser("cache-stats", help="show API cache statistics")
    cache_stats.add_argument("--db", default=DEFAULT_DB)
    cache_stats.set_defaults(func=_cmd_cache_stats)

    ingest = subparsers.add_parser("ingest", help="acquire one legal open-access full text and locate deposition methods")
    ingest.add_argument("--doi", required=True)
    ingest.add_argument("--db", default=DEFAULT_DB)
    ingest.add_argument("--raw-dir", default="data/raw")
    ingest.add_argument("--max-si", type=int, default=5)
    ingest.add_argument("--timeout", type=float, default=45.0)
    ingest.add_argument("--mailto", default=None)
    ingest.add_argument("--openalex-api-key", default=None)
    ingest.add_argument("--proxy", default=None, help="HTTP(S) proxy URL; prefer ALSCN_PROXY env")
    ingest.add_argument("--grobid-url", default=None, help="GROBID service URL; defaults to GROBID_URL")
    ingest.add_argument("--no-doi-resolve", action="store_true", help="skip the doi.org handle HEAD request")
    ingest.add_argument("--quiet", action="store_true")
    ingest.set_defaults(func=_cmd_ingest)

    extract = subparsers.add_parser("extract", help="extract one paper's recipe/targets/evidence")
    extract.add_argument("--doi", required=True)
    extract.add_argument("--db", default=DEFAULT_DB)
    extract.add_argument("--out", default="data/structured/extractions")
    extract.add_argument("--force", action="store_true")
    extract.set_defaults(func=_cmd_extract)

    extract_batch = subparsers.add_parser("extract-batch", help="extract all papers with primary full text")
    extract_batch.add_argument("--db", default=DEFAULT_DB)
    extract_batch.add_argument("--out", default="data/structured/extractions")
    extract_batch.add_argument("--limit", type=int, default=None)
    extract_batch.add_argument("--force", action="store_true")
    extract_batch.set_defaults(func=_cmd_extract_batch)

    export = subparsers.add_parser("export", help="export structured data to the six-sheet Excel template")
    export.add_argument("--db", default=DEFAULT_DB)
    export.add_argument("--format", choices=("xlsx",), default="xlsx")
    export.add_argument("--output", default="literature.xlsx")
    export.add_argument("--mode", choices=("approved", "review", "all"), default="approved")
    export.set_defaults(func=_cmd_export)

    validate = subparsers.add_parser("validate", help="run deterministic range/evidence validation")
    validate.add_argument("--doi", default=None)
    validate.add_argument("--db", default=DEFAULT_DB)
    validate.set_defaults(func=_cmd_validate)

    metrics = subparsers.add_parser("metrics", help="write extraction/acquisition quality metrics")
    metrics.add_argument("--db", default=DEFAULT_DB)
    metrics.add_argument("--output", default="docs/extraction_metrics.json")
    metrics.add_argument("--markdown", default="docs/extraction_metrics.md")
    metrics.set_defaults(func=_cmd_metrics)

    review = subparsers.add_parser("review-html", help="generate a static evidence review page")
    review.add_argument("--db", default=DEFAULT_DB)
    review.add_argument("--output", default="docs/review.html")
    review.set_defaults(func=_cmd_review_html)

    review_set = subparsers.add_parser("review-set", help="set recipe review status with an audit trail")
    review_set.add_argument("--db", default=DEFAULT_DB)
    review_set.add_argument("--record-id", required=True)
    review_set.add_argument("--status", required=True, choices=("new", "needs_review", "verified", "rejected"))
    review_set.add_argument("--note", default="")
    review_set.set_defaults(func=_cmd_review_set)

    review_field = subparsers.add_parser("review-field", help="mark one recipe field verified/rejected with evidence")
    review_field.add_argument("--db", default=DEFAULT_DB)
    review_field.add_argument("--record-id", required=True)
    review_field.add_argument("--field-key", required=True)
    review_field.add_argument("--decision", required=True, choices=("verified", "rejected", "needs_review"))
    review_field.add_argument("--note", default="")
    review_field.set_defaults(func=_cmd_review_field)

    review_resolve = subparsers.add_parser("review-resolve", help="resolve one review item after manual verification")
    review_resolve.add_argument("--db", default=DEFAULT_DB)
    review_resolve.add_argument("--review-id", required=True)
    review_resolve.add_argument("--note", default="")
    review_resolve.set_defaults(func=_cmd_review_resolve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as error:  # CLI boundary: report a clear failure, never a fake success
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


def _cmd_search(args) -> int:
    config = load_app_config(args.config)
    db = Database(args.db)
    try:
        selected_databases = _databases(args.databases, config.defaults.databases)
        plan = build_query_plan(
            config,
            query=args.query,
            databases=selected_databases,
            max_results=args.max_results,
            from_year=args.from_year,
            to_year=args.to_year,
            page_size=args.page_size,
        )
        mailto = args.mailto if args.mailto is not None else config.defaults.mailto
        api_key = args.openalex_api_key or os.environ.get("ALSCN_OPENALEX_API_KEY", "")
        timeout = args.timeout or config.defaults.request_timeout_seconds
        ttl_hours = args.cache_ttl_hours if args.cache_ttl_hours is not None else config.defaults.cache_ttl_hours
        retry = RetryPolicy(
            max_attempts=config.defaults.retry.max_attempts,
            base_delay_seconds=config.defaults.retry.base_delay_seconds,
            max_delay_seconds=config.defaults.retry.max_delay_seconds,
        )
        transport = _build_transport(args.proxy or os.environ.get("ALSCN_PROXY") or None)
        cache = None if args.no_cache else db
        http_clients = {}
        for source in selected_databases:
            interval = config.defaults.rate_limit_seconds.get(source, 0.0)
            http_clients[source] = CachedHttpClient(
                transport,
                cache=cache,
                retry_policy=retry,
                rate_limiter=RateLimiter(interval),
                timeout=timeout,
                user_agent=_user_agent(config.defaults.user_agent, mailto),
                cache_ttl_seconds=ttl_hours * 3600,
                offline=args.offline,
            )
        clients = {}
        if "openalex" in http_clients:
            clients["openalex"] = OpenAlexClient(
                http_clients["openalex"],
                mailto=mailto,
                api_key=api_key,
            )
        if "crossref" in http_clients:
            clients["crossref"] = CrossrefClient(
                http_clients["crossref"],
                mailto=mailto,
            )

        classifier = None
        if args.classifier == "llm":
            llm_http = CachedHttpClient(
                transport,
                cache=None,
                retry_policy=retry,
                timeout=config.llm.timeout_seconds,
                user_agent=_user_agent(config.defaults.user_agent, mailto),
                offline=args.offline,
            )
            classifier = LLMClassifier(llm_http, config.llm)

        pipeline = DiscoveryPipeline(
            config=config,
            db=db,
            clients=clients,
            output_dir=args.out,
            classifier=classifier,
        )
        summary = pipeline.run(plan)
        if not args.quiet:
            print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return 0 if summary.status in {"ok", "partial"} else 1
    finally:
        db.close()


def _cmd_ingest(args) -> int:
    config = load_app_config("config/search_queries.yaml")
    db = Database(args.db)
    try:
        mailto = args.mailto if args.mailto is not None else config.defaults.mailto
        api_key = args.openalex_api_key or os.environ.get("ALSCN_OPENALEX_API_KEY", "")
        proxy = args.proxy or os.environ.get("ALSCN_PROXY") or None
        transport = _build_transport(proxy)
        retry = RetryPolicy(
            max_attempts=config.defaults.retry.max_attempts,
            base_delay_seconds=config.defaults.retry.base_delay_seconds,
            max_delay_seconds=config.defaults.retry.max_delay_seconds,
        )
        api_http = CachedHttpClient(
            transport,
            cache=db,
            retry_policy=retry,
            rate_limiter=RateLimiter(config.defaults.rate_limit_seconds.get("openalex", 0.2)),
            timeout=args.timeout,
            user_agent=_user_agent(config.defaults.user_agent, mailto),
            cache_ttl_seconds=config.defaults.cache_ttl_hours * 3600,
        )
        resolver = OpenAlexDoiResolver(
            OpenAlexClient(api_http, mailto=mailto, api_key=api_key)
        )
        fetcher = DocumentFetcher(
            transport,
            timeout=args.timeout,
            retry_policy=retry,
            rate_limiter=RateLimiter(0.5),
        )
        grobid_url = args.grobid_url or os.environ.get("GROBID_URL") or ""
        grobid = GrobidClient(grobid_url, timeout=max(90.0, args.timeout), proxy=proxy) if grobid_url else None
        doi_resolver = None if args.no_doi_resolve else DoiHandleResolver(transport, timeout=args.timeout)
        service = AcquisitionService(
            db=db,
            resolver=resolver,
            fetcher=fetcher,
            store=RawStore(args.raw_dir),
            doi_resolver=doi_resolver,
            grobid=grobid,
            max_supplementary=max(0, args.max_si),
        )
        result = service.acquire_doi(args.doi)
        summary = {
            "paper_key": result.paper_key,
            "doi": result.doi,
            "status": result.status,
            "primary_document_id": result.primary_document_id,
            "deposition_section_id": result.deposition_section_id,
            "deposition_section": (
                {
                    "heading": result.deposition_section.get("heading"),
                    "section_type": result.deposition_section.get("section_type"),
                    "locator": result.deposition_section.get("locator"),
                    "score": result.deposition_section.get("score"),
                    "text_preview": (result.deposition_section.get("text") or "")[:1200],
                }
                if result.deposition_section
                else None
            ),
            "documents": [
                {
                    key: document.get(key)
                    for key in (
                        "document_id",
                        "document_type",
                        "access_status",
                        "http_status",
                        "parser",
                        "file_hash",
                        "byte_size",
                        "error_kind",
                        "error_message",
                    )
                }
                for document in result.documents
            ],
            "section_count": len(result.sections),
            "errors": result.errors,
            "metadata": result.metadata,
        }
        if not args.quiet:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if result.status in {"ok", "partial"} else 1
    finally:
        db.close()


def _cmd_extract(args) -> int:
    db = Database(args.db)
    try:
        doi = normalize_doi(args.doi)
        if not doi:
            print(f"ERROR: invalid DOI: {args.doi}", file=sys.stderr)
            return 1
        paper = db.find_paper_by_doi(doi)
        if not paper:
            print(f"ERROR: paper not found in DB: {doi}; run ingest/search first", file=sys.stderr)
            return 1
        pipeline = ExtractionPipeline(db, output_dir=args.out)
        result = pipeline.extract_paper(paper["paper_key"], force=args.force)
        summary = {
            "paper_key": result.paper_key,
            "doi": result.doi,
            "status": result.status,
            "recipes": len(result.recipes),
            "evidence": len(result.evidence),
            "review_items": len(result.review_items),
            "warnings": result.warnings,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if result.status == "extracted" else 1
    finally:
        db.close()


def _cmd_extract_batch(args) -> int:
    db = Database(args.db)
    try:
        pipeline = ExtractionPipeline(db, output_dir=args.out)
        summary = pipeline.extract_batch(limit=args.limit, force=args.force)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


def _cmd_export(args) -> int:
    db = Database(args.db)
    try:
        path = ExcelExporter("config/alscn_pvd_schema.json").export(db, args.output, mode=args.mode)
        print(json.dumps({"exported": path, "mode": args.mode, "recipes_total": db.count_recipes(), "evidence_total": db.count_evidence()}, ensure_ascii=False))
        return 0
    finally:
        db.close()


def _cmd_validate(args) -> int:
    db = Database(args.db)
    try:
        if args.doi:
            doi = normalize_doi(args.doi)
            paper = db.find_paper_by_doi(doi) if doi else None
            if not paper:
                print(f"ERROR: paper not found: {args.doi}", file=sys.stderr)
                return 1
            validator = RecipeValidator()
            issues = []
            recipes = db.list_recipes(paper["paper_key"])
            for recipe in recipes:
                found = validator.validate(recipe, db.list_evidence(recipe["record_id"]))
                for item in found:
                    db.add_review_item(item)
                issues.extend(found)
            summary = {"paper_key": paper["paper_key"], "recipes": len(recipes), "issues": len(issues), "open_review_items": db.count_review_items()}
        else:
            summary = validate_all(db)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


def _cmd_metrics(args) -> int:
    db = Database(args.db)
    try:
        metrics = extraction_metrics(db)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        Path(args.markdown).write_text(metrics_markdown(metrics), encoding="utf-8")
        print(json.dumps({k: metrics[k] for k in ("papers_total", "recipes", "evidence", "evidence_rate", "documents", "sections", "tables")}, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


def _cmd_review_html(args) -> int:
    db = Database(args.db)
    try:
        path = generate_review_html(db, args.output)
        print(json.dumps({"review_html": path, "recipes": db.count_recipes()}, ensure_ascii=False))
        return 0
    finally:
        db.close()


def _cmd_review_set(args) -> int:
    db = Database(args.db)
    try:
        result = db.record_review_action(args.record_id, args.status, args.note)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


def _cmd_review_field(args) -> int:
    db = Database(args.db)
    try:
        result = db.record_field_review(args.record_id, args.field_key, args.decision, args.note)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


def _cmd_review_resolve(args) -> int:
    db = Database(args.db)
    try:
        result = db.resolve_review_item(args.review_id, args.note)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


def _cmd_report(args) -> int:
    db = Database(args.db)
    try:
        run_id = args.run_id or db.latest_run_id()
        if not run_id:
            print("ERROR: no search run found; run `python -m app.cli search` first", file=sys.stderr)
            return 1
        if not db.get_run(run_id):
            print(f"ERROR: run not found: {run_id}", file=sys.stderr)
            return 1
        outputs = write_run_outputs(db, run_id, Path(args.out) / run_id)
        print(json.dumps(outputs, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


def _cmd_gold_eval(args) -> int:
    db = Database(args.db)
    try:
        result = evaluate_gold(db, args.gold)
        if args.labels:
            result["screening"] = evaluate_screening_labels(db, args.labels)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        print(text)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        return 0 if result["recall"] >= args.min_recall else 1
    finally:
        db.close()


def _cmd_init_db(args) -> int:
    db = Database(args.db)
    try:
        print(json.dumps({"db": str(Path(args.db).resolve()), "papers": db.count_papers(), "sources": db.count_sources()}, ensure_ascii=False))
        return 0
    finally:
        db.close()


def _cmd_cache_import(args) -> int:
    manifest_path = Path(args.manifest)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError("cache manifest must be a list or an object with an 'entries' list")
    db = Database(args.db)
    imported = 0
    try:
        for entry in entries:
            source = str(entry["source"])
            url = str(entry["url"])
            file_path = Path(entry["file"])
            if not file_path.is_absolute():
                file_path = manifest_path.parent / file_path
            body = file_path.read_bytes()
            db.cache_put(
                cache_key(source, url),
                source=source,
                url=url,
                status=int(entry.get("status", 200)),
                body=body,
                fetched_at=utc_now_iso(),
                expires_at=str(entry.get("expires_at") or "2099-01-01T00:00:00+00:00"),
            )
            imported += 1
        print(json.dumps({"db": args.db, "imported": imported, "cache_entries": db.count_cache()}, ensure_ascii=False))
        return 0
    finally:
        db.close()


def _cmd_cache_stats(args) -> int:
    db = Database(args.db)
    try:
        print(json.dumps({"db": args.db, "cache_entries": db.count_cache()}, ensure_ascii=False))
        return 0
    finally:
        db.close()


def _databases(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return tuple(default)
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _build_transport(proxy: str | None):
    try:
        return RequestsTransport(proxy=proxy)
    except ImportError:
        return UrllibTransport(proxy=proxy)


def _user_agent(base: str, mailto: str) -> str:
    if mailto and "mailto:" not in base:
        return f"{base} (mailto:{mailto})"
    return base


if __name__ == "__main__":
    raise SystemExit(main())
