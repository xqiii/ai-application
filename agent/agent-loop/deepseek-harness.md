# DeepSeek Harness (dsh) 的 Agent Loop 实现

> 调研对象：[deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)（2026-10 浅克隆版本，developer preview）。
> 所有结论均附 repo 相对路径与源码引用；行号以当时的 `main` 为准。

## 项目概览

**定位**：DeepSeek 开源的通用 agent harness（对标 Codex CLI / Claude Code），开发者通过 `npx @deepseek-ai/dsh web` 启动，Web UI 默认跑在 `http://127.0.0.1:3080`（README.md）。另有 headless / sdk / sdk-minimal / acp 等 profile 与 Electron 桌面应用。

**语言与构建**：TypeScript 为主（`strict: true`，全 ESM），pnpm monorepo，Node ^22.19 || >=24。`pnpm run build` 用 tsc 出 lib/types、tsdown 打 runtime。附带 Python SDK（内部仍是拉起 `dsh --profile sdk` 子进程）与少量 native 代码。

**Cordis 是什么**：来自 Koishi 生态的可组合插件框架（本仓库 vendor 到 `vendor/`，更名 `@deepseek-ai/cordis`），设计论文为《A Programming Paradigm for Spatiotemporal Composability》(arXiv:2608.25512)。核心概念：

- **Context**：贯穿一切的资源句柄。`ctx.effect()` / `ctx.on()` 注册的清理逻辑随插件卸载自动反向回滚（"Registrations are effects"，AGENTS.md）。
- **Service**：通过 `declare module '@deepseek-ai/cordis' { interface Context { ... } }` 声明合并，把服务挂到 `ctx.xxx` 上（如 `ctx.llm`、`ctx.tools`、`ctx.agents`）。`static inject = [...]` 声明依赖，依赖就绪才激活。
- **类型化事件**：三种调度语义——`emit`（广播）、`waterfall`（洋葱圈，监听器必须调 `next()` 委派，可改写值）、`serial`（顺序 await，无 `next()`）。
- **Fiber**：插件的活性单元，有 `UNLOADING/DISPOSED/FAILED` 状态，卸载时反向执行 effect。

**模块布局**（节选自 AGENTS.md "Repository layout"）：

| 目录 | 内容 |
|---|---|
| `apps/` | cli / web（Vite 前端）/ desktop / desktop-host |
| `packages/core/` | agent、agent-loop（默认 driver）、tools、session、system-prompt、scope 等 |
| `packages/llm/` | 模型 provider 适配（`ctx.llm` seam） |
| `packages/compaction/` | 上下文压缩（basic、tool-result-pruner） |
| `packages/subagent/` | 委托执行（in-process / ACP / Claude Code / Codex / SDK 后端 + tool-subagent） |
| `packages/sandbox/` | 进程限制（Linux Landlock、macOS、Windows ACL） |
| `packages/interaction/` | approval（人工审批）、ask-user、permission-presets、命令 |
| `packages/api/` | Web BFF：session-controller、gateway（WebSocket 流）、typert RPC |
| `packages/boot/` | app-boot（profile 组合）、plugin-manager、HMR |
| `native/` | `@deepseek-ai/node-addon-system`：Landlock launcher 与 POSIX flock 的 Node 原生绑定 |
| `docs/` | 架构文档（architecture.md、agent-lifecycle.md、cordis-primer.md 等） |

**"Everything-is-a-plugin"**（docs/architecture.md）：模型适配器、工具注册表、session log、agent loop 本身全是插件，都能从配置替换。"There is no privileged core to patch"。运行时的 `dsh` 是一个由 **profile → bundle → cordis.yml 行** 组合出的插件树；`dsh --profile web --dump-config` 可打印整棵树，任何一行都可以被上层 patch 覆盖。

## 核心循环在哪：关键文件地图

