# AUDIT_REPORT.md 修复记录

日期：2026-10-30
审计对象：`AUDIT_REPORT.md`
处理原则：**先冻结、再修复、重新验证；不把规则抽取结果冒充可信科学数据。**

## 1. 冻结

修复前产物已冻结：

- `data/archive/audit_20261030/corpus_pre_audit.sqlite3`
- `data/archive/audit_20261030/AlScN_PVD_literature_pre_audit.xlsx`
- `data/archive/audit_20261030/review_pre_audit.html`
- `data/archive/audit_20261030/MANIFEST.json`（SHA-256 + 冻结原因）

当前 `recipes` 全部 `review_status=needs_review`，并在 `remarks` 中写入
`[AUDIT 2026-10-30: quarantined; rule-based baseline; see AUDIT_REPORT.md]`。
审核页顶部也显示审计隔离提示。当前数据仍**不能直接用于工程统计**。

## 2. P0 修复

### P0-1 层厚度/步骤错配

- 新增 `_ALSCN_THICKNESS_RE`、`_THICKNESS_OF_LAYER_RE`、`_FILM_THICKNESS_RE`
  与 `_NON_FILM_THICKNESS_RE`；厚度必须绑定 AlScN/ScAlN/nitride/ferroelectric，
  电极/黏附层/种子层/金属栈不再作为功能层厚度。
- 回归样例：
  - `10.1002/advs.202500611`：`alscn_thickness_nm=365`（不再是 Mo 的 250 nm）；
  - `10.48550/arxiv.2501.02101`：`alscn_thickness_nm=None`（不再取 Ti 黏附层 10 nm）。
- 沉积时间排除 `etch / pre-treatment / cleaning / native oxide` 句子；
  `2501.02101` 的 8 min 刻蚀时间不再写入 `deposition_time_min`。
- 功率排除 `etch / pre-treatment / cleaning / plasma treatment`；
  `2501.02101` 的 30 W 刻蚀功率不再写入 `target1_power_w`。

### P0-2 气体、靶材、功率关系

- 重写气体解析 `_gas_flows()`：
  - `N2:Ar = 160:32 sccm` → N2=160, Ar=32；
  - `20 sccm for Ar and 12 sccm for N2` → Ar=20, N2=12；
  - `Ar flow of 20 sccm`、`N2 5 sccm` 等形式。
- `10.48550/arxiv.2501.02101` 当前为 `Ar=20, N2=12`（修复前 Ar=12、N2 缺失）。
- 新增双金属靶识别：`Al target` + `Sc target` → T1=Al、T2=Sc，
  `target_configuration=co-sputtering`；不再把 Al/Sc 双靶误写成一个 AlSc 合金靶。
- 逐靶功率关联：新增 `_target_power_pairs()`，识别
  `250 W on the Sc target` / `875 W on the Al target`。
  - `10.48550/arxiv.2405.11102`：T1 Al=875 W，T2 Sc=250 W（修复前 T1 Al=250 W、T2 空）；
  - 不再把首个功率无条件写入 T1。
- T2 证据：targets 不再默认指向 `target1_power_w`；按本靶 power/material/Sc
  证据回退，并校验 `evidence.entity_id == target_record_id`。
  当前 40 条 target（11 条 T2）中 invalid target evidence = 0。
- 修正 `target_configuration`：识别 `five independent targets`、
  `multiple targets`、`co-sputtering`，不再把 `single uninterrupted cycle` 判成单靶。
- 新增 `µbar/microbar` 压力单位（1 µbar = 0.001 mbar）。
- 修正 `kV/cm → MV/cm` 因子：`0.1` → `0.001`。

### P0-3 样品/测量身份与主键

- `sample_id` 从全局 `S1` 改为 `paper_key:S1`，71 条 recipe 现在对应 **71 个 sample**。
- `measurements` 使用同一作用域 `sample_id`；`orphan_measurements=0`，
  `recipes_missing_samples=0`。
- `clear_extraction()` 现在同时删除旧 `measurements`、`samples`、`targets`、
  `evidence`、`review_queue`，避免 `--force` 留下旧测量孤儿。
- 新增 `review_actions` 审计表与 `review-set` CLI：状态变更记录
  old_status/new_status/note，不再只是覆盖 `recipes.review_status`。

仍未完全修复：**多 Recipe/多样品、多步工艺、层实体关系**。当前仍是一篇论文
默认 1 条 `R1/S1`，因此 71 条 recipe 仍被隔离，不能按真实配方数使用。

