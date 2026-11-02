# 阶段 2 live 检索测试记录

- 日期：2026-10-29
- 网络：经 `<proxy-host>:<port>` 认证代理，使用 `requests` Basic 代理认证
- 凭据：仅通过 `ALSCN_PROXY` 环境变量传入；未写入代码、配置文件或日志
- 数据库：`data/structured/live14.sqlite3`
- 输出：`data/structured/live14/<run_id>/`
- 原始日志：`docs/live14_stdout.txt`、`docs/live14_gold_eval.txt`
- 运行 ID：`run_20261029T093842Z_127a8008`

## 1. 执行结果

| 指标 | 结果 |
|---|---|
| 默认查询 | 14 条 |
| 数据库 | OpenAlex + Crossref |
| 查询执行数 | 28 |
| 状态 | **ok，28/28 无错误** |
| 原始记录 | 2800 |
| 去重后论文 | 1607 |
| 去重移除 | 1193 |
| 初筛 `likely` | 311 |
| 初筛 `unclear` | 415 |
| 初筛 `not_relevant` | 881 |
| 去重审核队列 | 56 |
| DOI 重复 | **0** |

命令：

```powershell
$env:ALSCN_PROXY='http://<user>:<password>@<proxy-host>:<port>'
python -m app.cli search --max-results 100 `
  --db data/structured/live14.sqlite3 `
  --out data/structured/live14 `
  --timeout 30 --mailto research@example.org
```

## 2. 金标准召回

| 指标 | 结果 |
|---|---|
| 金标准 | 20 |
| 找回 | **19** |
| Recall | **0.95** |
| `likely` 中找回 | 16（0.80） |
| 缺失 | 1 篇 |
| 重复 DOI | 0 |

缺失：

- `10.1002/adma.200802611` — Akiyama 2008，双靶反应共溅射经典论文。

该篇在录制页重放中也未进入前 25；live 默认 14 条查询仍未进入 100 条上限内。
其余 19 篇全部找回，包括 2008 年经典论文之外的 2014–2026 年论文。

## 3. 初筛标签

9 条人工标签中 8 条正确（accuracy 0.889）：

- 5 条 `likely` 全部正确；
- 3 条 `unclear` 中 2 条正确；
- 1 条 `not_relevant` 被标为 `unclear`：`10.1007/s40820-024-01441-1`
  （New-Generation Ferroelectric AlScN Materials）。OpenAlex 元数据未把它标为
  `type=review`，标题也没有 review 字样；摘要中的综述信号尚未纳入规则。
  这是保守 `unclear`，不会进入 `likely`/已确认集合，但阶段 3 应结合全文确认。

`likely` 的 precision/recall（5 条正样本）仍为 1.00/1.00。

## 4. 结论

阶段 2 的 live 检索通过任务书验收线：

- 20 篇金标准找回 **19**（≥18）；
- DOI 去重 **0 重复**；
- 28/28 查询无网络/解析错误；
- 错误、缓存、重试字段均有记录。

阶段 3 可以开始。
