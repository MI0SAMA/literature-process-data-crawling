# AlScN-PVD 文献数据库：阶段 2–7
> GitHub: https://github.com/MI0SAMA/literature-process-data-crawling

> **审计状态（2026-10-30）**：当前 `corpus.sqlite3` 中全部 71 条 recipe 已隔离为 `needs_review`，不能直接用于工程决策。修复与未修复项见 [`docs/AUDIT_REMEDIATION.md`](docs/AUDIT_REMEDIATION.md)。

本仓库实现任务书中的**阶段 2 文献发现**、**阶段 3 全文获取**、**阶段 4 工艺抽取**、
**阶段 5 标准化/校验**、**阶段 6 Excel 导出**和**阶段 7 基础指标**。
阶段 1 的 [`alscn_pvd_schema.json`](config/alscn_pvd_schema.json) 和 Excel 字段定义未做修改。

当前语料：**86 篇唯一合法全文，71 篇定位到方法章节，71 条 recipe，13 条性能记录，340 条逐字段证据**。
阶段 8 的在线审核前端、每日调度和性能测试条件完整抽取仍未做；性能值已做基础抽取（见文末边界）。

## 已实现

| 模块 | 文件 | 说明 |
|---|---|---|
| 查询配置 | `config/search_queries.yaml` | 14 条默认查询，覆盖 AlScN、ScAlN、Sc-doped AlN、Al1-xScxN、ScxAl1-xN、scandium aluminum nitride 及 magnetron / reactive / co-sputter / RF / pulsed DC 等模式 |
| OpenAlex adapter | `app/clients/openalex.py` | 检索、分页、摘要倒排索引还原、OA/PMCID 元数据、作者/DOI/来源解析 |
| Crossref adapter | `app/clients/crossref.py` | `query.bibliographic`、分页、JATS 摘要清洗、component/dataset 过滤 |
| HTTP 层 | `app/http.py` | 限流、超时、指数退避、429 `Retry-After`、错误分类、SQLite 响应缓存、离线重放、API key/代理脱敏 |
| 去重 | `app/discovery/dedup.py` | DOI 标准化后精确去重；无 DOI 用标题+第一作者+年份；模糊匹配标记审核；不确定配对进审核队列 |
| 初筛 | `app/discovery/screening.py` | `likely_AlScN_PVD` / `unclear` / `not_relevant`；电极-only、综述、非 PVD、PLD 分别标记 |
| 发现流程 | `app/discovery/pipeline.py` | 执行 QueryPlan、记录查询、去重、初筛、落库、导出 |
| 全文获取 | `app/acquisition/service.py` | DOI→JATS/HTML/PDF/SI；仅在 OA 元数据允许时访问 |
| JATS/HTML/PDF | `app/acquisition/{jats,html,pdf}.py` | 章节、表、参考文献、补充材料；PDF 可选 GROBID，无服务时 pypdf fallback |
| 章节定位 | `app/acquisition/sections.py` | Methods/Experimental/Deposition/Fabrication/Growth/Sputtering/Supplementary Methods；摘要/结论/参考文献排除 |
| arXiv/PMC 批量 | `tools/harvest_arxiv.py`、`tools/harvest_pmc.py` | 合法 OA 全文批量获取；记录 preprint/published 版本 |
| 工艺抽取 | `app/extraction/` | 规则抽取 recipe/target/evidence；单位换算、科学计数法、证据片段 |
| 标准化/校验 | `app/normalize/validator.py` | 范围、单位、null/0、证据缺失检查；review_queue |
| Excel 导出 | `app/export/excel.py` | 六工作表、靶材1/2 宽表 + 靶材长表、证据表、字段字典、原表头映射 |
| 指标 | `app/evaluation/metrics.py` | 字段覆盖、证据率、抽取/获取状态、审核项 |
| 审核页 | `app/review/html.py` | 静态字段+证据并排审核页 |
| 数据库 | `app/storage.py` | Phase 2/3/4/5 表：检索、文档、章节、recipe、target、evidence、review_queue |
| CLI | `app/cli.py` | `search`、`ingest`、`extract`、`extract-batch`、`validate`、`export`、`metrics`、`review-html`、`review-set`、`review-field`、`review-resolve` 等 |
| 测试 | `tests/` | **185 个 unittest** |

## 快速开始

环境：Python 3.11+；当前机器为 Python 3.13。依赖：`PyYAML`、`requests`、`lxml`、
`pypdf`；测试 PDF 生成用 `reportlab`。