### P0-4 图表/公式/引用值/单位

- 修正 `kV/cm` 换算；补 `µbar`；气体比例与“值 for 气体”形式重写。
- 参考 PDF 增加 References 边界和引用行过滤（`et al.`、`doi:`、`vol.`、`pp.`、
  `University/Institute/Department`），减少引用值进入方法段。
- `sputtering_mode` 只取沉积句，排除 C-V/AC excitation/PUND/leakage/etch。
- `10.48550/arxiv.2506.22212` 当前：
  - N2=160, Ar=32（不再是 32/15）；
  - `target_configuration=multiple targets`；
  - `substrate_material=None`（AlN 超晶格不再当衬底）；
  - `sputtering_mode=DC`（不再混入 C-V 的 AC）。

## 3. P1/P2 修复

- `openpyxl` 已加入 `pyproject.toml` 依赖。
- `RecipeValidator` 增加语义审计：
  - 单靶标签 vs 多靶证据；
  - 衬底材料无明确 substrate/deposited on 支持；
  - AlN/TiN/Mo 被当衬底；
  - 厚度证据指向电极/金属栈；
  - AC 出现在 C-V/measurement 上下文；
  - 气体比例无法拆分；
  - 靶材 Sc 证据不含 target/alloy；
  - measurement/sample 孤儿外键。
- 当前验证：71 recipes，11 个新 issues，143 个 open review items（高 29 / 中 114）。
- `extraction_runs.metadata` 增加 `section_inventory`、`selected_section_type`、
  `ignored_section_count`、`table_count`、`coverage_warnings`，为后续完整性审计留下依据。
- 审核页新增 AUDIT 隔离横幅；仍为只读，在线提交/权限仍需后续实现。
- `review-set` 提供最小可审计状态变更；`record_review_action()` 会同步更新
  `recipes.data_json.review_status`，Excel 以 DB 顶层 status 为准。
  `verified` 前会阻断：缺失字段证据、未关闭 high review、target 证据不一致。
  review action 现在带 `extraction_id`，可追溯到对应抽取版本。
- 新增 `field_reviews` 表与 `review-field` CLI。`verified` 现在要求：
  - 每个非空科学字段都有当前抽取版本的 `verified` 字段审查；
  - 字段 evidence 存在且与当前 extraction_id 对应；
  - 无未关闭 high review；
  - target evidence 与 entity_id 一致。
  修复前旧门槛模拟 60/71 可通过；修复数据后旧门槛仍可放行 69/71；加入字段级审查后 **0/71 可通过**（当前 field_reviews=0）。
  这防止仅凭“证据存在”就批量 verified。
- 逐篇抽取写入使用 `begin/commit/rollback`；`--force` 删除旧结果与新写入在同一
  事务内，失败回滚，降低“删旧成功、写新一半失败”的部分数据风险。
- 温度：优先匹配 AlScN/AlBScN/nitride 沉积句，排除 bottom/top electrode、seed、adhesion。
  `10.1109/led.2026.3675494` 从底电极 150 °C 修正为 **AlBScN 300 °C**。
- 压力：只有明确的 `working/process/deposition/sputter/chamber pressure` 才写入工作气压；
  `10.1109/led.2025.3587950` 的真空探针台 `~10^-3 Torr` 不再入库。
- `room temperature` 不再折算为 25 °C；当前 11 条记录仅保留 `temperature_basis=room_temp`
  并生成 high review，`substrate_temperature_c=25` 为 **0 条**。
- 性能记录：只扫描 abstract/results/discussion/conclusion/body，排除 introduction；
  带 `[13,14,15]`、`et al.`、`reported by` 等引用语境的句子直接丢弃。
  - `10.3390/nano13020274` 的 d33=27.6（Introduction，[13,14,15]）已不再入库；
  - 32 条旧 measurement 中 19 条来自引言/引用语境被移除；保留 13 条（10 abstract、
    1 results、1 body、1 conclusion），全部 `review_status=needs_review` 并进入 review_queue。
  - 摘要值保留但标记 `source_scope=abstract`，需与当前样品核对。

## 3.1 方法与功率语义字段（新增）

- `targets` 长表作为靶级事实源，新增：
  - `target_material`、`target_power_w`（已有）；
  - `target_diameter_inch`、`power_density_w_cm2`；
  - `sputtering_mode`；
  - `power_basis`：`target_power / sputter_power / power_density / bias_power / etch_power / unknown`；
  - `power_statistic`：`average / peak / rated / unknown`；
  - `reported_power_text`：保留原文；
  - `equipment_model`、`deposition_rate_nm_min`。
