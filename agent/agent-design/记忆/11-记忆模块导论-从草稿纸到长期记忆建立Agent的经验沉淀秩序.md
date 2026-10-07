# 11｜记忆模块导论：从草稿纸到长期记忆，建立 Agent 的经验沉淀秩序

> 来源：极客时间《Agent 设计模式之美》黄佳 · 记忆：沉淀之美
> 原文链接：https://time.geekbang.org/column/article/987077 （时长 18:45）
> 课程主页：https://time.geekbang.org/column/intro/101162601?tab=catalog
> 本笔记为学习总结，版权归原作者所有。

## 一句话总结

记忆 = PRA 循环的时间维度：不只是"让 Agent 记住东西"，而是**防止长程任务在时间中漂移**——把承重信息（关键判断、依赖路径、失败方案、禁止动作）固定下来，设计一套可追踪、可审计、可纠错的经验系统。

## 开场案例：auth.py 事故（任务漂移的具体展现）

代码重构 Agent 把臃肿的 auth.py 拆三模块，前几轮发现 UserSession 与 PermissionCache 循环依赖（跨 4 文件），写出关键判断："先抽共享 types.py，**禁止先移动 UserSession**（会影响 43 个测试）"。

但交接的进度记录只写了主题和大方向："已完成：确认循环依赖…待继续：**开始移动 UserSession 相关代码**"。下一轮 Agent 读到这份自相矛盾的摘要，做了个"合理又危险"的解释：先移 UserSession 顺手补 types.py——大批文件改坏、CI 挂掉、回滚。

**教训**：长程任务在多轮会话、多次压缩、多次交接中推进，每次摘要损失细节、每次交接改变语义。最初的工程判断会漂移："先抽 types.py 禁止先移 UserSession" → "移动 UserSession 时顺手补 types.py"。记忆要留住的是**会影响后续行动的承重信息**：关键判断、依赖路径、失败方案、禁止动作、未完成假设、下一步边界。

## 记忆生命周期与四类记忆

```
context window（这一刻看见什么）
  ↓
scratchpad（这一刻正在怎么算）
  ↓
structured trace（这一轮发生了什么）
  ↓
long-term memory（哪些经验跨会话留下）
  ↓
retrieval / replay / forgetting（下次要不要取回、何时过期）
```

| 记忆类型 | 内容 | 对应模式 |
| --- | --- | --- |
| working memory 工作记忆 | 当前窗口里正在使用的信息 | 分层保留 |
| scratchpad 草稿纸 | 当前任务工作台：中间判断、工具结果、下一步计划 | （记忆工作台，最易被低估） |
| episodic memory 情节记忆 | 事件经过 | 进度追踪 |
| semantic memory 语义记忆 | 知识、文档 | RAG |
| failure memory 失败记忆 | 情节记忆里最该主动召回的一类 | 失败日记 |
| procedural memory 程序记忆 | 会做的活固化成流程 | Skill Package（反思模块讲） |

Scratchpad 例子：Anthropic "think" tool（把思考追加到日志处理顺序决策）、context editing 与 memory tool 分开"删 stale"和"存到窗外"、OpenAI reasoning tokens 默认不留在上下文需显式传回、LangGraph thread-level state + checkpointer。

**不要把模型原始思考过程（raw CoT）当业务记忆**——企业系统要的是可验证、可审计、能续接的判断，不是模型念头原样留档。

**正确写法示范**：scratchpad.write 记录 current_finding / cycle_path / tested_attempt / observed_failure / candidate_decision / **do_not_do_next**；任务收束后再把能复用、能交接、能审计的部分 memory.write 沉淀（goal/finding/decision/do_not_do/evidence/next_step）。摘要式进度记录丢掉的正是最值钱的**可执行约束**。

## 记忆解决的三个传统问题

1. **状态持久化**（对应 DB/session/checkpoint）：被打断后记得刚才在干什么。
2. **知识检索**（对应搜索索引/文档系统）：信息远超窗口容量，要有地方存、有办法取。
3. **经验累积**（最接近测试套件和事故复盘）：从过去执行里学习，下次少踩坑——传统软件没有完全等价物。

