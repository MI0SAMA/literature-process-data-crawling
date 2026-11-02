# 抽取核心代码导读

## 0. 先说明：当前没有“AI 字段抽取”阶段

当前 `app/extraction/` 是**确定性规则抽取**，不调用 LLM。全文不会发送给任何模型；
字段来自正则匹配 + 单位换算 + 证据片段。

现有唯一的 AI 提示词在 `app/discovery/llm.py`，只用于**阶段 2 标题/摘要分类**
（`likely_AlScN_PVD / unclear / not_relevant`），不参与工艺字段抽取。

因此本文所说的“原始提取 JSON”是规则抽取器的直接输出，不是 AI 输出。
判断错误时应分成：**解析/章节定位 → 字段匹配 → 单位标准化 → 入库/导出**，
而不是“AI 输出 vs 后处理”。

## 1. 数据链路

```text
PDF/HTML/JATS
  -> parse_pdf / parse_html / parse_jats
  -> ParsedSection[] + ParsedTable[]
  -> SQLite documents / document_sections / document_tables
  -> locate_deposition_sections() 选择 methods/deposition section
  -> RuleBasedExtractor.extract()
       context = primary_section.text + 所有 table caption/text
       regex -> set_field() -> raw/normalized + evidence
  -> ExtractionPipeline 保存 recipes / targets / samples / evidence
  -> RecipeValidator 范围/证据检查 -> review_queue
  -> ExcelExporter 六表导出
```

## 2. 核心文件

| 文件 | 作用 | 关键入口 |
|---|---|---|
| `app/acquisition/pdf.py` | PDF 文本提取、标题启发式、References 分界 | `parse_pdf()` L40、`_is_heading()` L119、`_extract_pages()` L139 |
| `app/acquisition/html.py` | HTML article 容器、h1–h6 切分、abstract-only 判定、表格/SI | `parse_html()` L28、`content_scope` L123 |
| `app/acquisition/jats.py` | PMC/JATS 结构化解析、ref-list、table-wrap、supplementary | `parse_jats()` L18、`_walk_sec()` L120 |
| `app/acquisition/sections.py` | 标题分类、沉积章节打分、Methods/结论/参考文献排除 | `classify_heading()` L90、`score_deposition_section()` L117、`locate_deposition_sections()` L155 |
| `app/extraction/extractor.py` | 正则字段匹配、`set_field`、证据片段、target 组装 | `extract()` L110、`set_field()` L172、gas L273、target config L465 |
| `app/extraction/units.py` | 科学计数法、压力/功率/长度/时间/温度/流量换算 | `parse_numeric()` L25、`convert()` L42、`normalize_temperature()` L61 |
| `app/extraction/performance.py` | Pr/2Pr/Ec/d33/FWHM/应力/漏电/粗糙度基础抽取 | `extract_measurements()` L43 |
| `app/extraction/pipeline.py` | 选论文、调用抽取、写 recipe/target/evidence/run、写 JSON | `ExtractionPipeline.extract_paper()` |
| `app/normalize/validator.py` | 数值范围、证据缺失、Ar=0、base/working 关系检查 | `RecipeValidator.validate()` L25、`validate_all()` L82 |
| `app/export/excel.py` | 六工作表、宽表/长表、证据表、字段字典 | `ExcelExporter.export()` |
| `app/discovery/llm.py` | 唯一的 AI 提示词：标题/摘要初筛 | `LLMClassifier.classify()` L51、`build_prompt()` L96 |

## 3. PDF 解析核心

```python
# app/acquisition/pdf.py:40
def parse_pdf(pdf_bytes: bytes) -> ParsedDocument:
    pages = _extract_pages(pdf_bytes)
    ...
    for page_number, page_text in enumerate(pages, start=1):
        for line in page_text.splitlines():
            if _REFERENCE_RE.match(line.strip(" .")):
                flush()
                in_references = True       # 进入 References 后不再识别新章节
                ...
            if _is_heading(line):
                flush()
                current_heading = line
                ...
```

