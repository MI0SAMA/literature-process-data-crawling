# 阶段 2 测试记录

> 注：本文件保留阶段 2 验收时的 90 项测试结果；阶段 3 完成后全量测试为 133 项（见 `Phase3_Test_Record.md`）。

- 日期：2026-10-29
- 环境：Windows；Python 3.13.14；标准库 `unittest`；SQLite 3
- 代码版本：阶段 2 初版（`0.2.0`）
- 测试命令：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

- 最近一次结果：

```text
Ran 90 tests in 1.534s

OK
```

退出码：0。

## 1. 测试分布

| 测试模块 | 数量 | 覆盖内容 |
|---|---:|---|
| `test_identifiers` | 11 | DOI URL/`doi:`/大小写/百分号/尾部标点标准化；标题 NFKD、标点、冠词；相似度 |
| `test_dedup` | 10 | 同 DOI 跨源合并；URL 前缀/大小写；无 DOI 精确/模糊合并；审核队列；作者不同不合并；作者缺失的精确标题合并；统计 |
| `test_screening` | 17 | 材料名 7 种写法；溅射/磁控/反应/共溅射；无方法；MOCVD；综述；电极-only；PLD；title-only；AI 提升/阻断/冲突 |
| `test_query_plan` | 8 | 默认材料/方法覆盖；库/年份/分页；自定义查询；去重；序列化；非法年份/库 |
| `test_http` | 9 | 429 Retry-After；5xx 重试；403 不重试；超时重试；JSON 解析错误；缓存命中；离线 stale；cache miss；API key 脱敏 |
| `test_clients` | 9 | OpenAlex 摘要倒排还原、作者/来源/分页/URL 参数；Crossref JATS 清洗、component 过滤、年份缺失 |
| `test_storage` | 8 | 建表；run/query 持久化；cluster/screening；源记录幂等；同 DOI 复用；run-paper 关联过滤；重复 DOI 报告；缓存；finish run |
| `test_pipeline` | 5 | 去重+初筛+导出；重跑幂等；单源失败 partial；全失败 failed；分页上限 |
| `test_reporting` | 4 | CSV/JSON 导出；金标准找回/缺失；非法 DOI 跳过；初筛标签评估 |
| `test_llm` | 5 | JSON/围栏 JSON；非法 label；缺配置；prompt 不含内部数据 |
| `test_cli` | 4 | `--help`；`init-db`；无 run 的 `report`；`cache-stats` |
| **合计** | **90** | |

## 2. 关键行为验证

### 2.1 DOI 去重

- `10.1002/ADMA.200802611`、`https://doi.org/10.1002/adma.200802611` 归一为同一 DOI。
- 跨 OpenAlex/Crossref 的同 DOI 记录只生成一个 `paper_key`，两条 `paper_sources` 都保留。
- 验收数据 75 条源记录 → 64 篇候选，`duplicate_doi_count=0`。

### 2.2 无 DOI 去重

- 精确标题 + 第一作者 + 年份：自动合并。
- 精确标题 + 年份，但作者元数据缺失：合并并标记 `review_required`，避免
  `paper_key` 冲突导致数据库少记；这是验收中实际发现并修复的案例
  （见 `Phase2_Failure_Cases.md` #3）。
- 标题相似但未达阈值：不合并，写入 `dedup_reviews`。
- 同标题不同作者：不合并，不进审核队列。

### 2.3 初筛边界

- `AlScN` + `sputtering/magnetron/reactive/co-sputter/RF/DC/pulsed DC` →
  `likely_AlScN_PVD`。
- `AlScN` + `MOCVD/ALD/MBE` → `unclear` + `non_pvd_method`。
- 综述 → `not_relevant` + `review_article`。
- `Sputtered AlScN electrodes for HfO2 capacitors` → `unclear` +
  `possible_electrode_only`，`eligible_for_confirmation=false`。
- `AlScN` + PLD → `likely` + `other_pvd`，但 `eligible_for_confirmation=false`。
- 无摘要 → `title_only`，仍可进入 `likely`，但阶段 3 必须用全文确认。

### 2.4 HTTP 失败处理

- 429 + `Retry-After: 2` → 等待 2 秒后重试；测试断言延迟。
- 403 → 只请求一次，`ApiError(kind="http_error", status=403, retryable=False)`。
- 连续超时 → 按 `max_attempts` 重试后抛 `ApiError(kind="timeout")`。
- 200 非 JSON → `ApiError(kind="parse_error")`。
- 离线无缓存 → `ApiError(kind="cache_miss")`。

### 2.5 幂等

- 同一批源记录重复保存：`papers` 和 `paper_sources` 数量不变。
- 同一计划重跑：新 `run_id`，但论文/源记录不重复。
- 缓存命中后第二次执行不再发起 HTTP 请求。

## 3. 验收重放测试

命令：

```powershell
python tools/acceptance_replay.py
```

结果（`docs/phase2_acceptance_evidence.json`）：

| 检查 | 结果 |
|---|---|
| `search_run_ok` | true |
| `recorded_queries_executed` | true（3 条） |
| `cache_entries_written` | true（3） |
| `offline_replay_completed` | true（无网络调用） |
| `papers_out_match_db` | true（64） |
| `source_records_preserved` | true（75） |
| `gold_recall_at_least_18_of_20` | true（18/20，0.90） |
| `no_duplicate_doi` | true（0） |
| `screening_labels_accuracy_at_least_0_9` | true（9/9，1.00） |

### 3.1 CLI 离线冒烟

```powershell
python tools/build_offline_fixture.py
python -m app.cli cache-import --manifest tests/fixtures/live/cache_manifest.json `
  --db data/structured/offline_demo.sqlite3
python -m app.cli search --query "AlScN sputtering" --databases openalex `
  --max-results 25 --page-size 25 --offline `
  --db data/structured/offline_demo.sqlite3 --out data/structured/offline_demo
```

结果：`status=ok`，25 条源记录 → 24 篇候选，`query_executions=1`，无网络错误，
退出码 0。说明 `cache-import`、`--offline`、SQLite 缓存键和 CLI 串联正常。

## 4. 网络失败测试

命令：

```powershell
python -m app.cli search --query "AlScN sputtering" --max-results 1 `
  --databases openalex --timeout 3 --mailto research@example.org
```

结果：

```text
status=failed
errors[0].kind=network
errors[0].message=Tunnel connection failed: 407 Proxy Authentication Required
exit code=1
```

该失败被完整写入 `query_executions` 和 `search_log.json`，未被伪装成空结果。

## 5. 复现说明

```powershell
# 清空并重跑测试
python -m unittest discover -s tests -p "test_*.py" -v

# 重跑验收（会重建 data/structured/acceptance_phase2.sqlite3）
python tools/acceptance_replay.py

# 单独跑某个模块
python -m unittest tests.test_dedup -v
python -m unittest tests.test_pipeline -v
```

测试不访问外部网络；所有 HTTP 用例使用内存 fake transport，OpenAlex/Crossref
用 `tests/fixtures/` 的公开元数据。
