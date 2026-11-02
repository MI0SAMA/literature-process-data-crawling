"""End-to-end Phase 2 discovery pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.config import AppConfig
from app.discovery.dedup import Deduplicator
from app.discovery.screening import RuleScreener, combine_rule_and_ai
from app.http import ApiError
from app.models import (
    PaperCluster,
    QueryExecution,
    QueryPlan,
    ScreeningDecision,
    utc_now_iso,
)
from app.storage import Database


class Classifier(Protocol):
    def classify(self, paper: PaperCluster) -> Any: ...


@dataclass
class RunSummary:
    run_id: str
    status: str
    records_in: int = 0
    papers_out: int = 0
    duplicates_removed: int = 0
    likely: int = 0
    unclear: int = 0
    not_relevant: int = 0
    review_items: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    query_executions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "records_in": self.records_in,
            "papers_out": self.papers_out,
            "duplicates_removed": self.duplicates_removed,
            "likely": self.likely,
            "unclear": self.unclear,
            "not_relevant": self.not_relevant,
            "review_items": self.review_items,
            "errors": self.errors,
            "outputs": self.outputs,
            "query_executions": self.query_executions,
        }


class DiscoveryPipeline:
    def __init__(
        self,
        *,
        config: AppConfig,
        db: Database,
        clients: dict[str, Any],
        output_dir: str | Path = "data/structured",
        classifier: Classifier | None = None,
        deduplicator: Deduplicator | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.clients = clients
        self.output_dir = Path(output_dir)
        self.classifier = classifier
        self.screener = RuleScreener(config.screening)
        self.deduplicator = deduplicator or Deduplicator(
            fuzzy_auto_merge_threshold=config.screening.fuzzy_auto_merge_threshold,
            fuzzy_review_threshold=config.screening.fuzzy_review_threshold,
        )

    def run(self, plan: QueryPlan) -> RunSummary:
        summary = RunSummary(run_id="", status="running")
        run_id = self.db.start_run(
            plan,
            params={
                "databases": sorted({db for spec in plan.specs for db in spec.databases}),
                "query_count": len(plan.specs),
            },
        )
        summary.run_id = run_id
        all_records = []
        errors: list[dict[str, Any]] = []

        for spec in plan.specs:
            for database in spec.databases:
                execution, query_records = self._execute_query(run_id, spec, database)
                self.db.save_query_execution(execution)
                summary.query_executions += 1
                # Preserve whatever pages were successfully fetched even if a
                # later page failed; the execution status still records the error.
                all_records.extend(query_records)
                if execution.status != "ok":
                    errors.append(
                        {
                            "database": database,
                            "query_id": spec.query_id,
                            "query": spec.query,
                            "kind": execution.error_kind,
                            "message": execution.error_message,
                        }
                    )

        dedup = self.deduplicator.deduplicate(all_records)
        summary.records_in = dedup.stats["records_in"]
        summary.duplicates_removed = dedup.stats["duplicates_removed"]
        summary.review_items = len(dedup.review_items)

        for item in dedup.review_items:
            self.db.save_review_item(run_id, item)

        for cluster in dedup.clusters:
            effective_key = self.db.save_cluster(cluster, run_id)
            decision = self._screen(cluster, effective_key)
            self.db.save_screening(decision)
            if decision.final_label == "likely_AlScN_PVD":
                summary.likely += 1
            elif decision.final_label == "unclear":
                summary.unclear += 1
            else:
                summary.not_relevant += 1

        summary.papers_out = len(dedup.clusters)
        summary.errors = errors
        if errors and dedup.clusters:
            summary.status = "partial"
        elif errors and not dedup.clusters:
            summary.status = "failed"
        else:
            summary.status = "ok"
        error_text = "; ".join(f"{item['database']}/{item['query_id']}: {item['kind']}" for item in errors) or None
        self.db.finish_run(
            run_id,
            status=summary.status,
            record_count=summary.records_in,
            paper_count=summary.papers_out,
            error=error_text,
        )

        from app.reporting import write_run_outputs

        summary.outputs = write_run_outputs(self.db, run_id, self.output_dir / run_id, summary=summary)
        return summary

    def _execute_query(self, run_id: str, spec, database: str) -> tuple[QueryExecution, list]:
        execution = QueryExecution(
            execution_id=f"{run_id}_{spec.query_id}_{database}",
            run_id=run_id,
            query_id=spec.query_id,
            database=database,
            query_text=spec.query,
            from_year=spec.from_year,
            to_year=spec.to_year,
            requested_max=spec.max_results,
            page_size=spec.page_size,
            started_at=utc_now_iso(),
        )
        client = self.clients.get(database)
        if client is None:
            execution.status = "error"
            execution.error_kind = "unsupported_database"
            execution.error_message = f"no client configured for {database}"
            execution.finished_at = utc_now_iso()
            return execution, []

        records = []
        seen_source_ids: set[str] = set()
        page = 1
        pages_fetched = 0
        request_count = 0
        cache_hits = 0
        retry_count = 0
        try:
            while len(records) < spec.max_results and page <= 100:
                page_result = client.search(spec, page=page)
                pages_fetched += 1
                request_count += 1
                cache_hits += 1 if page_result.cache_hit else 0
                retry_count += int(page_result.retries or 0)
                for record in page_result.records:
                    if record.source_id in seen_source_ids:
                        continue
                    seen_source_ids.add(record.source_id)
                    records.append(record)
                    if len(records) >= spec.max_results:
                        break
                if page_result.next_page is None:
                    break
                page += 1
            execution.status = "ok"
        except ApiError as error:
            execution.status = "error"
            execution.error_kind = error.kind
            execution.error_message = str(error)
        except Exception as error:  # defensive: one bad adapter must not abort the run
            execution.status = "error"
            execution.error_kind = "unexpected_error"
            execution.error_message = f"{type(error).__name__}: {error}"

        execution.finished_at = utc_now_iso()
        execution.pages_fetched = pages_fetched
        execution.result_count = len(records)
        execution.unique_count = len(seen_source_ids)
        execution.request_count = request_count
        execution.cache_hits = cache_hits
        execution.retry_count = retry_count
        return execution, records

    def _screen(self, cluster: PaperCluster, paper_key: str) -> ScreeningDecision:
        rule = self.screener.screen(cluster)
        combined = rule
        if self.classifier is not None:
            try:
                ai = self.classifier.classify(cluster)
                combined = combine_rule_and_ai(
                    rule,
                    ai.label,
                    confidence=float(getattr(ai, "confidence", 0.0)),
                    classifier=str(getattr(ai, "classifier", "ai")),
                    classifier_version=str(getattr(ai, "version", "phase2-llm-v1")),
                )
            except Exception as error:
                combined = rule
                combined.reasons = list(rule.reasons) + [f"AI classifier failed; rule result kept: {type(error).__name__}"]
                combined.flags = list(rule.flags) + ["ai_error"]
        return ScreeningDecision(
            paper_key=paper_key,
            rule_label=rule.label,
            final_label=combined.label,
            confidence=combined.confidence,
            matched_material=combined.matched_material,
            matched_method=combined.matched_method,
            flags=combined.flags,
            reasons=combined.reasons,
            review_required=combined.review_required,
            eligible_for_confirmation=combined.eligible_for_confirmation,
            classifier=combined.classifier,
            classifier_version=combined.classifier_version,
        )