标题判定不是“有方法关键字就切”：

```python
# app/acquisition/pdf.py:119
def _is_heading(line: str) -> bool:
    stripped = line.strip(" .")
    if re.search(r"\bet\s+al\.|\bdoi\s*:|\bvol\.|\bpp\.|University|Institute|Department", stripped, re.I):
        return False
    if _HEADING_RE.match(stripped):
        return True
    if not _HEADING_KEYWORD_RE.search(stripped):
        return False
    numbered = _NUMBERED_RE.match(stripped)
    if numbered and stripped[numbered.end():].lstrip()[:1].islower():
        return False
    uppercase_ratio = ...
    return uppercase_ratio >= 0.6 or bool(numbered)
```

局限：PDF 的标题/正文边界依赖文本行；表格不会结构化（`tables=[]`），因此 PDF
论文的工艺表参数往往缺失。

## 4. HTML 解析核心

```python
# app/acquisition/html.py:28
root = html.document_fromstring(html_bytes)
for element in root.xpath("//script|//style|//nav|//header|//footer|//aside|//form|//noscript|//svg"):
    _drop(element)

# 检测挑战页
if any(marker in lowered for marker in ("checking your browser", "just a moment", "recaptcha", ...)):
    raise HtmlBlockedError(...)

container = _article_container(root)   # article > main > role=main > article-body
headings = [e for e in container.iter() if _is_heading(e)]
```

全文/摘要判定：

```python
# app/acquisition/html.py:123
body_text_length = sum(len(s.text or "") for s in sections if s.section_type not in {"abstract","references"})
structural_sections = [
    s for s in sections
    if s.section_type in {"introduction","methods","results","conclusion","supplementary_methods"}
    and len(s.text or "") >= 200
    and not any(term in (s.heading or "").casefold() for term in ("title","search","fingerprint","cite","access","project","funding"))
]
content_scope = "fulltext" if structural_sections and body_text_length >= 200 else "abstract_only"
```

`abstract_only` 会被 `AcquisitionService` 拒绝作为主文档，避免把 arXiv abs 页当全文。

## 5. 章节定位

`app/acquisition/sections.py` 先分类标题，再打分：

- `Methods / Experimental / Deposition / Fabrication / Growth / Sputtering /
  Supplementary Methods` → `methods` / `supplementary_methods`
- `Abstract / Conclusion / Introduction / References / Acknowledgements` 不参与候选
- 打分 = 标题方法词 + 材料名 + `sputter` + 工艺词（power/pressure/sccm/
  temperature/target/thickness…）+ 数值单位

## 6. 字段匹配核心

### 6.1 构造送给抽取器的文本

```python
# app/extraction/extractor.py:149
text = primary["text"]
table_text = " ".join(
    " ".join(part for part in (table.get("caption",""), table.get("text","")) if part)
    for table in tables
)
context = f"{text} {table_text}".strip()
```

**没有 system prompt，没有 user prompt，没有模型调用。** 这段 `context` 是完整的
“送给抽取器的中间文本”；样例见
`docs/extraction_trace/10.48550_arxiv.2506.22212/03b_extractor_input.txt`。

### 6.2 `set_field`：字段写入 + 单位换算 + 证据

```python
# app/extraction/extractor.py:172
def set_field(field_key, value, raw_value, raw_unit, match_text, *, family=None, status="reported", ...):
    if field_key in data or value is None:
        return
    normalized = value
    normalized_unit = raw_unit
    if family:
        converted, target_unit = convert(float(value), raw_unit, family)
        normalized = converted
        normalized_unit = target_unit or raw_unit
        if normalized_unit != raw_unit:
            status = "derived"
            note = f"converted {raw_unit} -> {normalized_unit}"
    data[field_key] = normalized
    fields.append(ExtractedField(
        field_key=field_key,
        value=normalized,
        raw_value=raw_value,
        raw_unit=raw_unit,
        normalized_value=normalized,
        normalized_unit=normalized_unit,
        evidence_text=_sentence(context, match_text) or match_text,
        section=primary["heading"],
        locator=primary["locator"],
        status=status,
        ...
    ))
```

