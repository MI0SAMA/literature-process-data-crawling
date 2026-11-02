"""Lightweight static review page for recipes and evidence."""

from __future__ import annotations

import html
import json
from pathlib import Path

from app.storage import Database

_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>AlScN-PVD 审核页</title>
<style>
 body{font-family:system-ui,Segoe UI,Microsoft YaHei,sans-serif;margin:24px;background:#f6f7f9;color:#1d2329}
 h1{margin:0 0 4px} .sub{color:#667085;margin-bottom:18px}
 input{padding:8px 10px;width:420px;max-width:100%;border:1px solid #ccd3dc;border-radius:6px}
 .card{background:#fff;border:1px solid #e1e6ed;border-radius:8px;margin:14px 0;padding:14px}
 .head{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
 .doi{color:#2457a7;font-size:13px}.tag{font-size:12px;padding:3px 8px;border-radius:10px;background:#eef2f6}
 .ok{background:#e7f6ec;color:#17663a}.partial{background:#fff3d6;color:#8a5a00}.review{background:#fde8e8;color:#9b1c1c}
 table{border-collapse:collapse;width:100%;margin-top:10px} th,td{border:1px solid #e4e8ee;padding:6px 8px;vertical-align:top;font-size:13px}
 th{background:#f2f4f7;text-align:left} code{background:#f2f4f7;padding:1px 4px;border-radius:3px}
 .ev{color:#475467;font-size:12px;margin-top:4px}
 details{margin-top:8px} summary{cursor:pointer;color:#2457a7}
 .audit{background:#fff4e5;border:1px solid #f0b26b;color:#7a3e00;padding:10px 12px;border-radius:6px;margin:12px 0}
</style>
</head>
<body>
<h1>AlScN-PVD 文献审核页</h1>
<div class="audit"><b>AUDIT 2026-10-30：</b>当前抽取结果为规则基线并处于隔离状态（review_status=needs_review），
不能直接作为工程/科学结论。已验证的已知问题包括气体比例、层厚、靶材/衬底归属和 AC 测试被误当沉积模式；
请逐字段对照原文证据后再使用。</div>
<div class="sub">只读预览。共 __COUNT__ 条配方；证据与字段并排显示。审核状态可用 CLI 更新。</div>
<input id="q" placeholder="搜索 DOI / 标题 / 字段..." oninput="filterCards()">
<div id="cards">__CARDS__</div>
<script>
function filterCards(){
 const q=document.getElementById('q').value.toLowerCase();
 document.querySelectorAll('.card').forEach(c=>{
   c.style.display=c.innerText.toLowerCase().includes(q)?'':'none';
 });
}
</script>
</body>
</html>
"""


def generate_review_html(db: Database, output_path: str | Path) -> str:
    recipes = db.list_recipes()
    cards = []
    for recipe in recipes:
        data = recipe["data"]
        evidence = db.list_evidence(recipe["record_id"])
        targets = db.list_targets(recipe["record_id"])
        status = recipe.get("review_status") or "new"
        tag_class = "ok" if status == "verified" else ("review" if status == "needs_review" else "partial")
        rows = []
        for item in evidence:
            rows.append(
                "<tr>"
                f"<td><code>{html.escape(item['field_key'])}</code></td>"
                f"<td>{html.escape(str(item.get('raw_value') or ''))} {html.escape(str(item.get('raw_unit') or ''))}</td>"
                f"<td>{html.escape(str(item.get('normalized_value') if item.get('normalized_value') is not None else ''))} {html.escape(str(item.get('normalized_unit') or ''))}</td>"
                f"<td>{html.escape(item.get('evidence_status') or '')}</td>"
                f"<td>{html.escape(item.get('source_section') or '')} / {html.escape(item.get('page_table_figure') or '')}"
                f"<div class='ev'>{html.escape((item.get('evidence_text') or '')[:500])}</div></td>"
                "</tr>"
            )
        target_parts = []
        for target in targets:
            parts = [
                f"T{target.get('target_index')}: {target.get('target_material') or 'unknown'}",
                f"{target.get('target_power_w') if target.get('target_power_w') is not None else '—'} W",
            ]
            if target.get("target_diameter_mm"):
                parts.append(f"Ø{target['target_diameter_mm']:g} mm")
            if target.get("power_basis"):
                parts.append(str(target["power_basis"]))
            if target.get("power_density_w_cm2") is not None:
                parts.append(f"{target['power_density_w_cm2']:g} W/cm²")
            target_parts.append(" · ".join(parts))
        target_text = ", ".join(target_parts)
        cards.append(
            "<div class='card'>"
            "<div class='head'>"
            f"<div><b>{html.escape(data.get('paper_title') or '')}</b><div class='doi'>{html.escape(data.get('paper_doi') or recipe.get('paper_doi') or '')}</div></div>"
            f"<div><span class='tag {tag_class}'>{html.escape(status)}</span></div>"
            "</div>"
            f"<div class='sub'>记录 {html.escape(recipe['record_id'])} · {html.escape(str(data.get('deposition_method') or ''))} · {html.escape(str(data.get('sputtering_mode') or ''))} · 靶材: {html.escape(target_text)}</div>"
            "<details open><summary>字段与证据（" + str(len(evidence)) + "）</summary>"
            "<table><tr><th>字段</th><th>原始值</th><th>标准化</th><th>状态</th><th>章节/位置 + 原文</th></tr>"
            + "".join(rows) +
            "</table></details>"
            "</div>"
        )
    page = _TEMPLATE.replace("__COUNT__", str(len(recipes))).replace("__CARDS__", "\n".join(cards))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    return str(output)
