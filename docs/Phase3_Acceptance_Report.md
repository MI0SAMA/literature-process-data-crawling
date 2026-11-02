# 阶段 3 验收报告：全文与补充材料获取

- 项目：AlScN-PVD 文献工艺数据库
- 阶段：3（阶段 4 尚未开始）
- 日期：2026-10-29
- 环境：Windows；Python 3.13；SQLite 3；`requests` 代理；`lxml`、`pypdf`、ReportLab
- 网络：认证代理 `<proxy-host>:<port>`，凭据仅通过 `ALSCN_PROXY` 环境变量传入
- 证据：`docs/phase3_acceptance_evidence.json`
- 章节审计：`docs/phase3_deposition_sections.csv`
- 原始日志：`docs/phase3_acceptance_stdout.txt`
- 测试记录：`docs/Phase3_Test_Record.md`
- 失败案例：`docs/Phase3_Failure_Cases.md`

## 1. 验收结论

| 验收项 | 目标 | 实际 | 结论 |
|---|---|---|---|
| 合法可访问全文 | 10 篇 | **10 篇**（主文档 JATS 4、HTML 2、PDF 4；含 1 篇 partial） | 通过 |
| 定位主要沉积方法章节 | ≥ 9 篇 | **9 篇** | 通过 |
| 无全文时记录状态 | metadata_only / fulltext_unavailable | 两种状态均有 live 记录与测试 | 通过 |
| 不越权访问 | 不绕过付费墙/登录/验证码 | 仅使用 OpenAlex OA 入口；403/挑战页记录后停止 | 通过 |
| 文件可追溯 | URL、hash、大小、时间、版本 | `documents` 表 + `data/raw` 内容寻址文件 | 通过 |
| 章节/表格/参考文献 | 分离并定位 | 138 个 sections、5 张表；参考文献不参与方法定位 | 通过 |

**结论：阶段 3 通过验收。** 2026-10-29 用 live 网络获取 10 篇 OA 全文，9 篇定位到
沉积方法/实验/生长章节；1 篇（IntechOpen 图书章节）PDF 仅得到正文，未识别出独立
方法标题，状态为 `partial`。所有 403/挑战页/404 均记录失败原因，没有使用登录、
Cookie、验证码绕过或付费墙绕过。

## 2. 实现范围

| 任务书要求 | 实现 |
|---|---|
| DOI 解析 | `DoiHandleResolver`（doi.org HEAD，记录最终 URL/状态）；`OpenAlexDoiResolver`（DOI→OA 元数据） |
| 尝试 OA HTML / PMC-JATS / PDF / SI | `AcquisitionService` 按 JATS → HTML → PDF → SI 顺序；仅使用 OpenAlex `is_oa`/`oa_url`/`best_oa_location`/`locations` |
| HTML/JATS 优先 | JATS 解析 `app/acquisition/jats.py`；HTML 解析 `app/acquisition/html.py` |
| PDF 用 GROBID；表格单独解析 | `GrobidClient` + TEI 解析；无 GROBID 时用 `pypdf` 标题启发式 fallback，记录 parser；表格存 `document_tables` |
| 记录 URL、版权/访问状态、时间、hash、类型、版本 | `documents` 表：source/final URL、access_status、http_status、fetched_at、file_hash、byte_size、document_type、version、license |
| 定位 Methods/Experimental/Deposition/Fabrication/Growth/Sputtering/Supplementary Methods | `app/acquisition/sections.py` 分类 + 打分；正文 Methods 优先，SI 可回退 |
| 检查结果部分/补充材料工艺表 | 表格存储；定位器可接受 results/SI 候选，但摘要/结论/参考文献不参与 |
| 正文与参考文献分离 | JATS `ref-list`→`references`；HTML references section 单独；PDF 进入 References 后停止标题识别 |
| 失败保留原因 | `documents.error_kind/error_message`、`acquisition_runs.metadata.errors`、CLI 非零退出 |
| 不得返回看似成功的空白记录 | OA 页面无正文（abstract/landing）→ `abstract_only`；全部失败→`fulltext_unavailable` |

