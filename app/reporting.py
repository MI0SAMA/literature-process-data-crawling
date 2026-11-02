"""CSV/JSON exports and gold-standard evaluation for Phase 2."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.identifiers import normalize_doi
from app.models import utc_now_iso
from app.storage import Database

CANDIDATE_COLUMNS = [
    "paper_key",
    "doi",
    "title",
    "publication_year",
    "venue",
    "item_type",
    "first_author_family",
    "sources",
    "rule_label",
    "final_label",
    "confidence",
    "review_required",
    "eligible_for_confirmation",
    "matched_material",
    "matched_method",
    "flags",
    "reasons",
]

REVIEW_COLUMNS = [
    "review_id",
    "left_source",
    "left_source_id",
    "left_title",
    "right_source",
    "right_source_id",
    "right_title",
    "similarity",
    "reason",
    "status",
]


def write_run_outputs(db: Database, run_id: str, out_dir: str | Path, *, summary=None) -> dict[str, str]:
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    papers = db.list_papers(run_id)
    executions = db.list_query_executions(run_id)
    reviews = db.list_review_items(run_id)
    run = db.get_run(run_id) or {}

    candidates_csv = output / "candidates.csv"
    _write_csv(
        candidates_csv,
        CANDIDATE_COLUMNS,
        [_candidate_row(paper) for paper in papers],
    )

    candidates_json = output / "candidates.json"
    candidates_json.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "generated_at": utc_now_iso(),
                "count": len(papers),
                "papers": [_candidate_row(paper) for paper in papers],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    review_csv = output / "review_queue.csv"
    _write_csv(review_csv, REVIEW_COLUMNS, [_review_row(item) for item in reviews])

    search_log = output / "search_log.json"
    search_log.write_text(
        json.dumps(
            {
                "run": run,
                "executions": executions,
                "reviews": reviews,
                "generated_at": utc_now_iso(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    summary_json = output / "run_summary.json"
    if summary is not None:
        summary_payload = summary.to_dict()
    else:
        summary_payload = {
            "run_id": run_id,
            "status": run.get("status"),
            "records_in": run.get("record_count"),
            "papers_out": run.get("paper_count"),
        }
    summary_json.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "run_dir": str(output),
        "candidates_csv": str(candidates_csv),
        "candidates_json": str(candidates_json),
        "review_queue_csv": str(review_csv),
        "search_log_json": str(search_log),
        "run_summary_json": str(summary_json),
    }


def read_gold_csv(path: str | Path) -> list[dict[str, Any]]:
    gold_path = Path(path)
    if not gold_path.is_file():
        raise FileNotFoundError(f"gold standard file not found: {gold_path}")
    rows: list[dict[str, Any]] = []
    with gold_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            doi = normalize_doi(row.get("doi"))
            if not doi:
                continue
            rows.append(
                {
                    "doi": doi,
                    "title": (row.get("title") or "").strip(),
                    "year": _int_or_none(row.get("year")),
                    "category": (row.get("category") or "").strip(),
                    "notes": (row.get("notes") or "").strip(),
                }
            )
    if not rows:
        raise ValueError(f"gold standard contains no valid DOI rows: {gold_path}")
    return rows


def evaluate_gold(db: Database, gold_path: str | Path) -> dict[str, Any]:
    gold = read_gold_csv(gold_path)
    papers = db.list_papers()
    found_dois = {paper["doi_norm"] for paper in papers if paper.get("doi_norm")}
    likely_dois = {
        paper["doi_norm"]
        for paper in papers
        if paper.get("doi_norm") and paper.get("final_label") == "likely_AlScN_PVD"
    }
    found = [item for item in gold if item["doi"] in found_dois]
    missing = [item for item in gold if item["doi"] not in found_dois]
    likely_found = [item for item in gold if item["doi"] in likely_dois]
    duplicates = db.list_duplicate_dois()
    return {
        "gold_count": len(gold),
        "found_count": len(found),
        "recall": round(len(found) / len(gold), 4),
        "likely_count": len(likely_found),
        "likely_recall": round(len(likely_found) / len(gold), 4),
        "missing": missing,
        "found": found,
        "duplicate_dois": [{"doi": doi, "count": count} for doi, count in duplicates],
        "duplicate_doi_count": len(duplicates),
        "papers_in_db": len(papers),
    }


def evaluate_screening_labels(db: Database, labels_path: str | Path) -> dict[str, Any]:
    """Evaluate candidate-level classification on a small manually labeled set."""

    path = Path(labels_path)
    if not path.is_file():
        raise FileNotFoundError(f"screening labels file not found: {path}")
    labels: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            doi = normalize_doi(row.get("doi"))
            expected = (row.get("expected_label") or "").strip()
            if doi and expected:
                labels.append({"doi": doi, "expected_label": expected, "title": row.get("title", "")})
    papers = {paper["doi_norm"]: paper for paper in db.list_papers() if paper.get("doi_norm")}
    evaluated = 0
    correct = 0
    likely_tp = likely_fp = likely_fn = 0
    unclear = 0
    confusion: dict[str, dict[str, int]] = {}
    details = []
    for item in labels:
        paper = papers.get(item["doi"])
        expected = item["expected_label"]
        if not paper:
            details.append({**item, "predicted_label": None, "correct": False, "reason": "not_retrieved"})
            if expected == "likely_AlScN_PVD":
                likely_fn += 1
            continue
        predicted = paper.get("final_label") or "not_relevant"
        evaluated += 1
        confusion.setdefault(expected, {})
        confusion[expected][predicted] = confusion[expected].get(predicted, 0) + 1
        is_correct = predicted == expected
        if is_correct:
            correct += 1
        if expected == "likely_AlScN_PVD" and predicted == "likely_AlScN_PVD":
            likely_tp += 1
        elif expected != "likely_AlScN_PVD" and predicted == "likely_AlScN_PVD":
            likely_fp += 1
        elif expected == "likely_AlScN_PVD" and predicted != "likely_AlScN_PVD":
            likely_fn += 1
        if predicted == "unclear":
            unclear += 1
        details.append({**item, "predicted_label": predicted, "correct": is_correct, "reason": ""})
    accuracy = correct / evaluated if evaluated else 0.0
    precision_likely = likely_tp / (likely_tp + likely_fp) if (likely_tp + likely_fp) else 0.0
    recall_likely = likely_tp / (likely_tp + likely_fn) if (likely_tp + likely_fn) else 0.0
    return {
        "labels": len(labels),
        "evaluated": evaluated,
        "correct": correct,
        "accuracy": round(accuracy, 4),
        "precision_likely": round(precision_likely, 4),
        "recall_likely": round(recall_likely, 4),
        "likely_tp": likely_tp,
        "likely_fp": likely_fp,
        "likely_fn": likely_fn,
        "unclear": unclear,
        "confusion": confusion,
        "details": details,
    }


def _candidate_row(paper: dict) -> dict[str, Any]:
    return {
        "paper_key": paper.get("paper_key"),
        "doi": paper.get("doi") or "",
        "title": paper.get("title") or "",
        "publication_year": paper.get("publication_year") or "",
        "venue": paper.get("venue") or "",
        "item_type": paper.get("item_type") or "",
        "first_author_family": paper.get("first_author_family") or "",
        "sources": paper.get("sources") or "",
        "rule_label": paper.get("rule_label") or "",
        "final_label": paper.get("final_label") or "",
        "confidence": paper.get("confidence") if paper.get("confidence") is not None else "",
        "review_required": bool(paper.get("review_required")),
        "eligible_for_confirmation": bool(paper.get("eligible_for_confirmation")),
        "matched_material": "; ".join(paper.get("matched_material") or []),
        "matched_method": "; ".join(paper.get("matched_method") or []),
        "flags": "; ".join(paper.get("flags") or []),
        "reasons": "; ".join(paper.get("reasons") or []),
    }


def _review_row(item: dict) -> dict[str, Any]:
    return {column: item.get(column, "") for column in REVIEW_COLUMNS}


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
