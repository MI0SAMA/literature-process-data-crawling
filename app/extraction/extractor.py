"""Deterministic, evidence-backed rule extractor for Phase 4.

This is the baseline used when no LLM is configured. It extracts only what is
explicitly present in the located deposition section/table; missing values stay
null. Every non-null numeric value carries a source snippet and location.
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from typing import Any

from app.extraction.models import ExtractedField, ExtractionResult, RecipeExtraction
from app.extraction.units import convert, in_range, normalize_temperature, parse_numeric

_POWER_RE = re.compile(
    r"(?:(?P<mode>RF|DC|pulsed\s*DC|HiPIMS|AC)\s*)?(?:target\s*)?(?:power|P)\s*"
    r"(?:of|was|is|=|:)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>kW|W|mW)",
    re.IGNORECASE,
)
_POWER_SUFFIX_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>kW|W|mW)\s*(?P<mode>RF|DC|pulsed\s*DC|HiPIMS|AC)?\s*(?:target\s*)?(?:power|sputter)",
    re.IGNORECASE,
)
_POWER_GENERIC_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>kW|W|mW)\b", re.IGNORECASE)
_PRESSURE_RE = re.compile(
    r"(?P<num>(?:\d+(?:\.\d+)?\s*[×xX]\s*10\s*[−\-^]?\s*\d+|\d+(?:\.\d+)?(?:\s*[−\-]\s*\d+)?))"
    r"\s*(?P<unit>mTorr|microbar|µbar|μbar|ubar|mbar|Pa|Torr|bar)",
    re.IGNORECASE,
)
_GAS_RE = re.compile(
    r"\b(?P<gas>N2|N₂|Ar|O2|O₂|H2|H₂)\b[^.;]{0,40}?"
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>sccm|slm)",
    re.IGNORECASE,
)
_GAS_SUFFIX_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>sccm|slm)\s*(?:of\s*)?(?P<gas>N2|N₂|Ar|O2|O₂|H2|H₂)\b",
    re.IGNORECASE,
)
_TEMP_RE = re.compile(
    r"(?:substrate|deposition|growth|heater|chuck)\s*temperature\s*"
    r"(?:of|was|is|=|:|at)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>°C|C|K)",
    re.IGNORECASE,
)
_TEMP_GENERIC_RE = re.compile(
    r"(?:at|to|of)\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>°C|C)\b",
    re.IGNORECASE,
)
_TEMP_LAYER_RE = re.compile(
    r"(?:AlScN|AlBScN|ScAlN|Al1-xScxN|Al1-x-yBxScyN|nitride|ferroelectric)"
    r"[^.;]{0,110}?\bat\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>°C|C|K)\b",
    re.IGNORECASE,
)
_TEMP_DEPOSITION_RE = re.compile(
    r"(?:deposited|sputtered|co-sputtered|grown|deposition)"
    r"[^.;]{0,100}?\bat\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>°C|C|K)\b",
    re.IGNORECASE,
)
_THICKNESS_RE = re.compile(
    r"(?:(?:AlScN|ScAlN|film|layer)\s*)?(?:thickness\s*(?:of|was|is|=|:|at)?\s*)?"
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>nm|µm|um|mm)\s*(?:thick|thickness)?",
    re.IGNORECASE,
)
_THICKNESS_CONTEXT_RE = re.compile(
    r"(?:(?:AlScN|ScAlN|film|layer)\s*(?:thickness)?|thickness)\s*"
    r"(?:of|was|is|=|:|at)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>nm|µm|um|mm)",
    re.IGNORECASE,
)
_THICKNESS_THICK_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>nm|µm|um|mm)\s*[- ]?thick",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"(?:deposition|sputtering|deposited)\s*(?:time\s*)?(?:of|was|is|=|:|for)?\s*"
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>min|s|h)\b",
    re.IGNORECASE,
)
_TIME_GENERIC_RE = re.compile(
    r"(?:for|over)\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>min|h)\b",
    re.IGNORECASE,
)
_DISTANCE_RE = re.compile(
    r"(?:target[\s-]*substrate|target\s*to\s*substrate|TSD)\s*(?:distance)?\s*"
    r"(?:of|was|is|=|:|at)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>cm|mm)",
    re.IGNORECASE,
)
_DISTANCE_GENERIC_RE = re.compile(
    r"(?:distance|TSD)\s*(?:of|was|is|=|:)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>cm|mm)",
    re.IGNORECASE,
)
_SC_TARGET_RE = re.compile(
    r"(?:AlSc|ScAlN|scandium)[^.;]{0,80}?(?P<num>\d+(?:\.\d+)?)\s*(?:at\.?\s*%|at%|%)\s*(?:Sc|scandium)?",
    re.IGNORECASE,
)
_SC_CONTENT_RE = re.compile(
    r"(?:Sc|scandium)\s*(?:content|concentration|fraction)?\s*(?:of|was|is|=|:)?\s*"
    r"(?P<num>\d+(?:\.\d+)?)\s*(?:at\.?\s*%|at%|%)",
    re.IGNORECASE,
)
_SC_FORMULA_RE = re.compile(r"\bAl\s*0?\.(?P<al>\d+)\s*Sc\s*0?\.(?P<sc>\d+)\s*N\b", re.IGNORECASE)
_MODE_RE = re.compile(r"\b(RF|DC|pulsed\s*DC|HiPIMS|AC)\b", re.IGNORECASE)
_SUBSTRATE_MATERIAL_RE = re.compile(
    r"(Si\s*\(\s*1\s*0\s*0\s*\)|Si\s*\(\s*1\s*1\s*1\s*\)|4H[-\s]*SiC|SiC|sapphire|Al2O3|CSOI|"
    r"GaAs|GaN|glass|quartz|Pt\s*/\s*Ti\s*/\s*SiO2\s*/\s*Si|Si\s*/\s*SiO2|SiO2\s*/\s*Si|silicon|Si)",
    re.IGNORECASE,
)
_SUBSTRATE_ON_RE = re.compile(
    r"(?:deposited|grown|fabricated|formed)\s+on\s+(?:a\s+)?(?P<sub>" + _SUBSTRATE_MATERIAL_RE.pattern + r")(?:\s+substrate)?",
    re.IGNORECASE,
)
_SUBSTRATE_WAFER_RE = re.compile(
    r"(?:on|using)\s+[^.;]{0,100}?(?P<sub>" + _SUBSTRATE_MATERIAL_RE.pattern + r")\s+(?:wafers?|substrates?)",
    re.IGNORECASE,
)
_SUBSTRATE_NOUN_RE = re.compile(r"(?P<sub>" + _SUBSTRATE_MATERIAL_RE.pattern + r")\s+substrate", re.IGNORECASE)
_SUBSTRATE_IS_RE = re.compile(r"substrate\s*(?:is|was|=|:)\s*(?P<sub>" + _SUBSTRATE_MATERIAL_RE.pattern + r")", re.IGNORECASE)
_ALSCN_THICKNESS_RE = re.compile(
    r"(?:AlScN|ScAlN|Al1-xScxN|ScxAl1-xN|nitride|ferroelectric)\s*(?:layer|film|films)?\s*"
    r"(?:thickness\s*)?(?:of|was|is|=|:|at)?\s*[~≈]?\(?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>nm|µm|um|mm)",
    re.IGNORECASE,
)
_THICKNESS_OF_LAYER_RE = re.compile(
    r"thickness\s+of\s+(?:the\s+)?(?:AlScN|ScAlN|Al1-xScxN|ScxAl1-xN|nitride|ferroelectric)[^.;]{0,35}?"
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>nm|µm|um|mm)",
    re.IGNORECASE,
)
_ALSCN_VALUE_RE = re.compile(
    r"(?:AlScN|ScAlN|Al1-xScxN|ScxAl1-xN)[^.;]{0,60}?(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>nm|µm|um|mm)",
    re.IGNORECASE,
)
_FILM_THICKNESS_RE = re.compile(
    r"(?:film|layer)\s*thickness\s*(?:of|was|is|=|:|at)?\s*[~≈]?\(?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>nm|µm|um|mm)",
    re.IGNORECASE,
)
_NON_FILM_THICKNESS_RE = re.compile(
    r"electrode|adhesion|seed|buffer|Mo\b|Pt\b|Ti\b|TiN|top\s+electrode|bottom\s+electrode",
    re.IGNORECASE,
)
_ELECTRODE_RE = re.compile(
    r"\b(?P<metal>TiN|Pt|Mo|W|Al|Au|Cu)\b[^.;]{0,30}?(?P<where>top|bottom)\s*electrode",
    re.IGNORECASE,
)
_EQUIPMENT_RE = re.compile(r"\b(AJA[^,.;]{0,60}|ATC-1800|Orion\s*5[^,.;]{0,30})", re.IGNORECASE)


class RuleBasedExtractor:
    model = "rule-based"
    prompt_version = "none"
    parser_version = "phase4-rules-v1"

    def extract(
        self,
        *,
        paper_key: str,
        doi: str,
        title: str,
        year: int | None,
        sections: list[dict],
        tables: list[dict],
        source_url: str | None,
        source_document_id: str | None = None,
    ) -> ExtractionResult:
        deposition_sections = [
            section
            for section in sections
            if section.get("section_type") in {"deposition_methods", "supplementary_methods", "methods"}
            and section.get("text")
        ]
        if not deposition_sections:
            return ExtractionResult(
                paper_key=paper_key,
                doi=doi,
                status="no_deposition_section",
                warnings=["no deposition/methods section found"],
            )

        primary = sorted(
            deposition_sections,
            key=lambda section: (
                0 if section.get("section_type") == "deposition_methods" else 1,
                0 if section.get("section_type") == "methods" else 1,
                int(section.get("section_order", 0)),
            ),
        )[0]
        text = primary.get("text") or ""
        table_text = " ".join(
            " ".join(part for part in (table.get("caption", ""), table.get("text", "")) if part)
            for table in tables
        )
        context = f"{text} {table_text}".strip()
        record_id = f"{paper_key}:R1"
        recipe_id = "R1"
        sample_id = f"{paper_key}:S1"

        modes = _deposition_modes(context)
        data: dict[str, Any] = {
            "record_id": record_id,
            "paper_doi": doi,
            "paper_title": title,
            "publication_year": year,
            "recipe_id": recipe_id,
            "sample_id": sample_id,
            "record_scope": "actual_recipe",
            "review_status": "new",
            "deposition_method": "sputtering" if re.search(r"sputter", context, re.I) else "PVD",
            "sputtering_mode": _join_modes(modes),
            "deposition_method_detail": _method_detail(context, modes),
            "method_category": _method_category(modes),
            "target_configuration": _target_configuration(context),
            "sample_type": "thin film",
        }
        fields: list[ExtractedField] = []
        review: list[dict[str, Any]] = []
        warnings: list[str] = []

        def set_field(
            field_key: str,
            value: Any,
            raw_value: str,
            raw_unit: str,
            match_text: str,
            *,
            family: str | None = None,
            status: str = "reported",
            note: str | None = None,
            entity_type: str = "recipe",
            entity_id: str | None = None,
        ) -> None:
            if field_key in data or value is None:
                return
            normalized = value
            normalized_unit = raw_unit
            if family:
                converted, target_unit = convert(float(value), raw_unit, family)
                if converted is None:
                    status = "ambiguous"
                    note = f"cannot convert {raw_unit} to {family}"
                else:
                    normalized = converted
                    normalized_unit = target_unit or raw_unit
                    if normalized_unit != raw_unit or status == "derived":
                        status = "derived"
                        note = note or f"converted {raw_unit} -> {normalized_unit}"
            data[field_key] = normalized
            fields.append(
                ExtractedField(
                    field_key=field_key,
                    value=normalized,
                    raw_value=raw_value,
                    raw_unit=raw_unit,
                    normalized_value=float(normalized) if isinstance(normalized, (int, float)) else None,
                    normalized_unit=normalized_unit,
                    evidence_text=_sentence(context, match_text) or match_text,
                    section=primary.get("heading", ""),
                    locator=primary.get("locator", ""),
                    status=status,
                    confidence=0.75 if entity_type == "recipe" else 0.65,
                    entity_type=entity_type,
                    entity_id=entity_id or record_id,
                    derivation_note=note,
                )
            )

        # Sputtering power (prefer target-related, skip electrode powers).
        dual_target_match = _dual_target_match(context)
        target_powers = _target_power_pairs(context)
        if dual_target_match:
            set_field("target1_material", "Al", "Al", "", dual_target_match, entity_type="target", entity_id=f"{record_id}:T1")
            set_field("target2_material", "Sc", "Sc", "", dual_target_match, entity_type="target", entity_id=f"{record_id}:T2")
            data["target_configuration"] = "co-sputtering"
        if dual_target_match and target_powers:
            if "Al" in target_powers:
                item = target_powers["Al"]
                set_field("target1_power_w", item["value"], item["raw"], item["unit"], item["match"], family="power", entity_type="target", entity_id=f"{record_id}:T1")
                data["target1_power_basis"] = item["basis"]
                data["target1_power_statistic"] = item["statistic"]
                data["target1_reported_power_text"] = item["definition"]
            if "Sc" in target_powers:
                item = target_powers["Sc"]
                set_field("target2_power_w", item["value"], item["raw"], item["unit"], item["match"], family="power", entity_type="target", entity_id=f"{record_id}:T2")
                data["target2_power_basis"] = item["basis"]
                data["target2_power_statistic"] = item["statistic"]
                data["target2_reported_power_text"] = item["definition"]
        power_matches = list(_POWER_RE.finditer(context)) + list(_POWER_SUFFIX_RE.finditer(context))
        power_matches += [
            match
            for match in _POWER_GENERIC_RE.finditer(context)
            if re.search(r"target|sputter|AlSc|deposition", _sentence(context, match.group(0)), re.I)
        ]
        for match in power_matches:
            snippet = _sentence(context, match.group(0))
            if re.search(r"electrode|TiN|top\s+electrode|bottom\s+electrode", snippet, re.I):
                continue
            if re.search(r"\betch|pre[-\s]?treatment|cleaning|plasma\s+treatment|sputter\s+etch", snippet, re.I):
                continue
            unit = match.group("unit")
            value = parse_numeric(match.group("num"))
            basis = _power_basis(snippet)
            if basis in {"bias_power", "power_density", "etch_power"}:
                review.append(
                    {
                        "review_id": f"rev_power_{uuid.uuid4().hex[:12]}",
                        "paper_key": paper_key,
                        "record_id": record_id,
                        "field_key": "target1_power_w",
                        "reason": f"power candidate ignored: {basis}; not written as target power",
                        "severity": "high",
                        "evidence_id": None,
                        "status": "open",
                    }
                )
                continue
            set_field("target1_power_w", value, match.group("num"), unit, match.group(0), family="power", entity_type="target", entity_id=f"{record_id}:T1")
            if "target1_power_w" in data:
                data.setdefault("target1_power_basis", basis)
                data.setdefault("target1_power_statistic", _power_statistic(snippet))
                data.setdefault("target1_reported_power_text", snippet[:240])
            mode = (match.groupdict().get("mode") or "").strip()
            if mode:
                data.setdefault("target1_power_mode", mode)
            set_field("target1_material", _target_material(snippet), "", "", match.group(0), entity_type="target", entity_id=f"{record_id}:T1")
            break

        # AlScN target Sc content (target nominal, not film measured).
        target_sc = _SC_TARGET_RE.search(context)
        if target_sc and re.search(r"target|alloy|sputter", _sentence(context, target_sc.group(0)), re.I):
            value = parse_numeric(target_sc.group("num"))
            set_field("target1_sc_at_pct", value, target_sc.group("num"), "at.%", target_sc.group(0), entity_type="target", entity_id=f"{record_id}:T1")
            data["film_sc_basis"] = data.get("film_sc_basis") or "unknown"
        formula = _SC_FORMULA_RE.search(context)
        if formula:
            al = parse_numeric(formula.group("al")) or 0
            sc = parse_numeric(formula.group("sc")) or 0
            total = al + sc
            if total > 0:
                set_field("film_sc_at_pct", round(100 * sc / total, 2), formula.group(0), "at.%", formula.group(0), status="derived", note="derived from Al0.xSc0.yN formula")
                data["film_sc_basis"] = "formula"
        film_sc = _SC_CONTENT_RE.search(context)
        if film_sc and "film_sc_at_pct" not in data and not re.search(r"target", _sentence(context, film_sc.group(0)), re.I):
            set_field("film_sc_at_pct", parse_numeric(film_sc.group("num")), film_sc.group("num"), "at.%", film_sc.group(0))
            data["film_sc_basis"] = "measured" if re.search(r"XPS|EDS|measured|composition", context, re.I) else "nominal"

        # Working/base pressure.
        for match in _PRESSURE_RE.finditer(context):
            snippet = _sentence(context, match.group(0))
            value = _pressure_value(match.group("num"))
            if value is None:
                continue
            if re.search(r"base\s+pressure|background\s+pressure", snippet, re.I):
                set_field("base_pressure_mbar", value, match.group("num"), match.group("unit"), match.group(0), family="pressure")
            elif re.search(
                r"working\s+pressure|process\s+pressure|deposition\s+pressure|sputter(?:ing)?\s+pressure|chamber\s+pressure",
                snippet,
                re.I,
            ) and not re.search(r"probe\s+station|temperature[-\s]?dependent\s+measurement|vacuum\s+probe", snippet, re.I):
                set_field("working_pressure_mbar", value, match.group("num"), match.group("unit"), match.group(0), family="pressure")
            if "working_pressure_mbar" in data and "base_pressure_mbar" in data:
                break

        # Gas flows. Ratio and "value for gas" forms are parsed before the
        # loose name/value fallback so N2:Ar=160:32 is not read as N2=32.
        for gas, flow in _gas_flows(context).items():
            field_key = {"AR": "ar_flow_sccm", "N2": "n2_flow_sccm", "O2": "other_gas_flow_sccm", "H2": "other_gas_flow_sccm"}.get(gas)
            if not field_key:
                continue
            set_field(
                field_key,
                flow["value"],
                flow["raw"],
                flow["unit"],
                flow["match"],
                family="flow",
            )
            if gas in {"O2", "H2"}:
                set_field("other_gas", gas, gas, "", flow["match"])

        # Temperature: bind to the AlScN/AlBScN/nitride deposition sentence.
        # A bottom-electrode bake (e.g. 150 °C) must not win over the 300 °C
        # AlBScN deposition later in the same section.
        temp_match = _temperature_match(context)
        if temp_match:
            value = parse_numeric(temp_match.group("num"))
            if value is not None:
                normalized, unit = normalize_temperature(value, temp_match.group("unit"))
                set_field(
                    "substrate_temperature_c",
                    normalized,
                    temp_match.group("num"),
                    temp_match.group("unit"),
                    temp_match.group(0),
                    status="derived" if unit == "°C" else "reported",
                    note="temperature normalized to °C",
                )
                data["temperature_basis"] = "substrate_measured"
        elif re.search(r"room\s+temperature", context, re.I):
            data["temperature_basis"] = "room_temp"
            review.append(
                {
                    "review_id": f"rev_room_{uuid.uuid4().hex[:12]}",
                    "paper_key": paper_key,
                    "record_id": record_id,
                    "field_key": "substrate_temperature_c",
                    "reason": "room-temperature condition present but no explicit numeric deposition temperature",
                    "severity": "high",
                    "evidence_id": None,
                    "status": "open",
                }
            )

        # Thickness: require an explicit AlScN/ScAlN/nitride layer. A bare
        # "250 nm-Mo/20 nm-AlN/Si" or "10 nm Ti adhesion" must not become the
        # AlScN thickness.
        thickness = _THICKNESS_OF_LAYER_RE.search(context) or _ALSCN_THICKNESS_RE.search(context)
        if thickness is None:
            candidate = _ALSCN_VALUE_RE.search(context)
            if candidate and not _NON_FILM_THICKNESS_RE.search(_sentence(context, candidate.group(0))):
                thickness = candidate
        if thickness is None:
            candidate = _FILM_THICKNESS_RE.search(context)
            if candidate and not _NON_FILM_THICKNESS_RE.search(_sentence(context, candidate.group(0))):
                thickness = candidate
        if thickness is None:
            candidate = _THICKNESS_THICK_RE.search(context)
            snippet = _sentence(context, candidate.group(0)) if candidate else ""
            if candidate and re.search(r"AlScN|ScAlN|nitride", snippet, re.I) and not _NON_FILM_THICKNESS_RE.search(snippet):
                thickness = candidate
        if thickness:
            set_field("alscn_thickness_nm", parse_numeric(thickness.group("num")), thickness.group("num"), thickness.group("unit"), thickness.group(0), family="length")
        time_match = _TIME_RE.search(context)
        if time_match is None:
            candidate = _TIME_GENERIC_RE.search(context)
            if candidate and re.search(r"deposition|sputter|film", _sentence(context, candidate.group(0)), re.I):
                time_match = candidate
        if time_match:
            snippet = _sentence(context, time_match.group(0))
            if re.search(r"\betch|native\s+oxide|pre[-\s]?treatment|cleaning", snippet, re.I) and not re.search(
                r"(?:deposition|sputter(?:ing)?)\s+time|deposited\s+for", snippet, re.I
            ):
                time_match = None
        if time_match:
            set_field("deposition_time_min", parse_numeric(time_match.group("num")), time_match.group("num"), time_match.group("unit"), time_match.group(0), family="time")
        distance = _DISTANCE_RE.search(context)
        if distance is None:
            candidate = _DISTANCE_GENERIC_RE.search(context)
            if candidate and re.search(r"target|substrate", _sentence(context, candidate.group(0)), re.I):
                distance = candidate
        if distance:
            set_field("target_substrate_distance_mm", parse_numeric(distance.group("num")), distance.group("num"), distance.group("unit"), distance.group(0), family="distance")

        # Substrate / electrodes / equipment. Only explicit "on X (substrate)"
        # or "X substrate" phrases are accepted; AlN/TiN/Mo inside a film
        # stack are not substrates.
        substrate = _SUBSTRATE_ON_RE.search(context) or _SUBSTRATE_WAFER_RE.search(context) or _SUBSTRATE_NOUN_RE.search(context) or _SUBSTRATE_IS_RE.search(context)
        if substrate:
            material = substrate.group("sub")
            if material.casefold() == "silicon":
                material = "Si"
            set_field("substrate_material", material, material, "", substrate.group(0))
            set_field("substrate_stack", _sentence(context, substrate.group(0))[:250], material, "", substrate.group(0))
        for match in _ELECTRODE_RE.finditer(context):
            metal = match.group("metal")
            where = match.group("where").casefold()
            if where == "top":
                set_field("top_electrode_material", metal, metal, "", match.group(0))
            else:
                set_field("bottom_electrode_material", metal, metal, "", match.group(0))
        equipment = _EQUIPMENT_RE.search(context)
        if equipment:
            set_field("equipment_model", equipment.group(1).strip(), equipment.group(1).strip(), "", equipment.group(0))

        # Targets long table.
        targets = []
        target_diameter_mm = _target_diameter_mm(context)
        if target_diameter_mm:
            set_field(
                "target1_diameter_mm", target_diameter_mm, str(target_diameter_mm), "mm",
                "diameter", entity_type="target", entity_id=f"{record_id}:T1",
            )
        target_material = data.get("target1_material")
        if target_material or "target1_power_w" in data or "target1_sc_at_pct" in data or target_diameter_mm:
            targets.append(
                {
                    "target_record_id": f"{record_id}:T1",
                    "record_id": record_id,
                    "target_index": 1,
                    "layer_role": "AlScN",
                    "target_material": target_material or "unknown",
                    "target_composition": data.get("target1_composition"),
                    "target_sc_at_pct": data.get("target1_sc_at_pct"),
                    "target_power_w": data.get("target1_power_w"),
                    "power_basis": data.get("target1_power_basis"),
                    "power_statistic": data.get("target1_power_statistic"),
                    "reported_power_text": data.get("target1_reported_power_text"),
                    "power_mode": data.get("target1_power_mode"),
                    "sputtering_mode": data.get("sputtering_mode"),
                    "target_voltage_v": None,
                    "target_current_a": None,
                    "target_diameter_mm": target_diameter_mm,
                    "target_diameter_inch": round(target_diameter_mm / 25.4, 3) if target_diameter_mm else None,
                    "power_density_w_cm2": _power_density(data.get("target1_power_w"), target_diameter_mm) if data.get("target1_power_basis") in {"target_power", "sputter_power"} else None,
                    "diameter_evidence_id": f"{record_id}:target1_diameter_mm" if target_diameter_mm else None,
                    "density_evidence_id": f"{record_id}:target1_power_w" if _power_density(data.get("target1_power_w"), target_diameter_mm) is not None else None,
                    "density_formula": "P/(π(d/2)^2)" if _power_density(data.get("target1_power_w"), target_diameter_mm) is not None else None,
                    "equipment_model": data.get("equipment_model"),
                    "deposition_rate_nm_min": data.get("deposition_rate_nm_min"),
                    "target_purity_pct": None,
                    "evidence_id": (
                        f"{record_id}:target1_power_w"
                        if data.get("target1_power_w") is not None
                        else f"{record_id}:target1_material"
                        if data.get("target1_material") is not None
                        else f"{record_id}:target1_sc_at_pct"
                        if data.get("target1_sc_at_pct") is not None
                        else f"{record_id}:target1_diameter_mm"
                    ),
                }
            )
        if data.get("target2_material") or data.get("target2_power_w") is not None:
            targets.append(
                {
                    "target_record_id": f"{record_id}:T2",
                    "record_id": record_id,
                    "target_index": 2,
                    "layer_role": "AlScN",
                    "target_material": data.get("target2_material") or "unknown",
                    "target_composition": data.get("target2_composition"),
                    "target_sc_at_pct": data.get("target2_sc_at_pct"),
                    "target_power_w": data.get("target2_power_w"),
                    "power_basis": data.get("target2_power_basis"),
                    "power_statistic": data.get("target2_power_statistic"),
                    "reported_power_text": data.get("target2_reported_power_text"),
                    "power_mode": data.get("target2_power_mode"),
                    "sputtering_mode": data.get("sputtering_mode"),
                    "target_voltage_v": None,
                    "target_current_a": None,
                    "target_diameter_mm": target_diameter_mm,
                    "target_diameter_inch": round(target_diameter_mm / 25.4, 3) if target_diameter_mm else None,
                    "power_density_w_cm2": _power_density(data.get("target2_power_w"), target_diameter_mm) if data.get("target2_power_basis") in {"target_power", "sputter_power"} else None,
                    "diameter_evidence_id": None,
                    "density_evidence_id": f"{record_id}:target2_power_w" if _power_density(data.get("target2_power_w"), target_diameter_mm) is not None else None,
                    "density_formula": "P/(π(d/2)^2)" if _power_density(data.get("target2_power_w"), target_diameter_mm) is not None else None,
                    "equipment_model": data.get("equipment_model"),
                    "deposition_rate_nm_min": data.get("deposition_rate_nm_min"),
                    "target_purity_pct": None,
                    "evidence_id": (
                        f"{record_id}:target2_power_w"
                        if data.get("target2_power_w") is not None
                        else f"{record_id}:target2_material"
                        if data.get("target2_material") is not None
                        else f"{record_id}:target2_sc_at_pct"
                    ),
                }
            )

        # Range checks / review items.
        if "target1_power_w" in data and not in_range(data["target1_power_w"], 0, 5000):
            review.append(_review(record_id, paper_key, "target1_power_w", "power outside plausible 0-5000 W", "high"))
        if "working_pressure_mbar" in data and not in_range(data["working_pressure_mbar"], 1e-9, 10):
            review.append(_review(record_id, paper_key, "working_pressure_mbar", "pressure outside plausible range", "high"))
        if "substrate_temperature_c" in data and not in_range(data["substrate_temperature_c"], -100, 1200):
            review.append(_review(record_id, paper_key, "substrate_temperature_c", "temperature outside plausible range", "high"))
        if "target1_power_w" not in data:
            review.append(_review(record_id, paper_key, "target1_power_w", "target power not found", "medium"))
        if "ar_flow_sccm" not in data and "n2_flow_sccm" not in data:
            review.append(_review(record_id, paper_key, "gas_flow", "neither Ar nor N2 flow found", "medium"))
        if (data.get("review_status") or "new") == "new" and len(fields) < 3:
            warnings.append("fewer than 3 fields extracted")

        data["remarks"] = "rule-based extraction; null = not reported"
        data["target1_power_w"] = data.get("target1_power_w")
        data["target1_material"] = data.get("target1_material")
        recipe = RecipeExtraction(
            record_id=record_id,
            paper_key=paper_key,
            doi=doi,
            recipe_id=recipe_id,
            sample_id=sample_id,
            data=data,
            fields=fields,
            targets=targets,
            review_items=review,
            source_document_id=source_document_id,
            source_url=source_url,
            warnings=warnings,
        )
        evidence = [_field_evidence(field, record_id, doi, source_url) for field in fields]
        return ExtractionResult(
            paper_key=paper_key,
            doi=doi,
            status="extracted",
            recipes=[recipe],
            evidence=evidence,
            review_items=review,
            warnings=warnings,
            metadata={
                "section": primary.get("heading"),
                "locator": primary.get("locator"),
                "field_count": len(fields),
                "selected_section_type": primary.get("section_type"),
                "section_inventory": [
                    {
                        "heading": section.get("heading"),
                        "section_type": section.get("section_type"),
                        "locator": section.get("locator"),
                        "chars": len(section.get("text") or ""),
                    }
                    for section in sections
                ],
                "ignored_section_count": max(0, len(sections) - 1),
                "table_count": len(tables),
                "coverage_warnings": (
                    ["no_tables_parsed"] if not tables else []
                ) + (["single_section_selected"] if len(sections) > 1 else []),
            },
        )


_GAS_RATIO_RE = re.compile(
    r"\b(?P<g1>N2|Ar|O2|H2)\s*[:/]\s*(?P<g2>N2|Ar|O2|H2)\s*(?:=|:)?\s*"
    r"(?P<n1>\d+(?:\.\d+)?)\s*:\s*(?P<n2>\d+(?:\.\d+)?)\s*(?P<unit>sccm|slm)",
    re.IGNORECASE,
)
_GAS_VALUE_FIRST_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>sccm|slm)\s*(?:of|for)?\s*"
    r"(?P<gas>N2|Ar|O2|H2)\b",
    re.IGNORECASE,
)
_GAS_NAME_FIRST_RE = re.compile(
    r"\b(?P<gas>N2|Ar|O2|H2)\b[^.;0-9]{0,30}?"
    r"(?:(?:gas\s*)?flow(?:\s*rate)?\s*)?(?:of|was|is|=|:|at)?\s*"
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>sccm|slm)",
    re.IGNORECASE,
)


def _gas_flows(text: str) -> dict[str, dict]:
    normalized = re.sub(r"N\s*2", "N2", text or "")
    normalized = normalized.replace("N₂", "N2").replace("O₂", "O2").replace("H₂", "H2")
    flows: dict[str, dict] = {}

    def add(gas: str, num: str, unit: str, match: str) -> None:
        gas = gas.upper()
        if gas in flows:
            return
        value = parse_numeric(num)
        if value is None:
            return
        flows[gas] = {"value": value, "raw": num, "unit": unit, "match": match}

    for match in _GAS_RATIO_RE.finditer(normalized):
        add(match.group("g1"), match.group("n1"), match.group("unit"), match.group(0))
        add(match.group("g2"), match.group("n2"), match.group("unit"), match.group(0))
    for match in _GAS_VALUE_FIRST_RE.finditer(normalized):
        add(match.group("gas"), match.group("num"), match.group("unit"), match.group(0))
    for match in _GAS_NAME_FIRST_RE.finditer(normalized):
        add(match.group("gas"), match.group("num"), match.group("unit"), match.group(0))
    return flows


def _pressure_value(text: str) -> float | None:
    value = parse_numeric(text)
    return value


def _temperature_match(text: str):
    for pattern in (_TEMP_LAYER_RE, _TEMP_DEPOSITION_RE):
        for match in pattern.finditer(text):
            snippet = _sentence(text, match.group(0))
            if re.search(r"electrode|adhesion|seed|buffer|anneal", snippet, re.I):
                continue
            return match
    match = _TEMP_RE.search(text)
    if match and not re.search(r"electrode|adhesion|seed|buffer|anneal", _sentence(text, match.group(0)), re.I):
        return match
    for match in _TEMP_GENERIC_RE.finditer(text):
        snippet = _sentence(text, match.group(0))
        if re.search(r"AlScN|AlBScN|ScAlN|nitride|film|deposition", snippet, re.I) and not re.search(
            r"electrode|adhesion|seed|buffer|anneal", snippet, re.I
        ):
            return match
    return None


def _sentence(text: str, needle: str) -> str:
    position = text.find(needle)
    if position < 0:
        return ""
    # Split on sentence punctuation followed by whitespace, not on the dot in
    # "at.%" or decimal numbers.
    start = 0
    for boundary in re.finditer(r"(?<=[.!?])\s+|\n+", text):
        end = boundary.start()
        if start <= position < end:
            return re.sub(r"\s+", " ", text[start:end]).strip()[:800]
        start = boundary.end()
    return re.sub(r"\s+", " ", text[start:]).strip()[:800]


def _field_evidence(field: ExtractedField, record_id: str, doi: str, source_url: str | None) -> dict:
    return {
        "evidence_id": f"{record_id}:{field.field_key}",
        "record_id": record_id,
        "entity_type": field.entity_type,
        "entity_id": field.entity_id,
        "field_key": field.field_key,
        "paper_doi": doi,
        "source_url": source_url,
        "source_section": field.section,
        "page_table_figure": field.locator,
        "evidence_text": field.evidence_text,
        "raw_value": field.raw_value,
        "raw_unit": field.raw_unit,
        "normalized_value": field.normalized_value,
        "normalized_unit": field.normalized_unit,
        "evidence_status": field.status,
        "derivation_note": field.derivation_note,
    }


def _review(record_id: str, paper_key: str, field_key: str, reason: str, severity: str) -> dict:
    digest = hashlib.sha256(f"{record_id}|{field_key}|{reason}".encode()).hexdigest()[:12]
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


def _join_modes(values: list[str]) -> str:
    result = []
    for value in values:
        normalized = re.sub(r"\s+", " ", value).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return " + ".join(result)


def _method_category(modes: list[str]) -> str:
    joined = " ".join(modes).upper()
    if "HIPIMS" in joined:
        return "HiPIMS"
    if "PULSED DC" in joined:
        return "pulsed DC"
    if "RF" in joined and "DC" in joined:
        return "RF+DC"
    if "RF" in joined:
        return "RF"
    if "DC" in joined:
        return "DC"
    return "unknown"


def _method_detail(text: str, modes: list[str]) -> str:
    parts = []
    if re.search(r"reactive|nitrogen|N2|N₂", text, re.I):
        parts.append("reactive")
    if re.search(r"co[\s-]?sputter|multiple targets|five independent targets", text, re.I):
        parts.append("co-sputtering")
    if modes:
        parts.append(" + ".join(modes))
    return "; ".join(parts) if parts else "sputtering"


def _power_basis(text: str) -> str:
    lowered = text.casefold()
    if "power density" in lowered or "w/cm" in lowered:
        return "power_density"
    if "sputter power" in lowered or "sputtering power" in lowered:
        return "sputter_power"
    if "bias" in lowered:
        return "bias_power"
    if "etch" in lowered:
        return "etch_power"
    if "target" in lowered:
        return "target_power"
    return "unknown"


def _power_statistic(text: str) -> str:
    lowered = text.casefold()
    if "peak" in lowered:
        return "peak"
    if "rated" in lowered or "maximum" in lowered or "max." in lowered:
        return "rated"
    if "average" in lowered or "mean" in lowered:
        return "average"
    return "unknown"


_TARGET_DIAMETER_RES = (
    re.compile(r"(?P<num>\d+(?:\.\d+)?)[\s-]*(?P<unit>mm|cm|inch|in|\")\s*(?:diameter|dia\.?)\b", re.I),
    re.compile(r"\bdiameter\s*(?:of\s*)?(?P<num>\d+(?:\.\d+)?)[\s-]*(?P<unit>mm|cm|inch|in|\")", re.I),
    re.compile(r"(?P<num>\d+(?:\.\d+)?)[\s-]*(?P<unit>mm|cm|inch|in|\")[^.;]{0,25}?\btargets?\b", re.I),
)

def _target_diameter_mm(text: str) -> float | None:
    text = text or ""
    for index, pattern in enumerate(_TARGET_DIAMETER_RES):
        for match in pattern.finditer(text):
            value = parse_numeric(match.group("num"))
            if value is None:
                continue
            window = text[max(0, match.start() - 35): match.end() + 35]
            # "16.5 cm from the target" is a target-substrate distance, not
            # a diameter. For the loose target-near pattern, a thickness
            # phrase is also rejected; explicit "diameter" patterns are not.
            bad = r"distance|target[\s-]*substrate|TSD"
            if index >= 2:
                bad += r"|thickness|thick\b|(?:mm|cm|inch|in|\")\s+from\b"
            if re.search(bad, window, re.I):
                continue
            unit = match.group("unit").casefold()
            if unit in {"inch", "in", "\""}:
                value *= 25.4
            elif unit == "cm":
                value *= 10
            if 5 <= value <= 300:
                return round(value, 2)
    return None


def _power_density(power_w: float | None, diameter_mm: float | None) -> float | None:
    if power_w is None or diameter_mm is None or diameter_mm <= 0:
        return None
    area_cm2 = math.pi * ((diameter_mm / 10) / 2) ** 2
    if area_cm2 <= 0:
        return None
    return round(float(power_w) / area_cm2, 3)


def _deposition_modes(text: str) -> list[str]:
    modes = []
    for match in _MODE_RE.finditer(text):
        snippet = _sentence(text, match.group(0))
        if re.search(r"measurement|C-V|capacitance|excitation|PUND|endurance|leakage|breakdown|probe", snippet, re.I):
            continue
        if re.search(r"\betch|pre[-\s]?treatment|cleaning|native\s+oxide|plasma\s+etch", snippet, re.I):
            continue
        if re.search(r"sputter|deposit|target|plasma|power", snippet, re.I):
            mode = match.group(0).upper()
            if mode not in modes:
                modes.append(mode)
    return modes


def _target_configuration(text: str) -> str:
    lowered = text.casefold()
    if "co-sputter" in lowered or "cosputter" in lowered:
        return "co-sputtering"
    if re.search(r"\b(?:five|four|three|multiple|several)\s+(?:independent\s+)?targets?\b", lowered):
        return "multiple targets"
    if re.search(r"\bdual[\s-]+targets?\b", lowered):
        return "dual target"
    if re.search(r"\bsingle\s+(?:alloy\s+)?target\b|\bone\s+target\b", lowered):
        return "single target"
    return "unknown"


def _target_material(text: str) -> str:
    lowered = text.casefold()
    if re.search(r"\bal\s+target\b", lowered) and re.search(r"\bsc\s+target\b", lowered):
        return "Al/Sc"
    if re.search(r"\balsc\b", lowered) and "target" in lowered:
        return "AlSc alloy"
    if re.search(r"\bal\s+target\b", lowered):
        return "Al"
    if re.search(r"\bsc\s+target\b", lowered):
        return "Sc"
    if "aln" in lowered and "sc" in lowered and "target" in lowered:
        return "AlSc alloy"
    return ""


def _dual_target_match(text: str) -> str:
    patterns = (
        r"\bAl\s+target\b[^.;]{0,80}?\bSc\s+target\b",
        r"\bSc\s+target\b[^.;]{0,80}?\bAl\s+target\b",
        r"\bAl\b[^.;]{0,50}?\band\b[^.;]{0,30}?\bSc\b[^.;]{0,30}?\btargets?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return match.group(0)
    return ""


def _target_power_pairs(text: str) -> dict[str, dict]:
    """Return per-metal power for explicit '250 W on the Sc target' forms."""
    result: dict[str, dict] = {}
    patterns = (
        re.compile(
            r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>kW|W)\s*(?:on|to|for|applied\s+to)?\s*"
            r"(?:the\s+)?(?P<metal>Al|Sc|Ti|Pt|Mo)\s+target",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?P<metal>Al|Sc|Ti|Pt|Mo)\s+target\b[^.;]{0,40}?"
            r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>kW|W)",
            re.IGNORECASE,
        ),
    )
    for pattern in patterns:
        for match in pattern.finditer(text or ""):
            metal = match.group("metal").capitalize()
            if metal in result:
                continue
            value = parse_numeric(match.group("num"))
            if value is None:
                continue
            result[metal] = {
                "value": value,
                "raw": match.group("num"),
                "unit": match.group("unit"),
                "match": match.group(0),
                "basis": _power_basis(match.group(0)),
                "statistic": _power_statistic(match.group(0)),
                "definition": match.group(0)[:240],
            }
    return result