| 文件 | 职责 |
|---|---|
| `packages/core/agent-loop/src/agent.ts` | **ReactLoopAgent**：真正的 driver——turn/step 循环、step 内的 LLM 调用、流式消费、工具调度入口（约 690 行，核心中的核心） |
| `packages/core/agent-loop/src/index.ts` | **AgentLoop** 服务（`ctx.agentLoop`）：agent 工厂——create/resume、session 持久化、发布与反向 teardown |
| `packages/core/agent-loop/src/tool-calls.ts` | step 内工具调用调度：exclusive 屏障 + parallel 有界滚动池，结果按 model order 提交 |
| `packages/core/agent-loop/src/inbox.ts` | 驱动 agent 的持久化收件箱（next-turn / next-step 两个队列，spliced 事件可重放） |
| `packages/core/agent-loop/src/assistant-stream.ts` | 单次模型尝试的流累积（AssistantStreamAccumulator + BlockAssembler 双轨） |
| `packages/core/agent-loop/src/runtime-context.ts` | system prompt 与 runtime context 在 session surface 上的投影/和解 |
| `packages/core/agent/src/index.ts` | `ctx.agents`：AgentRegistry + AgentFactory 接口 + AsyncLocalStorage initiator 作用域 |
| `packages/core/agent/src/runtime-types.ts` | `agent/*` 事件全集与 Agent 接口 |
| `packages/core/tools/src/index.ts` | `ctx.tools`：ToolRuntime 注册表 + pre-execute/guards/execute/post-execute 管线（约 2000 行） |
| `packages/core/session/src/index.ts` | Session：append-only 事件日志 + `deriveMessages()` |
| `packages/core/system-prompt/src/index.ts` | `ctx.systemPrompt`：prompt 段落/变量/工具 schema 装配 + waterfall |
| `packages/llm/llm/src/index.ts` | `ctx.llm`：LlmRuntime 适配器注册表 + `prepareCall`/`stream` |
| `docs/agent-lifecycle.md` / `docs/architecture.md` | 官方 turn/step 时序图与架构说明（与源码严格同步） |

## 插件架构如何组织 agent loop

**agent loop 本身是一个 Cordis 服务 + 一个默认实现插件**。分层很清楚：

1. `dsh-agent`（packages/core/agent）只定义接口与注册表：`Agent` 接口、`AgentFactory`（`createAgent`/`resume`）、`agent/*` 事件。注册表不知道具体驱动。
2. `dsh-agent-loop`（packages/core/agent-loop）是默认 driver，构造时 `ctx.agents.setFactory(this)` 把自己注册为工厂（index.ts:369）：

```ts
export class AgentLoop extends Service implements AgentFactory {
  static inject = ['agents', 'sessions', 'llm', 'tools', 'systemPrompt', 'sessionProjections']
  constructor(ctx: Context, config: Config) {
    super(ctx, 'agentLoop')
    // ...
    ctx.effect(() => () => this.ownership.dispose(), 'agentLoop.transactions()')
    ctx.effect(() => ctx.agents.setFactory(this), 'agentLoop.setFactory()')
  }
}
```

3. 替换 loop = 卸载这个插件、装另一个实现 `AgentFactory` 的插件，不动其他任何东西。AGENTS.md 明文规定："Plugins, not loop changes: new behavior goes on documented extension points; changing agent-loop requires updating docs/architecture.md."

**各能力如何以插件挂入**（docs/architecture.md "Where new behavior goes" 表）：

| 能力 | 机制 |
|---|---|
| 模型 provider | 在 `ctx.llm` 上 `registerAdapter(providers, adapter)` |
| 模型可见工具 | 在 `ctx.tools` 上注册，schema 自动进入 prompt 装配 |
| Shell 执行 | 注册 `ctx.shell` backend（本地实现经 `ctx.subprocess` 派生） |
| 文件系统/策略 | `ctx.fs` provider 或监听 `fs/*` 事件 |
| 进程限制 | `ctx.sandbox` backend（消费者包装 argv 再 spawn） |
| 拦截请求/工具/turn | `agent/*`、`tools/*` 事件 |
| 模型可见上下文注入 | `agent.inject()`，落到下一个被接纳的请求 |
| 会话级不同能力集 | agent preset + `agent.ctx` scoped 注册 |

**关键组件关系**（loop 视角）：

```text
                 ┌────────────────────────────────────────────┐
                 │            Cordis Context 树               │
                 │                                            │
  AgentLoop ─────┤ ctx.agentLoop   (工厂/驱动插件)             │
       creates   │ ctx.agents      (AgentRegistry+Factory)    │
                 │ ctx.sessions    (Session 事件日志)          │
   ReactLoopAgent┤ ctx.systemPrompt(段落/变量/tools schema 装配)│
   (per session) │ ctx.llm         (LlmRuntime 适配器注册表)   │
       drives    │ ctx.tools       (ToolRuntime 执行管线)      │
                 │ ctx.approval    (人工审批, 可选)             │
                 │ ctx.sandbox / ctx.fs / ctx.shell / ...      │
                 └────────────────────────────────────────────┘
   UI: Web(127.0.0.1:3080) ⇄ WebSocket(typert RPC) ⇄ packages/api/session-controller
       消费 session/event(durable) + agent/*(live)
```