**关键点**：`raw_value` / `raw_unit` 和 `normalized_value` / `normalized_unit`
都在同一个 `ExtractedField` 里。如果 raw 对、normalized 错，问题在 `convert`；
如果 raw 就错，问题在正则匹配/上下文。

### 6.3 气流匹配（当前已知缺陷）

```python
# app/extraction/extractor.py:32
_GAS_RE = re.compile(
    r"\b(?P<gas>N2|N₂|Ar|O2|O₂|H2|H₂)\b[^.;]{0,40}?"
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>sccm|slm)",
    re.I,
)
```

对 `N2:Ar = 160:32 sccm`，正则要求数字后紧跟 `sccm`，所以只能匹配到 `32`，
不能把 160 和 32 拆成 N2/Ar。这是**字段匹配 bug**，不是单位换算。

### 6.4 靶材配置

```python
# app/extraction/extractor.py:465
def _target_configuration(text: str) -> str:
    lowered = text.casefold()
    if "co-sputter" in lowered or "cosputter" in lowered:
        return "co-sputtering"
    if "single" in lowered and "target" in lowered:
        return "single target"
    if "dual" in lowered and "target" in lowered:
        return "dual target"
    return "unknown"
```

`single uninterrupted vacuum cycle` + `five independent targets` 会被误判为
`single target`。

## 7. 后处理/标准化代码

```python
# app/extraction/units.py:42
def convert(value, unit, family):
    table = {"pressure": _PRESSURE, "power": _POWER, "length": _LENGTH, ...}[family]
    target_unit, factor = table[unit.casefold()]
    return value * factor, target_unit
```

- 5 kW → 5000 W
- 0.5 Pa → 0.005 mbar
- 190 nm → 190 nm
- 200 °C → 200 °C
- 100 cm → 1000 mm

## 8. 唯一的 AI 提示词（初筛，不是抽取）

`app/discovery/llm.py`：

```python
system = (
    "You classify public paper titles and abstracts for a materials-science "
    "literature database. Return only JSON with keys label, confidence, reason. "
    "label must be one of likely_AlScN_PVD, unclear, not_relevant. "
    "AlScN must be the functional layer prepared by PVD/sputtering; papers where "
    "AlScN is only an electrode are not likely. Reviews are not original experiments."
)
user = f"TITLE:\n{title[:500]}\n\nABSTRACT:\n{abstract[:4000]}\n\nClassify ..."
```

该提示词不接收 PDF/HTML 全文，也不输出 recipe 字段。

## 9. 样例追踪包

目录：`docs/extraction_trace/10.48550_arxiv.2506.22212/`

| 文件 | 内容 |
|---|---|
| `01_metadata.json` | 论文、文档、acquisition run 元数据 |
| `02_parsed_sections.json` | 解析后的全部 sections/tables（含 text、heading、locator） |
| `03a_primary_section.txt` | 被选中的 Fabrication section 原文 |
| `03b_extractor_input.txt` | 实际传给 `RuleBasedExtractor` 的 context（primary text + table text） |
| `04_raw_extraction.json` | 规则抽取器直接输出（`ExtractionResult.to_dict()`） |
| `05_postprocessed_recipes.json` | 入库后的 recipe/target JSON |
| `06_evidence.json` | 入库后的逐字段 evidence |
| `07_error_analysis.md` | 逐项判断错误发生阶段 |
| `08_current_after_fix.json` | 2026-10-30 修复后的当前 recipe/evidence |

注意：该目录是 **AUDIT_REPORT.md 修复前快照**，`04_raw_extraction.json` 保留当时的错误值用于归因；修复后的当前值见 `08_current_after_fix.json` 或 `docs/AUDIT_REMEDIATION.md`。

同一篇论文的完整核心代码压缩包：`docs/extraction_core_code.zip`。
