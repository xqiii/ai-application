# 03｜双轴框架（下）：理解与使用 Agent 设计坐标系

> 来源：极客时间《Agent 设计模式之美》黄佳 · 范式觉醒
> 原文链接：https://time.geekbang.org/column/article/980767 （时长 17:13）
> 课程主页：https://time.geekbang.org/column/intro/101162601?tab=catalog
> 本笔记为学习总结，版权归原作者所有。

## 一句话总结

双轴必须"正交"才能从分类表升级为设计坐标系；7×6=42 格矩阵放 28 个模式、留 14 个空格（空格是设计判断）；配 Pattern Selection Card 三步选型法、双轴评审五问、Compound Error 公理三大工程工具。

## 为什么双轴必须正交

正交 = 纵轴和横轴各自回答不同问题，彼此不能互替。不正交就会退化成"分类表"，到不了"设计坐标系"。

- **同一认知功能，换拓扑 → 工程后果完全不同**。例：推理×链式（串行分解作答，成本延迟可预测，失败模式是早期分解错则全链白费）vs 推理×循环（自我修正，但 token 预算、等待时间、停止条件、错误恢复路径全变了）。
- **同一拓扑，换认知功能 → 含义完全不同**。例：编排在推理里叫 Plan-and-Execute（拆任务），在治理里叫 Observability Harness（收集 trace/日志/指标），在协作里接近 manager-worker。只说"我们用了 Orchestrator-Workers"分不出是推理、协作还是治理——三者的错误模式分别是任务拆错、角色边界错、观测信号遗漏。

**模式完整的名字 = 功能 × 拓扑**，这样才有唯一地址和可讨论的工程后果。认知功能决定问题类型，执行拓扑决定传播路径。

## 42 格与 28 模式

7 脉 × 6 式 = 42 格；当前版本放入 28 个模式，留下 14 个空格。

**模式准入两条件**：① 已在真实生产或准生产系统中被使用过（不只是论文概念）；② 有可命名的边界和可复用的骨架（能在架构评审会上被讨论、挑战、实现）。

**空格是设计判断，不是没想全**，分三类：
1. 结构上不成立：如感知×层级——感知不是层级委派问题，硬塞会与协作×层级混淆。
2. 已被其他格覆盖：如反思×编排——容易退化成 Generator-Critic 变体。
3. 未来研究方向：如记忆×并行（多 Memory Store 并行查询+投票仲裁），还没形成稳定 production 形态。

> 这张图追求**可信度，不是覆盖率**。GoF 23 模式也不是被填满的数学矩阵。

## Pattern Selection Card：三步选型法

**第一步 ASSESS（对七脉打分）**：七个认知功能各打 None / Light / Heavy。不要一上来就讨论框架、几个 Agent、几个工具。

**第二步 ROUTE（判主拓扑）**：
- 低协作 + 短任务 → Chain / Route
- 中等复杂 + 多步骤 → Orchestrate / Loop
- 多专家 + 宽任务 → Parallel / Hierarchy
- 高风险动作 → Governance 优先（Route / Chain / Hierarchy 承载）

**第三步 SELECT（查矩阵）**：每个 Heavy 功能至少选一个模式；第一版总模式数控制在 **3~7 个**，超了就合并或降级非关键功能。第一版的目标是最小可行稳定组合，不是炫技。

**示例：代码评审 Agent（Argus）60 秒选型**

| 七脉 | 强度 | 落格 |
| --- | --- | --- |
| 感知 | Heavy | 感知×路由：上下文分诊 Context Triage（读 diff/文件/测试/规范，并决定不读什么） |
| 记忆 | Light | 只需 short-term state（看过哪些文件、哪些假设被证伪），暂不引入长期记忆 |
| 推理 | Heavy | 复杂度路由 或 结构化推理（判断 bug risk、设计风险、测试盲区） |
| 行动 | Light~Medium | 第一版只读+跑测试；未来自动提交 patch 时升级 Plan-and-Execute + 治理 |
| 反思 | Heavy | 生成评审 Generator-Critic（每个 finding 查证据、查误读、查可复现），可升级 Self-Heal |
| 协作 | Light | 一个 Agent 能跑通就不上多智能体；PR 大时按安全/性能/测试分专家 |
| 治理 | Light 但必须有 | 只读也要 trace 读写证据；能写代码/发评论/触发 CI 就必须加审批门控 |

