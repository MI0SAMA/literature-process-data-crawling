# 阶段 2 验收报告：文献发现、去重与相关性初筛

- 项目：AlScN-PVD 文献工艺数据库
- 阶段：2（阶段 3 在 live 验收后开始；当前仓库已包含阶段 3）
- 日期：2026-10-29
- 环境：Windows，Python 3.13.14，SQLite 3，stdlib `unittest`（未安装 pytest）
- 权威字段规范：`config/alscn_pvd_schema.json`（阶段 1，未改动）
- 证据文件：`docs/phase2_acceptance_evidence.json`
- live 记录：`docs/Phase2_Live_Test_Report.md`
- 测试记录：`docs/Phase2_Test_Record.md`
- 失败案例：`docs/Phase2_Failure_Cases.md`

## 1. 验收结论

| 验收项 | 目标 | 实际 | 结论 |
|---|---|---|---|
| 可执行搜索 CLI | 有 | `python -m app.cli search/report/gold-eval/...` | 通过 |
| papers / search_runs 数据模型 | 有 | SQLite 8 张表 + 索引/唯一约束 | 通过 |
| 检索日志 | 每条查询可追溯 | `search_runs` + `query_executions`，记录查询、库、年份、分页、结果数、时间、错误、缓存/重试 | 通过 |
| 候选 CSV/JSON | 有 | `candidates.csv`、`candidates.json`、`review_queue.csv`、`search_log.json` | 通过 |
| 单元测试 | 通过 | **90/90 通过** | 通过 |
| 20 篇金标准召回 | ≥ 18/20 | **live 19/20（Recall 0.95）**；录制重放 18/20 | 通过 |
| DOI 去重 | 0 重复 | **0 个重复 DOI**（live 1607 篇） | 通过 |
| 电极-only 不得直接确认 | 必须 | 规则标记 `possible_electrode_only` → `unclear`，不进入 `likely` | 通过（规则/测试） |

**结论：阶段 2 通过验收。** 2026-10-29 经认证代理完成 live 14 条查询 × 2 库，
28/28 查询成功，2800 条源记录 → 1607 篇候选，金标准找回 **19/20**，重复 DOI **0**。
录制页重放保留为离线回归证据（18/20）。详见 `docs/Phase2_Live_Test_Report.md`。

## 2. 实现模块

| 任务书要求 | 实现 |
|---|---|
| OpenAlexClient / CrossrefClient，独立分页/限流/重试/超时/错误 | `app/clients/openalex.py`、`app/clients/crossref.py`、`app/http.py` |
| 默认查询覆盖材料名与溅射模式 | `config/search_queries.yaml`，14 条查询；材料变体 6 类、方法变体 6 类 |
| QueryPlan 记录实际查询/库/年份/分页/结果/时间 | `app/discovery/query_plan.py`、`search_runs`、`query_executions` |
| DOI 标准化去重；无 DOI 标题+作者+年份；不确定进审核 | `app/identifiers.py`、`app/discovery/dedup.py`、`dedup_reviews` |
| 规则初筛 + AI 标题/摘要分类 | `app/discovery/screening.py`、`app/discovery/llm.py`；默认 heuristic，`--classifier llm` 可选 |
| 缓存、幂等、限流 | `api_cache` 表、`--offline`、`--no-cache`、`RateLimiter`、唯一约束 |
| 检索日志和候选 CSV/JSON | `app/reporting.py`；`data/structured/<run_id>/` |
| 单元测试及测试记录 | `tests/` 90 个测试；`docs/Phase2_Test_Record.md` |

## 3. 检索策略与审计

默认 QueryPlan 含 14 条查询，核心包括：

- `AlScN sputtering`
- `AlScN thin films`
- `AlScN`（宽召回）
- `ScAlN sputtering`
- `scandium aluminum nitride sputtering`
- `Sc-doped AlN sputtering`
- `Al1-xScxN sputtering`
- `ScxAl1-xN sputtering`
- `AlScN reactive sputtering`
- `AlScN magnetron sputtering`
- `AlScN co-sputtering`
- `AlScN pulsed DC sputtering`
- `AlScN RF sputtering`
- `scandium aluminum nitride magnetron sputtering`

每条查询 × 每个数据库单独记录：

- 实际查询字符串、数据库、起止年份、请求上限、页大小；
- 实际抓取页数、原始结果数、唯一 source_id 数；
- 开始/结束时间、状态、错误类型和错误消息；
- 请求数、缓存命中数、重试数。