每个 agent 还有一个 **scoped context**（`packages/core/scope`）：`agent.ctx` 下的注册（scoped 工具、prompt 段落）只对该 agent 可见，agent 销毁时随 scope 一起 unwind。工具注册表用 `ScopedLayers` 实现"全局注册 + 局部遮蔽"（tools/index.ts 的 ToolLayer）。

## 主循环详解

### 概念：turn 与 step

docs/architecture.md 的定义：**"A step is one model request plus the tools it calls. A turn is zero or more steps: it opens before its first input is claimed and closes once nothing is owed."**

- `turn`：一次用户对话回合，从 `turn/start` 到 `turn/end`；
- `step`：一次 LLM 请求 + 它引发的所有工具调用；
- 驱动来源是一个持久化 **inbox**：`next-turn`（排队等下一回合）与 `next-step`（steering/inject，等下一个 step 边界）。

### 从真实源码提炼的伪代码

综合 `agent.ts` 的 `kick()`/`turn()`/`step()`、`tool-calls.ts` 与 `inbox.ts`：

```text
# ReactLoopAgent.kick()  (agent.ts:252)
while await turn():            # turn() 返回 false 且 inbox 空则收敛为 idle

# ReactLoopAgent.turn()  (agent.ts:296)
session.append('turn/start', {turn})
target = 'next-turn'
loop:
    abortSignal.throwIfAborted()
    decision = preStep(target, {turn, step})        # 见下
    if decision == reject: turnEnds = blocked; break
    if 是首个 step 且 decision.messages 为空: turnEnds = completed; break   # 空回合不开模型调用
    session.append('step/start', {turn, step})

    stepEnd = step(decision)     # ← 一次 LLM 请求 + 工具执行
    # max-tokens 粘性：后续正常完成的 step 不降级 turn 结局
    finally: session.append('step/end', {turn, step})

    if stepEnd != null and inbox.nextStep 为空:
        dispatch.serial('agent/turn-stopping')      # 终局检查点，无 next()
        break
    target = 'next-step'          # 工具欠一个请求(返回 null)或有 steering → 继续
session.append('turn/end', {turn, reason: turnEnds})
if inbox.hasPending: return true  # 排队消息触发下一回合

# ReactLoopAgent.step(decision)  (agent.ts:398)
firstAttempt = true
loop:  # 重试循环（compaction 溢出恢复在此重入）
    {config, preparedCall} = prepareRequest()        # agent/request waterfall → ctx.llm.prepareCall
    # 同步准入：system prompt 和解 + 首次尝试才追加 user/message
    session.append('system/message', ...) × N
    if firstAttempt: session.append('user/message', decision.messages)
    request = buildRequest()   # 记 request/header、request/context；从日志 deriveMessages() 并 deepFreeze

    live = AssistantStreamAttempt(...)               # start/chunk/end 帧
    stream = preparedCall?.stream(request) ?? ctx.llm.stream(request)   # 经 llm/stream waterfall
    for await chunk in stream: live.push(chunk)      # 累积 + 发 agent/assistant-stream chunk

    if finish == error | aborted:
        session.append('assistant/attempt', {stream})        # 失败尝试只留流，不留模型历史
        action = waterfall('agent/request-error', ...)        # 插件可返回 {kind:'retry'}
        if action != retry: throw LlmError
        continue                                             # 原地重试（不重复 pre-step/装配/用户消息）

    session.append('assistant/message', {message, usage, stream})   # 成功：完整 compact 流内嵌
    if finish == max-tokens: return {kind:'max-tokens'}
    toolCalls = message 中 type=='tool-call' 的块
    if toolCalls 为空: return {kind:'completed'}               # 自然停止
    {concluded} = executeToolCalls(ctx, turn, step, toolCalls, signal, ...)
    return concluded ? completed : null   # null = 工具欠模型一个后续请求 → 下一个 step

# executeToolCalls  (tool-calls.ts:60)
planned = toolCalls 解析参数
while 还有未调度调用:
    mode = ctx.tools.executionMode(first)      # 'parallel' | 'exclusive'
    group = mode=='parallel' ? 剩余全部 : [first]        # exclusive 形成屏障
    runGroup(group):                                # 有界滚动池 (默认 max 10)
        逐个: session.append('tool/call') → prepare(pre-execute+guards+approval) → dispatch(tools/execute waterfall → tool.execute()) 
        并发度满/出现新 exclusive → 停止补充；Promise.race 等待任一完成
        结果按 model order 逐个 finalize(post-execute) → session.append('tool/result')
        additionalContexts → inbox.splice('next-step', ...)   # 注入下一 step 输入
    abort 时：未启动的调用补写合成 error result（保证重放有效）
```

