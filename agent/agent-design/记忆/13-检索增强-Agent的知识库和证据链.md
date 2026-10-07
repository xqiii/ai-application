# 13｜检索增强：Agent 的知识库和证据链

> 来源：极客时间《Agent 设计模式之美》黄佳 · 记忆：沉淀之美
> 原文链接：https://time.geekbang.org/column/article/989200 （时长 27:49）
> 课程主页：https://time.geekbang.org/column/intro/101162601?tab=catalog
> 本笔记为学习总结，版权归原作者所有。

## 一句话总结

生产级 RAG 不是"相似文本召回"，而是**知识供应链**：离线把文档做成可发布、可回滚、可版本控制的索引资产，在线按任务约束取回带出处的证据——目标是"可用、可信、可追溯的证据取回"（source/version/scope/citation 四件套 + RetrievalTrace）。

## RAG 为什么算记忆不算感知

- 感知问：这一次推理前哪些材料进 context；记忆问：长期知识怎么保存、索引、更新、过期、回滚。RAG 重心在写入侧（索引侧）：知识源登记、解析、切块、向量化、索引版本、权限范围。只关注读取侧，RAG 退化为喂几段相似文本。
- 双轴坐标演化：**naive RAG = 记忆 × 链式**（一次性流水线：文档→切块→索引→召回→重排→注入→生成）；**Agentic RAG = 记忆 × 循环**（检索→评估→改写查询→再检索的回路，CRAG/Self-RAG 是实例）。同一模式随工程成熟度跨格——双轴矩阵是描述性的。

## 薪酬 SaaS 案例：证据与真值分属两个平面

用户问："上海市场部 6 月薪资快照异常，哪些可自动通过哪些要人审？"——混着两类完全不同的信息：

| | 机械状态 | 业务证据 |
| --- | --- | --- |
| 例子 | 员工 id、批次 id、审批单号、社保基数 id | 6 月薪酬规则版本、扣款口径、审批阈值、人审规则 |
| 来源 | 工具返回 + SessionState，程序确定性绑定 | 制度文档/审批规则/历史政策 → RAG |
| 要求 | 按位精确 + provenance | 当前任务真正适用的那一条规则 |

**机械状态被污染的事故**：RAG 召回"上月奖金异常 FAQ"，模型顺手把里面的 payroll_batch_id 当成本月批次 id。LLM 不能凭印象复述 id，更不能从 RAG 里找"看起来像"的 id。

**执行型 Agent 分工**：RAG 查政策规则（可引用证据）→ SessionState 管机械真值 → Orchestrator 编排 → Verification Gate 高风险动作前检查。两条线在结构化推理处合流。**RAG 能告诉 Agent"规则怎么解释"，不能替它生成"对哪个批次执行哪个动作"的关键参数。**

## RAG 的来龙去脉与行业反思

- 血缘：2020 Patrick Lewis 提出 RAG（NeurIPS）；"取"继承信息检索（TF-IDF/BM25/向量空间，七八十年代）；"证据"继承企业数据工程（data provenance、ETL 可复现、hash 校验、蓝绿部署）。
- 关键转变：信息检索默认屏幕前有人判断哪条能用；Agent 拿到召回直接往下执行——**"相关"远远不够，Agent 要的是当前任务、租户、时间点、权限范围内真正适用且可引用的证据。相关性是给人看的排序，证据是给机器用的依据。**
- 2025 反思：Chroma《Context Rot》测 18 个模型——输入越长、语义相似度越低，表现退化越明显；ICML 2025 LaRA 基准（2326 用例）：RAG vs 长上下文取决于模型规模/长度/任务/召回质量，没有通吃。

## RAG 难在哪：五个环节叠加

换 embedding、调 chunk size、top-K 5→20 只能让 demo 好看一点。生产失败是叠加的：
1. **知识源没治理**：文档过期、互相矛盾、没 owner、概念多名字——embed 进去矛盾不会消失，只会更难排查。
2. **切块切坏**：条款/表格/代码注释/页眉页脚被硬切，召回后看不出属于哪章哪版哪条。
3. **metadata 太薄**：只存 doc_id+text，但业务检索需要 effective_date/product_version/region/permission_scope/source_owner/document_status。
4. **只看语义相似**：产品代码/合同编号/错误码/条款号靠 embedding 会漏——需要 BM25+稠密混合检索。
5. **答案没引用链**：出错分不清是召回、重排、生成还是知识源的问题。

## 企业 RAG 落地顺序

**第一件事不是选技术栈，是看知识源**：拿一批真实问题做证据表（正确答案依赖什么证据、有没有 owner/版本/生效日期/权限）。填不出来 = "把企业知识的混乱自动化"。

顺序：① 收集真实问题+补 owner/版本/生效日期/权限 → ② 定义 chunk schema、citation schema、index manifest → ③ 建 candidate index 用 golden questions 回归 → ④ 接入答案引用、RetrievalTrace、索引版本监控 → ⑤ 再考虑 Agentic RAG/多轮检索/LLM Wiki。

## 索引建设六步（先有 manifest 再有 vector DB）