- `recipes` 保留 `target1/2_*` 宽表字段作为兼容；靶级分析应以 `targets` 长表为准。
- `power_density_w_cm2` 仅当 `power_basis` 为 `target_power/sputter_power` 且直径已知时计算，公式为
  `P / (π(d/2)^2)`；它是**名义**功率密度，不是实测值。
- 5000 W 两条为 `target_power`；6000 W 两条原文为 `sputter power`，标为 `sputter_power`，不再与 target power 混为一类。
- `10.1109/jmems.2022.3178978` 的 450/1000 W 来自 Table III 的 deposition recipe，已纠正此前“刻蚀 recipe”的误排除。

### 3.2 字段审核与实体级修正

- 582 个“无独立证据”字段全部是派生/语义字段（`deposition_method`、`sputtering_mode`、
  `target1_power_basis` 等），已加入 `DERIVED_AUDIT_FIELDS`；`review-field` 允许这些字段
  在无独立 evidence 时做人工决定，不再卡死流程。
- 每次字段审核决定写入 `review_actions`（含 `field_key`、old/new decision、extraction_id）；
  `field_reviews` 只保存当前状态，历史不再被覆盖。
- 靶径规则改为优先 `5 cm diameter`、`diameter of 200 mm` 等显式直径，
  排除 `16.5 cm from the target` 和 `thickness of 6 mm`：
  - `10.3390/mi13060887`：50 mm（不再 165 mm），名义密度 15.279 W/cm²；
  - `10.3390/s25175370`：200 mm（不再 6 mm），无靶功率，密度为空。
- 靶径新增 `target1_diameter_mm` 字段 evidence，target 记录增加 `diameter_evidence_id`；
  密度记录 `density_evidence_id`/`density_formula`，审核页展示直径、功率语义和密度。
- `target_power_w` 不再接收 `bias_power`、`power_density`、`etch_power`：
  - `10.3390/s25175370` 的 8 W 偏压 → `target_power_w=None` + high review；
  - `10.48550/arxiv.2403.12361` 的 12.7 W/cm² → `target_power_w=None` + high review。

## 4. 复验结果

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| paper | 96 | 96 |
| document | 136 | 136 |
| section | 795 | 795 |
| table | 45 | 45 |
| recipe | 71 | 71 |
| sample | **1** | **71** |
| measurement | 32 | 13（19 条引言/引用语境移除） |
| orphan measurements | 未检查 | **0** |
| evidence | 462 | 340（更严格规则，少而更可信） |
| open review | 111 | 143（高 29 / 中 114） |
| Excel 工艺记录 | 71 | 71 |
| Excel 靶材明细 | 39 | 40 |
| Excel 性能记录 | 32 | 13 |
| Excel 证据记录 | 462 | 340 |

测试：`python -m unittest discover -s tests -p "test_*.py"` → **180 tests OK**
（审计修复新增了气体比例、µbar、kV/cm、层厚、双靶、AC/刻蚀排除、孤儿测量、
review_actions 等回归测试）。

## 5. 仍未修复/不能声称

1. **多 Recipe/多样品/多步/层实体**：当前仍一篇一条 `R1/S1`；不能把 71 条当真实配方数。
2. **PDF 表格/SI 完整性**：PDF fallback 不结构化表格；部分 SI 404/挑战页。
3. **AI 抽取**：仍无工艺 LLM；只有标题/摘要初筛提示词。
4. **95% Precision/Recall**：没有逐篇人工金标准，不能声称。
5. **审核闭环**：有 `review_actions` 和 `review-set`，但无在线审核 UI/权限/双人复核。
6. **事务性**：已改为逐篇 `begin/commit/rollback`，但 JSON 文件与 DB 不是同一事务，批次也不是全库原子；跨论文失败仍可能留下已完成论文。

## 6. 使用建议

- 不要把当前 `corpus.sqlite3` 或 Excel 当最终工程数据；所有 recipe 均为 `needs_review`。
- 先人工复核 4 个审计样例 + 随机 20 条；确认字段与证据后再考虑 `review-set verified`。
- 阶段 4 下一轮应优先做“实体/步骤/层关系 + 多 Recipe 候选 + 单事务写库”，
  再决定是否引入 LLM 抽取。