### 关键 TypeScript 片段

**turn 的骨架**（agent.ts:296-396，节选）：

```ts
private async turn(): Promise<boolean> {
  // ...
  this.session.append('turn/start', { turn })
  while (true) {
    signal.throwIfAborted()
    const step = phase.step + 1
    const decision = await this.preStep(target, { turn, step })
    if (decision.kind === 'reject') { turnEnds = { kind: 'blocked' }; return false }
    if (turnEnds && decision.messages.length === 0) break
    if (phase.step === 0 && decision.messages.length === 0) {   // 空首claim不开step
      turnEnds = { kind: 'completed' }; return false
    }
    this.session.append('step/start', { turn, step })
    try {
      const stepEnd = await this.step(decision)
      if (turnEnds === null || turnEnds.kind !== 'max-tokens') turnEnds = stepEnd
    } finally {
      this.session.append('step/end', { turn, step })
    }
    if (turnEnds && this.inbox.nextStep.length === 0) {
      await this.dispatch.serial('agent/turn-stopping', { turn, signal })
    }
    if (turnEnds && this.inbox.nextStep.length === 0) break
    target = 'next-step'
  }
  // ... finally: session.append('turn/end', { turn, reason: turnEnds! })
  if (!this.inbox.hasPending) return false
  return true
}
```

**step 内的流式消费与失败结算**（agent.ts:436-530，节选）：

```ts
const stream = preparedCall?.stream(request) ?? this.loopCtx.llm.stream(request)
live.start()
for await (const chunk of stream) {
  signal.throwIfAborted()
  live.push(chunk)
}
// ...
const finish = live.finish
if (finish.kind === 'error' || finish.kind === 'aborted') {
  live.settle('assistant/attempt', () => this.session.append('assistant/attempt', { turn, step, stream: live.stream }).seq)
  const action = await this.dispatch.waterfall('agent/request-error', { ... }, () => Promise.resolve<RequestErrorAction>(undefined))
  if (action?.kind !== 'retry') { throw new LlmError(finish.failure.message, finish.failure.code, finish.failure) }
  continue   // 原地重试
}
const message = createAssistantMessage({ content: live.blocks(), source: { provider: request.provider, model: request.model } })
live.settle('assistant/message', () => this.session.append('assistant/message', { turn, step, message, stream: live.stream }).seq)
if (finish.kind === 'max-tokens') return { kind: 'max-tokens' }
const toolCalls = message.content.filter(block => block.type === 'tool-call')
if (toolCalls.length === 0) return { kind: 'completed' }
const { concluded } = await executeToolCalls(this.loopCtx, turn, step, toolCalls, signal,
  context => this.inbox.splice('next-step', this.inbox.nextStep.length, 0, [context]))
return concluded ? { kind: 'completed' } : null
```

**请求从日志派生并冻结**（agent.ts:671-686）——这是本实现最独特的一点：请求的 messages 不是内存拼接，而是 `session.deriveMessages()` 从事件日志投影出来后 `deepFreeze`：

```ts
const boundaryMessages = session.deriveMessages()
for (const message of boundaryMessages) {
  if (this.frozenMessages.has(message)) continue
  deepFreeze(message)
  this.frozenMessages.add(message)
}
Object.freeze(boundaryMessages)
const request = markAgentLoopRequest(Object.freeze({
  ...header.config,
  messages: boundaryMessages,
  toolHistory: session.toolHistory(),
  ...header.tools !== undefined ? { tools: header.tools } : {},
  sessionId: this.session.id,
  signal,
}))
```

### pre-step：输入的权威裁决点

`agent/pre-step` 是 waterfall（agent.ts:276-285）。默认决策是 `enter(claimed messages + runtime context)`，监听器可改写消息或 **reject**：

