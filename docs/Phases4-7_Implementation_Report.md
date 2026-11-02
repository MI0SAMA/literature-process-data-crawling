# 阶段 4–7 实现报告：抽取、归一化、审核队列与 Excel 导出

- 日期：2026-10-29
- 数据库：`data/structured/corpus.sqlite3`
- 语料：86 篇唯一合法全文（71 篇定位到沉积方法章节）
- Excel：`data/structured/AlScN_PVD_literature_50.xlsx`
- 指标：`docs/extraction_metrics.json` / `docs/extraction_metrics.md`
- 审核页：`docs/review.html`

## 1. 完成范围

| 阶段 | 完成内容 | 状态 |
|---|---|---|
| 4 AI 工艺组合识别与字段抽取 | 规则抽取器 + 证据表 + recipes/targets/samples/evidence/extraction_runs | 基线完成；LLM 接口未接（无 API key） |
| 5 标准化、质量校验、人工审核 | 单位换算、范围校验、null/0 区分、review_queue、静态审核页 | 完成基础版 |
| 6 数据库与 Excel 导出 | 六工作表导出、宽表靶材1/2、长表靶材明细、证据表 | 完成 |
| 7 质量评测 | 字段覆盖、证据率、抽取状态、审核项统计 | 完成基础指标；未做 20 篇抽取金标准精确率 |
| 8 前端/批量/性能 | 静态审核页、批量抓取/抽取脚本、Pr/Ec/d33/FWHM/应力/漏电基础抽取 | 轻量版；在线前端/审核提交、每日调度未做 |

## 2. 语料

| 指标 | 结果 |
|---|---:|
| 目标 | ≥50 篇 |
| 唯一合法全文 | **86 篇** |
| 定位到方法章节 | **71 篇** |
| 部分全文（partial） | 15 篇 |
| document 记录 | 136 |
| sections | 795 |
| tables | 45 |
| 唯一 DOI | 86 |
| 重复 DOI | 0 |

来源：arXiv（preprint）、Europe PMC JATS、OpenAlex 标记的 OA 入口。未绕过付费墙/
登录/验证码；403、挑战页、404 均记录。语料清单见
`docs/corpus_50_manifest.csv`，汇总见 `docs/corpus_50_summary.json`。

## 3. 阶段 4：抽取

- 输入：`deposition_methods` / `supplementary_methods` / `methods` section + tables。
- 输出：每篇默认 1 条 recipe（`record_scope=actual_recipe`），不生成笛卡尔积组合。
- 字段：沉积方法、溅射模式、靶材配置、靶材材料/Sc 含量/功率、Ar/N2 流量、
  工作/本底气压、沉积温度、膜厚、沉积时间、靶基距离、衬底/电极、设备型号。
- 每个非空数值字段写入 `evidence`：原文片段、章节、locator、raw/normalized 值、
  raw/normalized 单位、evidence_status、derivation_note。
- 缺失字段保持 null；不猜测、不补零。
- 71 recipes、13 条 measurement、340 条 evidence；已抽取数值字段的证据率为 100%。

字段覆盖：

| 字段 | 有值/71 | 覆盖率 |
|---|---:|---:|
| AlScN 厚度 | 14 | 19.7% |
| 沉积温度 | 24 | 33.8% |
| 薄膜 Sc 含量 | 34 | 47.9% |
| 靶材 Sc 含量 | 7 | 9.9% |
| 靶材1功率 | 24 | 33.8% |
| 本底气压 | 16 | 22.5% |
| Ar 流量 | 14 | 19.7% |
| 工作气压 | 6 | 8.5% |
| N2 流量 | 19 | 26.8% |
| 沉积时间 | 0 | 0% |
| 靶基距离 | 5 | 7.0% |
| 靶材2功率 | 0 | 0% |

覆盖偏低的原因：大量 PDF 只有正文、没有结构化表格；部分论文把工艺参数放在
图中/补充材料；规则抽取只从方法章节和已解析表格取值。**不把缺失值补成 0。**

