# 04｜逆向五步法（上）：工业级 Agent 框架源码如何解码

> 来源：极客时间《Agent 设计模式之美》黄佳 · 范式觉醒
> 原文链接：https://time.geekbang.org/column/article/982574 （时长 22:02）
> 课程主页：https://time.geekbang.org/column/intro/101162601?tab=catalog
> 本笔记为学习总结，版权归原作者所有。

## 一句话总结

读懂几十万行 Agent 源码的快捷通道是**逆向五步法：Detect 找主循环 → Classify 归七脉 → Filter 滤噪声 → Map 落矩阵 → Verify 回源码**——先知道 Agent Harness 理论上应该长什么样，再去源码里验证它实际什么样。

## 前三讲地基回顾与本章定位

- 第 1 讲：为什么旧词汇不够用（决策权迁移到运行时，需要 Harness 约束）。
- 第 2、3 讲：双轴坐标系（模式 = 认知功能 × 执行拓扑）。
- 第 4、5 讲：第三块地基——**怎么看懂真实工业系统**，把陌生系统拆成可验证的结构图。

## 为什么是这 8 个框架

样本：Claude Code、Codex CLI、Aider、OpenCode、OpenClaw、Hermes Agent、DeerFlow、OpenHands。

> 8 个框架只是素材，真正的主角是庖丁解牛的那把刀。框架会变、版本会变，只要 Agent 还需要七脉，逆向五步法就仍然适用。

好的横切面要覆盖三件事：
1. **不同主战场**：代码 Agent（怕看错仓库改错文件）、个人助手（怕身份/权限/长期记忆失控）、多 Agent 编排（怕任务拆开收不回）、沙箱执行（怕"能做事"变"乱做事"）。
2. **不同工程姿态**：闭源产品 / 商业公司开源 / 社区驱动。社区项目常在一个关键不变量上打得很深——Aider 抓住 **Git 不变量**（所有修改可通过 diff/commit/rollback 审计和控制），OpenCode 抓住 **LSP 不变量**（借助 IDE 的语言理解能力）。
3. **2026 变量（六层系统工程形态）**：运行形态（CLI/IDE 插件/桌面应用）、协议与连接层（JSON-RPC/LSP/local-first gateway）、经验沉淀层（procedural skills 记住"怎么做事"）、执行与隔离层（sandbox/权限/本地确认）、可观测与审计层（event replay/trace/状态账本）、协作边界层（子 Agent 的职责/权限/上下文范围/交接边界）。

趋势判断：Agent 工程正从"模型会不会调用工具"转向"**模型如何被放进一个可靠运行时**"。

## 读源码的三个坑

1. **README 幻觉**：知道"能做什么"，不知道"为什么这样做"——主循环在哪、状态怎么传、权限在哪被卡住。
2. **关键词漫游**：一上来就 grep agent/tool/memory，跳出几百个文件，变成源码景点观光打卡。
3. **从入口函数钻到底**：大型项目 70% 的代码是配置、日志、类型、适配器、UI glue，顺调用栈下钻容易钻进不关键的支路。

根因是缺少预期框架。**Agent Harness 的理论骨架很稳定**：主循环 + 上下文管理 + 工具注册与调度 + 状态/事件记录 + 治理边界（哪怕很弱）。

## 逆向五步法

### Step 1 Detect：先找主循环

所有 Harness 都有一颗心脏——不断重复的运行循环：

```
input → build context → call model → parse response
      → dispatch tools / handoff / ask user
      → collect observations → update state → continue or stop
```

先回答三个问题：用户输入从哪里进来？LLM 调用在哪里发生？工具执行结果怎么回到下一轮上下文？

grep 锚点：`tool_call|function_call|tool_use|Observation`、`while|loop|turn|session`、`messages|context|history|state`。主循环一定同时碰到 **messages、model call、tool dispatch** 三件事。

产出物：一张不超过 10 个框的粗图（User Input → Session → Context Builder → LLM → Tool Dispatcher → Runtime/Sandbox → Observation → Event Log → 回到下一轮）。

### Step 2 Classify：把组件归到七脉

看组件**真正做什么**，不看名字。组件可以跨脉：第一遍先定主脉、再标副脉。