```powershell
# 1. 发现/检索
python -m app.cli search --max-results 100 --db data/structured/live14.sqlite3

# 2. 单篇全文获取
python -m app.cli ingest --doi 10.3390/biomimetics11080519 `
  --db data/structured/phase3_acceptance.sqlite3 --raw-dir data/raw

# 3. 批量抽取/校验
python -m app.cli extract-batch --db data/structured/corpus.sqlite3 --force
python -m app.cli validate --db data/structured/corpus.sqlite3

# 4. 六表 Excel
# 默认 formal approved（只导出 verified recipe/measurement）；审核包用 --mode review
python -m app.cli export --db data/structured/corpus.sqlite3 `
  --mode review --output data/structured/AlScN_PVD_literature_50.xlsx

# 5. 指标与静态审核页
python -m app.cli metrics --db data/structured/corpus.sqlite3
python -m app.cli review-html --db data/structured/corpus.sqlite3 --output docs/review.html
# 先逐字段核验，再整体 verified；缺字段审查会被拒绝
python -m app.cli review-field --db data/structured/corpus.sqlite3 --record-id doi:...:R1 --field-key substrate_temperature_c --decision verified --note "checked 300 C AlBScN"
python -m app.cli review-set --db data/structured/corpus.sqlite3 --record-id doi:...:R1 --status verified --note "all fields checked"
```

## 测试与验收

```powershell
# 全部单元/集成测试
python -m unittest discover -s tests -p "test_*.py" -v

# 阶段 2 离线重放验收
python tools/acceptance_replay.py

# 阶段 3 十篇 OA 全文 live 验收
python tools/phase3_acceptance.py

# 单篇全文获取
python -m app.cli ingest --doi 10.3390/biomimetics11080519 `
  --db data/structured/phase3_acceptance.sqlite3 --raw-dir data/raw
```

最近一次结果（2026-10-29）：

- 单元/集成测试：**185/185 通过**。
- 阶段 2 live 14 查询 × 2 库：28/28 成功，找回 **19/20**（Recall 0.95），DOI 重复 0。
- 阶段 3 live 10 篇 OA 全文：**10/10 全文，9/10 方法章节**（目标 ≥9）。
- 阶段 4–7 语料：**86 篇唯一合法全文，71 篇方法章节，71 条 recipe，13 条性能记录，340 条证据**。
- Excel：71 条工艺记录、40 条靶材记录、13 条性能记录、340 条证据记录，六工作表齐全。
- 阶段 2/3 证据：[`docs/Phase2_Acceptance_Report.md`](docs/Phase2_Acceptance_Report.md)、
  [`docs/Phase3_Acceptance_Report.md`](docs/Phase3_Acceptance_Report.md)
- 阶段 4–7 报告：[`docs/Phases4-7_Implementation_Report.md`](docs/Phases4-7_Implementation_Report.md)
- 审计修复：[`docs/AUDIT_REMEDIATION.md`](docs/AUDIT_REMEDIATION.md)
- 2026-11-02 审计修复：[`docs/Audit_20261102_Remediation.md`](docs/Audit_20261102_Remediation.md)
- 语料清单：[`docs/corpus_50_manifest.csv`](docs/corpus_50_manifest.csv)、
  [`docs/corpus_50_summary.json`](docs/corpus_50_summary.json)
- 指标：[`docs/extraction_metrics.md`](docs/extraction_metrics.md)
- 审核页：[`docs/review.html`](docs/review.html)
- 测试记录：[`docs/Phase2_Test_Record.md`](docs/Phase2_Test_Record.md)、
  [`docs/Phase3_Test_Record.md`](docs/Phase3_Test_Record.md)

## 当前网络与代理

当前环境可直连 OpenAlex/Crossref/arXiv/Europe PMC；出版商页面仍可能 403，此时只记录
`forbidden`，不绕过。若需要代理，凭据只从 `ALSCN_PROXY` 环境变量读取，**不写入代码、
配置或日志**：

```powershell
$env:ALSCN_PROXY='http://<user>:<password>@<proxy-host>:<port>'
python -m app.cli search --max-results 100 `
  --db data/structured/live14.sqlite3 `
  --out data/structured/live14
```

- 网络失败时，CLI 会明确写入 `query_executions.status=error` 或
  `documents.access_status=forbidden/error`，**不会返回看似成功的空结果**；退出码为 1。
- `requests` 负责 HTTPS CONNECT 的 Basic 代理认证；`--proxy` 也可显式传入，
  但建议用环境变量避免进入 shell history。
- 已实现 `--offline` 与 `cache-import`：把录制 JSON 放入 SQLite 缓存后，可在无网络下
  重复执行同一查询计划。

无网络演示（已实测通过）：

```powershell
python tools/build_offline_fixture.py
python -m app.cli cache-import `
  --manifest tests/fixtures/live/cache_manifest.json `
  --db data/structured/offline_demo.sqlite3