时间范围默认 2005 至今；可在 CLI 用 `--from-year` / `--to-year` 覆盖。分页按
`max_results` 停止，OpenAlex 用 `page`，Crossref 用 `offset`。

## 4. 去重与初筛

### 4.1 DOI 去重

- DOI 统一做 URL/`doi:`/百分号编码/大小写/尾部标点标准化。
- 同 DOI 跨源记录直接合并，保留全部 `paper_sources`。
- 无 DOI：标准化标题 + 第一作者 family + 年份完全一致时合并。
- 模糊标题（≥0.90）且作者/年份一致时合并，但 `review_required=1` 并记录原因。
- 处于审核带（≥0.45）但未合并的配对写入 `dedup_reviews`，不自动合并。
- 验收数据中 75 条源记录 → 64 篇候选，移除 11 条重复，`duplicate_doi_count=0`。

### 4.2 初筛标签

- `likely_AlScN_PVD`：材料名 + PVD/溅射方法，且无电极-only/非 PVD/综述阻断。
- `unclear`：AlScN 存在但方法不明、仅 MOCVD/ALD 等非 PVD、可能电极-only、
  标题-only 等；进入人工或阶段 3 全文复核。
- `not_relevant`：无 AlScN 材料名；综述（reference-only）；明确非目标。
- 综述标记 `review_article`，不把综述转述值当原始实验数据。
- 只有电极使用 PVD 的文章：`possible_electrode_only` → `unclear`，
  `eligible_for_confirmation=false`；不会进入 `likely`/已确认集合。
- PLD 等广义 PVD：`likely` 但 `other_pvd` 标记，`eligible_for_confirmation=false`，
  单独处理。
- AI 分类器为可选 OpenAI-compatible 适配器；未配置 API key 时不启用。
  未向外部发送任何内部 147 片样品工艺值。

## 5. 验收数据与结果

### 5.1 金标准

`tests/gold_standard/known_alscn_pvd_papers.csv` 人工准备 20 篇已知
AlScN/ScAlN 溅射原始研究，覆盖 2008–2026、单配方/多参数扫描、RF/DC/脉冲 DC/
HiPIMS、共溅射/单合金靶、SAW/BAW/MEMS/铁电等。全部 DOI 在 2026-10-29 用
OpenAlex DOI filter 批量核验存在且题名匹配。

### 5.2 录制检索页

由于当前代理阻断（见 §6），验收使用通过 harness `web_fetch` 取得的 3 条
OpenAlex 查询页公开元数据：

- `AlScN sputtering`：25 条；
- `AlScN thin films`：25 条；
- `AlScN`：25 条。

共 75 条源记录，覆盖 DOI/标题/年份/类型；文件为
`tests/fixtures/live/recorded_openalex.json`。运行 `tools/acceptance_replay.py`
走真实 `OpenAlexClient` + pipeline + SQLite cache + 初筛，然后第二次完全离线重放。

### 5.3 结果

| 指标 | 结果 |
|---|---|
| 查询页 | 3 |
| 源记录 | 75 |
| 去重后候选 | 64 |
| 去重移除 | 11 |
| 去重审核队列 | 3（作者缺失的精确标题配对） |
| DOI 重复 | 0 |
| 金标准找回 | **18 / 20** |
| Recall | **0.90** |
| 初筛 `likely` | 24 |
| 初筛 `unclear` | 39 |
| 初筛 `not_relevant` | 1 |
| 9 条初筛标签正确率 | 9/9 = 1.00 |
| `likely` precision / recall（9 条小样本） | 1.00 / 1.00 |
| 缓存条目 | 3 |
| 离线重放 | 成功，无网络调用 |

原始输出：`docs/phase2_unittest_output.txt`、`docs/phase2_acceptance_stdout.txt`、
`docs/phase2_gold_eval_stdout.txt`；机器可读汇总在
`docs/phase2_acceptance_evidence.json`。

找回的 18 篇包括 2008 年双靶反应共溅射经典论文、2019 年 AlScN 铁电论文、
2021 年室温反应溅射、2022 年共溅射 Lamb 波谐振器、2025 年 HiPIMS、2025 年
Nature Communications 多层堆栈等。

未找回 2 篇：

1. `10.1002/adma.200802611` — Akiyama 2008 双靶反应共溅射；
2. `10.1016/j.jre.2022.03.009` — AlScN 合金靶 + SAW。

原因：这两篇未进入上述 3 条查询的 OpenAlex 前 25 条；默认计划中的
`ScAlN sputtering` / `AlScN co-sputtering` / `AlScN thin films` 等更窄查询和
更大 `max_results` 有机会在 live 运行中补回，但**当前无法用 live 网络证明**。
这正是网络切换后需要复测的第一项。

