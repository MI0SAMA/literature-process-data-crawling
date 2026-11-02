"""SQLite persistence for Phase 2 search runs, papers, screenings and API cache."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.http import CachedResponse
from app.models import (
    PaperCluster,
    QueryExecution,
    QueryPlan,
    ReviewItem,
    ScreeningDecision,
    utc_now_iso,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_runs (
    run_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    params_json TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    record_count INTEGER NOT NULL DEFAULT 0,
    paper_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS query_executions (
    execution_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES search_runs(run_id),
    query_id TEXT NOT NULL,
    database TEXT NOT NULL,
    query_text TEXT NOT NULL,
    from_year INTEGER,
    to_year INTEGER,
    requested_max INTEGER NOT NULL,
    page_size INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    result_count INTEGER NOT NULL DEFAULT 0,
    unique_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    error_kind TEXT,
    error_message TEXT,
    request_count INTEGER NOT NULL DEFAULT 0,
    cache_hits INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS papers (
    paper_key TEXT PRIMARY KEY,
    doi TEXT,
    doi_norm TEXT,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    first_author_family TEXT,
    publication_year INTEGER,
    abstract TEXT,
    venue TEXT,
    item_type TEXT,
    landing_url TEXT,
    pdf_url TEXT,
    is_oa INTEGER,
    dedup_method TEXT NOT NULL,
    review_required INTEGER NOT NULL DEFAULT 0,
    review_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_papers_doi_norm
    ON papers(doi_norm) WHERE doi_norm IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_papers_title_year
    ON papers(normalized_title, publication_year);

CREATE TABLE IF NOT EXISTS paper_sources (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    paper_key TEXT NOT NULL REFERENCES papers(paper_key),
    doi TEXT,
    title TEXT,
    publication_year INTEGER,
    raw_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);

CREATE INDEX IF NOT EXISTS ix_paper_sources_paper
    ON paper_sources(paper_key);

CREATE TABLE IF NOT EXISTS screenings (
    paper_key TEXT PRIMARY KEY REFERENCES papers(paper_key),
    rule_label TEXT NOT NULL,
    final_label TEXT NOT NULL,
    confidence REAL NOT NULL,
    matched_material TEXT NOT NULL,
    matched_method TEXT NOT NULL,
    flags TEXT NOT NULL,
    reasons TEXT NOT NULL,
    review_required INTEGER NOT NULL,
    eligible_for_confirmation INTEGER NOT NULL,
    classifier TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_papers (
    run_id TEXT NOT NULL REFERENCES search_runs(run_id),
    paper_key TEXT NOT NULL REFERENCES papers(paper_key),
    PRIMARY KEY (run_id, paper_key)
);

CREATE INDEX IF NOT EXISTS ix_run_papers_paper ON run_papers(paper_key);

CREATE TABLE IF NOT EXISTS dedup_reviews (
    review_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES search_runs(run_id),
    left_source TEXT NOT NULL,
    left_source_id TEXT NOT NULL,
    right_source TEXT NOT NULL,
    right_source_id TEXT NOT NULL,
    left_title TEXT NOT NULL,
    right_title TEXT NOT NULL,
    similarity REAL NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_cache (
    cache_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    status INTEGER NOT NULL,
    body BLOB NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_api_cache_source ON api_cache(source);

CREATE TABLE IF NOT EXISTS acquisition_runs (
    acquisition_id TEXT PRIMARY KEY,
    paper_key TEXT,
    doi TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    primary_document_id TEXT,
    deposition_section_id TEXT,
    error_kind TEXT,
    error_message TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS ix_acquisition_runs_paper
    ON acquisition_runs(paper_key, started_at);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    paper_key TEXT,
    doi TEXT,
    document_type TEXT NOT NULL,
    version TEXT,
    source_url TEXT,
    final_url TEXT,
    access_status TEXT NOT NULL,
    http_status INTEGER,
    content_type TEXT,
    file_path TEXT,
    file_hash TEXT,
    byte_size INTEGER,
    fetched_at TEXT,
    parser TEXT,
    parser_version TEXT,
    license TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    parent_document_id TEXT,
    error_kind TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS ix_documents_paper ON documents(paper_key);
CREATE INDEX IF NOT EXISTS ix_documents_doi ON documents(doi);

CREATE TABLE IF NOT EXISTS document_sections (
    section_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    section_type TEXT NOT NULL,
    heading TEXT,
    text TEXT,
    char_start INTEGER,
    char_end INTEGER,
    page_start INTEGER,
    page_end INTEGER,
    section_order INTEGER NOT NULL,
    locator TEXT,
    is_reference INTEGER NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_document_sections_document
    ON document_sections(document_id, section_order);
CREATE INDEX IF NOT EXISTS ix_document_sections_type
    ON document_sections(section_type);

CREATE TABLE IF NOT EXISTS document_tables (
    table_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    label TEXT,
    caption TEXT,
    text TEXT,
    locator TEXT,
    page INTEGER,
    table_order INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_document_tables_document
    ON document_tables(document_id, table_order);

CREATE TABLE IF NOT EXISTS recipes (
    record_id TEXT PRIMARY KEY,
    paper_key TEXT,
    paper_doi TEXT,
    recipe_id TEXT,
    sample_id TEXT,
    review_status TEXT NOT NULL DEFAULT 'new',
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_recipes_paper ON recipes(paper_key);

CREATE TABLE IF NOT EXISTS targets (
    target_record_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    target_index INTEGER NOT NULL,
    layer_role TEXT,
    target_material TEXT,
    target_power_w REAL,
    power_basis TEXT,
    power_statistic TEXT,
    reported_power_text TEXT,
    target_diameter_mm REAL,
    target_diameter_inch REAL,
    power_density_w_cm2 REAL,
    sputtering_mode TEXT,
    equipment_model TEXT,
    deposition_rate_nm_min REAL,
    data_json TEXT NOT NULL,
    evidence_id TEXT
);

CREATE INDEX IF NOT EXISTS ix_targets_record ON targets(record_id);

CREATE TABLE IF NOT EXISTS samples (
    sample_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    paper_key TEXT,
    data_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS measurements (
    measurement_id TEXT PRIMARY KEY,
    record_id TEXT,
    sample_id TEXT,
    property_key TEXT,
    property_value REAL,
    property_unit TEXT,
    evidence_id TEXT,
    data_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    record_id TEXT,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    field_key TEXT NOT NULL,
    paper_doi TEXT,
    source_url TEXT,
    source_section TEXT,
    page_table_figure TEXT,
    evidence_text TEXT,
    raw_value TEXT,
    raw_unit TEXT,
    normalized_value REAL,
    normalized_unit TEXT,
    evidence_status TEXT NOT NULL,
    derivation_note TEXT
);

CREATE INDEX IF NOT EXISTS ix_evidence_record ON evidence(record_id, field_key);

CREATE TABLE IF NOT EXISTS extraction_runs (
    extraction_id TEXT PRIMARY KEY,
    paper_key TEXT,
    doi TEXT,
    model TEXT,
    prompt_version TEXT,
    parser_version TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    error TEXT,
    recipes_count INTEGER NOT NULL DEFAULT 0,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS review_queue (
    review_id TEXT PRIMARY KEY,
    paper_key TEXT,
    record_id TEXT,
    field_key TEXT,
    reason TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'medium',
    evidence_id TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_actions (
    action_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    action TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    note TEXT,
    extraction_id TEXT,
    field_key TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS field_reviews (
    review_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    field_key TEXT NOT NULL,
    decision TEXT NOT NULL,
    evidence_id TEXT,
    note TEXT,
    extraction_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(record_id, field_key)
);
"""


