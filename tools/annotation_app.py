"""Local double-annotation web app for the 30-paper OneDrive test set.

Run:
    python tools/annotation_app.py --port 8765

Then open http://127.0.0.1:8765/
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import socket
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pypdf import PdfReader

ROOT = Path("data/annotation/papers")
MANIFEST = Path("data/annotation/testset_manifest.csv")
DB_PATH = Path("data/annotation/testset.sqlite3")
CACHE = Path("data/annotation/text_cache")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_manifest(path: Path | None = None) -> list[dict]:
    with (path or MANIFEST).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS annotation_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id TEXT NOT NULL,
                annotator TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS ix_ann_test ON annotation_actions(test_id, annotator, created_at)")


def resolve_path(item: dict) -> Path:
    path = Path(item["path"])
    return path if path.is_absolute() else ROOT / path


def latest_annotation(test_id: str, annotator: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT data_json, created_at FROM annotation_actions "
            "WHERE test_id=? AND annotator=? ORDER BY id DESC LIMIT 1",
            (test_id, annotator),
        ).fetchone()
    if not row:
        return None
    data = json.loads(row["data_json"])
    data["test_id"] = test_id
    data["annotator"] = annotator
    data["_saved_at"] = row["created_at"]
    return data


def save_annotation(test_id: str, annotator: str, data: dict) -> dict:
    annotator = " ".join(str(annotator or "").split())
    if not annotator:
        raise ValueError("请先填写姓名")
    if len(annotator) > 40 or any(ord(char) < 32 for char in annotator):
        raise ValueError("姓名过长或包含非法字符")
    payload = dict(data)
    payload["test_id"] = test_id
    payload["annotator"] = annotator
    payload.pop("_saved_at", None)
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute(
            "INSERT INTO annotation_actions(test_id, annotator, data_json, created_at) VALUES (?, ?, ?, ?)",
            (test_id, annotator, json.dumps(payload, ensure_ascii=False), now()),
        )
        return {"id": cur.lastrowid, "test_id": test_id, "annotator": annotator, "created_at": now()}


def annotators() -> list[str]:
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute("SELECT DISTINCT annotator FROM annotation_actions ORDER BY annotator").fetchall()
    return [row[0] for row in rows]


def annotation_counts() -> dict:
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute(
            "SELECT test_id, annotator, COUNT(*) n FROM annotation_actions GROUP BY test_id, annotator"
        ).fetchall()
    result: dict[str, dict] = {}
    for test_id, annotator, count in rows:
        result.setdefault(test_id, {})[annotator] = count
    return result


def text_for(item: dict) -> str:
    cache = CACHE / f"{item['test_id']}.txt"
    if cache.exists():
        return cache.read_text(encoding="utf-8", errors="replace")
    path = resolve_path(item)
    try:
        reader = PdfReader(str(path), strict=False)
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:4])
    except Exception as error:
        text = f"[parse error] {type(error).__name__}: {error}"
    text = re.sub(r"[ \t]+", " ", text)
    cache.write_text(text, encoding="utf-8")
    return text


def compare(left: str = "", right: str = "") -> list[dict]:
    manifest = Handler.manifest or load_manifest()
    out = []
    keys = ("relevance", "has_recipe", "recipe_count")
    for item in manifest:
        a = latest_annotation(item["test_id"], left) if left else None
        b = latest_annotation(item["test_id"], right) if right else None
        diffs = []
        if a and b:
            for key in keys:
                if str(a.get(key, "")) != str(b.get(key, "")):
                    diffs.append(key)
            if json.dumps(a.get("recipes", []), ensure_ascii=False, sort_keys=True) != json.dumps(
                b.get("recipes", []), ensure_ascii=False, sort_keys=True
            ):
                diffs.append("recipes")
        out.append({"test_id": item["test_id"], "doi": item["doi"], "has_left": bool(a), "has_right": bool(b), "diffs": diffs})
    return out


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>AlScN 测试集双人标注</title>
<style>
:root{--line:#dfe5ee;--muted:#667085;--bg:#f6f8fb;--blue:#2457a7}
*{box-sizing:border-box} body{margin:0;font:14px/1.5 Segoe UI,Arial,sans-serif;background:var(--bg);color:#1d2939}
header{background:#1f4e78;color:#fff;padding:10px 16px;display:flex;gap:20px;align-items:center;position:sticky;top:0;z-index:5}
header b{font-size:16px} header .grow{flex:1} header a{color:#fff}
.wrap{display:grid;grid-template-columns:260px 1fr;gap:12px;padding:12px;max-width:1600px;margin:auto}
aside,.main{background:#fff;border:1px solid var(--line);border-radius:8px}
aside{padding:10px;max-height:calc(100vh - 70px);overflow:auto;position:sticky;top:58px}
.main{padding:14px}
.item{padding:7px 8px;border-radius:6px;cursor:pointer;margin:2px 0}.item:hover{background:#eef3fb}
.item.active{background:#dbe8fb}.item small{color:var(--muted)}
.row{display:flex;gap:10px;flex-wrap:wrap}.row>*{flex:1;min-width:140px}
label{display:block;font-weight:600;margin:10px 0 4px} input,select,textarea{width:100%;padding:7px;border:1px solid #cfd7e5;border-radius:5px;font:inherit;background:#fff}
textarea{min-height:70px} button{border:0;border-radius:6px;padding:8px 14px;background:var(--blue);color:#fff;cursor:pointer;font-weight:600}
button.secondary{background:#e8eef8;color:#1f4e78}.muted{color:var(--muted);font-size:12px}
iframe{width:100%;height:620px;border:1px solid var(--line);border-radius:6px;background:#fff}
table{width:100%;border-collapse:collapse} th,td{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}
th{font-size:12px;color:var(--muted)} .tag{padding:2px 7px;border-radius:99px;background:#e8eef8;font-size:12px}
.targets input,.targets select{min-width:0} .targets td{padding:4px}
details{margin:10px 0} summary{cursor:pointer;color:var(--blue);font-weight:600}
pre{white-space:pre-wrap;max-height:360px;overflow:auto;background:#f8fafc;border:1px solid var(--line);padding:8px;border-radius:6px}
.bar{position:sticky;bottom:0;background:#fff;border-top:1px solid var(--line);padding:10px 0;display:flex;gap:8px;align-items:center}
</style>
</head>
<body>
<header><b>AlScN 测试集双人标注</b><span class="grow"></span><span id="progress"></span><a href="/compare" target="_blank">A/B 对比</a> · <a href="/export.csv">导出 CSV</a></header>
<div class="wrap">
<aside><div class="muted">你的姓名</div><input id="annotatorName" maxlength="40" placeholder="输入姓名后开始" autocomplete="off" oninput="onNameChange()"><div id="list"></div></aside>
<main class="main">
<div id="meta"></div>
<div class="row"><div><label>相关性</label><select id="relevance"><option value="">--</option><option>relevant</option><option>not_relevant</option><option>unsure</option></select></div>
<div><label>有可执行 recipe</label><select id="has_recipe"><option value="">--</option><option>yes</option><option>no</option><option>unsure</option></select></div>
<div><label>recipe 数量</label><input id="recipe_count" type="number" min="0" step="1" placeholder="0/1/2..."></div></div>
<label>论文级备注</label><textarea id="notes" placeholder="论文级不确定点、版本、补充材料等"></textarea>
<label>论文级整体证据</label><textarea id="evidence" placeholder="跨 recipe 的总说明；每个 recipe 的证据请填在 recipe 内"></textarea>
<h3 style="margin-top:18px">工艺 / Recipe</h3>
<div id="recipes"></div>
<button class="secondary" onclick="addRecipe()">+ 添加工艺 / Recipe</button>
<div class="bar"><button onclick="save()">保存本次标注</button><span id="saved" class="muted"></span><span class="grow"></span><button class="secondary" onclick="move(-1)">上一条</button><button class="secondary" onclick="move(1)">下一条</button></div>
<details><summary>PDF 前 4 页文本（便于搜索）</summary><pre id="text">加载中...</pre></details>
<h3>PDF</h3><iframe id="pdf"></iframe>
</main></div>
<script>
let items=[], idx=0, current=null;
const paperFields=["relevance","has_recipe","recipe_count","notes","evidence"];
const recipeFields=["method_detail","sputtering_mode","equipment_model","working_pressure_mbar","base_pressure_mbar","substrate_temperature_c","n2_flow_sccm","ar_flow_sccm","alscn_thickness_nm","deposition_time_min","target_substrate_distance_mm","confidence","evidence"];
const targetFields=["material","power_w","power_basis","power_statistic","diameter_mm","mode","evidence"];
const labels={method_detail:"沉积方法细分",sputtering_mode:"溅射模式",equipment_model:"设备型号",working_pressure_mbar:"工作气压 mbar",base_pressure_mbar:"本底气压 mbar",substrate_temperature_c:"沉积温度 °C",n2_flow_sccm:"N2 sccm",ar_flow_sccm:"Ar sccm",alscn_thickness_nm:"膜厚 nm",deposition_time_min:"沉积时间 min",target_substrate_distance_mm:"靶基距离 mm",confidence:"置信度",evidence:"本工艺证据"};
function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function currentName(){return document.getElementById("annotatorName").value.trim()}
function onNameChange(){ localStorage.setItem("annotatorName", currentName()); loadCurrent(); }
function refreshProgress(counts){
 const names=new Set(); let total=0;
 Object.values(counts).forEach(x=>{Object.keys(x).forEach(n=>names.add(n)); total+=Object.values(x).reduce((a,b)=>a+b,0)});
 document.getElementById("progress").textContent=`${items.length} 篇 · ${names.size} 位标注者 · ${total} 次保存`;
}
async function boot(){
 items=await (await fetch("/api/manifest")).json();
 const counts=await (await fetch("/api/status")).json();
 document.getElementById("annotatorName").value=localStorage.getItem("annotatorName")||"";
 document.getElementById("list").innerHTML=items.map((it,i)=>`<div class="item" data-i="${i}" onclick="go(${i})"><div>${esc(it.test_id)} · ${esc(it.role)}</div><small>${esc(it.doi)} · ${esc(it.year)} ${Object.keys(counts[it.test_id]||{}).map(esc).join(", ")}</small></div>`).join("");
 refreshProgress(counts);
 go(0);
}
function go(i){ if(i<0||i>=items.length)return; idx=i; document.querySelectorAll(".item").forEach(el=>el.classList.toggle("active",+el.dataset.i===idx)); loadCurrent(); }
function move(d){go(idx+d)}
function setRecipeCount(){const n=document.querySelectorAll(".recipe").length; const el=document.getElementById("recipe_count"); if(el) el.value=String(n)}
function renumber(){document.querySelectorAll(".recipe").forEach((card,i)=>{const n=card.querySelector(".rnum"); if(n)n.textContent=i+1}); setRecipeCount();}
function fieldHtml(k,value,textarea=false){
 const v=esc(value??"");
 if(k==="confidence") return `<div><label>${labels[k]}</label><select data-rk="confidence"><option value="">--</option><option>high</option><option>medium</option><option>low</option></select></div>`;
 if(k==="sputtering_mode") return `<div><label>${labels[k]}</label><input data-rk="${k}" value="${v}" placeholder="DC / RF / pulsed DC / HiPIMS"></div>`;
 if(k==="method_detail") return `<div><label>${labels[k]}</label><input data-rk="${k}" value="${v}" placeholder="reactive co-sputtering; pulsed DC"></div>`;
 return `<div><label>${labels[k]}</label><input data-rk="${k}" value="${v}" inputmode="decimal" placeholder="数值或范围，如 300–350"></div>`;
}
function addRecipe(data={}){
 const card=document.createElement("div"); card.className="recipe"; card.style="border:1px solid #cfd7e5;border-radius:8px;padding:12px;margin:12px 0;background:#fbfcff";
 card.innerHTML=`<div style="display:flex;align-items:center;gap:10px"><b>工艺 <span class="rnum"></span></b><span class="grow" style="flex:1"></span><button class="secondary" onclick="this.closest('.recipe').remove();renumber()">删除本工艺</button></div>
 <div class="row">${fieldHtml("method_detail",data.method_detail)}${fieldHtml("sputtering_mode",data.sputtering_mode)}${fieldHtml("equipment_model",data.equipment_model)}</div>
 <h4>靶材（属于本工艺）</h4><table class="targets"><thead><tr><th>#</th><th>材料</th><th>功率 W</th><th>功率语义</th><th>统计口径</th><th>直径 mm</th><th>模式</th><th>证据原文</th></tr></thead><tbody></tbody></table>
 <button class="secondary" onclick="addTarget(this.closest('.recipe'))">+ 添加靶材</button>
 <div class="row">${fieldHtml("working_pressure_mbar",data.working_pressure_mbar)}${fieldHtml("base_pressure_mbar",data.base_pressure_mbar)}${fieldHtml("substrate_temperature_c",data.substrate_temperature_c)}</div>
 <div class="row">${fieldHtml("n2_flow_sccm",data.n2_flow_sccm)}${fieldHtml("ar_flow_sccm",data.ar_flow_sccm)}${fieldHtml("alscn_thickness_nm",data.alscn_thickness_nm)}</div>
 <div class="row">${fieldHtml("deposition_time_min",data.deposition_time_min)}${fieldHtml("target_substrate_distance_mm",data.target_substrate_distance_mm)}${fieldHtml("confidence",data.confidence)}</div>
 <label>本工艺证据</label><textarea data-rk="evidence" placeholder="本工艺的原文证据、层/步骤归属">${esc(data.evidence??"")}</textarea>`;
 document.getElementById("recipes").appendChild(card);
 const sel=card.querySelector('select[data-rk="confidence"]'); if(sel) sel.value=data.confidence??"";
 (data.targets&&data.targets.length?data.targets:[{}]).forEach(t=>addTarget(card,t));
 renumber();
}
function addTarget(card,data={}){
 const tbody=card.querySelector("tbody"); const tr=document.createElement("tr");
 tr.innerHTML=`<td>${tbody.querySelectorAll("tr").length+1}</td>`+targetFields.map(k=>`<td><input data-tk="${k}" value="${esc(data[k]??"")}" placeholder="${k}"></td>`).join("")+`<td><button class="secondary" onclick="this.closest('tr').remove()">×</button></td>`;
 tbody.appendChild(tr);
}
function legacyRecipes(d){
 if(Array.isArray(d?.recipes)) return d.recipes;
 const rec={}; for(const k of recipeFields) if(d?.[k]!==undefined) rec[k]=d[k];
 if(Array.isArray(d?.targets)) rec.targets=d.targets;
 return (Object.keys(rec).length?[rec]:[{}]);
}
async function loadCurrent(){
 const it=items[idx]; current=it; const annotator=currentName();
 document.getElementById("meta").innerHTML=`<h2 style="margin:0">${esc(it.test_id)} · ${esc(it.title_guess)}</h2><div class="muted">${esc(it.doi)} · ${esc(it.role)} · ${esc(it.folder)} · <a href="/download/${encodeURIComponent(it.test_id)}">下载 PDF</a></div>`;
 document.getElementById("pdf").src=`/pdf/${encodeURIComponent(it.test_id)}`;
 document.getElementById("text").textContent="加载中..."; fetch(`/text/${encodeURIComponent(it.test_id)}`).then(r=>r.text()).then(t=>document.getElementById("text").textContent=t);
 for(const k of ["relevance","has_recipe","recipe_count","notes","evidence"]){ const el=document.getElementById(k); if(el) el.value=""; }
 document.getElementById("recipes").innerHTML="";
 if(!annotator){ addRecipe(); document.getElementById("saved").textContent="请先在左侧填写姓名"; return; }
 const data=await (await fetch(`/api/annotation?test_id=${encodeURIComponent(it.test_id)}&annotator=${encodeURIComponent(annotator)}`)).json();
 for(const k of ["relevance","has_recipe","recipe_count","notes","evidence"]){ const el=document.getElementById(k); if(el) el.value=data?.[k]??""; }
 for(const r of legacyRecipes(data)) addRecipe(r);
 document.getElementById("saved").textContent=data?._saved_at?`已保存：${data._saved_at}`:"未保存";
}
function collect(){
 const d={}; for(const k of ["relevance","has_recipe","recipe_count","notes","evidence"]){ const el=document.getElementById(k); if(el) d[k]=el.value; }
 d.recipes=[...document.querySelectorAll(".recipe")].map(card=>{
  const r={}; card.querySelectorAll("[data-rk]").forEach(el=>r[el.dataset.rk]=el.value);
  r.targets=[...card.querySelectorAll("tbody tr")].map(tr=>{const o={};tr.querySelectorAll("input[data-tk]").forEach(i=>o[i.dataset.tk]=i.value);return o;}).filter(o=>Object.values(o).some(v=>String(v).trim()));
  return r;
 }).filter(r=>Object.values(r).some(v=>Array.isArray(v)?v.length:false)||["method_detail","sputtering_mode","equipment_model","working_pressure_mbar","base_pressure_mbar","substrate_temperature_c","n2_flow_sccm","ar_flow_sccm","alscn_thickness_nm","deposition_time_min","target_substrate_distance_mm","evidence"].some(k=>String(r[k]??"").trim()));
 return d;
}
async function save(){
 const annotator=currentName(); if(!annotator){ alert("请先在左侧填写姓名"); return; } const d=collect();
 const r=await fetch("/api/save",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({test_id:items[idx].test_id,annotator,data:d})});
 const j=await r.json(); document.getElementById("saved").textContent=r.ok?`已保存：${j.created_at}`:`错误：${j.error}`;
 if(r.ok){ const counts=await (await fetch("/api/status")).json(); refreshProgress(counts); }
}
boot();
</script>
</body></html>"""