## 3. 数据模型

新增 4 张表（代码见 `app/storage.py`）：

- `acquisition_runs`：DOI、状态、主文档、方法 section、错误、metadata JSON。
- `documents`：`document_id`、paper_key、doi、类型（jats/html/pdf/supplementary）、版本、
  source/final URL、access_status、http_status、content_type、file_path、file_hash、
  byte_size、fetched_at、parser/parser_version、license、is_primary、parent_document_id、
  error_kind/error_message。
- `document_sections`：section_type、heading、text、char/page 范围、order、locator、
  is_reference、score。
- `document_tables`：label、caption、text、locator、page、order。

文件按 `data/raw/<safe_paper_key>/<sha256前16位>_<filename>` 内容寻址保存；
同一内容不会重复写。原始文件不入库、不提交。

## 4. 解析与定位策略

1. **JATS**：PMC/Europe PMC XML；`front/article-title`、`abstract`、`body/sec` 递归、
   `ref-list` 单独存 references、`table-wrap` 单独存表、`supplementary-material` 提取 SI URL。
2. **HTML**：`article`/`main` 容器；移除 nav/header/footer/aside/script；按 h1–h6 切分；
   去掉表格/图内重复文本；识别 references、SI 链接；增加 `content_scope`：
   - 有 Introduction/Methods/Results/Conclusion 等结构且正文 ≥200 字符 → `fulltext`；
   - 否则 → `abstract_only`，服务不当作全文。
3. **PDF**：
   - 有 `GROBID_URL` 时调用 GROBID `processFulltextDocument`，解析 TEI。
   - 无 GROBID 时 `pypdf` 提取文本 + 标题启发式；识别 References 后停止新章节，
     避免把参考文献条目当作方法标题。
   - 记录 `parser=pypdf_heading_heuristic`，不伪装成 GROBID。
4. **方法定位**：对每个 section 打分（方法/沉积/生长标题 + AlScN/ScAlN + sputter +
   功率/气压/流量/温度/靶材等工艺词 + 数值单位）；摘要、结论、引言、参考文献、
   acknowledgements、标题页不参与；优先 Methods/Experimental/Deposition/Fabrication/
   Growth/Sputtering；无正文方法时允许 Supplementary Methods。
5. **SI**：从主文档提取，最多 `--max-si` 个；HTML/PDF 解析，ZIP/XLSX/DOCX 原样保存；
   403/挑战页/404 记录，不重试越权。

## 5. 10 篇 live 验收

| DOI | 状态 | 主文档 | 解析器 | 方法章节 | 表 |
|---|---|---|---|---|---|
| 10.3390/biomimetics11080519 | ok | jats | jats | 2. Experiments | 0 |
| 10.3390/mi13101698 | ok | jats | jats | 2.2. Fabrication of AlScN Films | 3 |
| 10.1038/s41467-025-62904-6 | ok | jats | jats | HfN0.4/AlScN/AlN/AlScN/TiN capacitor fabrication | 0 |
| 10.1063/5.0267904 | ok | pdf | pypdf_heading_heuristic | METHODS | 0 |
| 10.1557/s43579-026-00960-9 | ok | html | html | Growth | 0 |
| 10.3390/mi14051065 | ok | jats | jats | 2.3. Fabrication | 2 |
| 10.5772/intechopen.1015616 | **partial** | pdf | pypdf_heading_heuristic | —（未识别独立方法标题） | 0 |
| 10.1557/s43579-025-00709-w | ok | html | html | Experimental methods | 0 |
| 10.48550/arxiv.2512.24455 | ok | pdf | pypdf_heading_heuristic | Methods | 0 |
| 10.48550/arxiv.2501.02101 | ok | pdf | pypdf_heading_heuristic | Methods | 0 |