| 组件 | 主脉 | 副脉 | 理由 |
| --- | --- | --- | --- |
| Aider repo map | 感知 | 记忆 | 决定哪些代码结构进入 LLM 上下文；形成对仓库的压缩式持续认识 |
| OpenHands Event System | 记忆 | 治理 | 可追加、可回放的事件账本；过程可追踪可审计 |
| Claude Code Subagent | 协作 | 感知 | 任务分给子 Agent；每个子 Agent 独立上下文 = 观察范围隔离 |

### Step 3 Filter：主动丢掉 70% 噪声

读大型工程最反直觉的能力是**敢于不读**。CLI 参数解析、UI 布局、错误文案、telemetry 适配、schema 类型定义、provider SDK 包装、序列化、测试 fixture、平台兼容分支——第一轮都先放一边。

判断标准：**这个文件会不会改变 Agent 的决策、上下文、状态、行动或权限？** 不会 = boilerplate。

8 框架第一轮优先阅读清单：
- Claude Code：工具注册、工具执行、权限、AgentTool、context 装配（别陷进 UI）
- Codex CLI：core、protocol、sandboxing、exec policy（别陷进每个 crate）
- Aider：base_coder.py、repomap.py、repo.py（跳过 prompt variants）
- OpenCode：session、llm agent、permission、lsp（TUI 只看会话/权限触发）
- OpenClaw：gateway、channels contract、workspace、memory-host-sdk、skills 文档
- Hermes Agent：run_agent.py、hermes_state.py、memory_setup、skills_hub、gateway
- DeerFlow：agent router、skill router、memory router、sandbox/subagent tests（测试暴露系统边界）
- OpenHands：event service、sandbox service、conversation service、runtime README

### Step 4 Map：落到双轴矩阵

Map 不是贴标签，是把框架变成**可比较的工程对象**：说清是哪个功能、哪种拓扑、什么失败模式。

例：OpenHands EventLog 落矩阵后变成三个工程判断——① 解决 Memory（状态来自事件串，不散落在对象内存）；② 也解决 Governance（可审计、可回放）；③ 采用 Chain / append-only 拓扑（错误不会被悄悄覆盖，但日志会膨胀、replay 成本要管理）。

最终产物：**热力图**。横看框架性格（Aider 专注代码上下文与提交；OpenHands 重沙箱/事件/运行时；Hermes 重记忆/技能/用户模型；DeerFlow 重协作/路由/执行环境），竖看模式普及度（Tool Dispatch 几乎都有；Experience Replay 不是每家都有；Blast Radius 在 sandbox-heavy 系统强；Subagent 成熟度差异大）。

### Step 5 Verify：每个判断都要能回到源码

没有 Verify，逆向工程就是讲故事。证据分三层：
1. **源码**（最强）：permission 模块、sandbox manager、event store、repo map 实现、tool dispatcher。
2. **测试**（强力）：测试往往比实现更清楚暴露设计边界（sandbox 怎么限动作、Subagent 怎么传上下文、memory 何时写入）。
3. **官方文档**（辅助）：确认产品语义和公开承诺。

做法：热力图旁边放一张引用表（判断 → 证据文件/测试/文档位置）。工程论证必须回到代码层，产品解读可以停在概念层。

## 关键要点（复习用）

1. 先有预期框架再读源码：主循环、上下文、工具系统、状态账本、治理边界五大件。
2. 主循环 = 同时碰到 messages、model call、tool dispatch 的那段循环。
3. 归类看行为不看名字；组件先定主脉再标副脉。
4. Filter 的判据：是否改变决策/上下文/状态/行动/权限。
5. 落矩阵要产出三件事：功能、拓扑、失败模式；判断必须有源码/测试/文档证据。
6. 有意识地让思维"摩擦"，通过做具体的难事在 AI 时代保持思考力。

## 思考题（课程留题）

1. 选一个熟悉的 Agent 框架，先不看源码写出预期的五个地基，再去源码/文档验证。
2. 任选 Claude Code / Aider / OpenHands / Hermes 画一张不超过 10 框的主循环图，标出输入入口、模型调用点、工具结果回流路径。
3. 挑一个组件（repo map、event log、sandbox、permission policy、Subagent、skills hub）归七脉、落矩阵，说明为什么属于这个格子而不是另一个。

## 下一讲预告

05 讲拿这把"解牛刀"实际切 8 个真实 Agent Harness，把每个产品拆成工程地图。
