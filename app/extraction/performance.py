"""Optional deterministic performance-value extraction (Phase 8)."""

from __future__ import annotations

import re

from app.extraction.models import ExtractedField, ExtractionResult
from app.extraction.units import parse_numeric

_PATTERNS = [
    ("Pr", re.compile(r"(?:\bPr\b|remanent\s+polarization)\s*(?:=|of|was|is|:)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>µC/cm2|μC/cm2|uC/cm2|C/m2)", re.I)),
    ("2Pr", re.compile(r"(?:\b2\s*Pr\b|double\s+remanent\s+polarization)\s*(?:=|of|was|is|:)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>µC/cm2|μC/cm2|uC/cm2|C/m2)", re.I)),
    ("Ec", re.compile(r"(?:\bEc\b|coercive\s+field)\s*(?:=|of|was|is|:)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>MV/cm|kV/cm|V/cm)", re.I)),
    ("d33", re.compile(r"(?:\bd33(?:,f)?\b|piezoelectric\s+coefficient)\s*(?:=|of|was|is|:)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>pm/V|pC/N)", re.I)),
    ("FWHM", re.compile(r"(?:\bFWHM\b|full\s+width\s+at\s+half\s+maximum)\s*(?:=|of|was|is|:)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>°|degree|deg)", re.I)),
    ("stress", re.compile(r"(?:residual\s+)?stress\s*(?:=|of|was|is|:)?\s*(?P<num>-?\d+(?:\.\d+)?)\s*(?P<unit>GPa|MPa)", re.I)),
    ("leakage_J", re.compile(r"(?:leakage\s+current\s+density|current\s+density)\s*(?:=|of|was|is|:)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>A/cm2|mA/cm2|µA/cm2|μA/cm2)", re.I)),
    ("RMS", re.compile(r"(?:RMS\s+roughness|surface\s+roughness)\s*(?:=|of|was|is|:)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>nm)", re.I)),
]
_UNITS = {
    "µc/cm2": ("µC/cm²", 1.0),
    "μc/cm2": ("µC/cm²", 1.0),
    "uc/cm2": ("µC/cm²", 1.0),
    "c/m2": ("µC/cm²", 100.0),
    "mv/cm": ("MV/cm", 1.0),
    "kv/cm": ("MV/cm", 0.001),
    "v/cm": ("MV/cm", 1e-6),
    "pm/v": ("pm/V", 1.0),
    "pc/n": ("pm/V", 1.0),
    "°": ("degree", 1.0),
    "degree": ("degree", 1.0),
    "deg": ("degree", 1.0),
    "gpa": ("MPa", 1000.0),
    "mpa": ("MPa", 1.0),
    "a/cm2": ("A/cm²", 1.0),
    "ma/cm2": ("A/cm²", 0.001),
    "µa/cm2": ("A/cm²", 1e-6),
    "μa/cm2": ("A/cm²", 1e-6),
    "nm": ("nm", 1.0),
}


def extract_measurements(paper_key: str, doi: str, sections: list[dict], *, source_url: str | None = None) -> list[dict]:
    candidates = [
        section for section in sections
        if section.get("section_type") in {"abstract", "results", "discussion", "results and discussion", "conclusion", "body"}
        and section.get("text")
    ]
    measurements = []
    for section in candidates:
        text = section.get("text") or ""
        section_type = section.get("section_type") or "body"
        for property_key, pattern in _PATTERNS:
            for match in pattern.finditer(text):
                evidence_text = _sentence(text, match.group(0))
                # Do not import values that are explicitly cited from other
                # papers. These belong to the cited work, not this sample.
                if re.search(r"\[\s*\d+(?:\s*[-,]\s*\d+)*\s*\]|\bet\s+al\.|reported\s+by|previous\s+stud", evidence_text, re.I):
                    continue
                value = parse_numeric(match.group("num"))
                if value is None:
                    continue
                unit_key = match.group("unit").casefold().replace("²", "2")
                normalized_unit, factor = _UNITS.get(unit_key, (match.group("unit"), 1.0))
                measurement_id = f"{paper_key}:M{len(measurements) + 1}"
                method = "unknown"
                if re.search(r"PUND|P-E|polarization", text, re.I):
                    method = "PUND/P-E"
                elif re.search(r"XRD|diffraction", text, re.I):
                    method = "XRD"
                elif re.search(r"AFM", text, re.I):
                    method = "AFM"
                review_reason = "performance value requires sample/test-condition confirmation"
                if section_type == "abstract":
                    review_reason = "value taken from abstract; confirm against current experiment and sample"
                measurement = {
                    "measurement_id": measurement_id,
                    "record_id": f"{paper_key}:R1",
                    "sample_id": f"{paper_key}:S1",
                    "property_key": property_key,
                    "property_value": value * factor,
                    "property_unit": normalized_unit,
                    "measurement_method": method,
                    "test_conditions": "",
                    "source_scope": section_type,
                    "review_required": True,
                    "review_status": "needs_review",
                    "review_reason": review_reason,
                    "evidence_id": f"{measurement_id}:value",
                }
                measurements.append(
                    {
                        **measurement,
                        "evidence": {
                            "evidence_id": measurement["evidence_id"],
                            "record_id": measurement["record_id"],
                            "entity_type": "measurement",
                            "entity_id": measurement_id,
                            "field_key": property_key,
                            "paper_doi": doi,
                            "source_url": source_url,
                            "source_section": section.get("heading", ""),
                            "page_table_figure": section.get("locator", ""),
                            "evidence_text": evidence_text,
                            "raw_value": match.group("num"),
                            "raw_unit": match.group("unit"),
                            "normalized_value": value * factor,
                            "normalized_unit": normalized_unit,
                            "evidence_status": "derived" if factor != 1.0 else "reported",
                            "derivation_note": f"converted {match.group('unit')} -> {normalized_unit}" if factor != 1.0 else None,
                        },
                    }
                )
    return measurements


def _sentence(text: str, needle: str) -> str:
    pos = text.find(needle)
    if pos < 0:
        return ""
    start = 0
    for boundary in re.finditer(r"(?<=[.!?])\s+|\n+", text):
        end = boundary.start()
        if start <= pos < end:
            return re.sub(r"\s+", " ", text[start:end]).strip()[:600]
        start = boundary.end()
    return re.sub(r"\s+", " ", text[start:]).strip()[:600]
