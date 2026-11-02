"""Phase 5 deterministic validation and review-queue generation."""

from __future__ import annotations

import hashlib
import re

from app.storage import Database

_NUMERIC_FIELDS = {
    "target1_power_w": (0, 5000, "W"),
    "target2_power_w": (0, 5000, "W"),
    "working_pressure_mbar": (1e-10, 10, "mbar"),
    "base_pressure_mbar": (1e-12, 10, "mbar"),
    "substrate_temperature_c": (-100, 1200, "°C"),
    "alscn_thickness_nm": (0.1, 100000, "nm"),
    "n2_flow_sccm": (0, 2000, "sccm"),
    "ar_flow_sccm": (0, 2000, "sccm"),
    "film_sc_at_pct": (0, 100, "at.%"),
    "target1_sc_at_pct": (0, 100, "at.%"),
    "target_substrate_distance_mm": (1, 2000, "mm"),
    "deposition_time_min": (0.01, 10000, "min"),
}


class RecipeValidator:
    def validate(self, recipe: dict, evidence: list[dict]) -> list[dict]:
        data = recipe.get("data") or {}
        record_id = recipe.get("record_id")
        paper_key = recipe.get("paper_key")
        evidence_by_field = {item["field_key"]: item for item in evidence}
        issues = []

        def add(field_key: str, reason: str, severity: str = "medium") -> None:
            digest = hashlib.sha256(f"{record_id}|{field_key}|{reason}".encode()).hexdigest()[:12]
            issues.append(
                {
                    "review_id": f"rev_{digest}",
                    "paper_key": paper_key,
                    "record_id": record_id,
                    "field_key": field_key,
                    "reason": reason,
                    "severity": severity,
                    "evidence_id": (evidence_by_field.get(field_key) or {}).get("evidence_id"),
                    "status": "open",
                }
            )

        if not data.get("paper_doi"):
            add("paper_doi", "missing paper DOI", "high")
        if not data.get("paper_title"):
            add("paper_title", "missing paper title", "low")

        for field_key, value in data.items():
            if field_key not in _NUMERIC_FIELDS or value is None:
                continue
            minimum, maximum, unit = _NUMERIC_FIELDS[field_key]
            try:
                number = float(value)
            except (TypeError, ValueError):
                add(field_key, f"value is not numeric: {value!r}", "high")
                continue
            if not (minimum <= number <= maximum):
                add(field_key, f"value {number} {unit} outside plausible {minimum}-{maximum}", "high")
            if field_key not in evidence_by_field:
                add(field_key, "numeric value has no evidence record", "high")

        if data.get("ar_flow_sccm") == 0:
            evidence_text = (evidence_by_field.get("ar_flow_sccm") or {}).get("evidence_text", "").casefold()
            if not any(term in evidence_text for term in ("no ar", "ar-free", "without ar", "no argon")):
                add("ar_flow_sccm", "Ar=0 without explicit no-Ar evidence", "high")

        if (
            data.get("base_pressure_mbar") is not None
            and data.get("working_pressure_mbar") is not None
            and data["base_pressure_mbar"] >= data["working_pressure_mbar"]
        ):
            add("base_pressure_mbar", "base pressure is not lower than working pressure", "medium")

        # Semantic checks for known audit failure modes. These are deliberately
        # conservative: they flag suspicious records, never auto-correct them.
        target_config = str(data.get("target_configuration") or "").casefold()
        if target_config == "single target" and any(
            re.search(r"five independent targets|multiple targets|co-?sputter", item.get("evidence_text") or "", re.I)
            for item in evidence
        ):
            add("target_configuration", "single-target label conflicts with multi-target evidence", "high")

        substrate = str(data.get("substrate_material") or "")
        if substrate:
            substrate_evidence = {
                item.get("evidence_text", "")
                for item in evidence
                if item.get("field_key") == "substrate_material"
            }
            if substrate_evidence and not any(
                re.search(r"substrate|deposited on|grown on|fabricated on", text, re.I)
                for text in substrate_evidence
            ):
                add("substrate_material", "substrate material not supported by explicit substrate phrase", "high")
            if substrate.casefold() in {"aln", "tin", "mo"}:
                add("substrate_material", f"{substrate!r} is commonly a layer/electrode, not a substrate", "high")

        thickness_evidence = [
            item.get("evidence_text", "")
            for item in evidence
            if item.get("field_key") == "alscn_thickness_nm"
        ]
        if data.get("alscn_thickness_nm") is not None and thickness_evidence:
            text = " ".join(thickness_evidence)
            if re.search(r"\b(Mo|Pt|Ti|electrode|adhesion|seed|buffer)\b", text, re.I) and not re.search(
                r"AlScN|ScAlN|nitride", text, re.I
            ):
                add("alscn_thickness_nm", "thickness evidence points to electrode/stack, not AlScN", "high")

        mode = str(data.get("sputtering_mode") or "")
        if "AC" in mode:
            mode_evidence = " ".join(
                item.get("evidence_text", "") for item in evidence if item.get("field_key") == "sputtering_mode"
            )
            if re.search(r"C-V|capacitance|excitation|PUND|leakage|breakdown", mode_evidence, re.I):
                add("sputtering_mode", "AC appears in a measurement context, not deposition", "high")

        for ratio_key in ("ar_flow_sccm", "n2_flow_sccm"):
            text = " ".join(
                item.get("evidence_text", "") for item in evidence if item.get("field_key") == ratio_key
            )
            if re.search(r"\bN2\s*:\s*Ar\b|\bAr\s*:\s*N2\b", text, re.I) and (
                data.get("ar_flow_sccm") is None or data.get("n2_flow_sccm") is None
            ):
                add(ratio_key, "gas ratio expression could not be split into Ar and N2", "high")

        target_sc_evidence = [
            item.get("evidence_text", "")
            for item in evidence
            if item.get("field_key") == "target1_sc_at_pct"
        ]
        if target_sc_evidence and not any(re.search(r"target|alloy", text, re.I) for text in target_sc_evidence):
            add("target1_sc_at_pct", "Sc at.% evidence does not identify a target/alloy", "high")

        temp_evidence = [
            item.get("evidence_text", "")
            for item in evidence
            if item.get("field_key") == "substrate_temperature_c"
        ]
        if data.get("substrate_temperature_c") is not None and temp_evidence:
            text = " ".join(temp_evidence)
            if re.search(r"bottom\s+electrode|top\s+electrode|seed\s+layer|adhesion", text, re.I) and not re.search(
                r"AlScN|AlBScN|ScAlN|nitride|ferroelectric", text, re.I
            ):
                add("substrate_temperature_c", "temperature evidence points to an electrode/seed step", "high")
            if float(data["substrate_temperature_c"]) == 25.0 and re.search(r"room\s+temperature", text, re.I):
                add("substrate_temperature_c", "room-temperature measurement was converted to 25 °C deposition", "high")

        pressure_evidence = [
            item.get("evidence_text", "")
            for item in evidence
            if item.get("field_key") == "working_pressure_mbar"
        ]
        if data.get("working_pressure_mbar") is not None and pressure_evidence:
            text = " ".join(pressure_evidence)
            if re.search(r"probe\s+station|vacuum\s+probe|temperature[-\s]?dependent\s+measurement", text, re.I) and not re.search(
                r"working\s+pressure|process\s+pressure|deposition\s+pressure", text, re.I
            ):
                add("working_pressure_mbar", "pressure evidence belongs to a measurement/probe station, not deposition", "high")

        return issues