```ts
const decision = await this.dispatch.waterfall(
  'agent/pre-step', { messages: claimed, ...position, signal },
  (): Promise<PreStepDecision> => Promise.resolve<PreStepDecision>({
    kind: 'enter',
    messages: context === undefined ? claimed : [...claimed, context],
  }),
)
```

steering（用户中途插话）与注入上下文走同一条路径——它们只是被 claim 的 `next-step` 批次（docs/agent-lifecycle.md）。

## 一次请求的完整生命周期

以 Web UI 用户发送一条消息为例（结合 docs/agent-lifecycle.md 时序图与源码）：

1. **入站**：浏览器 → WebSocket（`packages/api/gateway/src/stream-server.ts` 的 RemoteStreamMuxServer，多路复用 typert RPC 流）→ `session-controller` 的 prompt 端点 → `agent.followup(message)`。
2. **入队**：`send()` → `inbox.splice('next-turn', ...)`，追加 `agent/inbox/spliced` session 事件（可重放），emit `agent/inbox/inserted`；`wakeDriver()` 把 phase 置为 running 并 emit `agent/status: running`。
3. **开回合**：`turn()` 追加 `turn/start`，claim next-step 全部 + next-turn 一条，逐条 emit `agent/inbox/claimed`。
4. **装配**：`ctx.systemPrompt.assemble()`（`system-prompt/assemble` waterfall，含工具 schema）→ runtime context 投影 → `agent/pre-step` 裁决。
5. **开 step**：`step/start` → `agent/request` waterfall 提议 config → `ctx.llm.prepareCall()` 绑定适配器（防 HMR 中途换适配器）→ 同步和解 system prompt（`system/message`）、首次尝试追加 `user/message`、按需 `request/header`/`request/context` → `deriveMessages()` 冻结请求。
6. **流式**：`preparedCall.stream(request)` 经 `llm/stream` waterfall（重试/路由/回放都在这层）→ 每个 `StreamChunk` 一边进 `AssistantStreamAccumulator`（持久 compact 流）与 `BlockAssembler`（内容块），一边 emit `agent/assistant-stream` chunk 帧（Web 的 Session-follow 适配器是唯一远程消费者，packages/api/session-controller/src/history.ts:54）。
7. **结算**：成功 → `assistant/message`（内嵌完整 compact 流 + usage）→ emit committed end 帧；失败/重试/取消 → `assistant/attempt`（只留流不留历史）→ `agent/request-error` waterfall 决定 retry 或抛出。
8. **工具**：`tool/call` 事件（先记后执行）→ pre-execute waterfall（权限/钩子）→ `ask` 走 `ctx.approval`（fail-closed）→ 单调 guard → `tools/execute` waterfall（超时/重试/指标）→ `tool.execute(args, exec)` → post-execute waterfall（accept/block/replace）→ `finalizeContent` → `tools/result` 通知 → `tool/result` 事件。工具 `deferContext()` 的附加上下文进 next-step inbox。
9. **收敛**：step/end → 若无 pending 输入且工具不欠请求 → `agent/turn-stopping`（serial 检查点）→ `turn/end`（带结构化 reason）→ `agent/status: idle`。
10. **持久化**：所有 session 事件经 mounted persistence backend 异步落盘（JSONL v0/v1+，zstd 压缩，版本化代际，见 docs/architecture.md "Session log"）；UI 重放读 `session/event`，增量更新读 `agent/*`。

## 工具系统

**定义**（`packages/core/tools/src/index.ts:223`）：`ToolDefinition extends ToolSchema`，核心字段：

- `execute(args, exec: ToolRunContext): Promise<unknown>`——只返回**canonical lossless-JSON value**；模型可见内容由 `output` 声明渲染：
  - `output.schema`：JSON Schema，校验每个成功返回值；
  - `output.render(args, value): ContentBlock[]`：纯投影，从值到模型内容；
  - `output.presentationMeta(args, value)`：UI 卡片的可重放私有元数据；
- `projectContent` / `finalizeContent`：执行前快照的两级内容变换钩子；
- `timeoutMs`：协作式超时预算（由 timeout-policy 插件在 `tools/execute` 包装层执行，不进 schema）；
- `isConcurrencySafe?(args): boolean`：只有显式 `true` 才能进 parallel 组；
- `presentCall` / `presentResult`：纯函数 UI 呈现（live 流式与日志重放都会调）。