→ 第一版选 3 个模式：**Context Triage + Structured Reasoning / Complexity Routing + Generator-Critic**。

## 双轴评审法：五个锋利的问题

1. 这个 Agent 的七脉状态是什么（None/Light/Heavy）？先澄清认知需求，再争技术方案。
2. 每个 Heavy 功能落在哪个拓扑？说不清 = 团队还没真正设计，只是在堆能力。
3. 主要错误传播路径是什么（级联/分派/聚合/拆错/复合/泄漏）？直接带出测试策略。
4. 哪些格子刻意留空？空格必须有理由（不需要 vs 忘了 vs demo 心态）。
5. v1 → v2 的升级路径是什么（Chain→Loop？单 Agent→并行专家？只读→写+审批门控）？

价值：把评审从"感觉不够稳"变成"推理×循环没有停止条件、治理×路由没有审批门、协作×层级没有子代理隔离"这样的具体工程项。

## Compound Error 公理

单步 95% 正确率听起来很高，但错误会复合：10 步任务成功率 ≈ 0.95¹⁰ ≈ 60%；20 步 ≈ 36%。每多一个节点就多一个出错点，每多一轮循环就多一次偏航机会——这就是 Anthropic 强调 simple, composable patterns 的原因。

选拓扑不仅要看功能，还要看错误如何复合，按拓扑对症下药：串行→缩短链、强化中间 schema；路由→classifier 可观测可回退；并行→设计 merge logic；编排→验证 plan；循环→设停止条件；层级→隔离与权限继承控制。

**应对复合错误四条路**：
1. **减少步数**——能一次可靠完成不要拆十步。
2. **提高单步质量**——上下文给准、工具描述写清、schema 约束明确；95%→99% 对长链收益巨大。
3. **加 verification**——在中间状态就加入 Reflection 或外部 checker。
4. **fail fast**——明显错了就停，别带着脏状态继续生成；Agent 世界也需要 Circuit Breaker，跳闸对象从服务调用变成推理轨迹。

> 拷问每个模式：它到底是在**减少步数、提高单步、增加校验，还是让系统更早失败**？四个都不符合，它可能只是装饰。

## 关键要点（复习用）

1. 模式完整名字 = 功能 × 拓扑，两轴合起来才是唯一地址。
2. 矩阵 42 格 28 模式 14 空格；空格是判断：不成立 / 已覆盖 / 未来方向。
3. 选型三步 ASSESS → ROUTE → SELECT；第一版 3~7 个模式。
4. 复合错误是硬约束：减少步数、提高单步、加校验、fail fast。
5. 双轴框架的原创性不在新名词，而在**给已有名词定坐标**——让大家"吵得更准"，而不是"背得一样"。

## 思考题（课程留题）

1. 把你正在做的 Agent 七脉打成 None/Light/Heavy，哪一脉最被低估？
2. 把团队说过的某个工作流拆成双轴坐标：是 Reasoning × Orchestrate 还是 Collaboration × Hierarchy？
3. 选一个 Loop 型设计写出停止条件；写不出来很可能还不能上线。
4. 从矩阵找一个空格，判断它属于哪类（不成立 / 已覆盖 / 未来方向）。

## 参考资料（课程列出的）

- 双轴论文：A Two-Dimensional Framework for AI Agent Design Patterns: Cognitive Function × Execution Topology
- 《Designing AI Agents》（Manning）
- Anthropic：Building Effective Agents / Claude Code Subagents / AI-orchestrated cyber espionage campaign 披露
- Google：ADK 技术概览、Sequential agents；OpenAI：Agents SDK Handoffs / Guardrails / Tracing
- LangChain：LangGraph Send / conditional edge API
- 论文：Reflexion（Shinn et al.）、CoALA（Sumers et al.）

## 下一讲预告

04、05 讲把双轴框架放到 8 个开源 Agent 框架源码上做实证：Claude Code、Codex CLI、Aider、OpenCode、OpenClaw、Hermes、DeerFlow、OpenHands——逐格对照每个框架占用哪些格、留空哪些格。