def serve_pdf(handler: BaseHTTPRequestHandler, item: dict) -> None:
    path = resolve_path(item)
    if not path.exists():
        handler.send_error(404, "PDF not found")
        return
    data = path.read_bytes()
    size = len(data)
    range_header = handler.headers.get("Range")
    if range_header:
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else size - 1
            end = min(end, size - 1)
            chunk = data[start:end + 1]
            handler.send_response(206)
            handler.send_header("Content-Type", "application/pdf")
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            handler.send_header("Content-Length", str(len(chunk)))
            handler.end_headers()
            handler.wfile.write(chunk)
            return
    handler.send_response(200)
    handler.send_header("Content-Type", "application/pdf")
    handler.send_header("Content-Length", str(size))
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    manifest: list[dict] = []
    by_id: dict[str, dict] = {}

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/manifest":
            self.send_json([{k: v for k, v in item.items() if k not in {"path"}} for item in self.manifest])
            return
        if parsed.path == "/api/status":
            self.send_json(annotation_counts())
            return
        if parsed.path == "/api/annotation":
            query = parse_qs(parsed.query)
            data = latest_annotation(query.get("test_id", [""])[0], query.get("annotator", [""])[0])
            self.send_json(data or {})
            return
        if parsed.path == "/export.csv":
            output = io.StringIO()
            fields = ["test_id", "annotator", "saved_at", "relevance", "has_recipe", "recipe_count",
                      "recipes_json", "evidence", "notes"]
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for item in self.manifest:
                for annotator in annotators():
                    data = latest_annotation(item["test_id"], annotator)
                    if not data:
                        continue
                    row = {key: data.get(key, "") for key in fields}
                    row.update({"test_id": item["test_id"], "annotator": annotator, "saved_at": data.get("_saved_at", "")})
                    row["recipes_json"] = json.dumps(data.get("recipes", []), ensure_ascii=False)
                    writer.writerow(row)
            body = ("\ufeff" + output.getvalue()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=annotations.csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/annotators":
            self.send_json(annotators())
            return
        if parsed.path == "/api/compare":
            query = parse_qs(parsed.query)
            names = annotators()
            left = query.get("left", [names[0] if names else ""])[0]
            right = query.get("right", [names[1] if len(names) > 1 else ""])[0]
            self.send_json(compare(left, right))
            return
        if parsed.path == "/compare":
            data = r"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<body style="font:14px Segoe UI,Arial;padding:20px">
<h2>标注者差异</h2>
<p><select id="left"></select> vs <select id="right"></select> <button onclick="load()">对比</button></p>
<table border="1" cellpadding="6" style="border-collapse:collapse"><thead><tr><th>ID</th><th>DOI</th><th id="hleft">左</th><th id="hright">右</th><th>差异</th></tr></thead><tbody id="rows"></tbody></table>
<script>
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function init(){
 const names=await (await fetch("/api/annotators")).json();
 if(names.length<2){document.body.insertAdjacentHTML("beforeend","<p>至少需要两位标注者提交后才能对比。</p>");return;}
 for(const id of ["left","right"]){const sel=document.getElementById(id);sel.innerHTML=names.map(n=>`<option>${esc(n)}</option>`).join("");}
 document.getElementById("right").selectedIndex=1; load();
}
async function load(){
 const left=document.getElementById("left").value, right=document.getElementById("right").value;
 document.getElementById("hleft").textContent=left; document.getElementById("hright").textContent=right;
 const rows=await (await fetch(`/api/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`)).json();
 document.getElementById("rows").innerHTML=rows.map(r=>`<tr><td>${esc(r.test_id)}</td><td>${esc(r.doi)}</td><td>${r.has_left?"✓":""}</td><td>${r.has_right?"✓":""}</td><td>${esc(r.diffs.join(", "))}</td></tr>`).join("");
}
init();
</script></body></html>"""
            body = data.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if parsed.path.startswith("/download/"):
            test_id = parsed.path.split("/download/", 1)[1]
            item = self.by_id.get(test_id)
            if not item:
                self.send_error(404)
                return
            path = resolve_path(item)
            if not path.exists():
                self.send_error(404)
                return
            data = path.read_bytes()
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{test_id}_{path.stem}")[:120]
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'attachment; filename="{safe}.pdf"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path.startswith("/pdf/"):
            test_id = parsed.path.split("/pdf/", 1)[1]
            item = self.by_id.get(test_id)
            if not item:
                self.send_error(404)
                return
            serve_pdf(self, item)
            return
        if parsed.path.startswith("/text/"):
            test_id = parsed.path.split("/text/", 1)[1]
            item = self.by_id.get(test_id)
            if not item:
                self.send_error(404)
                return
            body = text_for(item).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/plain; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):
        if self.path != "/api/save":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            test_id = str(payload.get("test_id", ""))
            annotator = str(payload.get("annotator", ""))
            if test_id not in self.by_id:
                raise ValueError("unknown test_id")
            result = save_annotation(test_id, annotator, payload.get("data") or {})
            self.send_json(result)
        except Exception as error:
            self.send_json({"error": str(error)}, status=400)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--manifest", default="data/annotation/testset_manifest.csv")
    parser.add_argument("--root", default="data/annotation/papers")
    args = parser.parse_args()
    global MANIFEST, ROOT
    MANIFEST = Path(args.manifest)
    ROOT = Path(args.root)
    init_db()
    Handler.manifest = load_manifest(MANIFEST)
    Handler.by_id = {item["test_id"]: item for item in Handler.manifest}
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"annotation app: http://127.0.0.1:{args.port}/", flush=True)
    lan_ip = socket.gethostbyname(socket.gethostname())
    print(f"LAN: http://{lan_ip}:{args.port}/", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