**注册**：`ctx.tools` 的 ScopedLayers——全局层 + agent scoped 层，同名 scoped 注册遮蔽全局；`ToolRestriction`（allow/deny 名单）做会话级裁剪；注册即 effect，返回 disposer。

**分发与执行管线**（prepare → dispatch → finalize 三段，供 loop 调度器重叠执行）：

1. `tools/pre-execute` waterfall：策略钩子，返回 `allow | deny | cancel | ask`；
2. `ask` → `ctx.approval`（ApprovalService）：无人应答即拒绝（**fail-closed**）；
3. **单调 guard**（`ToolGuard`，注册于 pre-execute 之后）：只能否决不能翻案——"listener ordering cannot turn a denial back into permission"（index.ts:723）；
4. `tools/execute` waterfall：around-dispatch（超时、重试、指标可替换 exec.signal，registry 会与 caller signal 融合防丢失）；
5. tool body；
6. `tools/post-execute` waterfall：`accept`（可换内容/加上下文）/ `block`（反馈变 error result）；
7. `finalizeContent` + 无损 JSON 物化 + `tools/result` 同步通知。

**执行环境与 native/**：`native/` 不是工具沙箱目录，而是 **`@deepseek-ai/node-addon-system`**——Landlock launcher 与 POSIX flock 的原生绑定（native/README.md），被 `dsh-sandbox-local` 用来在 Linux 上做内核级进程限制；macOS/Windows 各有平台包。真正的进程限制 seam 是 `ctx.sandbox`（sandbox/sandbox-policy 按 session 解析策略），shell/PTC/子进程消费者包装 argv 后再 spawn。**PTC（Programmatic Tool Calling）模式**是独树一帜的能力：`tools.mode: 'ptc'` 时模型只见一个 `run_code` 工具 + 自动生成的 TS/Python SDK prompt，工具调用变成在受限 runtime 里执行代码，代码内再 sub-dispatch 原生工具（tools/index.ts 的 createRunCodeTool，mode 为 `native | ptc | both`，可按 agent scope 用 `presentAs()` 选择）。

## 权限与安全

- **审批**（packages/interaction/user-approval）：`ApprovalService` 有 `ask | never` 两种 policy；`ask` 委托给组装的 answerer（Web UI 弹窗等），无 answerer 或不可达 = 拒绝。每次 ask 与 outcome 都以 `approval/ask`、`approval/policy` session 事件落盘（可审计、可重放）。
- **fail-closed 原则**贯穿：approval 不可达即拒；sandbox 不可用抛 `SANDBOX_UNAVAILABLE` 而非裸跑（sandbox-local README）；配置错误 load 时即抛（misconfiguration fails loud）。
- **三层否决设计**：可扩展策略（pre-execute waterfall，可翻案）→ 人工审批 → 单调 guard（不可翻案）。安全不变量放在最后兜底。
- **运行时自修改**（packages/extensions、plugin-manager）：`plugin_manager` 工具要求 `danger-full-access` 或逐调用审批；版本兼容性检查 + `compatibility.json` 豁免制，装插件前 `pnpm view` 预检。

## 上下文管理

- **单一事实源**：模型看到的上下文 = `session.deriveMessages()` 从事件日志投影（session/index.ts:856），增量缓存按 `contentGeneration` 失效。"Model-visible means logged"（architecture.md）——任何到达模型的输入必须可从日志重建。
- **压缩**（packages/compaction/compaction-basic）：纯插件实现，挂两个钩子（index.ts:158-244）：
  - `agent/pre-step`：step 间压力检测（`ctx.tokenMeter.measure(session)`，thresholdRatio/headroomTokens/retainTokens 按 provider+model 策略）；
  - `agent/request-error`：仅当 `CONTEXT_WINDOW_EXCEEDED` 时触发溢出恢复——先跑可选的 tool-result 剪枝，再 LLM 摘要；只有 surface 替换代数确实推进了才返回 `{kind:'retry'}` 原地重试，否则保留原始错误。
  - 摘要调用复用会话自身的 system prompt/tools/messages 前缀，**不打破 provider 的 KV cache**（summarize() 的设计说明）。
  - 压缩本身也是 surface 替换事件（shadowed range + 摘要消息），重放一致。
- **文件上下文注入**：工具结果经 `additionalContexts` / `deferContext()` 进入 next-step inbox，在下一个请求被 claim——不绕过日志。
- **token 预算**：`ctx.tokenMeter` 服务统一计价；maxTokens 由 envelope 或 adapter 默认解析（`adapterDefaults` 标记哪个值是适配器填的，下个 step 提议时剔除，agent.ts:64-70）。

## 中断与错误处理

- **取消**：三源融合的 AbortSignal——caller cancel、owner fiber unload、factory teardown（index.ts prepare() 的 abort controller 融合）。`cancel(cause: AgentCancelCause)` 带结构化原因（`user | parent | disposed | hook`），记入 `turn/end` 的 `aborted` reason。
- **中断时的流保全**：stream 中途取消，已产出的安全前缀（`interruptedBlocks()`）作为 `interrupted: true` 的 `assistant/message` 提交；无可见内容则记 `assistant/attempt`（agent.ts:445-486）。
- **工具中断一致性**：abort 后未启动的调用补写合成 error result（"tool call aborted before dispatch"），保证任何时刻日志重放都有效（tool-calls.ts:250-260）；step 失败时 `ToolCallRecovery` 观察器为 pending 调用补记缺失结果（agent.ts:331-353）。
- **重试**：模型请求重试不在 loop 硬编码——`agent/request-error` waterfall 由插件回答；`llm` 层另有 per-provider `retryPolicy`；compaction 溢出恢复也在同一 waterfall 内（同 step 内重试，不重复装配/pre-step/用户消息）。
- **错误结构化**：turn 失败以 `{kind:'error', error: {message, code}}` 记入 `turn/end`；LlmError 保留原失败，其他错误经 `errorChain` 拍平为 `UNKNOWN`。驱动边界 containment：`kick()` 捕获一切异常避免进程崩溃。
- **崩溃恢复**：resume 时对中断的最后一个 turn 追加合成 closers（缺失工具错误、step/end、turn/end），经同一写 handle 落盘（index.ts:840-863，`interruptedTurnClosers`）。

## 子代理与并行执行

- **子代理 seam**（packages/subagent）：`ctx.subagents` 统一接口，多个后端实现——`subagent-fork-in-process`（fork 当前会话）、`subagent-spawn-in-process`（进程内新 agent）、`subagent-acp`（子进程 ACP agent）、`subagent-claude-code` / `subagent-codex`（桥接其他产品！）、`subagent-dsh-sdk`。`tool-subagent` 把后端暴露为模型可调的命名工具，有 `one-shot` 与 `continuable`（后台持续子代理，返回 id 供后续消息）两种模式；`delegationDepth` 递归预算防无限委托。
- **工具级并行**：同 step 内 `isConcurrencySafe` 的调用进有界滚动池（默认 10），exclusive 调用形成排序屏障；dispatch 可重叠但 policy 与结果提交严格按 model order（tool-calls.ts）。
- **Agent Teams**（experimental）：`ctx.agentTeams` 上的 opt-in 协作 seam——durable roster、task board、mailbox，叠加在 continuable subagent 之上（architecture.md）。

## 与 Codex / Claude Code 相比的独特点

1. **loop 可替换**：Codex/Claude Code 的循环是二进制内的固定引擎；dsh 的 `agent-loop` 只是默认插件，`AgentFactory` 是 seam。
2. **事件溯源式上下文**：请求 messages 永远从 session 日志派生并冻结，不是内存数组——"model-visible means logged" 是硬约束（连系统提示都是 surface 节点，可被压缩替换）。
3. **三域事件模型**：`session/*`（durable 事实）、`agent/*`（live 控制）、capability 事件（`fs/*`、`tools/*`、`telemetry/*`），边界清晰到连数据格式都有版本化迁移链。
4. **dsh-plugin 生态**：任何 npm 包声明 `dsh.bundle` 字段即可成为可安装 bundle；Plugin Manager 支持 registry fallback、GitHub 预检、版本兼容豁免、装进运行中的进程（HMR）。
5. **PTC 模式**：把全部工具折叠成 `run_code` + 生成式 SDK，模型写代码调工具而非逐个发 tool call。
6. **桥接竞品**：subagent-claude-code / subagent-codex 把竞品 agent 当作委托后端。

## 值得借鉴的设计点

1. **"Plugins, not loop changes" 的扩展纪律**：新行为一律走已文档化的 extension point（事件/waterfall），修改 loop 本身需要同步改 architecture.md。这让 loop 保持约 700 行的可审计核心，所有策略（压缩、重试、提醒、审批）都是旁挂插件。收益：核心稳定、策略可组合可卸载。
2. **事件溯源作为上下文管理方式**：`deriveMessages()` + surface 替换语义让压缩/分叉/重放/持久化天然一致——不存在"内存里的 messages 数组和磁盘不同步"这类问题。对任何长会话 agent 都是强架构。
3. **失败尝试与成功消息分离**（`assistant/attempt` vs `assistant/message`）：失败/取消/重试的流留档但不进模型历史；成功消息内嵌 compact 流。崩溃恢复、错误分析、token 审计都有据可查。
4. **原地 step 重试语义**：`agent/request-error` waterfall 返回 retry 时，同 step 内只重做准备与请求派生，不重复 pre-step、prompt 装配、用户消息提交。上下文溢出恢复因此零浪费且不产生重复日志。
5. **工具结果按 model order 提交 + 并行 dispatch 重叠**：调度器三段式（prepare/dispatch/finalize）让策略有序而执行并发；中断时为未启动调用补合成结果保证重放闭合。这是并行工具执行里最严谨的处理方式之一。
6. **单调 guard 分层**：可扩展策略可翻案、guard 只能否决——安全不变量不会被监听器顺序意外放开。审批 fail-closed 同理。
7. **inbox 双队列（next-turn / next-step）**：followup / steer / inject 三个动词映射到两个 durable 队列，steering 天然在下个 step 边界生效，且全部是可重放的 splice 事件。
8. **prepareCall 绑定一代注册**：模型请求的准备与派生共享同一 adapter registration 快照，HMR/热替换适配器不会拼出"半个旧半个新"的请求；prepared call 只能派生一次。
9. **压缩摘要复用会话前缀以保住 KV cache**：把"摘要请求"构造成原对话的前缀延续，是对真实推理服务成本优化的细节。
10. **文档与代码的强同步**：turn 流程、事件图、组件表全部在 docs/ 且有 CI 门（doc-sync）；时序图由脚本生成。研究这种仓库几乎不需要读偏——文档即架构事实。

## 参考文件清单（repo 相对路径）

核心循环：

- `packages/core/agent-loop/src/agent.ts` — ReactLoopAgent（turn/step 主循环）
- `packages/core/agent-loop/src/index.ts` — AgentLoop 工厂与生命周期
- `packages/core/agent-loop/src/tool-calls.ts` — 工具调度（屏障/滚动池/model order）
- `packages/core/agent-loop/src/inbox.ts` — durable inbox 投影
- `packages/core/agent-loop/src/assistant-stream.ts` — 流累积与结算
- `packages/core/agent-loop/src/runtime-context.ts` — system prompt / runtime context 投影
- `packages/core/agent-loop/src/constants.ts` — DEFAULT_MAX_PARALLEL_TOOL_CALLS = 10

接口与事件：

- `packages/core/agent/src/index.ts` — AgentRegistry / AgentFactory / initiator 作用域
- `packages/core/agent/src/runtime-types.ts` — `agent/*` 事件全集
- `packages/core/agent/src/dispatch.ts` — agent 事件分发

周边子系统：

- `packages/core/session/src/index.ts` — Session 日志与 `deriveMessages()`
- `packages/core/session/src/surface.ts` — SurfaceManager 折叠
- `packages/core/system-prompt/src/index.ts` — prompt 装配 waterfall
- `packages/core/tools/src/index.ts` — ToolRuntime 管线
- `packages/llm/llm/src/index.ts` — LlmRuntime / prepareCall / llm-stream waterfall
- `packages/compaction/compaction-basic/src/index.ts` — 压缩触发与恢复
- `packages/subagent/`（各 README）— 委托后端
- `packages/sandbox/sandbox-local/README.md` — 进程限制
- `packages/interaction/user-approval/src/index.ts` — ApprovalService
- `packages/boot/plugin-manager/README.md` — dsh-plugin 安装生态
- `packages/api/gateway/src/stream-server.ts` — WebSocket 多路复用
- `packages/api/session-controller/src/history.ts` — Session-follow（assistant-stream 远程消费）

文档：

- `README.md`、`AGENTS.md`
- `docs/architecture.md` — turn flow / 组件表 / 扩展点表
- `docs/agent-lifecycle.md` — turn/step 时序图
- `docs/tool-execution-pipeline.md` — 工具管线流程图
- `docs/cordis-primer.md` — Cordis 入门
- `native/README.md` — node-addon-system（Landlock/flock）