def validate_all(db: Database) -> dict:
    validator = RecipeValidator()
    recipes = db.list_recipes()
    issues = 0
    for recipe in recipes:
        evidence = db.list_evidence(recipe["record_id"])
        found = validator.validate(recipe, evidence)
        if found:
            db.set_recipe_review_status(recipe["record_id"], "needs_review")
            for item in found:
                db.add_review_item(item)
            issues += len(found)

    for item in db.orphan_measurements():
        review = _orphan_review(
            item.get("paper_key") or "",
            item.get("record_id") or "",
            "measurement.sample_id",
            "measurement references a missing sample or recipe",
            "high",
        )
        db.add_review_item(review)
        issues += 1
    for item in db.recipes_missing_samples():
        review = _orphan_review(
            item.get("paper_key") or "",
            item.get("record_id") or "",
            "sample_id",
            "recipe references a missing sample",
            "high",
        )
        db.add_review_item(review)
        issues += 1
    for item in db.invalid_target_evidence():
        review = _orphan_review(
            item.get("paper_key") or "",
            item.get("record_id") or "",
            "target.evidence_id",
            f"target evidence missing or points to another entity: {item.get('target_record_id')}",
            "high",
        )
        db.add_review_item(review)
        issues += 1

    # Confirmed audit cases stay high-priority even after the extractor is
    # corrected, because a human must verify the layer/step attribution.
    confirmed_cases = {
        "10.1109/led.2026.3675494": "confirmed audit case: verify AlBScN temperature vs bottom-electrode temperature",
        "10.1109/led.2025.3587950": "confirmed audit case: verify working pressure vs vacuum-probe measurement pressure",
    }
    for recipe in recipes:
        reason = confirmed_cases.get(recipe.get("paper_doi") or "")
        if not reason:
            continue
        digest = hashlib.sha256(f"audit|{recipe['record_id']}|{reason}".encode()).hexdigest()[:12]
        db.add_review_item(
            {
                "review_id": f"rev_audit_{digest}",
                "paper_key": recipe.get("paper_key") or "",
                "record_id": recipe.get("record_id") or "",
                "field_key": "audit.confirmed_case",
                "reason": reason,
                "severity": "high",
                "evidence_id": None,
                "status": "open",
            }
        )
        issues += 1

    return {
        "recipes": len(recipes),
        "issues": issues,
        "open_review_items": db.count_review_items(),
    }


def _orphan_review(paper_key: str, record_id: str, field_key: str, reason: str, severity: str) -> dict:
    digest = hashlib.sha256(f"orphan|{record_id}|{field_key}|{reason}".encode()).hexdigest()[:12]
    return {
        "review_id": f"rev_{digest}",
        "paper_key": paper_key,
        "record_id": record_id,
        "field_key": field_key,
        "reason": reason,
        "severity": severity,
        "evidence_id": None,
        "status": "open",
    }