1. **知识源登记**：doc_id、source_uri、owner、permission_scope、effective_from/to、source_hash、document_status。
2. **可复现切块**：记录 parser_version、chunker_version、chunk_index、text_hash，chunk_id 稳定生成（hash 拼接）。
3. **ingestion manifest**：corpus_version、parser/chunker 版本、embedding 模型与维度、混合索引版本、collection 名、golden_set_passed（没过不许上线）。
4. **候选索引 + 灰度**：先建 candidate，用 golden questions（业务上最容易出错的关键问题）回归——检查召回命中、引用可追、权限过滤、过期排除。**RAG 索引不是建出来就算完成，要先证明自己能回答真正重要的问题。**
5. **别名蓝绿切换**：应用只认稳定别名（payroll_rag_current），底层 collection 动态切（Milvus alias；LanceDB 版本化写入 + time-travel）。
6. **删除/退休/备份/回滚**：很多"新文档没生效"事故根子是旧文档还在召回——被替换的规则要么删除要么用 status/effective_to/permission_scope 严格过滤。每版索引要能回答：上一版在哪、用的什么参数、能否快速切回、误入库能否定位清掉。

## 工程现场三切片

**切片一 Anthropic Contextual Retrieval**：chunk 脱离语境没法用（"本责任在等待期后生效"——哪款产品？哪版条款？等 90 还是 180 天？）。做法：每个 chunk 前加短上下文再进 embedding 和 BM25。官方数据：top-20 检索失败率 5.7% → 3.7%（Contextual Embeddings）→ 2.9%（+Contextual BM25）→ **1.9%（+reranker，相对降 67%）**。适合条款合同/财报研报/企业制度 FAQ；材料本身短而独立收益小。同题方案：Jina late chunking（长上下文模型先嵌全文再切块，文档越长增益越大）。

**切片二 LlamaIndex 转型文档工程**：2026 年不再自称 RAG framework，转向 agentic document processing——很多 RAG 失败在文档进入系统前就埋下（PDF 表格 OCR 乱、页眉页脚混入、双栏顺序错、附件断开、图表只剩"图 4.2"）。解析层是 RAG 下游幻觉和断引用的根因。自检：抽 50 条真实问题人工检查材料形态——解析对吗？表格行列保留吗？chunk 能追到页码章节吗？答不上来就别调 top-K。

**切片三 执行型 Agent 中的 RAG**：闲聊直接结束；analyze 适合 RAG（检索政策/历史口径给分析）；resolve（执行）RAG 只辅助——员工 id/批次 id/金额/状态流转由 SessionState 和工具结果托管，动作过 Verification Gate。

## 知识检索技术选型卡

| 路径 | 适用 |
| --- | --- |
| RAG | 语义记忆层的证据取回（分析型问题） |
| 结构化查询 | **看起来像知识检索、实际是状态读取**（批次什么状态、审批过没过、基数是多少）——优先查业务系统，别绕 RAG。让 LLM 从召回文字里"读"金额或 id 是执行型 Agent 最常见事故源 |
| Agentic Search | 见 09 讲：代码探索、隐私敏感 |
| LLM Wiki（Karpathy 2026-04） | 原始资料入库时一次性编译成结构化可交叉引用的 markdown 页，知识"编译一次、持续更新"——补 RAG 不擅长的长期知识策展和复利积累（大型企业还要补权限/审计/协作） |
| 记忆框架（Mem0/Zep/Letta） | 跨会话用户状态演化，与一次性证据召回互补，别混用（单一事实来源问题） |

## 证据契约（Evidence Contract）

一条可用证据四件套：**source**（来源）、**version**（文档/索引版本、生效/废止时间）、**scope**（地区/租户/角色/权限）、**citation**（页码/章节/条款号/chunk_id/hash/index_version）。

检索请求从裸语义查询升级为带业务约束的证据请求：semantic_query + filters（tenant_id/region/payroll_month/rule_status/permission_scope）+ required_citation。**这一步是 naive RAG 和企业级 RAG 的分水岭。**

Schema 要点：IndexManifest（构建参数+golden_set_passed）；EvidenceChunk（不只 text+embedding，还有 hash/context/effective 期/租户/权限/status，`is_usable_on()` 提醒召回到≠能用）；EvidenceRequest（mechanical_state_refs 记录依赖哪些机械状态但绝不交给 RAG 生成）；RetrievalTrace（request/index_version/candidates/reranked/used_in_answer/missing_reason）。

## RetrievalTrace：检索仪表盘

一次错误回答可能错在四个完全不同的地方：
1. 没召回到正确证据 → 召回问题（query/索引/过滤的锅）
2. 召回了但被重排压下去 → 重排问题
3. 用了但是过期版本 → 知识源问题
4. 材料没问题模型没采纳 → 推理/规划问题

没有 trace 只能靠猜；有了它，能从一次错误回答反查到哪一版索引的哪个 chunk——**这是企业 RAG 和玩具 RAG 的分界线**。

## 关键要点（复习用）

1. RAG = 知识供应链：货从哪来（source）、是不是这批（version）、能发到这个区域吗（scope）、有没有凭证（citation）、能不能召回批次（trace）。
2. 先治理知识源，再建索引：否则是把企业知识的混乱自动化。
3. 索引是软件资产：有 manifest、候选版本、回归验证、蓝绿切换、回滚。
4. 机械状态归 SessionState，业务证据归 RAG，决策点合流且都带 provenance。
5. 长程 Agent 的痛点是每步合理合起来跑偏——RAG 的责任是减少"凭印象判断"。

## 思考题（课程留题）

1. 挑 20 条真实用户问题审计：正确答案的证据在知识源里吗？有 owner/版本/生效日期/权限吗？
2. 召回的是"当前任务适用的证据"还是"语义相似的材料"？能从一段回答追回页码/条款号/索引版本吗？
3. 哪些信息该走 RAG、哪些必须走结构化查询或 SessionState？有没有 id/金额/批次号正被 RAG 或 LLM 自由文本悄悄污染？

## 下一讲预告

14 进度追踪：长任务跑到一半，Agent 怎么记住自己做了什么、为什么这么做、下一步怎么接——11 讲 auth.py 事故的对症药。
