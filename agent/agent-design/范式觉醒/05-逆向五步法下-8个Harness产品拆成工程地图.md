# 05｜逆向五步法（下）：8 个 Harness 产品拆成工程地图

> 来源：极客时间《Agent 设计模式之美》黄佳 · 范式觉醒
> 原文链接：https://time.geekbang.org/column/article/982581 （时长 19:40）
> 课程主页：https://time.geekbang.org/column/intro/101162601?tab=catalog
> 本笔记为学习总结，版权归原作者所有。

## 一句话总结

用逆向五步法横切 8 个真实 Agent Harness，提炼出八种工程性格、五个共同地基（主循环 / 上下文管理 / 工具注册执行 / 状态账本 / 治理边界）和三种工程性格分类——没有万能架构，只有按场景的 trade-off。

## 每个框架只问四个问题

不做产品评测、不列功能大全：① 主要解决什么问题？② 抓住了什么工程关键点？③ 2026 年最值得观察的新变化？④ 用五步法读它从哪里下手？

## 八个框架的工程地图

| 框架 | 核心不变量 / 最值得学 | 五步法入口 | 双轴矩阵强项 |
| --- | --- | --- | --- |
| **Claude Code** | Harness 比模型更重要："聪明只是入场券" | 工具注册执行 / 高风险权限流程 / 子 Agent 上下文隔离 / 结果回流 | Action×Route、Governance×Route、Collaboration×Hierarchy、Perception×Chain |
| **Codex CLI** | 协议层让 Agent 跨 surface（core/protocol/sandboxing/exec policy 分层，Cargo workspace 积木化） | 先看"通信规则"（统一消息格式）和"执行边界"（哪些动作允许/限制/隔离） | Governance×Hierarchy、Action×Route、Memory×Chain、Collaboration×Route |
| **Aider** | Git 是最小可靠账本：编辑/diff/commit/回退都在账本里；repo map = 感知的工业化表达 | base_coder.py、repomap.py、repo.py | Perception×Orchestrate/Chain、Action×Chain、Governance×Chain、Reflection×Loop |
| **OpenCode** | LSP 一等公民：grep 告诉你字符串在哪，LSP 告诉你符号真正指向哪——感知能力升级 | session、Agent、internal/lsp、permission | Perception×Route、Action×Route、Governance×Route、Collaboration×Route |
| **OpenClaw** | 个人助手核心是控制面（Gateway）：跨 channel/身份/权限域的本地控制面 | gateway、channels contract、workspace、memory-host-sdk、skills 文档 | Action×Route、Memory×Route、Governance×Route、Collaboration×Parallel |
| **Hermes** | 会成长的 Agent 靠程序性记忆：Working memory + Search Memory + Procedural Skills 三层 | skills hub、memory setup、state、gateway | Memory×Hierarchy、Reflection×Hierarchy、Action×Route、Governance×Chain |
| **DeerFlow** | Multi-agent 的重点是边界：难的不是派工是收回（执行隔离、结果聚合、memory 回写、失败重试）；测试文件直接暴露边界意识 | agent/skill/memory router、sandbox/subagent tests | Collaboration×Hierarchy、Action×Orchestrate、Governance×Hierarchy、Memory×Route |
| **OpenHands** | 事件流是软件 Agent 的黑匣子：append-only log 承担 replay/debug/审计/恢复；sandbox provider 可替换（Docker/Process/Remote） | event service、sandbox service、conversation service | Memory×Chain、Governance×Orchestrate、Action×Hierarchy、Collaboration×Parallel |

### 关键洞察摘录

