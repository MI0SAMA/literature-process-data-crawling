# 阶段 3 测试记录

- 日期：2026-10-29
- 环境：Windows；Python 3.13.14；SQLite 3；标准库 `unittest`
- 测试命令：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

- 最近一次结果：

```text
Ran 133 tests in 3.349s

OK
```

退出码：0。

## 1. 测试分布

| 测试模块 | 数量 | 覆盖内容 |
|---|---:|---|
| `test_identifiers` | 11 | DOI/标题标准化 |
| `test_dedup` | 10 | DOI/无 DOI/模糊去重、审核队列 |
| `test_screening` | 17 | 规则初筛、AI 组合、电极-only/PLD/综述 |
| `test_query_plan` | 8 | QueryPlan、查询覆盖、参数校验 |
| `test_http` | 9 | 重试、429、缓存、离线、脱敏 |
| `test_clients` | 9 | OpenAlex/Crossref 解析 |
| `test_storage` | 9 | Phase 2 表 + Phase 3 documents/sections/tables/acquisition_runs |
| `test_pipeline` | 5 | 发现流程、失败/partial、幂等 |
| `test_reporting` | 4 | CSV/JSON、金标准 |
| `test_llm` | 5 | 可选 LLM 分类器 |
| `test_cli` | 5 | CLI `--help`/`init-db`/`report`/`cache`/`ingest --help` |
| `test_sections` | 14 | 标题分类、沉积打分、Methods/结论/标题排除 |
| `test_parsers` | 13 | JATS/HTML/PDF 解析、表、参考文献、abstract-only |
| `test_fetch` | 5 | 403 不重试、429 重试、max bytes、网络错误 |
| `test_grobid` | 2 | TEI 解析、非法 XML |
| `test_acquisition_service` | 7 | JATS/HTML/SI、metadata_only、fulltext_unavailable、partial、DOI 解析 |
| **合计** | **133** | |

## 2. 阶段 3 关键测试

### 2.1 解析器

- JATS：标题、abstract、body/sec、ref-list、table-wrap、supplementary-material；
- HTML：article/main 容器、h1–h6 切分、表格、references、SI 绝对 URL；
- PDF：pypdf 标题启发式、页范围、References 后停止章节识别；
- HTML abstract/landing 页 → `content_scope=abstract_only`，服务不当作全文；
- 非法 PDF/XML/HTML 抛明确错误，不返回空成功。

### 2.2 章节定位

- “Experimental / Methods / Deposition / Fabrication / Growth / Sputtering /
  Supplementary Methods” 分类；
- AlScN/ScAlN + sputter + 功率/气压/流量/温度/靶材/时间等工艺词打分；
- 摘要、结论、引言、参考文献、标题页不参与；
- Methods 分数高于 Results；无正文 Methods 时可用 Supplementary Methods；
- 参考文献中的 “Deposition of…” 不会被当成方法章节（PDF 回归测试）。

### 2.3 获取流程

- JATS 优先；
- HTML 仅 `fulltext`；abstract-only 不保存为全文；
- PDF fallback；GROBID 可选；
- SI 最多 `max_si`，ZIP/XLSX 原样保存；
- 403/404/挑战页记录 `access_status`/`error_kind`；
- 全文无方法 → `partial`；无 OA → `metadata_only`；OA 全部失败 → `fulltext_unavailable`。

## 3. live 验收

命令：

```powershell
$env:ALSCN_PROXY='http://<user>:<password>@<proxy-host>:<port>'
python tools/phase3_acceptance.py
```

结果（`docs/phase3_acceptance_evidence.json`）：

| 检查 | 结果 |
|---|---|
| 10 篇合法全文 | **10/10** |
| 沉积方法章节 | **9/10**（目标 ≥9） |
| 403/挑战页不越权 | true |
| 文档 hash/大小/URL/版本 | 16 个文件有 SHA-256 |
| 章节/表 | 138 sections / 5 tables |

状态分布：9 `ok` + 1 `partial`；文档类型：4 JATS、5 HTML（3 成功、2 403）、
4 PDF、6 SI。

## 4. 失败/边界测试

```powershell
# closed paper -> metadata_only，不发起全文请求
python -m app.cli ingest --doi 10.1109/jmems.2022.3161055 `
  --db data/structured/phase3_metadata2.sqlite3

# OA metadata 但 APS/Aalto 全部 403 -> fulltext_unavailable
python -m app.cli ingest --doi 10.1103/physrevmaterials.5.035001 `
  --db data/structured/phase3_unavailable.sqlite3
```

结果分别记录在 `docs/phase3_metadata_only.txt` 与
`docs/phase3_fulltext_unavailable.txt`；CLI 退出码为 1，未伪造成功。

## 5. 复现

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python tools/phase3_acceptance.py
```

测试不访问网络；live 验收单独运行，仅使用 `ALSCN_PROXY` 环境变量中的代理凭据。
