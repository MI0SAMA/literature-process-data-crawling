# 2026-11-02 审计修复记录

基准：`literature_crawler_audit_20261102.md`，b681e6b。  
本轮只做可复核的小范围修复，不声称完成全部实体模型/金标准。

## 已修

### F02 · 表格/SI 证据定位

- 抽取器现在为 primary section 和每个 table 建立独立 source block。
- `set_field()` 根据实际命中文本回溯所属 table；命中表格时，`section` 写
  `Table: <label>`，`locator` 写 `jats:table:N` 等真实 locator，不再一律使用
  primary 方法章节。
- 回归：`test_table_field_locator_is_table_not_primary`。

仍缺：`document_id`、file hash、cell/bbox 未贯穿 evidence schema；SI 与主文同
Parser 路由仍未完成。

### F03 · rejected / needs_review 不进入正式导出

- `ExcelExporter.export(..., mode=...)`：
  - `approved`（默认）：仅 `verified` recipe；仅其 targets/evidence/measurements；
  - `review`：全部候选，用于审核包；
  - `all`：显式全量（诊断用）。
- approved 模式会移除 `field_reviews` 中 `rejected/needs_review` 的字段，并过滤
  对应 evidence。
- measurement 只有在 `data_json.review_status == verified` 时进入 approved。
- CLI：

```bash
python -m app.cli export --mode review --output review.xlsx
python -m app.cli export --mode approved --output formal.xlsx
```

- 回归：`test_approved_export_excludes_rejected_field_and_evidence`。

### F05 · force 失败与文件一致性（部分）

- 结果 JSON 先写 `.json.tmp`，数据库 `commit` 后才原子替换正式 JSON；
  rollback 时删除临时文件。
- force 失败 rollback 后写入 `rolled_back` extraction_run，保留旧成功数据，
  不再让失败看起来像成功。
- `extract_batch` 单篇异常不再中止后续论文；摘要包含 `failed/error`。
- `Database.begin()` 现在显式执行 `BEGIN`（若 sqlite 尚未处于事务）。
- 回归：`test_force_failure_keeps_old_data_and_logs_rollback`。

仍缺：batch_run/batch_item 表、staging 发布状态机、DB/文件 outbox/reconcile、
失败批次重试摘要。

### F04 · GROBID 空单元格

- 不再删除空 cell；`S1 |  | 60` 保留列位置。
- 表 caption 从 `<head>` 读取。
- 回归：`test_table_keeps_empty_cell_and_caption`。

仍缺：结构化 cells/rowspan/colspan/bbox、Docling/pdfplumber adapter、SI PDF
统一 Parser 路由、扫描件 OCR。

### F08 · 科学计数法漏电

- `parse_numeric()` 支持 `1e-6` 形式。
- performance 的数值正则支持科学计数法/符号。
- 回归：`test_leakage_scientific_notation`、`test_scientific_notation`。

仍缺：漏电流必须绑定测试电场/电压、温度和器件面积，当前只保证数值可抽。

## 未完成

- F01：Paper/Recipe/Sample/Step/Layer/Target/Measurement 实体模型与多 Recipe
  自动抽取仍固定 `R1/S1`；标注网页支持多 Recipe，但正式 extractor 没有。
- F02：evidence 未保存 document_id/file hash/cell bbox。
- F06：关键实体仍没有数据库外键；需要迁移和孤儿清理。
- F07：标注 DB 尚未与正式 recipes/field_reviews 裁决/发布桥接。
- F05：全批次原子发布、文件/数据库故障窗口未完全解决。

## 验证

```text
python -m unittest discover -s tests -p "test_*.py"
Ran 185 tests
OK
```

当前正式 approved 导出在现有语料上应为空（71 条 recipe 都还是 `needs_review`）；
审核包必须显式使用 `--mode review`。这不是失败，而是发布保护生效。
