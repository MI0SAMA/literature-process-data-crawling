"""Batch extraction pipeline: DB -> rules -> recipes/targets/evidence/runs."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.extraction.extractor import RuleBasedExtractor
from app.extraction.models import ExtractionResult
from app.extraction.performance import extract_measurements
from app.models import utc_now_iso
from app.storage import Database


class ExtractionPipeline:
    def __init__(self, db: Database, *, output_dir: str | Path = "data/structured/extractions") -> None:
        self.db = db
        self.extractor = RuleBasedExtractor()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._pending_result: tuple[Path, Path] | None = None
        self._run_context: dict | None = None

    def extract_paper(self, paper_key: str, *, force: bool = False) -> ExtractionResult:
        self._pending_result = None
        self._run_context = None
        self.db.begin()
        try:
            result = self._extract_paper(paper_key, force=force)
        except Exception as error:
            self.db.rollback()
            self._discard_pending()
            self._log_aborted("failed", f"{type(error).__name__}: {error}")
            raise
        if force and result.status != "extracted":
            self.db.rollback()
            self._discard_pending()
            self._log_aborted("rolled_back", "; ".join(result.warnings) or result.status)
            return result
        try:
            self.db.commit()
        except Exception as error:
            self.db.rollback()
            self._discard_pending()
            self._log_aborted("commit_failed", f"{type(error).__name__}: {error}")
            raise
        try:
            self._publish_pending()
            self.db.set_artifact_status(self._run_context["extraction_id"], "published")
        except Exception as error:
            self._discard_pending()
            self.db.set_artifact_status(
                self._run_context["extraction_id"], "failed", f"artifact publish failed: {type(error).__name__}: {error}"
            )
            result.warnings.append(f"artifact publish failed: {type(error).__name__}: {error}")
        return result

    def _extract_paper(self, paper_key: str, *, force: bool = False) -> ExtractionResult:
        if force:
            self.db.clear_extraction(paper_key)
        paper = self.db.get_paper(paper_key)
        if not paper:
            return ExtractionResult(paper_key=paper_key, doi="", status="failed", warnings=["paper not found"])
        sections = self.db.get_sections(paper_key=paper_key)
        tables = self.db.get_tables(paper_key=paper_key)
        documents = self.db.list_documents(paper_key)
        primary = next((document for document in documents if document.get("is_primary")), None)
        if primary is None and documents:
            primary = documents[0]
        source_url = (primary or {}).get("final_url") or (primary or {}).get("source_url")

        extraction_id = f"ext_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
        started_at = utc_now_iso()
        self._run_context = {
            "extraction_id": extraction_id,
            "paper_key": paper_key,
            "doi": paper.get("doi"),
            "started_at": started_at,
        }
        try:
            result = self.extractor.extract(
                paper_key=paper_key,
                doi=paper.get("doi") or "",
                title=paper.get("title") or "",
                year=paper.get("publication_year"),
                sections=sections,
                tables=tables,
                source_url=source_url,
                source_document_id=(primary or {}).get("document_id"),
            )
        except Exception as error:
            result = ExtractionResult(
                paper_key=paper_key,
                doi=paper.get("doi") or "",
                status="failed",
                warnings=[f"{type(error).__name__}: {error}"],
            )

        evidence_count = 0
        if result.status == "extracted":
            for recipe in result.recipes:
                self.db.save_recipe(
                    recipe.record_id,
                    paper_key,
                    paper.get("doi") or "",
                    recipe.recipe_id,
                    recipe.sample_id,
                    recipe.data,
                )
                self.db.save_sample(
                    recipe.sample_id,
                    recipe.record_id,
                    paper_key,
                    {
                        "sample_id": recipe.sample_id,
                        "record_id": recipe.record_id,
                        "substrate_material": recipe.data.get("substrate_material"),
                        "substrate_stack": recipe.data.get("substrate_stack"),
                        "alscn_thickness_nm": recipe.data.get("alscn_thickness_nm"),
                    },
                )
                for target in recipe.targets:
                    index = int(target.get("target_index", 1))
                    target.setdefault(
                        "evidence_id",
                        f"{recipe.record_id}:target{index}_power_w"
                        if target.get("target_power_w") is not None
                        else f"{recipe.record_id}:target{index}_material",
                    )
                    self.db.save_target(target["target_record_id"], recipe.record_id, target)
                    evidence = next(
                        (item for item in result.evidence if item["evidence_id"] == target["evidence_id"]),
                        None,
                    )
                    if evidence is None or evidence.get("entity_id") != target["target_record_id"]:
                        recipe.review_items.append(
                            {
                                "review_id": f"rev_target_{uuid.uuid4().hex[:12]}",
                                "paper_key": paper_key,
                                "record_id": recipe.record_id,
                                "field_key": f"target{index}.evidence_id",
                                "reason": "target evidence is missing or points to another entity",
                                "severity": "high",
                                "evidence_id": target.get("evidence_id"),
                                "status": "open",
                            }
                        )
                for evidence in result.evidence:
                    self.db.save_evidence(evidence)
                    evidence_count += 1
                for item in recipe.review_items:
                    self.db.add_review_item(item)
            measurements = extract_measurements(
                paper_key,
                paper.get("doi") or "",
                sections,
                source_url=source_url,
            )
            for measurement in measurements:
                evidence = measurement.get("evidence")
                payload = {key: value for key, value in measurement.items() if key != "evidence"}
                self.db.save_measurement(payload["measurement_id"], payload)
                if payload.get("review_required"):
                    self.db.add_review_item(
                        {
                            "review_id": f"rev_meas_{uuid.uuid4().hex[:12]}",
                            "paper_key": paper_key,
                            "record_id": payload["record_id"],
                            "field_key": f"measurement.{payload['property_key']}",
                            "reason": payload.get("review_reason") or "performance value requires review",
                            "severity": "medium",
                            "evidence_id": payload.get("evidence_id"),
                            "status": "open",
                        }
                    )
                if evidence:
                    self.db.save_evidence(evidence)
                    evidence_count += 1
            result.measurements = measurements
            self._write_result(result)

        self.db.save_extraction_run(
            {
                "extraction_id": extraction_id,
                "paper_key": paper_key,
                "doi": paper.get("doi"),
                "model": self.extractor.model,
                "prompt_version": self.extractor.prompt_version,
                "parser_version": self.extractor.parser_version,
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "status": result.status,
                "error": "; ".join(result.warnings) or None,
                "recipes_count": len(result.recipes),
                "evidence_count": evidence_count,
                "artifact_status": "pending",
                "metadata": result.metadata,
            }
        )
        return result

    def extract_batch(self, limit: int | None = None, *, force: bool = False) -> dict:
        if force:
            rows = self.db.conn.execute(
                """
                SELECT p.paper_key
                FROM papers p
                JOIN documents d ON d.paper_key = p.paper_key AND d.is_primary = 1
                GROUP BY p.paper_key
                ORDER BY p.publication_year DESC
                """
            ).fetchall()
        else:
            rows = self.db.conn.execute(
                """
                SELECT p.paper_key
                FROM papers p
                JOIN documents d ON d.paper_key = p.paper_key AND d.is_primary = 1
                WHERE p.paper_key NOT IN (
                    SELECT er.paper_key FROM extraction_runs er
                    WHERE er.extraction_id = (
                        SELECT e2.extraction_id FROM extraction_runs e2
                        WHERE e2.paper_key = er.paper_key
                        ORDER BY e2.started_at DESC, e2.rowid DESC LIMIT 1
                    )
                    AND er.status = 'extracted'
                    AND COALESCE(er.artifact_status, 'published') = 'published'
                )
                GROUP BY p.paper_key
                ORDER BY p.publication_year DESC
                """
            ).fetchall()
        keys = [row["paper_key"] for row in rows]
        if limit is not None:
            keys = keys[:limit]
        results = []
        for key in keys:
            try:
                result = self.extract_paper(key, force=force)
                summary = {
                    "paper_key": key,
                    "status": result.status,
                    "recipes": len(result.recipes),
                    "evidence": len(result.evidence),
                    "fields": sum(len(recipe.fields) for recipe in result.recipes),
                }
            except Exception as error:
                summary = {
                    "paper_key": key,
                    "status": "failed",
                    "recipes": 0,
                    "evidence": 0,
                    "fields": 0,
                    "error": f"{type(error).__name__}: {error}",
                }
            results.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
        return {
            "attempted": len(results),
            "extracted": sum(1 for item in results if item["status"] == "extracted"),
            "no_deposition_section": sum(1 for item in results if item["status"] == "no_deposition_section"),
            "failed": sum(1 for item in results if item["status"] == "failed"),
            "recipes": self.db.count_recipes(),
            "evidence": self.db.count_evidence(),
            "review_items": self.db.count_review_items(),
        }

    def _write_result(self, result: ExtractionResult) -> None:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", result.paper_key)[:120]
        path = self.output_dir / f"{safe}.json"
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self._pending_result = (temp, path)

    def _publish_pending(self) -> None:
        if self._pending_result:
            temp, path = self._pending_result
            temp.replace(path)
            self._pending_result = None

    def _discard_pending(self) -> None:
        if self._pending_result:
            temp, _ = self._pending_result
            temp.unlink(missing_ok=True)
            self._pending_result = None

    def _log_aborted(self, status: str, error: str) -> None:
        context = self._run_context
        if not context:
            return
        try:
            self.db.save_extraction_run(
                {
                    "extraction_id": context["extraction_id"],
                    "paper_key": context["paper_key"],
                    "doi": context.get("doi"),
                    "model": "rule-based",
                    "prompt_version": "none",
                    "parser_version": "phase4-rules-v1",
                    "started_at": context["started_at"],
                    "finished_at": utc_now_iso(),
                    "status": status,
                    "error": error,
                    "recipes_count": 0,
                    "evidence_count": 0,
                    "artifact_status": "failed",
                    "metadata": {"rolled_back": True},
                }
            )
        except Exception:
            # Failure logging must not mask the original extraction failure.
            pass
