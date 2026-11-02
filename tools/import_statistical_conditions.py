"""Import majie's statistical_conditions.md into the annotation database.

The markdown has per-paper shared conditions, per-recipe overrides, and
evidence IDs. This is a best-effort structured import; values remain text so
ranges such as 300–350 °C are preserved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MARKDOWN = Path("statistical_conditions.md")
DB = Path("data/annotation/testset.sqlite3")
ANNOTATOR = "majie"

PARAM_MAP = {
    "沉积方法细分": "method_detail",
    "溅射模式": "sputtering_mode",
    "设备型号": "equipment_model",
    "工作气压（mbar）": "working_pressure_mbar",
    "本底气压（mbar）": "base_pressure_mbar",
    "沉积温度（°C）": "substrate_temperature_c",
    "AlScN 膜厚（nm）": "alscn_thickness_nm",
    "沉积时间（min）": "deposition_time_min",
    "靶材–基底距离（mm）": "target_substrate_distance_mm",
    "备注说明": "notes",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_value(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^\*\*|\*\*$", "", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = value.replace("`", "").strip()
    if value.startswith("条件缺失") or value.startswith("不适用"):
        return ""
    return value


def parse_table(lines: list[str], header: str) -> list[dict[str, str]]:
    start = next((i for i, line in enumerate(lines) if line.strip().startswith(header)), None)
    if start is None:
        return []
    rows = []
    header_cells = [cell.strip() for cell in lines[start].strip().strip("|").split("|")]
    for line in lines[start + 1:]:
        if not line.strip().startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(dict(zip(header_cells, cells)))
    return rows


def as_number(value: str) -> str:
    value = clean_value(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", value.replace(",", "."))
    return match.group(0) if match else ""


def target_power_pairs(material: str, power: str) -> list[dict]:
    material = clean_value(material)
    power = clean_value(power)
    if not power:
        return []
    range_match = re.search(r"\d+(?:\.\d+)?\s*[-–—~]\s*\d+(?:\.\d+)?", power)
    if range_match:
        return [{"label": material or "unknown", "power": range_match.group(0), "range": True}]
    pairs = []
    for match in re.finditer(r"(AlSc|Al|Sc|Ti|Pt|Mo)?\s*(?:≈|~)?\s*(\d+(?:\.\d+)?)", power):
        label = (match.group(1) or "").strip()
        value = match.group(2)
        pairs.append({"label": label, "power": value})
    if not pairs:
        return []
    # "条件缺失；Al 电流 1.2 A" is not a power value.
    if "电流" in power and "W" not in power and "功率" not in power:
        return []
    if len(pairs) == 1:
        pairs[0]["label"] = material or "unknown"
    return pairs


def parse_gas(text: str) -> dict[str, str]:
    text = clean_value(text)
    out = {}
    ratio = re.search(r"N2\s*:\s*Ar\s*=\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", text, re.I)
    if ratio:
        return {"N2": ratio.group(1), "Ar": ratio.group(2)}
    ratio = re.search(r"Ar\s*:\s*N2\s*=\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", text, re.I)
    if ratio:
        return {"Ar": ratio.group(1), "N2": ratio.group(2)}
    for gas in ("N2", "Ar"):
        match = re.search(rf"{gas}\s*[:=]?\s*(\d+(?:\.\d+)?)", text, re.I)
        if not match:
            match = re.search(rf"(\d+(?:\.\d+)?)\s*sccm\s*{gas}", text, re.I)
        if match:
            out[gas] = match.group(1)
    return out


def build_targets(material: str, power: str, diameter: str, mode: str, evidence: str) -> list[dict]:
    material = clean_value(material)
    diameter = as_number(diameter)
    mode = clean_value(mode)
    pairs = target_power_pairs(material, power)
    if not pairs:
        if material:
            if re.search(r"Al", material) and re.search(r"Sc", material) and "合金" not in material:
                return [
                    {"material": "Al", "power_w": "", "power_basis": "unknown", "power_statistic": "unknown", "diameter_mm": diameter, "mode": mode, "evidence": evidence},
                    {"material": "Sc", "power_w": "", "power_basis": "unknown", "power_statistic": "unknown", "diameter_mm": diameter, "mode": mode, "evidence": evidence},
                ]
            return [{"material": material, "power_w": "", "power_basis": "unknown", "power_statistic": "unknown", "diameter_mm": diameter, "mode": mode, "evidence": evidence}]
        return []
    targets = []
    for pair in pairs:
        label = pair["label"].strip()
        if label in {"Al", "Sc", "Ti", "Pt", "Mo", "AlSc"}:
            material_label = label
        else:
            material_label = material or label or "unknown"
        targets.append(
            {
                "material": material_label,
                "power_w": pair["power"],
                "power_basis": "sputter_power" if "sputter" in power.lower() else "target_power",
                "power_statistic": "unknown",
                "diameter_mm": diameter,
                "mode": mode,
                "evidence": evidence,
            }
        )
    return targets


def make_recipe(label: str, shared: dict[str, str], overrides: dict[str, str], evidence_ids: list[str]) -> dict:
    values = dict(shared)
    values.update({k: v for k, v in overrides.items() if clean_value(v)})
    data = {
        "recipe_id": label,
        "method_detail": clean_value(values.get("沉积方法细分", "")),
        "sputtering_mode": clean_value(values.get("溅射模式", "")),
        "equipment_model": clean_value(values.get("设备型号", "")),
        "working_pressure_mbar": clean_value(values.get("工作气压（mbar）", "")),
        "base_pressure_mbar": clean_value(values.get("本底气压（mbar）", "")),
        "substrate_temperature_c": clean_value(values.get("沉积温度（°C）", "")),
        "alscn_thickness_nm": clean_value(values.get("AlScN 膜厚（nm）", "")),
        "deposition_time_min": clean_value(values.get("沉积时间（min）", "")),
        "target_substrate_distance_mm": clean_value(values.get("靶材–基底距离（mm）", "")),
        "confidence": "medium",
        "evidence": "statistical_conditions.md；证据：" + "、".join(evidence_ids[:8]) if evidence_ids else "statistical_conditions.md",
        "notes": "；".join(dict.fromkeys(
            part for part in (
                clean_value(values.get("备注说明", "")),
                clean_value(overrides.get("备注说明", "")),
                clean_value(values.get("模式／靶状态／基底偏压", "")),
            ) if part
        )),
    }
    gas = parse_gas(values.get("每种气体流量（sccm）", ""))
    data["n2_flow_sccm"] = gas.get("N2", "")
    data["ar_flow_sccm"] = gas.get("Ar", "")
    data["targets"] = build_targets(
        values.get("靶材材料", ""),
        values.get("功率（W）", ""),
        values.get("靶材直径（mm）", ""),
        values.get("溅射模式", ""),
        data["evidence"],
    )
    return data


def coverage_map(text: str) -> dict[str, str]:
    result = {}
    for match in re.finditer(r"^\|\s*\[?(OD-\d{2})\]?\([^)]*\)\s*\|\s*[^|]+\|\s*([^|]+)\|", text, re.M):
        result[match.group(1)] = match.group(2).strip()
    return result


def parse_section(od: str, section: str, coverage: str) -> dict:
    shared = {}
    for row in parse_table(section.splitlines(), "|参数|共享条件"):
        shared[row.get("参数", "").strip()] = row.get("共享条件（变体可覆盖）", "").strip()
    evidence_ids = list(dict.fromkeys(re.findall(r"OD-\d{2}-E\d+", section)))
    entries = []
    pattern = re.compile(r"^\*\*(OD-\d{2}-R[^*]+)\*\*\s*$", re.M)
    matches = list(pattern.finditer(section))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        block = section[match.end():end]
        override_rows = parse_table(block.splitlines(), "|覆盖参数|值|")
        overrides = {row.get("覆盖参数", "").strip(): row.get("值", "").strip() for row in override_rows}
        entries.append(make_recipe(match.group(1), shared, overrides, evidence_ids))
    if not entries:
        # A paper with shared conditions but no variant still gets one recipe.
        no_experiment = any(token in coverage for token in ("无AlScN实验", "理论计算"))
        if not no_experiment:
            entries.append(make_recipe(f"{od}-R01", shared, {}, evidence_ids))
    return {
        "relevance": "not_relevant" if any(token in coverage for token in ("无AlScN实验", "理论计算")) else ("unsure" if "综述" in coverage else "relevant"),
        "has_recipe": "unsure" if "综述" in coverage else ("yes" if entries else "no"),
        "recipe_count": len(entries),
        "recipes": entries,
        "notes": f"来源：majie statistical_conditions.md；统计对象：{coverage}",
        "evidence": "statistical_conditions.md；证据：" + "、".join(evidence_ids),
    }


def od30(section: str) -> dict:
    lines = section.splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.strip().startswith("|二手配方ID")), None)
    recipes = []
    if header_index is not None:
        header_cells = [cell.strip() for cell in lines[header_index].strip().strip("|").split("|")]
        for line in lines[header_index + 1:]:
            if not line.strip().startswith("|"):
                break
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 8 or set(line) <= {"|", "-", ":"}:
                continue
            row = dict(zip(header_cells, cells))
            evidence = clean_value(row.get("证据", ""))
            pressure = clean_value(row.get("原表压力换算mbar（性质未核）", ""))
            recipes.append(
                {
                    "recipe_id": clean_value(row.get("二手配方ID（原文引用号）", "")),
                    "method_detail": clean_value(row.get("沉积方法", "")),
                    "sputtering_mode": "",
                    "equipment_model": "",
                    "working_pressure_mbar": "" if pressure.startswith("条件缺失") else pressure,
                    "base_pressure_mbar": "",
                    "substrate_temperature_c": clean_value(row.get("温度°C", "")),
                    "alscn_thickness_nm": clean_value(row.get("膜厚nm", "")),
                    "deposition_time_min": "",
                    "target_substrate_distance_mm": "",
                    "n2_flow_sccm": "",
                    "ar_flow_sccm": "",
                    "confidence": "low",
                    "evidence": f"statistical_conditions.md；{evidence}",
                    "notes": f"二手综述 Table1；Sc={clean_value(row.get('Sc%', ''))}；基底/电极={clean_value(row.get('基底／电极', ''))}；压力性质未核",
                    "targets": [],
                }
            )
    # Prose recipe Ref24-opt
    match = re.search(r"\*\*正文补充配方 30-Ref24-opt.*?\*\*(.*?)(?=\n\n|\Z)", section, re.S)
    if match:
        text = match.group(1)
        recipes.append(
            {
                "recipe_id": "30-Ref24-opt",
                "method_detail": "RF reactive sputter",
                "sputtering_mode": "RF",
                "equipment_model": "",
                "working_pressure_mbar": "0.026664",
                "base_pressure_mbar": "",
                "substrate_temperature_c": "300",
                "alscn_thickness_nm": "25",
                "deposition_time_min": "",
                "target_substrate_distance_mm": "",
                "n2_flow_sccm": "",
                "ar_flow_sccm": "",
                "confidence": "low",
                "evidence": "statistical_conditions.md；OD-30-E2",
                "notes": "正文补充配方；Sc30%；TiN；与Table1 Ref24的400 °C不同",
                "targets": [],
            }
        )
    return {
        "relevance": "unsure",
        "has_recipe": "unsure",
        "recipe_count": len(recipes),
        "recipes": recipes,
        "notes": "来源：majie statistical_conditions.md；综述二手配方，未当作新增独立实验",
        "evidence": "statistical_conditions.md；OD-30-E1、OD-30-E2、OD-30-E3",
    }


def parse_markdown(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    coverage = coverage_map(text)
    starts = list(re.finditer(r"^## (OD-\d{2}) · .+$", text, re.M))
    out = []
    for index, match in enumerate(starts):
        od = match.group(1)
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        section = text[match.end():end]
        data = od30(section) if od == "OD-30" else parse_section(od, section, coverage.get(od, ""))
        data["test_id"] = od
        out.append(data)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown", default=str(MARKDOWN))
    parser.add_argument("--db", default=str(DB))
    parser.add_argument("--annotator", default=ANNOTATOR)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replace", action="store_true", help="explicitly archive and replace this annotator's history")
    args = parser.parse_args()
    rows = parse_markdown(Path(args.markdown))
    print(f"papers={len(rows)} recipes={sum(len(r['recipes']) for r in rows)}")
    for row in rows:
        print(f"{row['test_id']}: {row['relevance']:12} recipes={len(row['recipes']):2} has={row['has_recipe']}")
    if not args.apply:
        print("dry-run: pass --apply to write")
        return 0
    source = Path(args.markdown)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    import_run_id = f"import_{source_hash[:12]}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS annotation_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id TEXT NOT NULL,
                annotator TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        for column in ("import_run_id TEXT", "source_hash TEXT"):
            try:
                con.execute(f"ALTER TABLE annotation_actions ADD COLUMN {column}")
            except sqlite3.OperationalError:
                pass
        con.execute(
            """CREATE TABLE IF NOT EXISTS annotation_imports (
                import_run_id TEXT PRIMARY KEY,
                annotator TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                source_name TEXT,
                tests_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        existing = con.execute(
            "SELECT import_run_id FROM annotation_imports WHERE annotator=? AND source_hash=?",
            (args.annotator, source_hash),
        ).fetchone()
        if existing and not args.replace:
            print(f"already imported source_hash={source_hash[:12]} as {existing[0]}; use --replace to re-import")
            return 0
        if args.replace:
            con.execute(
                """CREATE TABLE IF NOT EXISTS annotation_actions_archive AS
                   SELECT * FROM annotation_actions WHERE 0"""
            )
            con.execute(
                """INSERT INTO annotation_actions_archive
                   SELECT * FROM annotation_actions WHERE annotator=?""",
                (args.annotator,),
            )
            con.execute("DELETE FROM annotation_actions WHERE annotator=?", (args.annotator,))
        for row in rows:
            row["import_run_id"] = import_run_id
            row["source_hash"] = source_hash
            row["source_name"] = source.name
            con.execute(
                """INSERT INTO annotation_actions(
                       test_id, annotator, data_json, created_at, import_run_id, source_hash
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (row["test_id"], args.annotator, json.dumps(row, ensure_ascii=False), now(), import_run_id, source_hash),
            )
        con.execute(
            """INSERT INTO annotation_imports(import_run_id, annotator, source_hash, source_name, tests_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (import_run_id, args.annotator, source_hash, source.name, len(rows), now()),
        )
        con.commit()
    print(f"wrote {len(rows)} annotations for {args.annotator} to {db}; import_run_id={import_run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
