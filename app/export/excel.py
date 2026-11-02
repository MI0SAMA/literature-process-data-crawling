"""Excel export to the six-sheet Phase 1 schema template."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.storage import Database

SHEETS = ("工艺记录", "靶材明细", "性能记录", "证据记录", "字段字典", "原表头映射")


class ExcelExporter:
    def __init__(self, schema_path: str | Path = "config/alscn_pvd_schema.json") -> None:
        self.schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        self.tables = self.schema["tables"]

    def export(self, db: Database, output_path: str | Path) -> str:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        workbook.remove(workbook.active)

        recipes = db.list_recipes()
        targets = db.list_targets()
        evidence = db.list_evidence()
        measurements = [dict(row) for row in db.conn.execute("SELECT * FROM measurements ORDER BY measurement_id")]
        self._target_cache = targets

        self._write_table(workbook, "工艺记录", self.tables["recipes"], [self._recipe_row(recipe) for recipe in recipes])
        self._write_table(workbook, "靶材明细", self.tables["targets"], [self._target_row(target) for target in targets])
        self._write_table(workbook, "性能记录", self.tables["measurements"], [self._measurement_row(item) for item in measurements])
        self._write_table(workbook, "证据记录", self.tables["evidence"], [self._evidence_row(item) for item in evidence])
        self._write_field_dictionary(workbook)
        self._write_header_mapping(workbook)
        workbook.save(output)
        return str(output)

    def _write_table(self, workbook: Workbook, title: str, fields: list[dict], rows: list[dict[str, Any]]) -> None:
        sheet = workbook.create_sheet(title)
        headers = [field["label"] for field in fields]
        keys = [field["key"] for field in fields]
        sheet.append(headers)
        for row in rows:
            sheet.append([_excel_value(row.get(key)) for key in keys])
        self._style_sheet(sheet, headers)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

    def _write_field_dictionary(self, workbook: Workbook) -> None:
        sheet = workbook.create_sheet("字段字典")
        headers = ["数据表", "字段Key", "Excel表头", "类别", "标准单位", "数据类型", "优先级", "定义/说明"]
        sheet.append(headers)
        for table_name, fields in self.tables.items():
            for field in fields:
                sheet.append(
                    [
                        table_name,
                        field.get("key", ""),
                        field.get("label", ""),
                        field.get("group", ""),
                        field.get("unit", ""),
                        field.get("type", ""),
                        field.get("priority", ""),
                        field.get("definition", ""),
                    ]
                )
        self._style_sheet(sheet, headers)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

    def _write_header_mapping(self, workbook: Workbook) -> None:
        sheet = workbook.create_sheet("原表头映射")
        headers = ["原始表头", "规范化字段", "说明", "类型"]
        sheet.append(headers)
        for row in self.schema.get("original_header_mapping") or []:
            sheet.append(
                [
                    row.get("original_header", ""),
                    row.get("canonical_mapping", ""),
                    row.get("note", ""),
                    row.get("type", ""),
                ]
            )
        self._style_sheet(sheet, headers)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

    def _recipe_row(self, recipe: dict) -> dict[str, Any]:
        data = recipe.get("data") or {}
        row = dict(data)
        row.setdefault("record_id", recipe.get("record_id"))
        row.setdefault("paper_doi", recipe.get("paper_doi"))
        row.setdefault("recipe_id", recipe.get("recipe_id"))
        row.setdefault("sample_id", recipe.get("sample_id"))
        row.setdefault("review_status", recipe.get("review_status"))
        # The DB status is authoritative; data_json may be from an older
        # extraction or may not include a later review-set action.
        row["review_status"] = recipe.get("review_status") or row.get("review_status")
        for target in self._targets_for(recipe.get("record_id")):
            index = int(target.get("target_index") or 0)
            if index in (1, 2):
                target_data = target.get("data") or {}
                row[f"target{index}_material"] = target.get("target_material")
                row[f"target{index}_composition"] = target_data.get("target_composition")
                row[f"target{index}_sc_at_pct"] = target_data.get("target_sc_at_pct")
                row[f"target{index}_power_w"] = target.get("target_power_w")
                row[f"target{index}_power_mode"] = target_data.get("power_mode")
                row[f"target{index}_diameter_mm"] = target_data.get("target_diameter_mm")
        return row

    def _targets_for(self, record_id: str | None) -> list[dict]:
        if not record_id:
            return []
        return [target for target in self._all_targets if target.get("record_id") == record_id]

    @property
    def _all_targets(self) -> list[dict]:
        return getattr(self, "_target_cache", [])

    def _target_row(self, target: dict) -> dict[str, Any]:
        data = target.get("data") or {}
        row = dict(data)
        for key in ("target_record_id", "record_id", "target_index", "layer_role", "target_material", "target_power_w", "evidence_id"):
            row.setdefault(key, target.get(key))
        return row

    def _measurement_row(self, item: dict) -> dict[str, Any]:
        data = json.loads(item.get("data_json") or "{}")
        data.setdefault("measurement_id", item.get("measurement_id"))
        data.setdefault("record_id", item.get("record_id"))
        data.setdefault("sample_id", item.get("sample_id"))
        data.setdefault("property_key", item.get("property_key"))
        data.setdefault("property_value", item.get("property_value"))
        data.setdefault("property_unit", item.get("property_unit"))
        data.setdefault("evidence_id", item.get("evidence_id"))
        return data

    def _evidence_row(self, item: dict) -> dict[str, Any]:
        return {field["key"]: item.get(field["key"]) for field in self.tables["evidence"]}

    def _style_sheet(self, sheet, headers: list[str]) -> None:
        header_fill = PatternFill("solid", fgColor="1F4E78")
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for index, header in enumerate(headers, start=1):
            width = min(60, max(12, len(str(header)) * 2 + 4))
            sheet.column_dimensions[get_column_letter(index)].width = width
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=False)


def _excel_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value
