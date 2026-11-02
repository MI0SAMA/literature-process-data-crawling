# OneDrive 论文目录作为独立测试集

## 1. 结论

可以，但**不能把 634 个 PDF 整体当作 AlScN 测试集**。这个目录是混合文献库，包含 AlScN、铁电、MoS2、ML、催化、方法学等。

目前已完成只读盘点，并建立了一个 **30 篇独立盲测候选集**：

- 盘点目录：`<OneDrive>/Paper`
- 相关目录：`AlScN/`、`alscn aog/`、`ferroelectric/`
- 178 个 PDF，全部可解析前 2 页
- 其中 93 个出现 AlScN/ScAlN
- 63 个同时出现 AlScN 与 sputter/deposition
- 去重后 64 个有效 DOI 候选
- 与现有 corpus 重叠 4 个，已排除
- 选取 30 篇：19 篇 deposition、7 篇 characterization、4 篇 theory/review

清单：

- [onedrive_testset_inventory.csv](onedrive_testset_inventory.csv) — 178 篇全量索引
- [onedrive_testset_manifest.csv](onedrive_testset_manifest.csv) — 30 篇盲测集

## 2. 为什么不能直接用全部

1. **主题不纯**：MoS2、ML、催化不是 AlScN PVD 论文。
2. **类型不纯**：第一性原理、分子动力学、综述没有沉积配方。
3. **重复**：同一 DOI 可能有多份 PDF；现有 corpus 已有 4 篇。
4. **缺 DOI**：部分 PDF 前两页没有 DOI，需要人工补。
5. **补充材料**：SI 与正文分开，不能只测正文。
6. **没有金标准**：清单只是候选集，不是已经标注好的 test set。

## 3. 建议的测试集结构

| 层 | 数量 | 用途 |
|---|---:|---|
| deposition / experimental | 19 | 评估 recipe 字段与证据 |
| characterization / property | 7 | 评估性能值与样品关联 |
| theory / review | 4 | 负样本，检查是否误抽配方 |
| 合计 | 30 | 首轮盲测 |

这 30 篇只用于评估，不用于调规则。调参必须使用另一批开发集。

## 4. 标注协议

每篇至少标注：

### Recipe 级
- 是否存在可执行的 AlScN 沉积 recipe
- 是否多 recipe、多样品、多步骤
- 沉积方法、设备、靶材数

### 字段级
- 靶材材料、靶功率、功率语义（target/sputter/bias/density）
- 靶径、名义功率密度
- 溅射模式、气压、温度、气体流量、膜厚、时间、靶基距离
- 每个字段：`present / absent / ambiguous` + 原文证据 + 层/步骤归属

### 性能级
- 性能值、样品 ID、测试条件
- 是否摘要值、引用值、图/表值

### 审核
- 两人独立标注
- 分歧由第三人裁决
- 保留标注者、时间、原文 locator

## 5. 评估指标

- 字段 precision / recall（按字段分别计算）
- evidence 支持率
- 层/步骤归属错误率
- 多 recipe 召回率
- 性能值来源错误率（摘要/引用/图）
- 每条 recipe 的审核通过率

## 6. 当前还不能声称的事

- 不能说这 30 篇已经有 ground truth；
- 不能把索引结果当作抽取准确率；
- 不能把 30 篇结果直接外推到全部 634 篇；
- 不能用同一批 30 篇反复调参后再宣称“盲测”。

## 7. 下一步

1. 从 30 篇中先做 10 篇双人标注，校准字段定义。
2. 建立 `gold_standard_v1`，冻结。
3. 用当前 extractor 跑这 10 篇，计算逐字段 precision/recall。
4. 只在开发集上修规则，测试集保持不动。
5. 通过 10 篇后扩到 30 篇，再决定是否进入生产试点。
