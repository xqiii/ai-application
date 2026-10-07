# Agent Loop 调研与实现指南

本目录调研了四个有代表性的 coding agent 的 **agent loop（智能体主循环）** 实现，并从中提炼出一套"如何实现自己的 agent loop"的路线图。

## 调研文档

| 文档 | 对象 | 语言 | 一句话定位 |
|---|---|---|---|
| [codex.md](./codex.md) | [openai/codex](https://github.com/openai/codex) | Rust | 工程化最重的实现：四层循环分层、tokio 多任务、Op/Event 双队列 |
| [deepseek-harness.md](./deepseek-harness.md) | [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) | TypeScript | 插件化（Cordis waterfall 事件）、事件溯源会话、durable inbox |
| [kimi-code.md](./kimi-code.md) | [MoonshotAI/kimi-code](https://github.com/MoonshotAI/kimi-code) | TypeScript | 纯函数状态机内核（xstate 风格）+ DI 服务外壳，三层状态机嵌套 |
| [pi.md](./pi.md) | [badlogic/pi-mono](https://github.com/badlogic/pi-mono) | TypeScript | 极简教材级实现：~940 行纯函数循环 + 钩子，知识全部外挂 |

四份文档均基于对仓库源码的实读（浅克隆 + 文件级证据），每份都含：文件地图、主循环伪代码、关键源码引用、工具/审批/上下文/中断/持久化各节、值得借鉴的设计点。

---

## 一、横向对比

| 维度 | codex | deepseek-harness | kimi-code | pi |
|---|---|---|---|---|
| 循环形态 | 4 层：队列调度 → turn 重启 → `run_turn` → SSE 泵 + 重试壳 | 2 层：`turn()` / `step()`，由 durable inbox 驱动 | 3 层状态机：AgentMachine / TurnMachine / ToolMachine | 1 个双层 while（内层 tool 驱动、外层 follow-up） |
| 并发模型 | tokio task + `async_channel`，提交队列有界 512 | 单 agent 协程 + `Promise.race` 滚动池 | 每个工具 spawn 独立 actor | 顺序 await，工具批次内可并行 |
| 提交协议 | `Op` 非穷尽枚举 + oneshot 回执 | inbox 双队列（next-turn / next-step） | prompt 队列 + 门控（promptGateActor） | steering / follow-up 两个队列 |
| 事件流 | `EventMsg` 无界事件队列 → TUI | `session.append(事件)` 追加日志 + 订阅 | machine 事件投影为 delta/stepCompleted | 单一 `emit(event)` 流：agent_start → turn → agent_end |
| 工具并行 | `FuturesOrdered`：读锁并发、写锁串行，边收流边启动、按序回填 | parallel/exclusive 分组 + 有界池（≤10） | 全部并行 spawn 成 actor | 默认并行，preflight 串行、结果按 model order 入列；可声明 sequential |
| 上下文管理 | PreTurn / MidTurn / PostTurn 三时机 auto-compact | `deriveMessages()` 从日志投影 + 插件压缩 | fullCompaction 挂 `onWillBeginStep` 钩子 | `prepareNextTurn` 钩子里做 compaction |
| 错误处理 | 非致命错误 emit Error + break（结束这轮不结束会话） | 失败尝试写 `assistant/attempt` 不入模型历史，可原地重试 | 错误即状态迁移（failed / retrying），空响应按远程失败处理 | `stopReason: "error" \| "aborted"` 是数据不是异常 |
| 中断 | 双 CancellationToken：`preempt`（插话）vs 主 token（ESC 丢弃） | AbortSignal + abort 时给未启动工具补合成结果 | abort 宽限 2.5s 后强制收齐 | AbortSignal 贯穿；中止合成规整事件序列 |
| 持久化 | rollout JSONL + resume/fork | append-only session log（"model-visible means logged"） | 事件溯源 eventStore + wire append-log 分支树 | append-only JSONL 会话树 + projection |
| 独一无二的设计 | `StepContext` 不可变快照：发给模型的 schema 与实际执行的 handler 共享同一视角 | 请求 messages 从日志投影后 `deepFreeze`；工具审批三层否决 fail-closed | 内核纯函数可同步 refold（重放） | `stopReason === "length"` 时拒绝执行所有 tool call |

**结论先行**：四个实现在**骨架层面完全一致**（下一节），差异全部集中在**工程化程度**和**状态归属**上。所以实现自己的 agent loop 的正确姿势是：先写 100 行的骨架，再按路线图把四个实现验证过的工程细节逐层加上去。

---

## 二、共识：所有实现都收敛到的同一个骨架

去掉一切工程包装，agent loop 的本体就是这个：

```typescript
async function agentLoop(messages: Message[], tools: Tool[], signal: AbortSignal) {
  while (true) {
    // 1. 调模型（流式）
    const assistant = await streamLLM(messages, tools, signal);
    messages.push(assistant);

    // 2. 没有工具调用 → 任务结束
    const toolCalls = assistant.content.filter(c => c.type === "tool_call");
    if (toolCalls.length === 0) break;

    // 3. 执行工具，结果回填（错误也是结果！）
    const results = await executeTools(toolCalls, signal);
    for (const r of results) messages.push(toToolResultMessage(r));
  }
  return messages;
}
```

所有实现的本质都是这个循环。四个项目用血泪验证出的**不变量**（缺一不可）：

1. **消息历史是唯一的状态。** 循环不持有其他业务状态；模型看到的 = 历史里的。deepseek-harness 把这做成硬约束："model-visible means logged"——请求从日志派生。
2. **流式响应必须能累积。** 模型是流式返回的，text delta / tool call 参数 delta 进来时先放一个 partial message，收完后**原位替换**。
3. **工具错误必须变成 tool result 回填，不能击穿循环。** 参数校验失败、执行抛异常、找不到工具——全部转成 `isError: true` 的结果让模型自己纠正。四个实现无一例外。
4. **停止条件只有一个：模型不再请求工具。** 外加错误 / 中止两个"硬退出"分支。
5. **中断是一等公民。** 从第一天就用 `AbortSignal` / `CancellationToken` 贯穿 LLM 调用与工具执行。

---

## 三、关键设计决策（分歧点与推荐）

骨架之外，有六个决策会显著影响你的实现质量。以下是四个项目的做法与推荐：

### 决策 1：循环分几层？

- **codex**：4 层——`submission_loop`（收 Op 分发）→ `RegularTask`（turn 自动重启）→ `run_turn`（agent 循环）→ `run_sampling_request`（网络重试壳）。
- **pi**：就 1 个双层 while。重试归上层的 `AgentSession`，不进循环。
- **kimi-code / dsh**：概念上分 turn / step 两级。

**推荐**：从 1 层开始（pi 式），但**在心里划清三个语义边界**：agent 循环（采样→工具→续跑）、网络重试（不该重复装配 prompt 和用户消息）、turn 续跑（插话/队列输入）。三者纠缠在一个 while 里是 codex 分 4 层的原因——你早晚会需要拆开，不如一开始就把重试放在循环**外层函数**里。

### 决策 2：循环本体写成什么？

- **pi**：无状态纯函数 `runAgentLoop(prompts, context, config, emit, signal, streamFn)`，一切变化点做成 `AgentLoopConfig` 钩子。
- **kimi-code**：内核是 xstate 风格纯状态机（无 IO 依赖、可同步 refold 重放），服务层通过注入 actor / hook 从外面接进去。
- **dsh**：核心 ~700 行 `ReactLoopAgent` + waterfall 事件插件。
- **codex**：async Rust，`StepContext` 不可变快照。

**推荐**：循环本体写成**纯函数或纯状态机**，IO（LLM 调用、工具执行、持久化、UI）全部从参数/钩子注入。收益：可单测（用假 stream 函数）、可重放、可整体替换。dsh 的硬约束值得抄：**每个 LLM 请求调用 `convertToLlm(messages)` 做一次性边界转换**，内部 transcript 格式（可含自定义消息类型）与 wire 格式解耦。

### 决策 3：UI 与循环怎么通信？

- **codex**：`Op`（UI→core）+ `EventMsg`（core→UI）双队列，提交走有界队列（512）带 oneshot 回执；事件队列无界，反压交给消费端。
- **pi**：单一 `emit(event)` 流，循环对"谁在听"零感知；事件协议 `agent_start → (turn_start → message_* → tool_execution_* → turn_end)* → agent_end`。
- **dsh**：所有事件 `session.append(...)` 进追加日志，UI 是日志的订阅者。

**推荐**：抄 pi 的单一事件流（最少代码），但加上 codex 的一个精华：**提交（submit）也要有回执**，让调用方知道输入是"被接受为新 turn""插话进当前 turn"还是"拒绝"，而不是去事件流里猜。

### 决策 4：工具怎么并行？

- **pi**：preflight（查找+校验+beforeToolCall）**串行**，执行并行，`toolResult` **按 assistant 消息中的原始顺序**入列——满足 provider 的 tool_use/tool_result 配对要求。工具可声明 `executionMode: "sequential"` 独占。
- **dsh**：按 `executionMode`（parallel/exclusive）分组，有界滚动池（默认 ≤10），exclusive 形成屏障。
- **codex**：`FuturesOrdered`——**边收流边启动**工具 future（模型还没说完就开始执行），结果按序回填；读工具并发、写工具串行。

**推荐**：preflight 串行 + 执行并行 + 结果按 model order 回填。这个三件套同时满足安全性（审批/校验不会乱序）、性能（并行收益）和正确性（配对顺序）。codex 的"边收流边启动"是后期优化项。

### 决策 5：历史用可变数组还是 append-only 日志？

- **pi / dsh / kimi-code**：append-only（JSONL 或事件溯源），**模型可见上下文永远是日志的投影**。压缩 = 追加一条 summary entry，分支/回滚/重试 = 追加 entry，文件永不重写。
- **codex**：历史在内存 + rollout 文件记录，压缩原地替换历史。

**推荐**：append-only + projection。它是压缩、断点恢复、崩溃一致性三个难题的一次性解法（pi：`buildSessionProjection` 重放出模型可见上下文；dsh：`deriveMessages()` 投影后 `deepFreeze`——冻结后模型看到的消息不可被后续代码悄悄改写）。

### 决策 6：插话（steering）与排队怎么表达？

- **pi**：`getSteeringMessages()`（模型思考/执行期间输入的 → 下一个 tool boundary 注入）/ `getFollowUpMessages()`（agent 停下后该处理的消息 → 触发外层续跑）。
- **dsh**：durable inbox 双队列 `next-turn` / `next-step`，steering 与注入上下文走同一条 claim 路径。
- **codex**：`InputQueue.pending_input` + `preempt` token（用户流中途说话 → 不算 abort，而是 `needs_follow_up = true`）。

**推荐**：两个队列 + AbortSignal 组合。关键细节（codex 验证过）：**插话 ≠ 中断**——插话要让当前流保留输出、把新输入排进去；只有显式 ESC 才丢弃。

---

## 四、实现路线图

### Phase 0：最小可跑循环（~200 行，1 天）

目标：能对话、能调 2 个工具（read_file / write_file）、有 REPL。

```typescript
// 需要实现的全部：
// 1) LLM client：POST /chat/completions 或 /responses，SSE 解析
// 2) 工具接口 + 2 个工具实现
// 3) 上面第二节的 30 行 while 循环
// 4) 终端 REPL

interface Tool {
  name: string;
  description: string;
  parameters: JsonSchema;                       // 函数签名 → JSON Schema
  execute(args: unknown, signal: AbortSignal): Promise<ToolResult>;
}

// 流式累积的胶水（最容易写错的地方）：
let partial: AssistantMessage | null = null;
for await (const event of stream) {
  switch (event.type) {
    case "start":       partial = event.message; messages.push(partial); break;
    case "text_delta":  partial.text += event.delta; break;
    case "tool_call_delta": partial.toolCalls[event.index].args += event.delta; break;
    case "done":        messages[messages.length - 1] = event.finalMessage; break;
  }
}
```

### Phase 1：工程化（1 周）

- **事件流**：循环不再直接输出文本，改 `emit(event)`；REPL 是订阅者。事件类型照抄 pi：`agent_start / turn_start / message_start / message_update / message_end / tool_execution_start / _update / _end / turn_end / agent_end`。
- **工具结果三通道**：`content`（给模型）/ `details`（给 UI 渲染）/ `structuredContent`（给程序）/ `isError`。
- **错误即数据**：`stopReason: "stop" | "length" | "error" | "aborted"`，工具异常 catch 成 error result。
- **AbortSignal 贯穿**：Ctrl-C 中断流与工具。
- **JSON Schema 校验**：用 ajv 或 typebox 在 preflight 阶段校验参数，失败回 error result。
- **`isError` 的 tool result 也必须进历史**——否则模型会永远等结果而死循环。

### Phase 2：生产化（2-3 周）

- **会话持久化**：append-only JSONL（每条：`{type: "message" | "tool_result" | "compaction" | "branch", ...}`）；启动时 replay 出历史。支持 `--continue` / resume。
- **Compaction**：token 数超过阈值（如上下文窗口 70%）→ 用模型把旧消息摘要成一条 → **追加** summary entry，投影时替换掉被压缩的区间。注意 pi 的坑：`stopReason === "length"` 时（输出被截断）**tool call 参数可能不完整，全部拒绝执行**，回 error result 让模型重发；codex 的兜底：注释里明说"只要 compaction 有效把 token 压得足够低，就不用担心无限循环"。
- **审批机制**：工具调用前插入审批钩子（allowlist / ask / deny），审批请求走事件流到 UI，UI 回复走 submit 通道。dsh 的三层否决可以简化为两层：策略（可自动放行）+ 人工（无应答即拒绝，fail-closed）。
- **并行工具**：按决策 4 的三件套实现。
- **Steering / follow-up**：两个队列接进循环（决策 6）。

### Phase 3：平台化（按需）

- **扩展/插件系统**：参考 pi 的模式——不要动循环，把能力做成钩子（`beforeToolCall / afterToolCall / prepareNextTurn / finishTurn`）和事件订阅；dsh 更进一步：压缩、重试、审批、提醒全部是旁挂的 waterfall 插件，循环核心保持 ~700 行可审计。
- **子代理**：把一个 agent 作为另一个 agent 的工具（dsh 的 agentTool、kimi-code 的 agent.md）。
- **沙箱**：codex 用 Seatbelt(macOS) / Landlock(Linux)；起步阶段可以先只做"工作目录限制 + 命令 allowlist"。
- **多 provider**：抽 `StreamFn` 接口（pi 的 pi-ai 是范本），把 provider 差异关在流式解析层里。

---

## 五、逐模块实现 checklist

**LLM Client**
- [ ] 统一流式事件类型（start / text_delta / tool_call_delta / reasoning_delta / done / error / usage）
- [ ] stopReason 归一化（不同 provider 的 finish_reason 各不相同）
- [ ] usage（input/output tokens）逐请求记录，供预算与压缩判断
- [ ] 重试：指数退避 + 只在"流未产生任何内容"时重试最安全

**Tool 系统**
- [ ] 工具 = {name, description, parameters(JSON Schema), execute}
- [ ] 参数校验在 preflight 做，失败 → error tool result（不抛）
- [ ] 执行异常 catch → error tool result
- [ ] 工具结果按 model order 入列（配对正确性）
- [ ] 截断防护：`stopReason === "length"` 时拒绝执行 tool call

**上下文管理**
- [ ] token 计数（估算够用，精确更好）
- [ ] 压缩触发：请求前检查预算 + 每 step 后检查
- [ ] 摘要 prompt 保留：用户意图、已完成动作、当前状态、下一步
- [ ] 压缩后把摘要作为"系统注入"追加，不删除原始日志

**中断**
- [ ] AbortSignal 贯穿：LLM 流、每个工具、重试等待
- [ ] 区分"中止"（丢弃输出）与"插话"（保留输出 + 排入新输入）
- [ ] 工具收到 abort 后，未启动的调用补合成 error result（保持日志闭合、可重放）

**持久化**
- [ ] append-only JSONL，每条记录带 seq
- [ ] 模型可见消息 = 日志投影（需要时 deepFreeze）
- [ ] 崩溃恢复 = replay 最后 N 条

---

## 六、踩坑清单（四个项目实战验证过）

1. **截断的 tool call 不能执行**（pi）：`stopReason === "length"` 时流式 JSON salvage 可能"能解析但参数悄悄不完整"，逐个回 error result。
2. **空响应要当失败处理**（kimi-code）：模型返回空 body 时构造 `empty_response` 错误走重试，否则循环卡死。
3. **工具错误永不击穿循环**（全部）：任何工具失败都是一条 `isError` 的 tool result。
4. **preflight 与执行分离**（pi/dsh）：校验/审批串行，执行并行——审批不会被并发打乱。
5. **tool_use / tool_result 顺序配对**（pi/codex）：并行完成顺序 ≠ 回填入列顺序，按 model order 回填。
6. **重试不该重复副作用**（codex）：网络重试放在采样壳里，不重跑 pre-step 装配与用户消息追加。
7. **压缩后防死循环**（codex）：压缩必须把 token 压到远低于上限，否则"超限→压缩→还是超限"死循环。
8. **streaming partial 原位替换**（pi）：流中的 partial message 先入历史（UI 能看到增量），结束时原位替换为 final，不能 push 两次。
9. **错误结束这一轮，不要结束会话**（codex）：非致命错误 emit Error + break，让用户能继续对话。
10. **审批 fail-closed**（dsh）：无应答/超时 = 拒绝，绝不能默认放行。
11. **失败尝试不入模型历史**（dsh）：失败/取消的流另存（`assistant/attempt`），只有成功消息进 transcript——审计与恢复都有据可查。
12. **别让模型看到被悄悄改写的历史**（dsh）：请求派生后 `deepFreeze`，杜绝内存态与持久态不同步。

---

## 七、选型速查：如果你要写……

| 你的目标 | 抄谁 | 抄什么 |
|---|---|---|
| 学习 / 教学 / 2 小时写个能用的 | **pi** | 纯函数循环 + 钩子 + 双层 while |
| 可测试 / 可重放 / 强一致 | **deepseek-harness** | 事件溯源 + 日志投影 + waterfall 插件 |
| 单机 CLI 产品，重交互 | **kimi-code** | 状态机内核 + 服务外壳 + 门控 |
| 高并发 / 多端 / 生产级 | **codex** | 四层分层 + 双队列 + 双 token + 快照 |

---

## 参考资料

- [openai/codex](https://github.com/openai/codex) — Rust，四层循环，`codex-rs/core/src/session/`
- [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) — TypeScript，Cordis 插件架构
- [MoonshotAI/kimi-code](https://github.com/MoonshotAI/kimi-code) — TypeScript，xstate 风格状态机内核
- [badlogic/pi-mono](https://github.com/badlogic/pi-mono) — TypeScript，极简 agent loop 教材
  - [Pi Coding Agent: Architecture, Agent Loop, Extension System](https://xiaow.dev/claude_notes/2026-03-27---Research---Pi-Coding-Agent---Agent-Loop,-Extension-and-Plugin-System)（社区研究笔记）
  - [pi-mono DeepWiki](https://deepwiki.com/badlogic/pi-mono)