初筛 `likely` 只覆盖 24/64，主要因为录制 fixture 只保留标题/年份/类型，没有摘要；
许多 `AlScN: ...` 标题没有出现 sputtering 字样，被保守标为 `unclear`。这不是
全文阶段的否定结论，阶段 3 取得 Methods/Experimental 后再确认。

## 6. 网络限制与失败处理

当前 Windows 代理 `<proxy-host>:<port>` 对 OpenAlex/Crossref 返回
`HTTP 407 Proxy Authentication Required`。实测：

```text
python -m app.cli search --query "AlScN sputtering" --max-results 1 --databases openalex --timeout 3
→ status=failed
→ error kind=network
→ message="Tunnel connection failed: 407 Proxy Authentication Required"
→ exit code 1
```

处理原则已落实：

- 不把 407/超时/429 当成空结果；`query_executions.status=error` 保存错误类型和消息；
- 单条查询失败不终止其他查询：至少一条成功时 run 状态为 `partial`；
- 全部失败时 `failed`，CLI 返回非 0；
- API key 在 URL/错误/日志中脱敏；
- 429 遵守 `Retry-After`，5xx/超时指数退避，403 不重试；
- 缓存只存 2xx；离线缓存缺失时报 `cache_miss`，不伪造成功。

**这不是阶段 2 功能失败，而是当前网络环境限制。** 网络切换后必须：
1. 用默认 14 条查询跑 live `search`；
2. 重新跑 `gold-eval`，确认 20 篇召回 ≥18；
3. 检查 `query_executions` 中每个库的真实状态；
4. 再进入阶段 3 全文获取。

## 7. 已知限制

1. **live 未验收**：18/20 来自录制页重放，不是当前 live API 结果。
2. **Crossref 未 live 验证**：adapter 已用 fixture 测试，但当前网络未实跑；
   live 后需检查 Crossref 的排名和 component 过滤。
3. **录制元数据缺作者/摘要**：验收 fixture 只有 DOI/标题/年份/类型。实际 API
   响应包含作者和摘要；缺少作者时，无 DOI 去重的精确率会偏低，代码对
   “标题+年份相同但作者缺失”采取“合并 + 审核标记”而不是静默丢弃。
4. **初筛是标题/摘要级**：不能替代全文确认；`likely` 只是候选，`unclear` 不是否定。
5. **AI 分类器未 live 调用**：实现和 fake 测试完成；没有 API key，默认 heuristic。
6. **阶段 2 不下载全文**：不访问期刊网站、不绕过付费墙；`pdf_url` 仅来自 API 元数据。

## 8. 阶段 3 交接

阶段 3 可直接从以下字段开始：

- `papers.doi`、`papers.landing_url`、`papers.pdf_url`、`papers.is_oa`；
- `papers.paper_key`（稳定主键）；
- `paper_sources.raw_json`（源记录）；
- `screenings.final_label`：优先 `likely_AlScN_PVD`，`unclear` 进入全文/人工复核；
- `search_runs` / `query_executions`：检索审计；
- `dedup_reviews`：未合并的疑似重复。

阶段 3 需要新增（本阶段未做）：`ingest` / `extract` CLI、全文/补充材料获取、
GROBID/JATS 解析、`documents`/`fulltext` 表、版本与 hash、章节定位、
`metadata_only` / `fulltext_unavailable` 状态。

## 9. 复现命令

```powershell
# 测试
python -m unittest discover -s tests -p "test_*.py" -v

# 离线验收（录制页 -> 线上解析/缓存 -> 离线重放 -> 金标准）
python tools/acceptance_replay.py

# 单独查看金标准
python -m app.cli gold-eval `
  --db data/structured/acceptance_phase2.sqlite3 `
  --gold tests/gold_standard/known_alscn_pvd_papers.csv `
  --labels tests/gold_standard/screening_labels.csv

# 网络切换后的 live 验收
python -m app.cli search --max-results 100 --mailto "you@example.org" `
  --db data/structured/alscn_pvd_live.sqlite3
python -m app.cli gold-eval --db data/structured/alscn_pvd_live.sqlite3
```

## 10. 验收签署建议

建议状态：**阶段 2 有条件通过**。

- 代码/测试/模型/CLI/日志/导出：通过；
- 20 篇召回：录制页重放 18/20，通过；
- live 网络召回与 Crossref 实跑：待网络切换后补测，作为阶段 3 的前置门槛。
