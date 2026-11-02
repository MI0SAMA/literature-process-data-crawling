# 阶段 3 失败案例与处理记录

原则：只使用 OpenAlex 标记的开放获取入口；遇到 403、挑战页、404、付费墙立即
记录并停止，不登录、不使用 Cookie、不绕过验证码、不伪造空成功。

## 1. 出版商 403 / Cloudflare 挑战页

**现象**

- MDPI DOI 页面 403；
- APS `link.aps.org` 403；
- ScienceDirect OA PDF 403；
- Wiley `pdfdirect` 403；
- PMC HTML 返回 “Checking your browser - reCAPTCHA”。

**处理**

- `DocumentFetcher` 将 401/403 标为 `forbidden`，不重试；
- HTML 解析器检测 “checking your browser / recaptcha / access denied” →
  `HtmlBlockedError`，服务记录 `blocked`，继续尝试其他 OA 入口（如 JATS）；
- 不提交登录表单、不携带用户 Cookie。

**结果**：部分论文通过 PMC JATS/arXiv/机构仓库取得合法全文；仍失败的记录为
`fulltext_unavailable`。

## 2. OA 元数据存在但只有摘要/落地页（已修复误判）

**现象**：Aalto 机构仓库、arXiv abs 页返回 200 HTML，但只有标题/摘要/导航。
早期版本把标题 section 当 Methods，导致 false positive。

**处理**

- HTML 解析增加 `content_scope`：必须有 Introduction/Methods/Results/Conclusion
  等结构 section 且正文足够长，才标 `fulltext`；否则 `abstract_only`；
- 服务拒绝 `abstract_only` 作为主文档，继续尝试 PDF；
- `locate_deposition_sections` 排除标题页、摘要、结论、引言、参考文献。

**结果**：Aalto/arXiv abs 页不再被当作全文；arXiv PDF 可正常 fallback。

## 3. PDF 参考文献中的 “Deposition of…” 被误当方法标题（已修复）

**现象**：IntechOpen 图书章节的 PDF 中，参考文献条目以 “deposition of aluminum
scandium…” 开头，早期 PDF 标题启发式把它当成 Methods，实际文本是参考文献。

**处理**

- PDF 解析遇到 References/Bibliography 标题后进入 `in_references`，后续行不再
  识别为新章节；
- 该 section 标 `is_reference=True`、`section_type=references`，定位器排除；
- 增加回归测试 `test_references_do_not_create_deposition_sections`。

**结果**：不再选中参考文献；IntechOpen 最终没有独立 Methods 标题，状态 `partial`。

## 4. 结论章节被误选为方法（已修复）

**现象**：arXiv 2606 的 “3. Conclusion” 含 AlScN/sputter 关键词，打分较高，
曾进入候选。

**处理**：`locate_deposition_sections` 排除 conclusion/abstract/introduction/
references/acknowledgements；增加回归测试。该论文最终被替换为另一篇 Methods
明确的 arXiv 论文。

## 5. PMC Supplementary 相对路径 404

**现象**：JATS 中 `supplementary-material xlink:href="...-s001.zip"` 是相对路径；
最初拼到 Europe PMC XML URL，404；改为 PMC `bin/` 基址后仍 404/挑战页。

**处理**

- `urljoin` 解析 SI URL；
- JATS + PMCID 时使用 `https://pmc.ncbi.nlm.nih.gov/articles/<PMCID>/bin/`；
- 404/挑战页记录 `not_found` / `supplementary_html_parse_error`；
- ZIP/XLSX 等不可解析文件原样保存到 `data/raw`，不强行解析。

**结果**：部分 SI 成功保存，部分失败但可追溯；不影响主全文验收。

## 6. DOI.org HEAD 解析 403

**现象**：部分 DOI 的 handle 解析返回 403（出版商反爬），但 OpenAlex 元数据仍给出
OA 入口。

**处理**：DOI 解析结果写入 `metadata.doi_resolution`，失败不阻断；继续使用
OpenAlex OA 入口。不会因 DOI 解析失败而绕过访问控制。

## 7. GROBID 未运行

**现象**：当前机器没有 GROBID 服务/容器，`GROBID_URL` 未设置。

**处理**：

- 实现 `GrobidClient` + TEI 解析 + fixture 测试；
- 服务在 `GROBID_URL` 存在时优先 GROBID，否则 `pypdf` 标题启发式；
- `documents.parser` 明确记录 `pypdf_heading_heuristic`，不伪装成 GROBID；
- PDF 表格在 fallback 中为空，阶段 4 前建议补 GROBID。

## 8. 10 篇验收中的 partial

**现象**：`10.5772/intechopen.1015616` 取得 PDF 全文，但 PDF 标题启发式未识别出
独立 Methods 标题，状态 `partial`。

**处理**：保留全文与章节，不伪造方法章节；验收按 9/10 方法章节通过。
阶段 4 可用 GROBID 重跑该 PDF，或人工指定章节。

## 9. 未做/明确不在阶段 3 范围

- 不做 recipe/sample/target/measurement 字段抽取（阶段 4）；
- 不做图片 OCR；图中数值仍标记人工审核；
- 不做多版本合并（published/accepted/preprint 分别记录）；
- 不做前端审核页、批量调度、性能抽取。