- **Claude Code**：工程量大部分花在"让模型行动前后都有轨道"——行动前控制能看什么用什么，行动中控制怎么执行是否确认，行动后控制结果回流、错误修正、过程审计。趋势：代码 Agent 从 CLI 进入 IDE、远程环境、CI 和企业流程。
- **Codex CLI**：Agent runtime 从"应用里的功能"变成平台级协议——同一 Agent 要跑在终端/编辑器/桌面/Web/CI/远程 runner，协议层变成 Harness 的脊椎。
- **OpenClaw 的多通道误读**：能接 WhatsApp/Slack/Discord 只是适配器多。真正的工程问题：不同 channel 是否同一身份？记忆能否跨 channel？哪些动作必须本机确认？哪些记忆只留本地？——本质归 Memory 和 Governance。个人助手竞争点从"回复更像人"转向统一控制面。
- **Hermes 的记忆演进**：从向量检索往程序性资产演进——只记住事实不够，还要记住做法；把 trace 里的失败转成下一版 skill。长期价值 = 把经验压缩成技能，把技能变成下一次行动的起点。
- **DeerFlow**：multi-agent 做成规划师/研究员/执行员互相聊天 = 角色扮演；Lead 拆任务只是开始，Sub-Agent 执行、沙箱隔离、结果聚合、memory 更新、失败重试才决定是不是工程系统。
- **OpenHands**：执行型 Agent 底线——没有事件账本难复盘，没有沙箱边界难授权，没有 replay 难把失败变成工程知识。事件日志和沙箱接近基础生命维持系统。

## 五个共同地基

1. **主循环**：把输入、模型调用、工具结果、状态更新串起来；没有主循环，Agent 只是一次 LLM call。
2. **上下文管理**：Claude Code 装配+隔离、Aider repo map、OpenCode LSP、Hermes/OpenClaw memory、DeerFlow skill 渐进加载——都在回答"有限 context 里放什么"。
3. **工具注册与执行**：不管叫 tool/skill/runtime/action/provider，本质都是把模型意图翻译成可控外部动作。
4. **状态账本**：Aider 用 Git、OpenHands 用 Event System、Hermes 用 memory/state、Codex 用 protocol/history、OpenClaw 用 gateway/session。没有账本，长任务不可恢复、错误不可复盘。
5. **治理边界**：Permission、sandbox、approval、tool restriction、channel policy、memory scope、audit log——穿过工具、状态、协作和执行环境的横切机制。

**选型五问**：主循环清楚吗？上下文管理是显式机制还是靠 prompt 硬撑？工具调用有没有 schema、权限和失败处理？状态可恢复、可 replay、可审计吗？高风险动作的边界在哪里？答不上这五问，不具备进生产的条件。

## 三种工程性格

| 性格 | 框架 | 核心矛盾 | 最重的脉 |
| --- | --- | --- | --- |
| 开发者工具型 | Claude Code、Codex CLI、Aider、OpenCode | 如何在开发者已有工作流里行动，不另造世界 | Perception、Action、Governance |
| 个人助手型 | OpenClaw、Hermes | 如何成为长期伴随的控制面，摆脱一次性聊天窗口 | Memory、Action、Governance |
| 重执行/多 Agent 型 | DeerFlow、OpenHands | 任务拆开又不让执行失控 | Collaboration、Action、Governance、Observability |

结论：没有万能架构。做代码助手不必学 channel gateway；做个人助手必须学 OpenClaw/Hermes 的 memory 和 identity；做云端执行 Agent 绕不开 OpenHands/DeerFlow 的 sandbox 和 event 体系。

## 范式觉醒模块收官

- 01：为什么 Agent 时代需要新模式
- 02~03：新模式如何落到双轴坐标
- 04~05：如何把真实框架逆向成工程地图

手里有范式、有坐标、有"拆机"方法，从下一讲开始进入每个认知功能内部，拆开具体模式看落地。

## 思考题（课程留题）

1. 选最熟的框架，先写预期的 5 个地基再验证。
2. 任选 Aider / OpenHands / Hermes 画不超过 10 框的主循环图。
3. 选一个组件同时归七脉和双轴矩阵，写清为什么不是另一个格子。
4. 团队的 Agent 项目有没有状态账本？没有的话失败后如何 replay？
5. 实际跑一遍 Detect/Classify/Filter/Map/Verify 产出文档，看能否被同事复用。

## 下一站

感知模块（06~10 讲）：Agent 的第一道门——看见什么、不看见什么；有限注意力花在哪里。Claude Code 的 context 装配、Aider 的 repo map、OpenCode 的 LSP、Hermes 的 memory、DeerFlow 的 skill loading 都是同一个问题（Agent 怎样认识世界）的不同答案。
