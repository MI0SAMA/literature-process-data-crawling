"""QueryPlan construction and audit metadata."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app.config import AppConfig
from app.models import QueryPlan, QuerySpec

SUPPORTED_DATABASES = ("openalex", "crossref")


def build_query_plan(
    config: AppConfig,
    *,
    query: str | None = None,
    databases: tuple[str, ...] | list[str] | None = None,
    max_results: int | None = None,
    from_year: int | None = None,
    to_year: int | None = None,
    page_size: int | None = None,
) -> QueryPlan:
    defaults = config.defaults
    selected_databases = tuple(databases or defaults.databases)
    for database in selected_databases:
        if database not in SUPPORTED_DATABASES:
            raise ValueError(f"unsupported database {database!r}; supported: {', '.join(SUPPORTED_DATABASES)}")
    if not selected_databases:
        raise ValueError("at least one database must be selected")

    selected_max = int(max_results if max_results is not None else defaults.max_results_per_query)
    selected_page_size = int(page_size if page_size is not None else defaults.page_size)
    selected_from = from_year if from_year is not None else defaults.from_year
    selected_to = to_year if to_year is not None else defaults.to_year
    if selected_max < 1:
        raise ValueError("max_results must be >= 1")
    if not 1 <= selected_page_size <= 200:
        raise ValueError("page_size must be between 1 and 200")
    if selected_from is not None and selected_to is not None and selected_from > selected_to:
        raise ValueError("from_year must be <= to_year")
    selected_page_size = min(selected_page_size, selected_max)

    if query:
        raw_queries = [("custom_001", query.strip())]
    else:
        raw_queries = [(item.query_id, item.query) for item in config.queries]

    specs: list[QuerySpec] = []
    seen: set[str] = set()
    for index, (query_id, query_text) in enumerate(raw_queries, start=1):
        cleaned = " ".join(query_text.split())
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        specs.append(
            QuerySpec(
                query_id=query_id if query_id else f"q{index:02d}",
                query=cleaned,
                databases=selected_databases,
                max_results=selected_max,
                from_year=selected_from,
                to_year=selected_to,
                page_size=selected_page_size,
            )
        )
    if not specs:
        raise ValueError("query plan is empty")

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    digest_source = json.dumps([spec.to_dict() for spec in specs], sort_keys=True)
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:12]
    plan_id = f"plan_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{digest}"
    return QueryPlan(plan_id=plan_id, generated_at=generated_at, specs=specs)
