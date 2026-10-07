# Kimi Code CLI (Moonshot AI) 的 Agent Loop 实现

> 研究对象：[MoonshotAI/kimi-code](https://github.com/MoonshotAI/kimi-code)（2026 年开源，MIT 协议，TypeScript monorepo）。
> 注意区分：`MoonshotAI/kimi-cli` 是**旧版 Python CLI**，已归档，由本仓库（`kimi-code`，即 "Kimi Code CLI"）取代。本文只研究新版。
> 研究方式：shallow clone 真实源码（commit 浅克隆于 2026-10），所有结论均附仓库相对路径证据。

## 项目概览：定位、语言、分层架构

Kimi Code CLI 是 Moonshot AI 的终端 AI 编码代理（对标 Claude Code / Gemini CLI），单二进制分发，自带 TUI，支持 ACP（Agent Client Protocol）接入 Zed/JetBrains。底层为 pnpm monorepo（根 `package.json` 名为 `@moonshot-ai/monorepo`），Node ≥ 24，TypeScript 6，vitest 测试。

分层架构（据根目录 `AGENTS.md` 的 Project Map 与各 `package.json`）：

| 层 | 包 | 职责 |
|---|---|---|
| 应用层 | `apps/kimi-code` | CLI/TUI 应用，只通过 `@moonshot-ai/kimi-code-sdk` 消费核心能力，不许直接依赖引擎包 |
| 服务层 | `packages/kap-server` | Kimi Code server（REST + WebSocket `/api/v1`），Web UI / 远程控制的后端 |
| SDK 层 | `packages/node-sdk`（`@moonshot-ai/kimi-code-sdk`）+ `packages/klient` | 公开 TypeScript SDK；klient 是 agent-core-v2 之上的 contract-driven facade，可走 IPC 或内存传输 |
| 引擎层 | `packages/agent-core-v2` | **统一 agent 引擎（"DI × Scope architecture"），agent loop 就在这一层**。四级 `LifecycleScope`：App / Workspace / Session / Agent（`src/app/scopes.ts`），服务通过 DI decorator 注入 |
| 模型层 | `packages/kosong` + `agent-core-v2/src/human/llm*` | LLM/provider 抽象（OpenAI/Anthropic/Google/Kimi 多协议），流式解析 |
| 执行层 | `packages/kaos` | 执行环境抽象（local / ssh / 沙箱化 process、filesystem） |
| 周边包 | `packages/pi-tui`、`packages/acp-server`、`packages/minidb`、`packages/oauth`、`packages/telemetry`、`packages/transcript`、`packages/tree-sitter-bash` | TUI 组件、ACP 服务器、嵌入式文档库、OAuth、遥测、转录渲染、纯 TS bash 解析器 |

引擎内部（`packages/agent-core-v2/src/`）再分两套命名空间：

- `src/agent/**`：DI 服务层（带 `Service`、hook、config、telemetry 的"重"层），如 `loop/`、`toolExecutor/`、`fullCompaction/`、`subagent`；
- `src/human/**`：**纯函数的内核状态机层**（xstate 风格自研 `xstate2`），如 `human/agent/machine.ts`、`human/agent/turn.ts`、`human/tool/machine.ts`、`human/eventStore/`。`package.json` 里用 subpath imports 把 `#human/*` 映射到 `./src/human/*`。

这个"服务层包装纯状态机内核"的双层设计是整个项目最核心的架构决策：循环逻辑本身无 IO 依赖、可同步 refold（重放），持久化、权限、遥测全部通过注入的 actor/gate/hook 从外面接进去。

## 核心循环在哪：关键文件地图

```
packages/agent-core-v2/src/
├── agent/loop/                    # DI 服务层循环
│   ├── loop.ts                    #   IAgentLoopService 接口（242 行，纯类型）
│   ├── loopService.ts             #   AgentLoopService（2294 行）：submit/steer/cancel、
│   │                              #   gate()（每步前置门）、turn 生命周期、事件投影
│   ├── turnOps.ts / turnEvents.ts #   turn 状态持久化与事件定义
│   ├── promptChannel.ts           #   prompt 队列 channel
│   └── machine/                   # 机器引擎粘合层
│       ├── engine.ts              #   attachMachineEngine：把服务层请求/工具/日志接进状态机
│       ├── requester.ts           #   createMachineRequester：LLM 请求包装 + step gate
│       ├── tools.ts               #   createMachineTools：工具批量执行包装
│       └── storeJournal.ts        #   事件日志 journal
└── human/                         # 纯状态机内核（真正的主循环）
    ├── agent/machine.ts           #   createAgentMachine（949 行）：agent 级状态机
    │                              #   idle/running/aborting + 队列/通知/提醒/后台工具
    ├── agent/turn.ts              #   createTurnMachine（886 行）：★ 核心循环 ★
    │                              #   gating→thinking→acting→draining→…→done
    ├── tool/machine.ts            #   createToolMachine（256 行）：单个工具调用状态机
    ├── llm/requester/actor.ts     #   createRequestActor：LLM 请求 as xstate actor
    ├── llm/requester/bases/openai/requester.ts  # OpenAI 兼容 SSE 流式实现
    ├── llm-kimi/trait.ts          #   Kimi API 的协议 trait（thinking/usage/工具转换）
    └── eventStore/eventStore.ts   #   事件溯源 store（slice reducer + journal 重放）
```

一句话定位：**`human/agent/turn.ts` 是主循环（step 循环），`human/agent/machine.ts` 是外层 turn/队列循环，`agent/loop/loopService.ts` 是把它们接入 DI 世界的服务外壳。**

## 主循环详解

### 三层嵌套的循环结构

整个 agent loop 是三层状态机嵌套（均为 `setup().createMachine()` 的 xstate 风格）：

1. **AgentMachine**（`human/agent/machine.ts`）：管理 prompt 队列、通知（notification）、提醒（reminder）、后台工具、暂停/恢复、abort。状态：`linking → restoring → idle(ready/waiting/gating) ⇄ running(active/aborting) → closing → disposed`。
2. **TurnMachine**（`human/agent/turn.ts`）：一个 turn（一次用户输入到最终答复）内的 step 循环。状态：`gating → thinking → acting → draining → (回到 gating) … → done | failed | aborted`。
3. **ToolMachine**（`human/tool/machine.ts`）：单个工具调用。状态：`preparing → executing → finishing → succeeded | failed | aborted`。

### 主循环伪代码（从 `human/agent/turn.ts` 提炼）

```
turn(request, history, maxSteps):
    produced = []
    steps = 1
    while true:
        # ── gating：每步前置钩子（服务层注入 compaction 检查等）──
        await onBeforeStep(history + produced)          # turn.ts L458-475

        # ── thinking：一次 LLM 流式请求 ──
        accumulator = new HistoryAccumulator()           # 收集流式片段
        llmActor(request, history + produced, signal)    # turn.ts L492-507
        on llm.streaming.part   -> accumulator.push(part)
        on llm.streaming.usage  -> accumulator.pushUsage(usage)
        on llm.streaming.finish -> accumulator.pushFinish(finish)
        on llm.failed.remote:
            if recovery 能提出策略 (换凭证/改写消息):
                appliedRecoveries += proposal; attempt = 1; continue  # 重新 thinking
            elif shouldRetry(attempt, error):                    # 退避重试
                attempt += 1; delay = retryAfter ?? backoff(attempt); goto retrying
            else: fail
        on llm.done:
            entry = accumulator.finish()
            produced += entry
            if entry.toolCalls 非空: pendingToolCalls = entry.toolCalls; goto acting
            elif 空响应判定 emptyErrorOf(): 按远程失败处理（可重试）   # turn.ts L594-601
            else: done                                      # 无工具调用 → 循环结束

        # ── acting：并行执行本轮全部工具调用 ──
        signalParent('turn.spawn_tools', pendingToolCalls)  # turn.ts L729-736
        每个 toolCall 由 AgentMachine spawn 一个独立 ToolMachine actor   # machine.ts L376-394
        等待所有 outcomes 收齐（tool.done / tool.failed / tool.aborted / tool.detached）
        produced += 每个 toolCall 的 ToolEntry（outcomes 映射为 tool message）
        # abort 分支：给未完成工具 2.5s 宽限，然后强制收齐          # turn.ts L809-822

        # ── draining：收割运行中涌入的外部消息 ──
        signalParent('turn.drain')                          # machine.ts 把
                                                            # notifications + reminders 发回 turn
        on turn.notify messages:
            produced += messages（异步工具完成通知、reminder 等）
            steps = messages 非空 ? 1 : steps + 1            # 有新消息则重置步数
            if paused: done                                 # 暂停状态下直接收尾
            if maxSteps 超限: fail(MaxStepsExceededError)
            goto gating                                     # ★ 回到循环顶部
```

循环终止条件：LLM 回复不含 toolCalls（自然结束）、`maxStepsPerTurn` 超限、abort、或 pause 后 drain 直接收尾。

### 关键代码引用

**thinking 态发起 LLM 请求**（`turn.ts` L492-507）：

```ts
invoke: {
  src: 'llmActor',
  input: ({ context }) => {
    const entries = [...context.input.history, ...context.produced];
    return {
      config: context.input.request,
      signal: context.llmScope.signal,
      content: {
        messages: attemptMessages(context),
        usedContextTokens: estimateUsedContextTokens(entries, {
          systemPrompt: context.input.request.systemPrompt,
          tools: context.input.request.tools,
        }),
      },
    };
  },
```

注意 `usedContextTokens` 被一路传给 provider 层，供 Kimi 计算 `max_completion_tokens` 兜底（见下文流式节）。

**llm.done 的三分支路由**（`turn.ts` L572-621）：有 toolCalls → `acting`；空响应 → 构造 `empty_response` 错误走重试；否则 → `done`。

**acting 态并行工具收割**（`turn.ts` L738-755）：

```ts
always: [
  { guard: ({ context }) =>
      context.outcome === 'aborted' &&
      context.pendingToolCalls.every((tc) => context.outcomes[tc.id] !== undefined),
    target: 'aborted', actions: assign(({ context }) => collectToolOutcomes(context)) },
  { guard: ({ context }) =>
      context.pendingToolCalls.every((tc) => context.outcomes[tc.id] !== undefined),
    target: 'draining', actions: assign(({ context }) => collectToolOutcomes(context)) },
],
```

**AgentMachine 把每个工具调用 spawn 成独立 actor**（`machine.ts` L376-394）：

```ts
spawnTurnTools: assign(({ context, spawn, self, event }) => {
  ...
  for (const toolCall of event.toolCalls) {
    const scope = withAbort(context.scope.signal);
    turnTools[toolCall.id] = {
      toolCall, scope,
      ref: spawn(context.toolLogic, { id: toolCall.id,
        input: { toolCall, signal: scope.signal, waitForTasks } }),
    };
  }
  return { turnTools };
}),
```

### 服务层如何驱动状态机（loopService.ts）

`AgentLoopService`（`agent/loop/loopService.ts`）不直接跑循环，而是：

- `submit()` 创建 prompt waiter，投递给 engine 的 `input.submit`；
- `attachEngine()`（L234）把 `machineEngineAttachBundle()` 产出的 store/turnLogic/toolLogic/requester 组装成 `createAgentMachine()` 并挂上事件监听；
- **`gate()`（L970-1039）是每步前置门**：machine 的 requester 在每次 LLM 请求前调用它，服务层在此做 turn 绑定、maxSteps 检查、`onWillBeginStep` hook（fullCompaction 在这里注册，见 `fullCompactionService.ts` L187）：
  ```ts
  await this.hooks.onWillBeginStep.run({
    turnId: turn.id, step: stepOrdinal, firstStepOfTurn: stepOrdinal === 1, signal: step.signal,
  });
  ```
- 机器事件经 `projectMachineEvent` 投影为对外的 `delta` / `stepCompleted` / `toolDone` 等事件（`machine/engine.ts` L363-502 的 `ref.on(...)` 订阅表）。

## 一次请求的完整生命周期

1. **输入**：TUI（`apps/kimi-code/src/tui/`）或 ACP/SDK 调 `IAgentLoopService.submit(UserEntry)`（`loopService.ts` L302）。
2. **排队与门控**：`AgentMachine.idle.gating` 跑 `promptGateActor`（可拦截/改写 prompt，`machine.ts` L663-724），通过后 `commitPendingToHistory` 把队首 + 通知写入事件 store。
3. **turn 启动**：进入 `running`，emit `turn.started`，spawn TurnMachine（`machine.ts` L727-763）。
4. **step 循环**：gating（服务层 gate：maxSteps、compaction 检查）→ thinking（LLM 流式）→ acting（工具并行）→ draining（收割通知/提醒）→ 回 gating……直到 LLM 不再调工具。
5. **流式增量外送**：`llm.streaming.part` 一路 `forwardToParent` 冒泡到 engine，`createDeltaSplitter()`（`engine.ts` L218-260）把 `StreamedMessagePart` 拆成 `assistant` / `thinking` / `toolCall` 三种 delta 推给 UI。
6. **turn 收尾**：`turn.done/failed/aborted` → 产出的消息写回事件 store（`machineAppended` + `turnEnded`，`machine.ts` L766-784），`endTurn()`（`loopService.ts` L2039）发 telemetry、settle prompt waiter。
7. **空闲等待**：若还有队列项或后台工具（`background`）完成的通知，`idle.ready` 的 `always` 守卫会自动开下一个 turn；否则等待用户输入。ESC 双击可打开 undo 选择器（`editor-keyboard.ts` L250+）。

## 工具系统

**契约**（`src/tool/toolContract.ts`）：工具实现 `ExecutableTool<Input>`，核心是两段式：

```ts
resolveExecution(input: Input): ToolExecution | Promise<ToolExecution>
// ToolExecution = RunnableToolExecution | ExecutableToolErrorResult
// RunnableToolExecution 声明 accesses（资源访问）、approvalRule、
// stopBatchAfterThis、execute(ctx) 等
```

`resolveExecution` 在执行前就能返回错误结果（参数/schema 校验失败、不可用），并且可以重写 toolCall 或声明拒绝（denied）——`ToolMachine.preparing` 态消费这个决策（`human/tool/machine.ts` L100-160）。

**注册**：每个内置工具通过 `registerAgentToolService(IReadTool, ReadTool, { name: 'Read', domain: 'os/backends' })` 注册成 DI collection contribution（如 `tools/os/read/readTool.ts` L623）。内置工具族（`src/agent/tools/`）：`Read/Write/Edit`（edit 目录）、`Bash`（os/bash，含后台任务支持）、`Glob/Grep`、`WebSearch`、`FetchUrl`、`ReadMediaFile`（视频/图像输入）、`AskUserQuestion`、`SelectTools`、`Agent`（子代理）、`task-*`（后台任务管理 task-list/task-output/task-stop/task-wait）。工具描述用 `.md` 文件 `?raw` 导入并模板渲染（`bashTool.ts` 的 `renderBashDescription`）。

**执行与调度**：`AgentToolExecutorService.execute()`（`toolExecutorService.ts` L178+）是 async generator：先逐个 preflight（注册表查找、guard），再 `prepareToolCall`（走 before-execute 事件链 = 权限审批），然后用 **`ToolScheduler`（`toolScheduler.ts`）做冲突调度**——每个工具声明 `ToolAccesses`（对哪些路径 read/write/readwrite/search，或 `all`），`ToolAccesses.conflict()` 判定两个调用是否争抢同一资源；冲突则排队，不冲突则真正并行。这是"并行工具调用"的实现方式：**默认并行、按文件资源访问冲突串行化**。

**结果格式**：`ExecutableToolResult { output: string | ContentPart[], isError?, stopTurn?, truncated?, note?, delivery?, spill? }`。超长输出截断（`DEFAULT_TOOL_RESULT_MAX_CHARS = 50_000`），spill 机制把全量输出落到文件并只保留引用（`spill.outputPath`），上限 `DEFAULT_TOOL_RESULT_MAX_RETAINED_CHARS = 10_000_000`。结果在 turn 层被 `toolOutcomeEntry` 转成 tool message（`turn.ts` L240-251）。

**后台/异步工具**：工具可通过 `detach(ack)` 把自己"脱钩"成后台任务（`ToolEvent 'tool.detached'`），turn 立即拿到 ack 作为结果继续；完成后 AgentMachine 把 `[async tool completed] …` 塞进 notifications，下个 turn drain 时注入上下文（`machine.ts` L159-206、L828-843）。Bash 的 `run_in_background`、超时自动转后台都走这条路。

## 权限与安全

- **权限模式**（`src/agent/permissionMode/`）：全局模式（含 `auto`、yolo 等），可随时切换并广播。
- **策略链**（`src/agent/permissionPolicy/policies/`，每策略一个类，按序 evaluate 返回 `approve | deny | ask | undefined`）：
  - `yolo-mode-approve` / `auto-mode-approve`（模式直批）；
  - `user-configured-allow/ask/deny`（用户规则）；
  - `dangerous-command-ask`：用自研 **tree-sitter-bash 解析器**（`packages/tree-sitter-bash`，纯 TS、无 wasm）静态分析 Bash 命令 AST，识别危险命令（shutdown/mkfs…）、sudo/doas 提权、嵌套 shell（≤4 层）、通配符等，命中则强制 ask（`dangerous-command-ask.ts`，含 `SIMPLE_DANGEROUS_COMMANDS`、`PRIVILEGE_WRAPPERS` 等集合）；
  - `sensitive-file-access-ask`、`git-control-path-access-ask`、`fallback-ask`（默认问）；
  - `session-approval-history`：会话内"始终允许"记忆。
- **审批执行**：`IAgentToolApprovalService.requestToolApproval`（`toolApproval/toolApproval.ts`）把 `ask` 结果发给交互层（TUI 弹窗 / ACP 请求），用户可附反馈，拒绝信息回注成工具结果。
- **路径安全**：`PathSecurityError`（`toolExecutorService.ts` 引 `#/tool/path-access`）约束文件工具的工作区边界。
- **执行环境**：`packages/kaos` 抽象 local/ssh 执行环境，为沙箱化留出接缝（`environment.ts`/`ssh.ts`）。
- auto 模式下 `AskUserQuestion` 被显式 deny（`auto-mode-ask-user-question-deny.ts`：*"AskUserQuestion is disabled while auto permission mode is active; decide and continue."*）。

## 上下文管理与压缩

- **内存模型**：`IAgentContextMemoryService`（`src/agent/contextMemory/`）维护 `ContextMessage[]`（带 origin/undo 锚点），循环产出的 assistant/tool 消息经 `loopEventFold.ts` 折叠进来。
- **投影**：发给 LLM 前经 `IAgentContextProjectorService`（`contextProjector/`）投影：strict 结构修正、旧媒体降质（`MEDIA_DEGRADE_KEEP_RECENT`）、可按快照剥离媒体；请求被拒为过大时自动"降级媒体重发 → 剥离媒体重发"（`llmRequesterService.ts` L565-596）。
- **token 计数**：`session/tokenCounting` + `estimateTokensForMessage`（`llm-adapter/contract/tokens`）估算；`estimateUsedContextTokens` 在每次请求时随 payload 传递。
- **auto-compact（fullCompaction）**：`fullCompactionService.ts`（989 行）在 `onWillBeginStep` / `onDidFinishStep` hook 中检查：
  - 触发阈值：默认 `triggerRatio = 0.85`（占 `max_input_tokens`），另有 `reservedContextSize = 50_000` 的保留窗口触发；`blockRatio` 达到则阻塞（`strategy.ts` L18-28）；
  - 切分点选择 `computeCompactCount`：保留最近 N 条消息（`maxRecentMessages=4` 或占窗口 20%），且只能在"安全边界"切——不能切在 user 消息后、不能切穿 assistant(toolCalls)/tool 配对（`canSplitAfter`，`strategy.ts` L242-250）；
  - 压缩执行：用 LLM 生成"交接摘要"（prompt 在 `compaction-instruction.md`：为接手模型写 handoff——最新请求意图、生效约束、已做之事的高保真记录、仍未知的信息、前进计划），摘要替换被压缩段，溢出时最多再试 3 次（`maxOverflowCompactionAttempts=3`）；
  - compaction 可被用户 ESC 取消（`editor-keyboard.ts` 的 `cancelCurrentCompaction`）。
- **undo**：会话是分支树（wire tree），`undoService` 支持回退 N 个 turn（受 compaction 边界限制，`undo.ts` 的 `UndoAvailability.stoppedAtCompaction`）。

## 中断与错误处理

- **ESC/Ctrl-C 中断**：TUI 的 `editor-keyboard.ts` L228-260：streaming 中 Esc/Ctrl-C → `cancelCurrentStream()` → `session.cancel()` → `loopService.cancel()` → engine `input.abort`。AgentMachine 进入 `aborting`，向 turn 发 `turn.abort` 并 abort 所有工具 scope，**10s 超时后强制 stopChild**（`machine.ts` L920-932 的 `after: { abortTimeout }`）；ToolMachine 在 executing 中收到 `tool.abort` 后由 executor 的 signal 抛出，转为 `tool.aborted`。
- **部分输出抢救**：abort 时 `salvageAborted`（`turn.ts` L408-418）调用 `salvageInterruptedMessage` 把已流出的半截 assistant 消息修整后标记 `source: 'salvaged'` 保留进历史，不丢弃。
- **流取消**：AbortSignal 贯穿 llmActor → requester → OpenAI SDK（`request.params, { signal }`）；`llm.sent` 前重试会 `discardAttemptStream` 回滚 accumulator（`turn.ts` L526-531）。
- **API 重试**：`human/llm/requester/retry.ts`——指数退避（base 500ms ×2，上限 32s，+25% jitter），可重试状态码 `[408,409,429,500,502,503,504,529]`；`Retry-After` 头优先；默认每 step 最多 10 次，`KIMI_CODE_INFINITE_RETRY` 可开无限重试（`llmRequesterService.ts` 引入该 env）。
- **错误分类恢复**：`turn.failure.evaluated` 事件（`turn.ts` L644-705）先问 `LlmRecovery`（如 `credentialsRecovery`——OAuth 过期自动刷新凭证后原样重发），再退到普通重试；appliedRecoveries 记录在案并随 `llm.sent` 事件上报。
- **maxSteps**：`maxStepsPerTurn` 可配（`loop_control.max_steps_per_turn`），超限抛 `LOOP_MAX_STEPS_EXCEEDED`，错误消息直接教用户怎么调配置（`loop.ts` L55-62）；通知类消息可 `bypassMaxSteps`。

## 会话持久化与恢复

- **事件溯源**：核心是 `createEventStore`（`human/eventStore/eventStore.ts`）：状态 = slices（reducer 集合）对 journal 中事件的重放（refold）。dispatch 先跑 reducer（Immer produce）再 append journal；内部事件排队 drain（上限 100 防无限循环）。**状态机和会话历史都是同一种事件流**，`agentSlices`（`human/agent/slices.ts`）定义 history/queue/notifications/reminders/turnIndex 等 slice。
- **wire 日志**：`WireService`（`src/wire/wireService.ts`）是 append-log 持久化实现（`IAppendLogStore`，backends：node-fs / minidb / memory），带版本迁移（`migration/`）、损坏修复（`repair.ts`）、分支树（`tree/`，支持 fork/undo 的 branch 切换）。机器引擎的 journal 通过 `wireStoreJournal(this.wire, ENGINE_JOURNAL_DOMAIN)` 直接落在 wire 上（`loopService.ts` L207）。
- **恢复**：重启后 `restoring` 态从 journal refold 出 queue/notifications/turn 计数（`machine.ts` L599-618），`loopService.rebuildRestoredRecords()`（L263）重建 prompt waiters，未完成的队列继续执行；`historyEndsMidToolChain` 守卫（`machine.ts` L237-242）检测"历史停在半截工具链"的情况，允许用户 `input.continue` 续跑。
- **会话管理**：`app/sessionManager`、`sessionIndex`、`sessionExport`；旧 Python CLI 数据可 `kimi migrate` 迁移（`packages/migration-legacy`）。

## 特色功能（与循环的联动）

- **子代理（subagent）**：`Agent` 工具（`tools/agent/`）spawn "same-process loop instance"——同进程内另一个完整 agent scope + 独立上下文 + 独立 wire 文件（`agent.md`），支持后台并行（`agent-background-enabled.md`）、resume 已有子代理、实验性 fork 当前上下文（`spawn.ts` 的 fork 兼容性校验）。内置 profile：`coder`（默认）、`explore`、`plan`。
- **任务系统**：`src/agent/task/` 提供前台/后台任务的注册、超时自动转后台（`autoBackgroundOnTimeout`）、task-list/output/stop/wait 工具族。
- **steer（转向）**：运行中用户新输入不打断循环，而是经 `input.steer` 把队列中的 prompt 合并改写成 `inTurn` 通知注入当前 turn（`machine.ts` L490-523），turn 在 draining 态吃到后带着新指示继续。
- **ACP 集成**：`packages/acp-server` 把会话映射到 Agent Client Protocol；`apps/kimi-code` 还有 VS Code 扩展（`apps/vscode`）。
- **插件/技能/MCP**：marketplace 安装（信任级别展示）、AI 原生 `/mcp-config`、lifecycle hooks。
- **多协议模型层**：Kimi 走 OpenAI 兼容 chat completions（`llm-kimi/trait.ts`），同时完整实现 Anthropic / Google GenAI / OpenAI Responses 协议 base（`llm/requester/bases/`），trait 机制做 per-provider 差异（Kimi 的 thinking 配置、`prompt_cache_key`、choice 级 usage 提取等）。

### 流式处理细节（Kimi API）

- **协议**：标准 OpenAI 风格 SSE——官方 `openai` npm SDK 的 `client.chat.completions.create(params, { signal }).withResponse()` 拿到 async iterable chunk 流（`bases/openai/requester.ts` L161-196），`for await (const chunk of stream)` 逐 chunk 解析。Kimi 默认 base URL `https://api.moonshot.ai/v1`，`KIMI_API_KEY`/`KIMI_BASE_URL` env 可覆盖（`llm-kimi/trait.ts` L17-25）。
- **事件化**：底层把 chunk 解析成 `llm.streaming.headers/part/usage/finish/message_id` 事件流（`requester.ts` L25-35 的 `LlmRequestEvent`），turn 的 accumulator 消费 part 累积消息、usage/finish/messageId 存进 assistant meta。
- **累积器**：`createHistoryAccumulator`（`turn.ts` L120-174）包一层 `ToolCallIdNormalizer`——流式 tool call id 与 index 的重映射/去重（provider 重复 id 改写为 agent 唯一 id，`llmRequesterService.ts` L466 附近有对应 warn 日志），支持 `rollback()` 丢弃失败尝试的半截流。
- **thinking 流**：`think` part 携带 `encrypted`/`reasoningKey`/`detailsIndex`（`engine.ts` L228-236），ReasoningKeyDialect 自动探测 Kimi 的 reasoning 字段方言（`bases/openai/reasoning-key.ts`）。
- **usage 双位置**：Kimi 的 usage 可能在顶层也可能在 `choices[0].usage`，trait 的 `extractUsage` 两处都取（`llm-kimi/trait.ts` L151-162）。
- **补全预算**：`kimiUnsetCompletionTokens`（trait L69-76）在未显式设置时按 `窗口 - usedContextTokens` 推导 `max_completion_tokens`，与上下文管理联动。

## 值得借鉴的设计点

1. **纯状态机内核 + DI 服务外壳的双层循环**。`human/`（无 IO 的 xstate 状态机、事件 store）与 `agent/`（DI 服务、hook、telemetry）严格分层，循环核心可同步重放、可单测，持久化/权限/压缩全部从 `gate`/`actor`/`hook` 接缝注入。做长生命周期 agent 时，这种"逻辑与效果分离"比一个巨型 while 循环可维护得多。
2. **事件溯源贯穿始终**。会话状态（含队列、通知、turn 计数）全部是 journal 事件重放的结果（`eventStore.ts` 的 refold + slices），崩溃恢复 = 重放，undo/分支 = 换 journal 起点（wire tree 的 branch）。持久化格式和状态机说同一种语言，没有"序列化快照 vs 内存状态"两套真相。
3. **turn 内四态循环：thinking → acting → draining**。把"工具执行完后收割运行期涌入的消息（异步工具完成、提醒、steer）"做成显式的 draining 态，而不是散落的回调，使异步事件注入点单一、可推理；`steps` 在有新消息时重置、maxSteps 检查放在服务层 gate，防失控与防饿死兼顾。
4. **按资源访问冲突调度并行工具**。`ToolAccesses`（file read/write/search 或 all）+ `ToolAccesses.conflict()` 让同批工具调用默认并行、冲突自动排队（`toolScheduler.ts`），比"全串行"快、比"全并行"安全，且语义对模型可解释。
5. **abort 是一等公民：宽限期 + 半截输出抢救 + 补全 tool 结果**。abort 时未完成工具结果统一补 `aborted` 占位（保证 toolCalls/tool 配对完整）、LLM 半截回复 `salvage` 保留、10s 强杀兜底（`machine.ts` aborting、`turn.ts` salvageAborted/abortOutcomes）——中断后历史仍然"合法"，下一次请求不会被悬空 tool call 卡死。
6. **错误处理分层：recovery > retry > fail**。`turn.failure.evaluated` 先让 recovery 策略提案（凭证刷新后原样重发、消息改写），再指数退避重试（状态码白名单 + Retry-After 优先 + 可选无限重试），最后才失败；每层都向上发 `llm.retrying/recovering` 事件供 UI 呈现。
7. **压缩即"交接摘要"prompt 工程 + 安全切分边界**。compaction 用精心设计的 handoff prompt（要求保留：请求意图、已定/未定决策、高保真命令与结果、未知项、前进计划）而非机械截断；切分点校验保证不切穿 tool 配对（`canSplitAfter`），溢出自动缩圈重试，还支持保留窗口（reservedContextSize）双阈值触发。
8. **两段式工具契约 `resolveExecution → execute`**。解析阶段即可拒绝/改写调用（参数校验、不可用说明、审批规则声明），执行阶段才真正跑；配合 `stopBatchAfterThis`（批内后续跳过）和 `detach`（转后台）语义，工具的"准备-执行-收尾"三态机（preparing/executing/finishing）干净地挂接审批与 hook。
9. **Bash 安全用真解析器而非正则**。`packages/tree-sitter-bash` 纯 TS 解析 Bash AST（带 timeout/节点数预算），危险命令检测在 AST 上做（提权包装、嵌套 shell 深度、重定向目标），解析失败则降级为 ask——比正则匹配可靠且可解释。
10. **工具结果 spill 机制**。超长输出（>50k 字符）截断并可选落盘（`spill.outputPath` 全量保留，10M 上限），上下文里只留引用，配套 task-output 工具按需取回——在"喂给模型"与"不丢失信息"之间取得工程平衡。

## 参考文件清单（仓库相对路径）

主循环与状态机：

- `packages/agent-core-v2/src/human/agent/turn.ts` — TurnMachine，核心 step 循环
- `packages/agent-core-v2/src/human/agent/machine.ts` — AgentMachine，turn/队列外层循环
- `packages/agent-core-v2/src/human/tool/machine.ts` — ToolMachine
- `packages/agent-core-v2/src/human/llm/requester/actor.ts` — LLM 请求 actor
- `packages/agent-core-v2/src/agent/loop/loop.ts` — IAgentLoopService 契约
- `packages/agent-core-v2/src/agent/loop/loopService.ts` — 服务层循环外壳（gate/submit/cancel/endTurn）
- `packages/agent-core-v2/src/agent/loop/machine/engine.ts` — 机器引擎粘合（事件投影、delta 拆分）
- `packages/agent-core-v2/src/agent/loop/machine/requester.ts` — step gate 包装的 LLM requester
- `packages/agent-core-v2/src/agent/loop/machine/tools.ts` — 工具批量执行包装

流式与协议：

- `packages/agent-core-v2/src/human/llm/requester/bases/openai/requester.ts` — SSE 流式实现
- `packages/agent-core-v2/src/human/llm/requester/retry.ts` — 重试/退避策略
- `packages/agent-core-v2/src/human/llm-kimi/trait.ts` — Kimi 协议 trait
- `packages/agent-core-v2/src/agent/llmRequester/llmRequesterService.ts` — 请求服务（降级重发、id 重写）

工具系统：

- `packages/agent-core-v2/src/tool/toolContract.ts` — 工具契约与 ToolAccesses
- `packages/agent-core-v2/src/agent/toolExecutor/toolExecutorService.ts` — 执行服务（preflight/审批/批执行）
- `packages/agent-core-v2/src/agent/toolExecutor/toolScheduler.ts` — 冲突调度器
- `packages/agent-core-v2/src/agent/tools/os/bash/bashTool.ts` — Bash 工具示例
- `packages/agent-core-v2/src/agent/tools/agent/agentTool.ts` / `agent.md` — 子代理工具

权限与安全：

- `packages/agent-core-v2/src/agent/permissionPolicy/policies/dangerous-command-ask.ts` — AST 危险命令检测
- `packages/agent-core-v2/src/agent/toolApproval/toolApproval.ts` — 审批服务契约
- `packages/tree-sitter-bash/` — 纯 TS bash 解析器

上下文与压缩：

- `packages/agent-core-v2/src/agent/fullCompaction/strategy.ts` — 压缩阈值与切分策略
- `packages/agent-core-v2/src/agent/fullCompaction/fullCompactionService.ts` — 压缩服务
- `packages/agent-core-v2/src/agent/fullCompaction/compaction-instruction.md` — 交接摘要 prompt
- `packages/agent-core-v2/src/agent/contextProjector/contextProjectorService.ts` — 发送前投影

持久化：

- `packages/agent-core-v2/src/human/eventStore/eventStore.ts` — 事件溯源 store
- `packages/agent-core-v2/src/wire/wireService.ts` — append-log + 分支树持久化

应用与交互：

- `apps/kimi-code/src/tui/controllers/editor-keyboard.ts` — ESC/Ctrl-C/undo 交互
- `AGENTS.md`（仓库根）— 权威架构地图
