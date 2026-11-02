"""Section classification and deposition-method location."""

from __future__ import annotations

import re

from app.acquisition.models import ParsedSection

MATERIAL_TERMS = (
    "alscn",
    "scaln",
    "al1-xscxn",
    "scxal1-xn",
    "scandium aluminum nitride",
    "aluminum scandium nitride",
    "scandium aluminium nitride",
    "aluminium scandium nitride",
    "sc-doped aln",
    "sc doped aln",
)
DEPOSITION_TERMS = (
    "deposit",
    "sputter",
    "magnetron",
    "cosputter",
    "co-sputter",
    "pld",
    "pulsed laser",
    "pvd",
    "physical vapor",
    "evaporat",
    "film growth",
    "grown by",
    "growth",
)
METHOD_HEADING_TERMS = (
    "experimental",
    "method",
    "materials and methods",
    "methodology",
    "fabrication",
    "device fabrication",
    "sample preparation",
    "synthesis",
    "deposition",
    "sputtering",
    "growth",
    "thin film preparation",
)
REFERENCE_TERMS = ("reference", "bibliography", "works cited", "literature cited")
ABSTRACT_TERMS = ("abstract", "summary")
RESULT_TERMS = ("result", "discussion")
CONCLUSION_TERMS = ("conclusion", "summary and outlook")
INTRO_TERMS = ("introduction", "background")
ACK_TERMS = ("acknowledg", "funding", "author contributions")
PROCESS_UNIT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:w|kw|sccm|pa|mbar|torr|nm|min|khz|mhz|hz|v|a|rpm|°c|degree)\b",
    re.IGNORECASE,
)
PROCESS_TERMS = (
    "target power",
    "sputtering power",
    "power",
    "pressure",
    "working pressure",
    "base pressure",
    "gas flow",
    "flow rate",
    "sccm",
    "substrate temperature",
    "deposition temperature",
    "target-substrate",
    "target substrate",
    "distance",
    "bias",
    "pulse frequency",
    "duty cycle",
    "deposition time",
    "thickness",
    "rpm",
    "ar flow",
    "n2 flow",
)


def normalize_heading(heading: str) -> str:
    return re.sub(r"\s+", " ", (heading or "").casefold()).strip(" .:0123456789")


def classify_heading(heading: str, text: str = "") -> str:
    value = normalize_heading(heading)
    if not value:
        return "body"
    if any(term in value for term in REFERENCE_TERMS):
        return "references"
    if "supplementary" in value and any(term in value for term in ("method", "experimental", "material")):
        return "supplementary_methods"
    if any(term in value for term in ABSTRACT_TERMS):
        return "abstract"
    if any(term in value for term in CONCLUSION_TERMS):
        return "conclusion"
    if any(term in value for term in ACK_TERMS):
        return "acknowledgements"
    if any(term in value for term in INTRO_TERMS):
        return "introduction"
    if any(term in value for term in METHOD_HEADING_TERMS):
        return "methods"
    if any(term in value for term in RESULT_TERMS):
        return "results"
    if "table" in value or "figure" in value:
        return "table"
    if "supplementary" in value:
        return "supplementary"
    return "body"


def score_deposition_section(section: ParsedSection) -> float:
    if section.is_reference or section.section_type == "references":
        return -20.0
    if len((section.text or "").strip()) < 40:
        return 0.0
    heading = normalize_heading(section.heading)
    text = (section.text or "").casefold()
    if any(term in heading for term in REFERENCE_TERMS):
        return -20.0

    score = 0.0
    if any(term in heading for term in DEPOSITION_TERMS):
        score += 3.0
    if any(term in heading for term in METHOD_HEADING_TERMS):
        score += 2.0
    if "supplementary" in heading and "method" in heading:
        score += 2.5
    if any(term in heading for term in RESULT_TERMS):
        score += 0.5

    has_material = any(term in text or term in heading for term in MATERIAL_TERMS)
    has_sputter = "sputter" in text or "sputter" in heading or "magnetron" in text
    if has_material:
        score += 2.0
    if has_sputter:
        score += 2.0
    if any(term in text for term in DEPOSITION_TERMS):
        score += 1.0

    process_hits = sum(1 for term in PROCESS_TERMS if term in text)
    score += min(3.0, process_hits * 0.45)
    if PROCESS_UNIT_RE.search(section.text or ""):
        score += 1.0
    if not (has_material or has_sputter):
        score = min(score, 2.5)
    return round(score, 4)


def locate_deposition_sections(
    sections: list[ParsedSection],
    document_title: str | None = None,
) -> list[ParsedSection]:
    title_key = normalize_heading(document_title or "")
    candidates: list[ParsedSection] = []
    for section in sections:
        heading_key = normalize_heading(section.heading)
        effective_type = section.section_type
        if effective_type in {"other", "body"}:
            effective_type = classify_heading(section.heading, section.text)
        if effective_type in {
            "abstract",
            "references",
            "conclusion",
            "acknowledgements",
            "introduction",
        } or section.is_reference:
            continue
        if heading_key and (heading_key.startswith("title ") or heading_key.startswith("title:")):
            continue
        if title_key and heading_key == title_key:
            continue
        score = score_deposition_section(section)
        section.score = score
        if score >= 3.0:
            candidates.append(section)
    candidates.sort(key=lambda item: (-item.score, item.order))
    if candidates:
        best = candidates[0]
        if best.section_type != "supplementary_methods":
            best.section_type = "deposition_methods"
        for other in candidates[1:]:
            if other.section_type == "body":
                other.section_type = "deposition_related"
    return candidates