## 4. 阶段 5：归一化与审核

- 单位换算：Pa/mbar/Torr/mTorr/bar、W/kW/mW、nm/µm/mm/cm、s/min/h、
  Hz/kHz/MHz、sccm/slm、°C/K/F。
- 科学计数法：`1.0 × 10−6 Torr`、`10−6` 可解析。
- 类型/范围校验：功率、气压、温度、流量、厚度、Sc 含量、时间、距离。
- 校验结果：71 recipes，11 个新问题，143 个 open review items（含抽取阶段标记）。
- 审核页：`docs/review.html`，字段与原文证据并排，支持搜索；只读。
- `review_status` 保留 `new/needs_review/verified/rejected`；`validate` 会把有问题的
  recipe 标为 `needs_review`。

## 5. 阶段 6：Excel 导出

`python -m app.cli export --db data/structured/corpus.sqlite3 --output data/structured/AlScN_PVD_literature_50.xlsx`

六工作表：

| 工作表 | 行数（含表头） |
|---|---:|
| 工艺记录 | 47 |
| 靶材明细 | 40 |
| 性能记录 | 14（13 条 measurement + 表头） |
| 证据记录 | 341 |
| 字段字典 | 106 |
| 原表头映射 | 36 |

- 工艺记录使用 `target1_*` / `target2_*` 宽表；靶材明细按 `target_index` 长表。
- 单靶时第二靶留空，不写 0。
- 证据记录逐字段保留 raw/normalized/单位/状态/原文/章节。
- 字段字典和原表头映射从阶段 1 schema 自动生成，未修改字段规范。

## 6. 阶段 7：指标

```json
{
  "papers_total": 96,
  "recipes": 71,
  "measurements": 13,
  "evidence": 340,
  "evidence_rate": 1.0,
  "documents": 136,
  "sections": 795,
  "tables": 45,
  "review_items": 143
}
```

- `papers_total` 含 96 条已入库论文元数据；其中 86 条有合法全文。
- `evidence_rate` 是“已抽取数值字段中有 evidence 的比例”，不是全字段覆盖率。
- 尚未建立 20 篇人工抽取金标准，因此**不能声称 95% 字段精确率**；需要人工抽样
  评估后才能进入“高置信生产”状态。

## 7. 阶段 8 边界

已完成：

- 静态审核页 `docs/review.html`；
- 批量抓取脚本 `tools/batch_acquire.py`、`tools/harvest_arxiv.py`、`tools/harvest_pmc.py`；
- 批量抽取 `python -m app.cli extract-batch`。

未做（按任务书“稳定后再做”）：

- 在线 Web 前端（列表筛选、审核提交、用户权限）；
- 性能字段已做基础版（Pr/2Pr/Ec/d33/FWHM/应力/漏电），但未做测试条件/频率/波形完整抽取；
- 每日增量调度/任务队列；
- 20 篇抽取金标准与字段级 Precision/Recall 回归。

## 8. 复现

```powershell
# 50 篇语料
python tools/harvest_arxiv.py
python tools/harvest_pmc.py
python tools/reparse_pdf_corpus.py

# 抽取/校验/导出/指标/审核页
python -m app.cli extract-batch --db data/structured/corpus.sqlite3 --force
python -m app.cli validate --db data/structured/corpus.sqlite3
python -m app.cli export --db data/structured/corpus.sqlite3 `
  --output data/structured/AlScN_PVD_literature_50.xlsx
python -m app.cli metrics --db data/structured/corpus.sqlite3
python -m app.cli review-html --db data/structured/corpus.sqlite3 --output docs/review.html
```

## 9. 结论

阶段 4–7 的**数据链路**已完成：86 篇合法全文 → 71 条配方 + 13 条性能记录 → 340 条逐字段证据 →
六表 Excel。阶段 5 审核与阶段 7 精确率评测仍是“基础版”，不能把规则抽取结果
当作已人工确认的最终数据；所有 `new/needs_review` 记录应经过审核后再用于工程决策。
