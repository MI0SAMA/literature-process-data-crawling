# AlScN-PVD 文献工艺数据库：Agent 开发任务书

版本：v1.0，2026-10-29

## 一、目标与已完成的阶段 1

**业务目标：** 自动发现 AlScN/ScAlN 功能层通过 PVD 制备的原始研究论文，识别其中真实进行的工艺组合和样品，抽取各类沉积参数，保存逐字段原文证据，并导出用户现有 PVD 工艺表头兼容的数据。结构、电学和压电性能作为后续扩展。

阶段 1（字段规范）已有两个交付：

1. AlScN_PVD_Literature_Template.xlsx：工艺记录、靶材明细、性能记录、证据记录、字段字典、原表头映射六个工作表。
2. alscn_pvd_schema.json：英文字段 Key、中文显示名、类型、单位、优先级及原始字段映射，作为编程接口的权威定义。

用户原表含 147 个有效样品（A 84、B 36、C 27），但存在 6 个重复表头区段、列位移和底部汇总行。新数据库只继承字段定义，不把内部 147 片数据直接混入论文记录。

### 业务规则

- 首批聚焦 reactive magnetron sputtering、RF/DC/pulsed-DC sputtering、单合金靶及多靶共溅射。其他广义 PVD（如 PLD）单独标记。
- **必须是 AlScN 功能层通过 PVD 制备。** 仅顶/底电极使用 PVD 的论文不得进入核心集合。
- 综述主要用于发现参考文献，不将综述转述的实验数值当作原始实验数据。
- 只使用合法可访问的 HTML、PDF 和 Supplementary Information；不绕过付费墙、登录墙、验证码和网站限制。
- 内部 147 片的真实工艺值不发送给外部 AI API；只发送表头规范及可依法处理的公开论文内容。
- Null 为未报告，不是零；明确 no Ar 才能填 Ar = 0。
- 论文给出温度范围与功率范围，并不代表所有笛卡尔积组合都进行了实验。

## 二、数据模型：先确定“记录单位”

建议数据库实体如下：

1. **papers**：一篇原始论文及 DOI、标题、版本、出版日期、来源链接。
2. **recipes**：论文中明确实际进行的一组 AlScN 功能层沉积配方。
3. **samples**：某配方制备的实际样品，记录薄膜厚度、衬底、底电极和薄膜成分；多个样品可共享一个配方。
4. **targets**：配方下的各个靶材。靶材数量不限，靶材料、合金比例、功率、RF/DC 模式、靶电压、靶电流分别记录。Excel 主表只把前两个靶展开为靶材1/靶材2；更多靶保存在长表。
5. **measurements**：某个具体样品在特定测试条件下的性能数值。Pr、2Pr、Ec、FWHM、应力、d33、d33,f、漏电等必须区分。
6. **evidence**：逐字段原文证据，保存来源 URL、章节、PDF 页码或表图编号、原文、原数值与单位、换算值、证据状态。
7. **study_parameter_ranges**：论文研究的参数扫描范围、步长、优化窗口。这类信息不得伪造成样品级配方。
8. **extraction_runs**：输入版本、解析器/模型/提示词版本、日志、成本、失败类型及重试状态。

关系：paper 1:N recipes；recipe 1:N samples；recipe 1:N targets；sample 1:N measurements；evidence 分别关联上述对象。

**配方/样品关联规则：** 一篇论文可能有一组固定生长配方对应几种不同电极结构，也可能有多组温度实验但只对最优样品测试铁电性能。Agent 必须保持这些关系，不能将最优性能值分配到所有工艺组合。

### 重要字段口径

- Al/Sc 双靶功率必须分别属于两个靶，不允许作为一个总功率。
- 薄膜实测 Sc at.% 与合金靶标称 Sc at.% 分开。
- 沉积温度、退火温度、测试温度分开；载台设定温度与衬底实测温度保留类型标志。
- 基底偏压 V、基底 RF 功率 W、靶电压 V、靶功率 W 是不同量。
- 保留原始数值和原始单位；程序负责可核查的单位换算，不能让 LLM 随意换算。
- AlScN 主表只记录功能层的沉积参数；顶电极、底电极、种子层的制备过程另外标识。

## 三、阶段 2：文献发现和初筛

**目标：** 根据主题自动查找 AlScN PVD 原始研究，输出可去重、可追溯的候选论文列表。

### Agent 实现任务

1. 分别实现 OpenAlexClient、CrossrefClient；后续按需实现 SemanticScholarClient。每个 client 独立处理 API 参数、分页、限流、重试、超时及错误。
2. 默认查询词覆盖 AlScN、ScAlN、Sc-doped AlN、Al1-xScxN、ScxAl1-xN、scandium aluminum nitride，结合 magnetron sputter、reactive sputter、co-sputter、RF sputter、pulsed DC sputter。
3. 检索策略生成 QueryPlan，记录每一条实际执行的查询、数据库、时间范围、分页、结果数及时间戳。
4. DOI 标准化后去重；无 DOI 时使用标题+作者+年份进行候选匹配，不确定的重复项进入审核队列。
5. 规则初筛非相关文献，再用 AI 对标题/摘要分类：likely_AlScN_PVD、unclear、not_relevant。真正的沉积方法以后续全文为准。
6. 缓存 API 结果，支持幂等再次执行；遵守官方限流，不应使用大规模并发触发平台限制。