统计：

- 10/10 获得全文；9/10 定位到沉积方法章节（目标 9）。
- 19 个 document 记录：4 JATS、5 HTML（3 成功、2 403）、4 PDF、6 SI。
- 138 个 sections、5 张表；16 个文件有 SHA-256。
- 4 篇通过 JATS，2 篇通过 HTML，3 篇通过 PDF 定位方法（另 1 篇 IntechOpen partial）。

## 6. 状态与失败示例

- `metadata_only`：`10.1109/jmems.2022.3161055`，OpenAlex 标记 closed，未发起全文请求；
  `docs/phase3_metadata_only.txt`。
- `fulltext_unavailable`：`10.1103/physrevmaterials.5.035001`，OA 元数据存在，但
  APS/Aalto PDF 均 403、仓库页仅摘要；记录 `forbidden`/`abstract_only` 后停止；
  `docs/phase3_fulltext_unavailable.txt`。
- SI 失败：PMC bin 404、publisher 挑战页；记录 `not_found`/`supplementary_html_parse_error`。
- 图书章节 `10.5772/intechopen.1015616`：PDF 正文取得，但标题启发式未识别出
  Methods 标题，状态 `partial`，不伪造方法章节。

## 7. 已知限制

1. **GROBID 未 live 运行**：当前机器没有 GROBID 服务/容器；已实现客户端 + TEI 解析
   并用 fixture 测试。3 篇 PDF 使用 `pypdf` fallback。阶段 4 前建议启动 GROBID，
   重新跑这 3 篇，比较章节边界。
2. **PDF 表格未结构化**：fallback 只提取文本，表格在 `document_tables` 中为空；
   阶段 4 需要 GROBID/表格解析器。
3. **SI 是 best-effort**：6 个 SI 记录中部分为 404/挑战页或导航页；未绕过访问限制。
4. **图片中的数值**：未做 OCR；图中数值仍标记人工审核，不在阶段 3 自动读取。
5. **preprint/version**：arXiv 记录为 `preprint`；其余按 OpenAlex version 写
   `published`/`unknown`；未对同一论文的多个版本做去重合并。
6. **未做字段抽取**：阶段 3 只保存全文/章节/表格；recipe/sample/target/measurement
   抽取属于阶段 4。

## 8. 阶段 4 交接

阶段 4 可消费：

- `papers.paper_key` + `documents.document_type/version/parser`；
- `document_sections`：`deposition_methods` / `supplementary_methods` 作为 Pass A 的首选输入；
- `document_tables`：工艺表；
- `documents.file_hash` + `file_path`：原文定位；
- `acquisition_runs.status`：`ok`、`partial`、`metadata_only`、`fulltext_unavailable`；
- `documents.access_status`：`open_access`、`forbidden`、`blocked`、`not_found`。

阶段 4 不应把 `partial` 当成全文方法已确认；也不要把 `metadata_only` 的摘要当全文。
进入阶段 4 前建议先用 GROBID 重跑 PDF 子集，并把 `document_tables` 作为表格证据来源。

## 9. 复现

```powershell
# 单元/集成测试
python -m unittest discover -s tests -p "test_*.py" -v

# 10 篇 live 验收（凭据仅从环境变量读取）
$env:ALSCN_PROXY='http://<user>:<password>@<proxy-host>:<port>'
python tools/phase3_acceptance.py

# 单篇
python -m app.cli ingest --doi 10.3390/biomimetics11080519 `
  --db data/structured/phase3_acceptance.sqlite3 --raw-dir data/raw
```

## 10. 验收签署建议

建议状态：**阶段 3 通过（有已知限制）**。

- 10/10 合法全文、9/10 方法章节：通过；
- 文件/版本/访问状态/hash/章节/表格：通过；
- 403/挑战页/404 不越权、不伪成功：通过；
- GROBID live 与 PDF 表格结构化：待阶段 4 前补强。