DERIVED_AUDIT_FIELDS = {
    "deposition_method", "sputtering_mode", "deposition_method_detail", "method_category",
    "target_configuration", "sample_type", "film_sc_basis", "temperature_basis",
    "target1_power_basis", "target1_power_statistic", "target1_reported_power_text",
    "target2_power_basis", "target2_power_statistic", "target2_reported_power_text",
    "target1_power_mode",
}


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self._in_transaction = False
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        # Migration for databases created before extraction-version linkage.
        try:
            self.conn.execute("ALTER TABLE review_actions ADD COLUMN extraction_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            self.conn.execute("ALTER TABLE review_actions ADD COLUMN field_key TEXT")
        except sqlite3.OperationalError:
            pass
        for column, ctype in (
            ("power_basis", "TEXT"),
            ("power_statistic", "TEXT"),
            ("reported_power_text", "TEXT"),
            ("target_diameter_mm", "REAL"),
            ("target_diameter_inch", "REAL"),
            ("power_density_w_cm2", "REAL"),
            ("sputtering_mode", "TEXT"),
            ("equipment_model", "TEXT"),
            ("deposition_rate_nm_min", "REAL"),
        ):
            try:
                self.conn.execute(f"ALTER TABLE targets ADD COLUMN {column} {ctype}")
            except sqlite3.OperationalError:
                pass
        self._commit()

    def begin(self) -> None:
        if self._in_transaction:
            raise RuntimeError("transaction already active")
        if not self.conn.in_transaction:
            self.conn.execute("BEGIN")
        self._in_transaction = True

    def commit(self) -> None:
        self.conn.commit()
        self._in_transaction = False

    def rollback(self) -> None:
        self.conn.rollback()
        self._in_transaction = False

    def _commit(self) -> None:
        if not self._in_transaction:
            self.conn.commit()

    def backup(self, path: str | Path) -> str:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(target)) as backup:
            self.conn.backup(backup)
        return str(target)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self._commit()
        else:
            self.conn.rollback()
        self.close()

    # ------------------------------------------------------------------ runs
    def start_run(self, plan: QueryPlan, params: dict | None = None) -> str:
        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            "INSERT INTO search_runs(run_id, plan_id, plan_json, params_json, started_at, status) "
            "VALUES (?, ?, ?, ?, ?, 'running')",
            (
                run_id,
                plan.plan_id,
                json.dumps(plan.to_dict(), ensure_ascii=False),
                json.dumps(params or {}, ensure_ascii=False),
                utc_now_iso(),
            ),
        )
        self._commit()
        return run_id

    def finish_run(self, run_id: str, *, status: str, record_count: int, paper_count: int, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE search_runs SET finished_at=?, status=?, record_count=?, paper_count=?, error=? WHERE run_id=?",
            (utc_now_iso(), status, record_count, paper_count, error, run_id),
        )
        self._commit()

    def get_run(self, run_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM search_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def latest_run_id(self) -> str | None:
        row = self.conn.execute(
            "SELECT run_id FROM search_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return row["run_id"] if row else None

    def save_query_execution(self, execution: QueryExecution) -> None:
        self.conn.execute(
            """
            INSERT INTO query_executions(
                execution_id, run_id, query_id, database, query_text, from_year, to_year,
                requested_max, page_size, started_at, finished_at, pages_fetched, result_count,
                unique_count, status, error_kind, error_message, request_count, cache_hits, retry_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(execution_id) DO UPDATE SET
                finished_at=excluded.finished_at,
                pages_fetched=excluded.pages_fetched,
                result_count=excluded.result_count,
                unique_count=excluded.unique_count,
                status=excluded.status,
                error_kind=excluded.error_kind,
                error_message=excluded.error_message,
                request_count=excluded.request_count,
                cache_hits=excluded.cache_hits,
                retry_count=excluded.retry_count
            """,
            (
                execution.execution_id,
                execution.run_id,
                execution.query_id,
                execution.database,
                execution.query_text,
                execution.from_year,
                execution.to_year,
                execution.requested_max,
                execution.page_size,
                execution.started_at,
                execution.finished_at,
                execution.pages_fetched,
                execution.result_count,
                execution.unique_count,
                execution.status,
                execution.error_kind,
                execution.error_message,
                execution.request_count,
                execution.cache_hits,
                execution.retry_count,
            ),
        )
        self._commit()

    def list_query_executions(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM query_executions WHERE run_id=? ORDER BY started_at, database, query_id",
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ---------------------------------------------------------------- papers
    def save_cluster(self, cluster: PaperCluster, run_id: str | None = None) -> str:
        paper_key = cluster.paper_key
        if cluster.doi:
            existing = self.conn.execute(
                "SELECT paper_key FROM papers WHERE doi_norm=?", (cluster.doi,)
            ).fetchone()
            if existing:
                paper_key = existing["paper_key"]
                cluster.paper_key = paper_key

        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO papers(
                paper_key, doi, doi_norm, title, normalized_title, first_author_family,
                publication_year, abstract, venue, item_type, landing_url, pdf_url, is_oa,
                dedup_method, review_required, review_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(paper_key) DO UPDATE SET
                doi=COALESCE(excluded.doi, papers.doi),
                doi_norm=COALESCE(excluded.doi_norm, papers.doi_norm),
                title=excluded.title,
                normalized_title=excluded.normalized_title,
                first_author_family=excluded.first_author_family,
                publication_year=COALESCE(excluded.publication_year, papers.publication_year),
                abstract=CASE
                    WHEN excluded.abstract IS NOT NULL AND length(excluded.abstract) >= length(COALESCE(papers.abstract, ''))
                    THEN excluded.abstract ELSE papers.abstract END,
                venue=COALESCE(excluded.venue, papers.venue),
                item_type=COALESCE(excluded.item_type, papers.item_type),
                landing_url=COALESCE(excluded.landing_url, papers.landing_url),
                pdf_url=COALESCE(excluded.pdf_url, papers.pdf_url),
                is_oa=COALESCE(excluded.is_oa, papers.is_oa),
                dedup_method=excluded.dedup_method,
                review_required=excluded.review_required,
                review_reason=COALESCE(excluded.review_reason, papers.review_reason),
                updated_at=excluded.updated_at
            """,
            (
                paper_key,
                cluster.doi,
                cluster.doi,
                cluster.title,
                cluster.normalized_title,
                cluster.first_author_family,
                cluster.publication_year,
                cluster.abstract,
                cluster.venue,
                cluster.item_type,
                cluster.landing_url,
                _first_pdf_url(cluster),
                1 if any(record.is_oa for record in cluster.records) else (0 if any(record.is_oa is False for record in cluster.records) else None),
                cluster.method,
                1 if cluster.review_required else 0,
                cluster.review_reason,
                now,
                now,
            ),
        )

        for record in cluster.records:
            self.conn.execute(
                """
                INSERT INTO paper_sources(
                    source, source_id, paper_key, doi, title, publication_year, raw_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    paper_key=excluded.paper_key,
                    doi=excluded.doi,
                    title=excluded.title,
                    publication_year=excluded.publication_year,
                    raw_json=excluded.raw_json,
                    fetched_at=excluded.fetched_at
                """,
                (
                    record.source,
                    record.source_id,
                    paper_key,
                    record.doi,
                    record.title,
                    record.publication_year,
                    json.dumps(record.raw, ensure_ascii=False),
                    now,
                ),
            )
        if run_id:
            self.conn.execute(
                "INSERT OR IGNORE INTO run_papers(run_id, paper_key) VALUES (?, ?)",
                (run_id, paper_key),
            )
        self._commit()
        return paper_key

    def save_screening(self, decision: ScreeningDecision) -> None:
        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO screenings(
                paper_key, rule_label, final_label, confidence, matched_material,
                matched_method, flags, reasons, review_required, eligible_for_confirmation,
                classifier, classifier_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(paper_key) DO UPDATE SET
                rule_label=excluded.rule_label,
                final_label=excluded.final_label,
                confidence=excluded.confidence,
                matched_material=excluded.matched_material,
                matched_method=excluded.matched_method,
                flags=excluded.flags,
                reasons=excluded.reasons,
                review_required=excluded.review_required,
                eligible_for_confirmation=excluded.eligible_for_confirmation,
                classifier=excluded.classifier,
                classifier_version=excluded.classifier_version,
                updated_at=excluded.updated_at
            """,
            (
                decision.paper_key,
                decision.rule_label,
                decision.final_label,
                decision.confidence,
                json.dumps(decision.matched_material, ensure_ascii=False),
                json.dumps(decision.matched_method, ensure_ascii=False),
                json.dumps(decision.flags, ensure_ascii=False),
                json.dumps(decision.reasons, ensure_ascii=False),
                1 if decision.review_required else 0,
                1 if decision.eligible_for_confirmation else 0,
                decision.classifier,
                decision.classifier_version,
                now,
                now,
            ),
        )
        self._commit()

    def get_screening(self, paper_key: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM screenings WHERE paper_key=?", (paper_key,)).fetchone()
        if not row:
            return None
        result = dict(row)
        for key in ("matched_material", "matched_method", "flags", "reasons"):
            result[key] = json.loads(result[key])
        return result

    def count_papers(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0])

    def get_paper(self, paper_key: str) -> dict | None:
        row = self.conn.execute(
            """
            SELECT p.*, s.final_label, s.eligible_for_confirmation
            FROM papers p LEFT JOIN screenings s ON s.paper_key = p.paper_key
            WHERE p.paper_key=?
            """,
            (paper_key,),
        ).fetchone()
        return dict(row) if row else None

    def find_paper_by_doi(self, doi: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM papers WHERE doi_norm=? LIMIT 1", (doi,)).fetchone()
        return dict(row) if row else None

    def count_sources(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM paper_sources").fetchone()[0])

    def list_papers(self, run_id: str | None = None) -> list[dict]:
        if run_id:
            rows = self.conn.execute(
                """
                SELECT p.*, s.rule_label, s.final_label, s.confidence, s.matched_material,
                       s.matched_method, s.flags, s.reasons, s.review_required AS screening_review_required,
                       s.eligible_for_confirmation, s.classifier, s.classifier_version,
                       (SELECT GROUP_CONCAT(ps.source, ';') FROM paper_sources ps WHERE ps.paper_key = p.paper_key) AS sources
                FROM papers p
                JOIN run_papers rp ON rp.paper_key = p.paper_key AND rp.run_id = ?
                LEFT JOIN screenings s ON s.paper_key = p.paper_key
                ORDER BY p.publication_year DESC, p.title
                """,
                (run_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT p.*, s.rule_label, s.final_label, s.confidence, s.matched_material,
                       s.matched_method, s.flags, s.reasons, s.review_required AS screening_review_required,
                       s.eligible_for_confirmation, s.classifier, s.classifier_version,
                       (SELECT GROUP_CONCAT(ps.source, ';') FROM paper_sources ps WHERE ps.paper_key = p.paper_key) AS sources
                FROM papers p
                LEFT JOIN screenings s ON s.paper_key = p.paper_key
                ORDER BY p.publication_year DESC, p.title
                """
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("matched_material", "matched_method", "flags", "reasons"):
                if item.get(key):
                    item[key] = json.loads(item[key])
                else:
                    item[key] = []
            result.append(item)
        return result

    def list_duplicate_dois(self) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            "SELECT doi_norm, COUNT(*) AS c FROM papers WHERE doi_norm IS NOT NULL "
            "GROUP BY doi_norm HAVING c > 1"
        ).fetchall()
        return [(row["doi_norm"], row["c"]) for row in rows]

    # --------------------------------------------------------------- reviews
    def save_review_item(self, run_id: str, item: ReviewItem) -> None:
        self.conn.execute(
            """
            INSERT INTO dedup_reviews(
                review_id, run_id, left_source, left_source_id, right_source, right_source_id,
                left_title, right_title, similarity, reason, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                f"rev_{uuid.uuid4().hex[:16]}",
                run_id,
                item.left_source,
                item.left_source_id,
                item.right_source,
                item.right_source_id,
                item.left_title,
                item.right_title,
                item.similarity,
                item.reason,
                utc_now_iso(),
            ),
        )
        self._commit()

    def list_review_items(self, run_id: str | None = None) -> list[dict]:
        if run_id:
            rows = self.conn.execute(
                "SELECT * FROM dedup_reviews WHERE run_id=? ORDER BY similarity DESC", (run_id,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM dedup_reviews ORDER BY similarity DESC").fetchall()
        return [dict(row) for row in rows]

    # ----------------------------------------------------------------- cache
    def cache_get(self, key: str) -> CachedResponse | None:
        row = self.conn.execute("SELECT * FROM api_cache WHERE cache_key=?", (key,)).fetchone()
        if not row:
            return None
        return CachedResponse(
            status=row["status"],
            body=row["body"],
            fetched_at=row["fetched_at"],
            expires_at=row["expires_at"],
            url=row["url"],
        )

    # ResponseCache protocol aliases used by CachedHttpClient.
    def get(self, key: str) -> CachedResponse | None:
        return self.cache_get(key)

    def put(
        self,
        key: str,
        *,
        source: str,
        url: str,
        status: int,
        body: bytes,
        fetched_at: str,
        expires_at: str | None,
    ) -> None:
        self.cache_put(
            key,
            source=source,
            url=url,
            status=status,
            body=body,
            fetched_at=fetched_at,
            expires_at=expires_at,
        )

    def count_cache(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0])

    # ------------------------------------------------------------- phase 3
    def save_document(self, document: dict) -> str:
        document_id = str(document["document_id"])
        self.conn.execute(
            """
            INSERT INTO documents(
                document_id, paper_key, doi, document_type, version, source_url, final_url,
                access_status, http_status, content_type, file_path, file_hash, byte_size,
                fetched_at, parser, parser_version, license, is_primary, parent_document_id,
                error_kind, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                paper_key=excluded.paper_key,
                doi=excluded.doi,
                document_type=excluded.document_type,
                version=excluded.version,
                source_url=excluded.source_url,
                final_url=excluded.final_url,
                access_status=excluded.access_status,
                http_status=excluded.http_status,
                content_type=excluded.content_type,
                file_path=excluded.file_path,
                file_hash=excluded.file_hash,
                byte_size=excluded.byte_size,
                fetched_at=excluded.fetched_at,
                parser=excluded.parser,
                parser_version=excluded.parser_version,
                license=excluded.license,
                is_primary=excluded.is_primary,
                parent_document_id=excluded.parent_document_id,
                error_kind=excluded.error_kind,
                error_message=excluded.error_message
            """,
            (
                document_id,
                document.get("paper_key"),
                document.get("doi"),
                document.get("document_type", "unknown"),
                document.get("version"),
                document.get("source_url"),
                document.get("final_url"),
                document.get("access_status", "unknown"),
                document.get("http_status"),
                document.get("content_type"),
                document.get("file_path"),
                document.get("file_hash"),
                document.get("byte_size"),
                document.get("fetched_at"),
                document.get("parser"),
                document.get("parser_version"),
                document.get("license"),
                1 if document.get("is_primary") else 0,
                document.get("parent_document_id"),
                document.get("error_kind"),
                document.get("error_message"),
            ),
        )
        self._commit()
        return document_id

    def save_sections(self, document_id: str, sections: list[dict]) -> None:
        self.conn.execute("DELETE FROM document_sections WHERE document_id=?", (document_id,))
        for index, section in enumerate(sections):
            section_id = str(section.get("section_id") or f"{document_id}:s{index}")
            self.conn.execute(
                """
                INSERT INTO document_sections(
                    section_id, document_id, section_type, heading, text, char_start, char_end,
                    page_start, page_end, section_order, locator, is_reference, score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    section_id,
                    document_id,
                    section.get("section_type", "body"),
                    section.get("heading", ""),
                    section.get("text", ""),
                    section.get("char_start"),
                    section.get("char_end"),
                    section.get("page_start"),
                    section.get("page_end"),
                    int(section.get("order", index)),
                    section.get("locator", ""),
                    1 if section.get("is_reference") else 0,
                    float(section.get("score", 0.0)),
                ),
            )
        self._commit()

    def save_tables(self, document_id: str, tables: list[dict]) -> None:
        self.conn.execute("DELETE FROM document_tables WHERE document_id=?", (document_id,))
        for index, table in enumerate(tables):
            table_id = str(table.get("table_id") or f"{document_id}:t{index}")
            self.conn.execute(
                """
                INSERT INTO document_tables(
                    table_id, document_id, label, caption, text, locator, page, table_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    table_id,
                    document_id,
                    table.get("label", ""),
                    table.get("caption", ""),
                    table.get("text", ""),
                    table.get("locator", ""),
                    table.get("page"),
                    int(table.get("order", index)),
                ),
            )
        self._commit()

    def get_tables(self, *, document_id: str | None = None, paper_key: str | None = None) -> list[dict]:
        clauses = []
        params: list = []
        if document_id:
            clauses.append("t.document_id=?")
            params.append(document_id)
        if paper_key:
            clauses.append("d.paper_key=?")
            params.append(paper_key)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT t.*, d.paper_key, d.document_type
            FROM document_tables t
            JOIN documents d ON d.document_id = t.document_id
            {where}
            ORDER BY t.table_order
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_document(self, document_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()
        return dict(row) if row else None

    def list_documents(self, paper_key: str | None = None) -> list[dict]:
        if paper_key:
            rows = self.conn.execute(
                "SELECT * FROM documents WHERE paper_key=? ORDER BY is_primary DESC, fetched_at",
                (paper_key,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM documents ORDER BY fetched_at").fetchall()
        return [dict(row) for row in rows]

    def set_primary_document(self, paper_key: str, document_id: str) -> None:
        self.conn.execute("UPDATE documents SET is_primary=0 WHERE paper_key=?", (paper_key,))
        self.conn.execute("UPDATE documents SET is_primary=1 WHERE document_id=?", (document_id,))
        self._commit()

    def get_sections(
        self,
        *,
        document_id: str | None = None,
        paper_key: str | None = None,
        section_type: str | None = None,
    ) -> list[dict]:
        clauses = []
        params: list = []
        if document_id:
            clauses.append("s.document_id=?")
            params.append(document_id)
        if paper_key:
            clauses.append("d.paper_key=?")
            params.append(paper_key)
        if section_type:
            clauses.append("s.section_type=?")
            params.append(section_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT s.*, d.paper_key, d.document_type
            FROM document_sections s
            JOIN documents d ON d.document_id = s.document_id
            {where}
            ORDER BY s.section_order
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_acquisition_run(self, run: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO acquisition_runs(
                acquisition_id, paper_key, doi, started_at, finished_at, status,
                primary_document_id, deposition_section_id, error_kind, error_message, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(acquisition_id) DO UPDATE SET
                finished_at=excluded.finished_at,
                status=excluded.status,
                primary_document_id=excluded.primary_document_id,
                deposition_section_id=excluded.deposition_section_id,
                error_kind=excluded.error_kind,
                error_message=excluded.error_message,
                metadata_json=excluded.metadata_json
            """,
            (
                run["acquisition_id"],
                run.get("paper_key"),
                run.get("doi"),
                run.get("started_at"),
                run.get("finished_at"),
                run.get("status", "unknown"),
                run.get("primary_document_id"),
                run.get("deposition_section_id"),
                run.get("error_kind"),
                run.get("error_message"),
                json.dumps(run.get("metadata") or {}, ensure_ascii=False),
            ),
        )
        self._commit()

    def latest_acquisition(self, paper_key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM acquisition_runs WHERE paper_key=? ORDER BY started_at DESC LIMIT 1",
            (paper_key,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        except json.JSONDecodeError:
            result["metadata"] = {}
        return result

    def count_documents(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])

    def count_sections(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM document_sections").fetchone()[0])

    def count_tables(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM document_tables").fetchone()[0])

    def count_measurements(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0])

    # ------------------------------------------------------------- phase 4/5
    def save_recipe(self, record_id: str, paper_key: str, doi: str | None, recipe_id: str, sample_id: str | None, data: dict) -> None:
        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO recipes(record_id, paper_key, paper_doi, recipe_id, sample_id, review_status, data_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                paper_key=excluded.paper_key,
                paper_doi=excluded.paper_doi,
                recipe_id=excluded.recipe_id,
                sample_id=excluded.sample_id,
                review_status=excluded.review_status,
                data_json=excluded.data_json
            """,
            (
                record_id,
                paper_key,
                doi,
                recipe_id,
                sample_id,
                data.get("review_status", "new"),
                json.dumps(data, ensure_ascii=False),
                now,
            ),
        )
        self._commit()

    def save_target(self, target_record_id: str, record_id: str, data: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO targets(target_record_id, record_id, target_index, layer_role, target_material, target_power_w,
                power_basis, power_statistic, reported_power_text, target_diameter_mm, target_diameter_inch,
                power_density_w_cm2, sputtering_mode, equipment_model, deposition_rate_nm_min, data_json, evidence_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_record_id) DO UPDATE SET
                record_id=excluded.record_id,
                target_index=excluded.target_index,
                layer_role=excluded.layer_role,
                target_material=excluded.target_material,
                target_power_w=excluded.target_power_w,
                power_basis=excluded.power_basis,
                power_statistic=excluded.power_statistic,
                reported_power_text=excluded.reported_power_text,
                target_diameter_mm=excluded.target_diameter_mm,
                target_diameter_inch=excluded.target_diameter_inch,
                power_density_w_cm2=excluded.power_density_w_cm2,
                sputtering_mode=excluded.sputtering_mode,
                equipment_model=excluded.equipment_model,
                deposition_rate_nm_min=excluded.deposition_rate_nm_min,
                data_json=excluded.data_json,
                evidence_id=excluded.evidence_id
            """,
            (
                target_record_id,
                record_id,
                int(data.get("target_index", 1)),
                data.get("layer_role"),
                data.get("target_material"),
                _float_or_none(data.get("target_power_w")),
                data.get("power_basis"),
                data.get("power_statistic"),
                data.get("reported_power_text"),
                _float_or_none(data.get("target_diameter_mm")),
                _float_or_none(data.get("target_diameter_inch")),
                _float_or_none(data.get("power_density_w_cm2")),
                data.get("sputtering_mode"),
                data.get("equipment_model"),
                _float_or_none(data.get("deposition_rate_nm_min")),
                json.dumps(data, ensure_ascii=False),
                data.get("evidence_id"),
            ),
        )
        self._commit()

    def save_sample(self, sample_id: str, record_id: str, paper_key: str, data: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO samples(sample_id, record_id, paper_key, data_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(sample_id) DO UPDATE SET
                record_id=excluded.record_id,
                paper_key=excluded.paper_key,
                data_json=excluded.data_json
            """,
            (sample_id, record_id, paper_key, json.dumps(data, ensure_ascii=False)),
        )
        self._commit()

    def save_measurement(self, measurement_id: str, data: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO measurements(measurement_id, record_id, sample_id, property_key, property_value, property_unit, evidence_id, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(measurement_id) DO UPDATE SET
                record_id=excluded.record_id,
                sample_id=excluded.sample_id,
                property_key=excluded.property_key,
                property_value=excluded.property_value,
                property_unit=excluded.property_unit,
                evidence_id=excluded.evidence_id,
                data_json=excluded.data_json
            """,
            (
                measurement_id,
                data.get("record_id"),
                data.get("sample_id"),
                data.get("property_key"),
                _float_or_none(data.get("property_value")),
                data.get("property_unit"),
                data.get("evidence_id"),
                json.dumps(data, ensure_ascii=False),
            ),
        )
        self._commit()

    def save_evidence(self, evidence: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO evidence(
                evidence_id, record_id, entity_type, entity_id, field_key, paper_doi,
                source_url, source_section, page_table_figure, evidence_text,
                raw_value, raw_unit, normalized_value, normalized_unit,
                evidence_status, derivation_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(evidence_id) DO UPDATE SET
                record_id=excluded.record_id,
                entity_type=excluded.entity_type,
                entity_id=excluded.entity_id,
                field_key=excluded.field_key,
                paper_doi=excluded.paper_doi,
                source_url=excluded.source_url,
                source_section=excluded.source_section,
                page_table_figure=excluded.page_table_figure,
                evidence_text=excluded.evidence_text,
                raw_value=excluded.raw_value,
                raw_unit=excluded.raw_unit,
                normalized_value=excluded.normalized_value,
                normalized_unit=excluded.normalized_unit,
                evidence_status=excluded.evidence_status,
                derivation_note=excluded.derivation_note
            """,
            (
                evidence["evidence_id"],
                evidence.get("record_id"),
                evidence.get("entity_type", "recipe"),
                evidence.get("entity_id"),
                evidence["field_key"],
                evidence.get("paper_doi"),
                evidence.get("source_url"),
                evidence.get("source_section"),
                evidence.get("page_table_figure"),
                evidence.get("evidence_text"),
                evidence.get("raw_value"),
                evidence.get("raw_unit"),
                _float_or_none(evidence.get("normalized_value")),
                evidence.get("normalized_unit"),
                evidence.get("evidence_status", "reported"),
                evidence.get("derivation_note"),
            ),
        )
        self._commit()

    def save_extraction_run(self, run: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO extraction_runs(
                extraction_id, paper_key, doi, model, prompt_version, parser_version,
                started_at, finished_at, status, error, recipes_count, evidence_count, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(extraction_id) DO UPDATE SET
                finished_at=excluded.finished_at,
                status=excluded.status,
                error=excluded.error,
                recipes_count=excluded.recipes_count,
                evidence_count=excluded.evidence_count,
                metadata_json=excluded.metadata_json
            """,
            (
                run["extraction_id"],
                run.get("paper_key"),
                run.get("doi"),
                run.get("model"),
                run.get("prompt_version"),
                run.get("parser_version"),
                run.get("started_at"),
                run.get("finished_at"),
                run.get("status", "running"),
                run.get("error"),
                int(run.get("recipes_count", 0)),
                int(run.get("evidence_count", 0)),
                json.dumps(run.get("metadata") or {}, ensure_ascii=False),
            ),
        )
        self._commit()

    def add_review_item(self, item: dict) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO review_queue(
                review_id, paper_key, record_id, field_key, reason, severity, evidence_id, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["review_id"],
                item.get("paper_key"),
                item.get("record_id"),
                item.get("field_key"),
                item.get("reason", ""),
                item.get("severity", "medium"),
                item.get("evidence_id"),
                item.get("status", "open"),
                utc_now_iso(),
            ),
        )
        self._commit()

    def set_recipe_review_status(self, record_id: str, status: str) -> None:
        self.conn.execute("UPDATE recipes SET review_status=? WHERE record_id=?", (status, record_id))
        self._commit()

    def _latest_extraction_id(self, paper_key: str) -> str | None:
        row = self.conn.execute(
            "SELECT extraction_id FROM extraction_runs WHERE paper_key=? ORDER BY started_at DESC LIMIT 1",
            (paper_key,),
        ).fetchone()
        return row["extraction_id"] if row else None

    def record_field_review(self, record_id: str, field_key: str, decision: str, note: str = "") -> dict:
        if decision not in {"verified", "rejected", "needs_review"}:
            raise ValueError(f"invalid field decision: {decision}")
        row = self.conn.execute(
            "SELECT paper_key, data_json FROM recipes WHERE record_id=?", (record_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"recipe not found: {record_id}")
        data = json.loads(row["data_json"])
        if field_key not in data or data[field_key] is None:
            raise ValueError(f"field is not present/non-null: {field_key}")
        evidence = self.conn.execute(
            "SELECT evidence_id FROM evidence WHERE record_id=? AND field_key=? ORDER BY evidence_id LIMIT 1",
            (record_id, field_key),
        ).fetchone()
        if evidence is None and field_key not in DERIVED_AUDIT_FIELDS:
            raise ValueError(f"field has no evidence; cannot review: {field_key}")
        evidence_id = evidence["evidence_id"] if evidence else None
        extraction_id = self._latest_extraction_id(row["paper_key"])
        old_decision_row = self.conn.execute(
            "SELECT decision FROM field_reviews WHERE record_id=? AND field_key=?", (record_id, field_key)
        ).fetchone()
        old_decision = old_decision_row["decision"] if old_decision_row else None
        review_id = f"field_{uuid.uuid4().hex[:16]}"
        self.conn.execute(
            """
            INSERT INTO field_reviews(review_id, record_id, field_key, decision, evidence_id, note, extraction_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_id, field_key) DO UPDATE SET
              decision=excluded.decision,
              evidence_id=excluded.evidence_id,
              note=excluded.note,
              extraction_id=excluded.extraction_id,
              created_at=excluded.created_at
            """,
            (review_id, record_id, field_key, decision, evidence_id, note, extraction_id, utc_now_iso()),
        )
        action_id = f"action_{uuid.uuid4().hex[:16]}"
        self.conn.execute(
            "INSERT INTO review_actions(action_id, record_id, action, old_status, new_status, note, extraction_id, field_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (action_id, record_id, "field_review", old_decision, decision, note, extraction_id, field_key, utc_now_iso()),
        )
        self._commit()
        return {
            "record_id": record_id,
            "field_key": field_key,
            "decision": decision,
            "evidence_id": evidence_id,
            "extraction_id": extraction_id,
        }

    def resolve_review_item(self, review_id: str, note: str = "") -> dict:
        row = self.conn.execute(
            "SELECT record_id, paper_key FROM review_queue WHERE review_id=?", (review_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"review item not found: {review_id}")
        self.conn.execute("UPDATE review_queue SET status='resolved' WHERE review_id=?", (review_id,))
        extraction_id = self._latest_extraction_id(row["paper_key"])
        action_id = f"action_{uuid.uuid4().hex[:16]}"
        self.conn.execute(
            "INSERT INTO review_actions(action_id, record_id, action, old_status, new_status, note, extraction_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (action_id, row["record_id"], "resolve_review", "open", "resolved", note, extraction_id, utc_now_iso()),
        )
        self._commit()
        return {"action_id": action_id, "review_id": review_id, "status": "resolved", "note": note}

    def list_field_reviews(self, record_id: str | None = None) -> list[dict]:
        if record_id:
            rows = self.conn.execute(
                "SELECT * FROM field_reviews WHERE record_id=? ORDER BY field_key", (record_id,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM field_reviews ORDER BY record_id, field_key").fetchall()
        return [dict(row) for row in rows]

    def missing_field_reviews(self, record_id: str, data: dict, extraction_id: str | None) -> list[str]:
        metadata = {
            "record_id", "paper_doi", "paper_title", "publication_year", "recipe_id",
            "sample_id", "record_scope", "review_status", "remarks",
        }
        latest = {
            row["field_key"]
            for row in self.conn.execute(
                "SELECT field_key FROM field_reviews WHERE record_id=? AND decision='verified' AND extraction_id IS ?",
                (record_id, extraction_id),
            )
        }
        return [
            key for key, value in data.items()
            if key not in metadata and value is not None and key not in latest
        ]

    def record_review_action(self, record_id: str, status: str, note: str = "") -> dict:
        if status not in {"new", "needs_review", "verified", "rejected"}:
            raise ValueError(f"invalid review status: {status}")
        row = self.conn.execute(
            "SELECT paper_key, review_status, data_json FROM recipes WHERE record_id=?", (record_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"recipe not found: {record_id}")
        data = json.loads(row["data_json"])
        old_status = row["review_status"]

        if status == "verified":
            extraction_id = self._latest_extraction_id(row["paper_key"])
            missing_field_reviews = self.missing_field_reviews(record_id, data, extraction_id)
            evidence_fields = {
                item["field_key"]
                for item in self.conn.execute("SELECT field_key FROM evidence WHERE record_id=?", (record_id,))
            }
            numeric_fields = (
                "target1_power_w", "target2_power_w", "working_pressure_mbar", "base_pressure_mbar",
                "substrate_temperature_c", "alscn_thickness_nm", "n2_flow_sccm", "ar_flow_sccm",
                "film_sc_at_pct", "target1_sc_at_pct", "target_substrate_distance_mm", "deposition_time_min",
            )
            missing_evidence = [
                key for key in numeric_fields if data.get(key) is not None and key not in evidence_fields
            ]
            high_open = int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM review_queue WHERE record_id=? AND status='open' AND severity='high'",
                    (record_id,),
                ).fetchone()[0]
            )
            bad_targets = []
            for target in self.conn.execute(
                "SELECT target_record_id, evidence_id FROM targets WHERE record_id=?", (record_id,)
            ):
                evidence = self.conn.execute(
                    "SELECT entity_id FROM evidence WHERE evidence_id=?", (target["evidence_id"],)
                ).fetchone()
                if evidence is None or evidence["entity_id"] != target["target_record_id"]:
                    bad_targets.append(target["target_record_id"])
            if missing_evidence or high_open or bad_targets or missing_field_reviews:
                raise ValueError(
                    "cannot verify recipe: "
                    f"missing_evidence={missing_evidence}, high_open_reviews={high_open}, "
                    f"bad_targets={bad_targets}, missing_field_reviews={missing_field_reviews}"
                )

        data["review_status"] = status
        extraction = self.conn.execute(
            "SELECT extraction_id FROM extraction_runs WHERE paper_key=? ORDER BY started_at DESC LIMIT 1",
            (row["paper_key"],),
        ).fetchone()
        extraction_id = extraction["extraction_id"] if extraction else None
        action_id = f"action_{utc_now_iso()}_{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            "UPDATE recipes SET review_status=?, data_json=? WHERE record_id=?",
            (status, json.dumps(data, ensure_ascii=False), record_id),
        )
        self.conn.execute(
            "INSERT INTO review_actions(action_id, record_id, action, old_status, new_status, note, extraction_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (action_id, record_id, "set_status", old_status, status, note, extraction_id, utc_now_iso()),
        )
        self._commit()
        return {
            "action_id": action_id,
            "record_id": record_id,
            "old_status": old_status,
            "new_status": status,
            "note": note,
            "extraction_id": extraction_id,
        }

    def list_review_actions(self, record_id: str | None = None) -> list[dict]:
        if record_id:
            rows = self.conn.execute(
                "SELECT * FROM review_actions WHERE record_id=? ORDER BY created_at", (record_id,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM review_actions ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    def list_recipes(self, paper_key: str | None = None) -> list[dict]:
        if paper_key:
            rows = self.conn.execute("SELECT * FROM recipes WHERE paper_key=? ORDER BY record_id", (paper_key,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM recipes ORDER BY paper_key, record_id").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item.pop("data_json"))
            result.append(item)
        return result

    def list_targets(self, record_id: str | None = None) -> list[dict]:
        if record_id:
            rows = self.conn.execute(
                "SELECT * FROM targets WHERE record_id=? ORDER BY target_index", (record_id,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM targets ORDER BY record_id, target_index").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item.pop("data_json"))
            result.append(item)
        return result

    def list_evidence(self, record_id: str | None = None) -> list[dict]:
        if record_id:
            rows = self.conn.execute(
                "SELECT * FROM evidence WHERE record_id=? ORDER BY field_key, evidence_id", (record_id,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM evidence ORDER BY record_id, field_key").fetchall()
        return [dict(row) for row in rows]

    def list_extraction_runs(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM extraction_runs ORDER BY started_at").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            result.append(item)
        return result

    def count_recipes(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0])

    def count_evidence(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0])

    def count_review_items(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM review_queue WHERE status='open'").fetchone()[0])

    def count_samples(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0])

    def orphan_measurements(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT m.measurement_id, m.record_id, m.sample_id, r.paper_key
            FROM measurements m
            LEFT JOIN samples s ON s.sample_id = m.sample_id
            LEFT JOIN recipes r ON r.record_id = m.record_id
            WHERE s.sample_id IS NULL OR r.record_id IS NULL
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def recipes_missing_samples(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT r.record_id, r.paper_key, r.sample_id
            FROM recipes r
            LEFT JOIN samples s ON s.sample_id = r.sample_id
            WHERE s.sample_id IS NULL
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def invalid_target_evidence(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT t.target_record_id, t.evidence_id, t.record_id, r.paper_key
            FROM targets t
            JOIN recipes r ON r.record_id = t.record_id
            LEFT JOIN evidence e ON e.evidence_id = t.evidence_id
            WHERE e.evidence_id IS NULL OR e.entity_id != t.target_record_id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def clear_extraction(self, paper_key: str) -> None:
        record_rows = self.conn.execute("SELECT record_id FROM recipes WHERE paper_key=?", (paper_key,)).fetchall()
        record_ids = [row["record_id"] for row in record_rows]
        for record_id in record_ids:
            self.conn.execute("DELETE FROM targets WHERE record_id=?", (record_id,))
            self.conn.execute("DELETE FROM samples WHERE record_id=?", (record_id,))
            self.conn.execute("DELETE FROM measurements WHERE record_id=?", (record_id,))
            self.conn.execute("DELETE FROM evidence WHERE record_id=?", (record_id,))
            self.conn.execute("DELETE FROM review_queue WHERE record_id=?", (record_id,))
        self.conn.execute("DELETE FROM recipes WHERE paper_key=?", (paper_key,))
        self._commit()

    def cache_put(
        self,
        key: str,
        *,
        source: str,
        url: str,
        status: int,
        body: bytes,
        fetched_at: str,
        expires_at: str | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO api_cache(cache_key, source, url, status, body, fetched_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                source=excluded.source,
                url=excluded.url,
                status=excluded.status,
                body=excluded.body,
                fetched_at=excluded.fetched_at,
                expires_at=excluded.expires_at
            """,
            (key, source, url, status, sqlite3.Binary(body), fetched_at, expires_at),
        )
        self._commit()


def _first_pdf_url(cluster: PaperCluster) -> str | None:
    for record in cluster.records:
        if record.pdf_url:
            return record.pdf_url
    return None


def _float_or_none(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