### 阶段交付

- 可执行搜索 CLI。
- papers/search_runs 数据库模型。
- 检索日志和候选论文 CSV/JSON。
- 单元测试及测试记录。

### 验收

人工准备 20 篇已知 AlScN 溅射论文，首轮目标至少找回 18 篇。DOI 去重不能产生重复论文；只有电极 PVD 的文章不能直接进入已确认样本集合。

## 四、阶段 3：全文与补充材料获取

**目标：** 获得合法可用的结构化正文，并保持文件、版本和原文位置可追溯。

### 实现任务

1. DOI 解析后尝试开放获取 HTML、PMC/JATS、可合法访问 PDF、Supplementary Information。
2. HTML/JATS 优先；PDF 使用 GROBID 进行章节和参考文献解析，表格单独解析；图中的数值先标记人工审核。
3. 记录源 URL、版权/访问状态、获取时间、文件 hash、文档类型、期刊版/接受版/预印本版本。
4. 重点定位 Methods、Experimental、Deposition、Fabrication、Growth、Sputtering、Supplementary Methods，但同时检查结果部分和补充材料的工艺表。
5. 分离正文与参考文献，避免抓取被引用论文的参数。
6. 解析失败保留失败原因及原文位置；不得返回看似成功的空白记录。

### 验收

取 10 篇合法可访问全文，能定位至少 9 篇的主要沉积方法章节。无全文时明确记录 metadata_only/fulltext_unavailable，不发起越权访问。

## 五、阶段 4：AI 工艺组合识别与字段抽取

这是系统的核心，建议至少采用两遍抽取。

**Pass A：实验结构识别**

- 确定 AlScN 功能层的实际沉积方式。
- 找到当前论文所有独立工艺条件、真实样品标签、变化参数、固定参数、最优配方和仅作为扫描范围的参数。
- 判断工艺组合与衬底、电极、薄膜成分和性能结果的关联。
- 建立稳定 recipe_id、sample_id、target_index；不能根据范围自动制造组合。

**Pass B：严格字段抽取**

- 以 alscn_pvd_schema.json 生成 Pydantic/JSON Schema 输出结构。
- 每个非空关键数值必须携带 evidence_id，保留最小充分原文片段或表格单元内容。
- 记录 raw_value、raw_unit、normalized_value、normalized_unit、evidence_status。
- 缺失字段填 null。歧义字段为 ambiguous，不能自行选一个可能的答案。
- 可能需要按章节分批抽取，避免长篇 PDF 超出上下文或混淆段落。
- 输出不合法时可有限次数重试修复结构；仍失败则进入 needs_review。
- 记录模型、版本和提示词版本，保证结果可重复比较。

### 验收

使用 20 篇人工金标准论文，其中至少 10 篇含多组配方/样品。关键数字字段（靶功率、气流、工作气压、温度、膜厚）的字段级精确率目标 ≥95%，每个非空已入库数字都有可定位证据。配方数量必须准确，不得生成未实际做过的 DOE 组合。

## 六、阶段 5：标准化、质量校验、人工审核

**目标：** 把模型抽取结果转换成适合 PVD 工程分析的数据。

### 实现任务

1. 确定性单位转换：Pa/mbar/mTorr/Torr，W/kW，nm/μm/mm/cm，s/min 等。
2. 归一化 AlScN/ScAlN 材料名称和沉积模式，但保留原文。
3. 校验数据类型和物理含义；不能把 300 V 写到靶功率 W，不允许将未提及的 Ar 流量填写为 0。
4. 对正文、表格、补充材料冲突列出审核项；不允许自动选择“看起来更合理”的值。
5. 设计轻量审核页面：原文证据与结构化字段并排显示；支持确认、更正、拒绝。
6. 人工更正作为审计记录，不覆盖原始 LLM 输出；二次抽取不覆盖已审核结果。

### 验收

已审核字段导出后仍保留更正；derived 类型可以反查原值和换算过程；null/0/ambiguous/not_applicable 不能混同。

## 七、阶段 6：数据库与 Excel 导出

**目标：** 数据存储稳定、可检索、可再次导出。

### 实现任务

1. 使用 SQLite + SQLAlchemy/Alembic，建立主键、外键、唯一约束、迁移脚本及事务保护。
2. 规范表驱动导出到六个 Excel 工作表；工艺主表采用靶材1/靶材2宽表，靶材明细支持任意靶数。
3. 提供论文 DOI、材料成分、温度、工作气压、N2/Ar 流量、靶功率、衬底/电极、设备等筛选。
4. 提供 papers、recipes、samples、targets、measurements、evidence、review-queue、export API。
5. 如果未来要和内部 147 片样品合并查询，必须标识 data_origin=internal/literature 并保留原始内部配方 ID。