理论脉络：memex（Vannevar Bush 1945——存得多没意义，需要时能取出来才有价值）、CoALA 认知架构（Sumers 2023）、MemGPT 虚拟内存分页（2023）；2026 年已进工程产品（文件式记忆工具、thread checkpoint、时序知识图谱、写入阶段抽取压缩）。

## 四个记忆工程问题

1. **框架选哪家**：Letta（Agent 自己在 core/archival/recall 间搬运，Agentic 记忆操作系统）、Mem0（单遍层次抽取+多信号检索，整理前移到写入阶段）、Zep（时序知识图谱，表示"事实随时间怎么变"）、Anthropic Memory Tool（底层，读写持久化记忆目录）。**不建议同时上两套——记忆有单一事实来源问题。**
2. **长程 Agent 如何记忆**：论文《Episodic Memory is the Missing Piece》——不能只记抽象事实，也要记具体事件；Anthropic "Dreaming" 预览把记忆整理从会话内搬到会话间。记忆存下来后还要**整理和消化**。
3. **向量库还是文件系统**：Manus 把文件系统当外部化上下文——结构化记忆（任务清单/配置/进度）放文件系统（简单可调试可解释）；非结构化大库（历史对话/文档全文）才交给向量库/图库。
4. **Agent 自己读写还是框架强制**：健康中间态 = **Agent 提议、框架定规矩**——Agent 判断记什么，框架约束按什么 schema 记，trace 验证有没有真被用上。

> 记忆不是存储的堆叠，而是**经验的治理**：什么值得留、留多久、何时取回、何时过期。

## Memory Trace：记忆仪表盘

没有它，看不出 Agent 当时手里有没有相关记忆。排查链路：

```
should_write → memory.write → memory.retrieve → memory.use → avoid_repeat_failure
```

每环对应一类问题：没写入=沉淀问题；没取回=检索问题；取回不用=推理/规划问题；用了还错=记忆质量或适用边界问题。

排查清单：任务要它做什么 / 错在哪一步 / 依赖哪段过去经验 / 写没写 / 写了为什么没召回（没命中、被淘汰、召回后对不上）/ 没写是哪个环节漏了 / 下一版怎么改记忆 pipeline。

## 感知 vs 记忆的边界（回应 06 讲思考题）

- **感知关心空间**：这一轮会话里 Agent 看到了什么。
- **记忆关心时间**：窗口关掉之后，有用的东西怎么沉淀下来。
- 边界模糊处：长期保留的 CLAUDE.md 两边都占——想清楚边界对设计有帮助。

## 安全提醒：记忆攻击

《记忆碎片》的深意：外部记忆被选择性记录、错误解释、故意篡改时，不再是通向真相的路径，而是制造幻觉的机器。未来 Agent 攻击不只是 prompt injection，还会有**记忆注入（memory injection）、记忆投毒（memory poisoning）、记忆篡改（memory tampering）**——攻击者先把错误线索写进外部记忆，下一轮 Agent 取回时以为是"过去验证过的经验"，自然沿错误方向行动。

## 关键要点（复习用）

1. 记忆防的是长程任务漂移：留住承重信息，特别是**禁止动作和已排除方案**。
2. 四类记忆 × 四模式映射：working→分层保留、semantic→RAG、episodic→进度追踪、failure→失败日记、procedural→技能包。
3. 生命周期五段：窗口 → 草稿纸 → 结构化追踪 → 长期记忆 → 检索/重放/遗忘。
4. Agent 提议 + 框架定规矩 + trace 验证，是记忆读写权的健康中间态。
5. 结构化进度记录要写"为什么、排除了什么、下一步不能做什么"，不是只写"做了什么"。

## 思考题（课程留题）

1. 实验：同一长任务，一次给完整结构化记忆、一次只给一行摘要，对比成功率。
2. 用 Memory Trace 复盘一次 Agent 反复犯错/交接走偏：判断该改 prompt 还是补记忆链路。
3. 列出项目的十类信息（用户偏好/项目规则/任务进度/失败记录/工具返回/临时推理/代码约束/接口文档/历史对话/业务指标），标注：留窗口 / 放 scratchpad / 进长期记忆 / 进失败日记 / 过期淘汰。

## 下一讲预告

12 分层保留：工程上怎么分层，怎么决定谁留在 context、谁换出到外存。
