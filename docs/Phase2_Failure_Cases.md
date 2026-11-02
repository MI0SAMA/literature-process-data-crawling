# 阶段 2 失败案例与处理记录

本文件记录阶段 2 实际遇到或主动覆盖的失败场景。原则：**不把失败伪装成空结果，
不自动补齐缺失值，不绕过付费墙/登录墙/验证码。**

## 1. 当前网络代理阻断 API（未解决，环境限制）

**现象**

```text
python -m app.cli search --query "AlScN sputtering" --max-results 1 --databases openalex
→ network: <urlopen error Tunnel connection failed: 407 Proxy Authentication Required>
→ status=failed, exit code=1
```

**原因**：Windows 代理 `<proxy-host>:<port>` 需要认证；Python `urllib` 和
`curl` 直连均得到 407。`web_fetch` 走的是 harness 的另一条网络路径，可以访问
OpenAlex，但 CLI 不能复用该路径。

**处理**

- CLI 把 407 归类为 `network`，写入 `query_executions.error_kind/error_message`，
  run 状态 `failed`，退出码 1。
- 不返回 0 条“成功”记录。
- 验收改用真实 OpenAlex 结果页的录制元数据重放（`tools/acceptance_replay.py`）。
- 网络切换后必须补 live 运行；这是阶段 3 的前置条件。

**影响**：18/20 召回是录制页结果，不是当前 live API 结果。

## 2. 金标准中 2 篇未进入录制页前 25 条（已知覆盖缺口）

**现象**：20 篇金标准找回 18 篇，缺失：

- `10.1002/adma.200802611`（Akiyama 2008，双靶反应共溅射）；
- `10.1016/j.jre.2022.03.009`（AlScN 合金靶 + SAW）。

**原因**：录制页只取了 3 条查询的 OpenAlex 前 25 条；这两篇排名在 25 名之后。
默认计划的更窄查询（`ScAlN sputtering`、`AlScN co-sputtering`）和
`max_results=100` 有机会补回，但当前无法用 live 网络验证。

**处理**：保留在 `missing` 列表中，不手工塞入候选；验收报告明确标注。
网络切换后先跑默认 14 条查询，再复测这两篇。

## 3. 无 DOI 精确标题重复导致 paper_key 冲突（已修复，有回归测试）

**现象**：验收第一次运行时 `papers_out=67`，但数据库只有 65 篇。原因是有两条
无 DOI 记录标题/年份完全相同、作者元数据缺失（录制 fixture 只有标题），去重时
未合并，却生成了相同 `paper_key`，SQLite upsert 把其中一条覆盖。

**处理**：

- 精确标题 + 年份一致、但至少一方作者缺失时：合并，并标记
  `review_required=True`，同时写入审核队列，原因 `exact title/year match but author metadata is missing`；
- 不再生成重复 `paper_key`；
- 增加回归测试 `test_exact_title_missing_author_is_merged_with_review_flag`。

**结果**：75 源记录 → 64 篇，`papers_out == papers_in_db`，`duplicate_doi_count=0`。

## 4. Crossref 返回 component/dataset 子记录（已过滤，有测试）

**现象**：Crossref 检索 `AlScN sputtering` 时，前几条可能是
`type=component` 的补充材料（如 `.../mm1`），不是论文本身。

**处理**：`CrossrefClient` 跳过 `component`、`dataset`、`peer-review`、`grant`、
`database`。测试 fixture 中特意放了一条 component，断言只解析出 1 篇论文。

**残留风险**：Crossref type 字段不总是规范；live 后需抽样复核。

## 5. AlScN 标题不含“sputtering”被保守标为 unclear（设计取舍）

**现象**：`AlScN: A III-V semiconductor based ferroelectric`、
`Decoupling polarization ... AlScN/AlN/AlScN stack` 等标题没有沉积方法词，
规则初筛给 `unclear`，不是 `likely`。

**原因**：阶段 2 只有标题/摘要；不能从标题推断全文方法。任务书要求“真正的
沉积方法以后续全文为准”。

**处理**：保留 `unclear`，不强行提升；阶段 3 取得 Methods/Experimental 后再确认。
录制 fixture 无摘要，进一步放大了该现象（24 likely / 39 unclear）。

## 6. AI 分类器未配置 API key（默认关闭）

**现象**：`--classifier llm` 需要 `ALSCN_LLM_BASE_URL`、`ALSCN_LLM_MODEL`、
`ALSCN_LLM_API_KEY`；当前环境未配置。

**处理**：默认 `heuristic`；LLM 适配器用 fake HTTP 测试了 JSON/围栏 JSON/
非法 label/缺配置；未 live 调用。只发送公开标题和摘要，不发送内部 147 片数据。

## 7. 429 的 Retry-After 被错误截断（已修复，有测试）

**现象**：测试中 `Retry-After: 2` 被 `max_delay_seconds=0` 截断为 0，未按服务端
要求等待。

**处理**：`RetryPolicy.delay()` 对服务端 `Retry-After` 不再套用本地退避上限；
测试断言实际 sleep=2.0。

## 8. 验收重放 fixture 的 source_id 冲突（已修复，仅影响验收工具）

**现象**：验收 transport 最初用 `recorded/{index}` 作为 OpenAlex id，三条查询的
第 1 条都变成 `source_id=1`，`paper_sources` 主键冲突，源记录只剩 25 条。

**处理**：改为 `recorded/{query_slug}_{index}`，源记录恢复 75 条。生产
`OpenAlexClient` 使用真实 OpenAlex Work ID，不受影响。

## 9. 未做/明确不在阶段 2 范围

- 不下载期刊 PDF/HTML，不访问付费墙；
- 不做 PDF 乱码/GROBID 章节解析（阶段 3）；
- 不做 recipe/sample/target/measurement 字段抽取（阶段 4）；
- 不做 Excel 六表导出（阶段 6）；
- 不做前端/多 Agent/RAG。
