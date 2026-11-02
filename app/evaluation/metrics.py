"""Phase 7 quantitative quality report for the extraction corpus."""

from __future__ import annotations

from collections import Counter

from app.storage import Database

NUMERIC_FIELDS = (
    "target1_power_w",
    "target2_power_w",
    "working_pressure_mbar",
    "base_pressure_mbar",
    "substrate_temperature_c",
    "alscn_thickness_nm",
    "n2_flow_sccm",
    "ar_flow_sccm",
    "film_sc_at_pct",
    "target1_sc_at_pct",
    "target_substrate_distance_mm",
    "deposition_time_min",
)


def extraction_metrics(db: Database) -> dict:
    recipes = db.list_recipes()
    evidence = db.list_evidence()
    acquisitions = [dict(row) for row in db.conn.execute("SELECT status, COUNT(*) AS c FROM acquisition_runs GROUP BY status")]
    extractions = [dict(row) for row in db.conn.execute("SELECT status, COUNT(*) AS c FROM extraction_runs GROUP BY status")]
    reviews = [dict(row) for row in db.conn.execute("SELECT severity, status, COUNT(*) AS c FROM review_queue GROUP BY severity, status")]
    evidence_by_record: dict[str, set[str]] = {}
    for item in evidence:
        evidence_by_record.setdefault(item["record_id"], set()).add(item["field_key"])

    coverage = {}
    for field in NUMERIC_FIELDS:
        present = sum(1 for recipe in recipes if recipe["data"].get(field) is not None)
        coverage[field] = {
            "present": present,
            "total": len(recipes),
            "rate": round(present / len(recipes), 4) if recipes else 0.0,
        }

    numeric_total = 0
    numeric_with_evidence = 0
    for recipe in recipes:
        record_id = recipe["record_id"]
        for field in NUMERIC_FIELDS:
            if recipe["data"].get(field) is None:
                continue
            numeric_total += 1
            if field in evidence_by_record.get(record_id, set()):
                numeric_with_evidence += 1

    return {
        "papers_total": db.count_papers(),
        "documents": db.count_documents(),
        "sections": db.count_sections(),
        "tables": db.count_tables(),
        "acquisitions": {row["status"]: row["c"] for row in acquisitions},
        "recipes": len(recipes),
        "measurements": db.count_measurements(),
        "samples": db.count_samples(),
        "orphan_measurements": len(db.orphan_measurements()),
        "recipes_missing_samples": len(db.recipes_missing_samples()),
        "invalid_target_evidence": len(db.invalid_target_evidence()),
        "recipes_per_paper": round(len(recipes) / len({recipe["paper_key"] for recipe in recipes}), 4) if recipes else 0.0,
        "evidence": len(evidence),
        "evidence_per_recipe": round(len(evidence) / len(recipes), 4) if recipes else 0.0,
        "numeric_fields": numeric_total,
        "numeric_fields_with_evidence": numeric_with_evidence,
        "evidence_rate": round(numeric_with_evidence / numeric_total, 4) if numeric_total else 0.0,
        "field_coverage": coverage,
        "extraction_runs": {row["status"]: row["c"] for row in extractions},
        "review_queue": [
            {"severity": row["severity"], "status": row["status"], "count": row["c"]}
            for row in reviews
        ],
        "duplicate_dois": db.list_duplicate_dois(),
    }


def metrics_markdown(metrics: dict) -> str:
    lines = [
        "# Extraction metrics",
        "",
        f"- papers total: {metrics['papers_total']}",
        f"- recipes: {metrics['recipes']}",
        f"- measurements: {metrics['measurements']}",
        f"- samples: {metrics['samples']}",
        f"- orphan measurements: {metrics['orphan_measurements']}",
        f"- recipes missing samples: {metrics['recipes_missing_samples']}",
        f"- invalid target evidence: {metrics['invalid_target_evidence']}",
        f"- evidence: {metrics['evidence']}",
        f"- numeric field evidence rate: {metrics['evidence_rate']:.2%}",
        f"- documents: {metrics['documents']}",
        f"- sections: {metrics['sections']}",
        f"- tables: {metrics['tables']}",
        "",
        "## Acquisitions",
        "",
    ]
    for key, value in sorted(metrics["acquisitions"].items()):
        lines.append(f"- {key}: {value}")
    lines += ["", "## Field coverage", "", "| field | present | total | rate |", "|---|---:|---:|---:|"]
    for field, item in metrics["field_coverage"].items():
        lines.append(f"| {field} | {item['present']} | {item['total']} | {item['rate']:.1%} |")
    lines += ["", "## Extraction runs", ""]
    for key, value in sorted(metrics["extraction_runs"].items()):
        lines.append(f"- {key}: {value}")
    if metrics["duplicate_dois"]:
        lines += ["", "## Duplicate DOIs", ""]
        for doi, count in metrics["duplicate_dois"]:
            lines.append(f"- {doi}: {count}")
    return "\n".join(lines) + "\n"