python -m app.cli search `
  --query "AlScN sputtering" `
  --databases openalex `
  --max-results 25 --page-size 25 --offline `
  --db data/structured/offline_demo.sqlite3 `
  --out data/structured/offline_demo
```

切换网络后：

```powershell
python -m app.cli search --mailto "you@example.org" --db data/structured/alscn_pvd.sqlite3
python -m app.cli gold-eval --db data/structured/alscn_pvd.sqlite3
python -m app.cli ingest --doi 10.3390/biomimetics11080519 `
  --db data/structured/phase3_acceptance.sqlite3 --raw-dir data/raw
```

## 数据与合规边界

- 阶段 2 只使用公开 API 元数据；阶段 3 只下载 OpenAlex 标记为 OA 的全文/PDF，
  不绕过付费墙、登录、验证码或网站访问限制。
- 不把内部 147 片样品的工艺数值发送给任何外部模型；本仓库的测试、fixture 和
  验收数据都不含内部样品值。
- `null` 表示未报告，不是 0；初筛阶段不生成任何实验参数组合。
- 只有电极使用 PVD 的文章不会被提升为 `likely_AlScN_PVD`，而是标记
  `possible_electrode_only` 并进入 `unclear`。
- 综述只用于发现参考文献，初筛标记为 `not_relevant` + `review_article`，
  不把综述转述值当原始数据。

## 目录

```text
app/
  cli.py                 # 命令入口
  config.py              # YAML 配置加载
  http.py                # 限流/重试/缓存/脱敏
  identifiers.py         # DOI/标题标准化
  models.py              # 数据类
  reporting.py           # CSV/JSON/金标准
  storage.py             # SQLite
  clients/               # OpenAlex / Crossref
  discovery/             # query_plan / dedup / screening / llm / pipeline
  acquisition/           # DOI resolve / fetch / JATS / HTML / PDF / sections / GROBID / arXiv
  extraction/            # rule extractor / units / pipeline
  normalize/             # validator / review queue
  export/                # six-sheet Excel
  evaluation/            # metrics
  review/                # static evidence review page
config/
  search_queries.yaml
  alscn_pvd_schema.json  # 阶段 1 权威 schema，未改动
tests/
  fixtures/live/         # 录制 OpenAlex 元数据（公开）
  gold_standard/         # 20 篇金标准 + 9 条初筛标签
tools/
  acceptance_replay.py
  phase3_acceptance.py
  harvest_arxiv.py
  harvest_pmc.py
  reparse_pdf_corpus.py
docs/
```

## 数据接口

- `papers` / `documents` / `document_sections` / `document_tables`：文献与全文证据；
- `recipes` / `targets` / `samples`：工艺记录（宽表 + 长表）；
- `evidence`：逐字段原文、raw/normalized、单位、状态、章节/locator；
- `review_queue` / `recipes.review_status`：审核项与状态；
- `extraction_runs` / `acquisition_runs`：模型、parser、版本、状态、错误；
- `paper_sources.raw_json` / `dedup_reviews`：溯源与去重审核。

## 标注网页（可选）

本地双人/多人标注工具不依赖外部前端：

```powershell
python tools/annotation_app.py --host 0.0.0.0 --port 8767 --manifest docs/annotation_manifest.example.csv --root <OneDrive>/Paper
```

- 不再需要账号密码，打开页面后填写姓名；
- 每篇论文支持多个 Recipe，靶材绑定到各自 Recipe；
- PDF 预览与下载、A/B（任意两人）对比、CSV 导出；
- 具体字段定义见 [`docs/Double_Annotation_Guide.md`](docs/Double_Annotation_Guide.md)。

仓库不包含论文 PDF、SQLite、Excel 或标注数据库；这些由本地 `data/` 和 `--manifest` 提供。

已有同事的 Markdown 统计表可导入为某个标注者的记录：

```powershell
python tools/import_statistical_conditions.py --markdown statistical_conditions.md --annotator majie --apply
```


## 阶段 8 边界

已完成：批量 arXiv/PMC 获取、批量抽取、六表导出、静态审核页、基础指标。

未做：在线 Web 前端（审核提交/权限）、性能字段（Pr/2Pr/Ec/FWHM/d33/漏电）抽取、
每日增量调度、20 篇抽取金标准与字段级 Precision/Recall 回归。按任务书这些应在
核心数据链路稳定后再做；当前 71 条 recipe 仍是规则抽取基线，`new/needs_review`
记录需人工审核后才能用于工程决策。