### 验收

一个 DOI 对应多个实际 recipe/sample 时，数据库和 Excel 的关系一致；双靶两条功率均保留；单靶时第二靶为空；第三靶及以上存在靶材明细表中。导出再导入后字段值、单位和证据链接均保持一致。

## 八、阶段 7：质量评测与回归测试

建立 20 篇人工金标准集：

- 5 篇单一工艺；
- 10 篇多组工艺/多样品；
- 5 篇重要参数在表格或 SI 中。

可包含 Nature Communications 2025 的超薄 AlScN 耐久性论文及 2026 年的 ScAlN 反应脉冲 DC 工艺优化论文，二者分别代表单配方和多参数扫描。

必须计算：

1. 检索召回率，以及 PVD 分类 Precision/Recall。
2. 实际配方识别准确率（数量及样品关联）。
3. 字段级 Precision/Recall，特别是数值和单位。
4. 证据有效率、漏报率和歧义率。
5. 每篇论文请求数、模型 token、耗时、失败和重试次数。

每次更改模型、提示词、PDF 解析器都运行固定回归测试，保存新旧差异报告；不得只展示几个成功案例。

## 九、阶段 8：前端、批量与可选性能抽取

前述稳定后再做：

- Web 文献列表、工艺表筛选、字段证据查看和审核队列。
- 以 DOI 或论文列表触发批量抽取；计划任务增量发现新论文。
- 性能抽取按独立 measurement 记录：Pr、2Pr、Ec、XRD FWHM、应力、粗糙度、d33、d33,f、漏电流密度。
- 每条性能必须保留测试方法、温度、频率、波形、电极几何和相关原文证据。

现阶段不引入复杂多 Agent、自建向量检索、RAG 或不必要的微服务。先让一个编程 Agent 构建清晰、可测的确定性流程，再将模型用于语义任务。

## 十、项目建议结构

    alscn-pvd-literature/
      README.md
      pyproject.toml
      config/alscn_pvd_schema.json
      config/search_queries.yaml
      app/
        cli.py
        api.py
        clients/
        discovery/
        acquisition/
        extraction/
        normalize/
        database/
        export/
        review/
      tests/
        fixtures/
        gold_standard/
      data/raw/          # gitignored
      data/structured/   # gitignored
      docs/Agent_Development_Plan.md

文件较少时允许合理扁平化，不要为形式上“分层”创建大量空包。所有 API key 使用环境变量，不入库、不写日志。

## 十一、每阶段交付与命令接口

推荐支持以下同等 CLI 行为（参数名称可调整）：

    python -m app.cli search --query "AlScN sputtering" --max-results 100
    python -m app.cli ingest --doi "10.1038/s41467-025-68221-2"
    python -m app.cli extract --doi "10.1038/s41467-025-68221-2"
    python -m app.cli validate --doi "10.1038/s41467-025-68221-2"
    python -m app.cli export --format xlsx --output literature.xlsx

每阶段必须提交代码、使用说明、测试记录、失败案例和简短验收结果。分类处理网络超时、429、权限阻断、全文缺失、PDF 乱码、AI JSON 错误、证据冲突、单位歧义。允许有限修复和重跑，但保护已审核数据和人工更正。

## 十二、可直接给 Codex 的启动指令

> 你是 AlScN-PVD 文献数据项目的主开发 Agent。先阅读 Agent_Development_Plan.md、alscn_pvd_schema.json 和 Excel 的字段字典。阶段 1 的字段规范已经确立，不允许随意改动；如确有新增字段需要，提出版本化 schema migration。
>
> **现在只实现阶段 2（文献发现）。** 编写 OpenAlex/Crossref 检索 adapter、关键词扩展、分页、限流、去重、标题摘要初筛、日志、数据库模型和可运行 CLI，附测试与示例结果。不要直接进行大规模全文下载，也不要在这一步开发 React 前端或多 Agent 自治系统。用户内部 147 片样品的工艺数值不得提交给外部模型。
>
> 交付前报告：实现模块、运行命令、测试通过情况、已知代表论文的检索覆盖、失败/限制、下一阶段接口。后续阶段按本计划依次实施。所有非空实验工艺数值必须有可追溯的原文证据；缺失不得猜测补齐。

## 十三、待用户确认的原表字段

1. Ar2(sccm) 是否实际指 Ar 流量？
2. 靶截距(cm) 是否实际指靶—基底距离？
3. AlSc(W)/电压、AlN(W)/电压的 W/V 是如何根据方案和机台区分的？

这三个问题只影响将内部样品与文献库精确合并比较的过程，不阻止按规范开展外部文献提取。未确认前保留 ambiguity 标记。
