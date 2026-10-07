# Pi (badlogic/pi-mono) 的 Agent Loop 实现

> 调研对象：`github.com/badlogic/pi-mono`（现已迁移为 `github.com/earendil-works/pi`，作者 Mario Zechner / @badlogic），MIT 协议。
> 调研基于源码 shallow clone，commit `28dcce2`（2026-10-05），包版本 `pi-coding-agent@1.0.4`。
> 所有结论均出自真实源码，文件路径为 repo 相对路径。

## 项目概览：定位、monorepo 包布局、依赖关系

Pi 自我定位是"minimal, extensible agent harness"——一个极简、可扩展的编码代理骨架。README 明确说它刻意不做 sub-agent 和 plan mode，让用户用 extension 自己拼。它的分层非常干净，这正是它适合当作 agent loop 教材的原因。

### monorepo 包布局（`packages/`）

| 包 | npm 名 | 职责 | 与 loop 的关系 |
|----|--------|------|---------------|
| `packages/agent` | `@earendil-works/pi-agent-core` | **通用 agent runtime：主循环本体** | ★ 核心循环在这 |
| `packages/ai` | `@earendil-works/pi-ai` | 统一多 provider LLM 客户端（40+ provider，10 种 API 方言） | loop 通过 `StreamFn` 接口调用它 |
| `packages/coding-agent` | `@earendil-works/pi-coding-agent` | 编码代理 CLI：AgentSession、工具、extension、session 持久化、compaction | 策略层，不含 loop |
| `packages/tui` | `@earendil-works/pi-tui` | 差分渲染终端 UI 库 | 纯 UI |
| `packages/mcp` / `packages/codemode` / `packages/evals` / `packages/durable` / `packages/chord` / `packages/protocol` / `packages/server` / `packages/client` / `packages/telemetry` / `packages/env` | — | MCP 接入、非 LLM 模型脚本、评测、持久会话运行时、服务组合等 | 外围 |

### 依赖关系（来自各 package.json）

```
pi-tui（无内部依赖）
pi-ai   → pi-telemetry, openai/anthropic/google SDK, typebox
agent   → pi-ai, typebox          ← 仅两个依赖，loop 本体与 provider 完全解耦
coding-agent → pi-agent-core, pi-ai, pi-tui, pi-mcp, pi-codemode, chord
```

关键点：**`packages/agent`（loop 所在包）不依赖任何 UI 和任何具体 provider**。它对 LLM 的全部认知是一个 `StreamFn` 函数类型；`coding-agent/src/core/sdk.ts` 里用 `setDefaultStreamFn(streamSimple)` 把 pi-ai 的实现注入进去。方向是外层依赖内层、通过函数注入反转。

## 核心循环在哪：关键文件地图

```
packages/agent/src/
├── agent-loop.ts   (~940 行) ★ 主循环：runAgentLoop / runLoop / streamAssistantResponse
│                              / executeToolCalls(Sequential|Parallel) / prepareToolCall
├── agent.ts        (~610 行)  Agent 类：有状态包装（transcript、steering/followUp 队列、abort）
├── types.ts        (~530 行)  AgentLoopConfig（全部钩子）、AgentTool、AgentEvent、AgentToolResult
├── stream-fn.ts    (~25 行)   默认 StreamFn 注入点
└── proxy.ts        (~400 行)  Agent 的事件代理工具（多消费者分发）

packages/ai/src/
├── types.ts                    AssistantMessageEvent（统一流事件协议）、Message、Tool
├── models.ts                   ModelsImpl.streamSimple：按 provider 分发
├── utils/event-stream.ts       EventStream / AssistantMessageEventStream（push 型异步迭代器）
└── api/*.ts                    每种 API 方言一个适配器（anthropic-messages、openai-responses…）
└── providers/*.ts              40+ provider 定义（模型目录、鉴权）

packages/coding-agent/src/core/
├── agent-session.ts  (~4383 行) ★ AgentSession：持久化/重试/compaction/extension 编排
├── agent-session-runtime.ts     各运行模式共用的宿主（创建/切换 session）
├── sdk.ts                       createAgentSession()：SDK 编程入口
├── system-prompt.ts             system prompt 分节组装（sections 机制）
├── session-manager.ts (~2000 行) JSONL append-only 会话树
├── compaction/compaction.ts     compaction 触发判定 + 摘要生成
├── tools/{bash,edit,read,write,grep,find,ls}.ts  内置工具
└── extensions/types.ts (~2260 行) ExtensionAPI：不改核心循环的扩展点
```

## 主循环详解

这是全文重点。pi 的核心 loop 在 `packages/agent/src/agent-loop.ts`，一个文件、约 940 行（含工具执行的全部编排），纯函数式——不持有状态，状态由调用方以 `AgentContext`（`{messages, tools}`）快照传入，钩子以 `AgentLoopConfig` 传入，输出走 `emit(event)` 回调。

### 从真实源码提炼的完整伪代码

```text
runAgentLoop(prompts, context, config, emit, signal, streamFn):
  # 1. 初始消息进入前，向模型"宣告"工具集变化（以 system 消息携带 toolsAdded/toolsRemoved）
  initialMessages = declareToolChanges(context, prompts)
  context.messages += initialMessages
  emit(agent_start); emit(turn_start)
  runLoop(context, newMessages, config, signal, emit, streamFn)
  return newMessages

runLoop:                       # ← 核心双层循环
  pendingSteering = config.getSteeringMessages()   # 启动前先检查排队消息
  while true:                                      # ── 外层：follow-up / 显式续跑
    hasMoreToolCalls = true
    while hasMoreToolCalls or pendingSteering:     # ── 内层：tool call 驱动
      # (a) 回合间钩子：compaction、system prompt 刷新、换模型都在这里发生
      snapshot = config.prepareNextTurn(lastCompletedTurn)   # 可替换 context/model/thinkingLevel/追加消息
      prepare 期间新排入的 steering 也要捞一次
      emit(turn_start)

      # (b) 注入本回合要处理的用户/系统消息（带工具集变更声明）
      for m in declareToolChanges(context, prepared + steering):
          emit(message_start/end); context.messages.push(m)

      # (c) 请求前最后一钩子（每次 provider 请求前调用）
      config.prepareRequest({context, model, thinkingLevel})  # 可再替换 context/model

      # (d) 流式拿一条 assistant 消息（见下 streamAssistantResponse）
      message = streamAssistantResponse(context, config, signal, emit, streamFn)

      # (e) 错误/中止是硬退出：合成事件序列后 return
      if message.stopReason in (error, aborted): emit(turn_end, agent_end); return

      # (f) 执行工具
      toolCalls = message.content.filter(type == toolCall)
      if toolCalls:
          if message.stopReason == "length":        # 输出被截断 → 参数可能不完整
              results = 每个 toolCall 都返回 error tool result（不执行，让模型重发）
          else:
              results = executeToolCalls(...)       # sequential 或 parallel
          context.messages += results               # append toolResult 消息
          hasMoreToolCalls = !batch.terminate       # 全部结果 terminate=true 才提前终止
      else:
          hasMoreToolCalls = false

      # (g) 回合收尾钩子（extension 的 turn_end 由此驱动），可决定 end / continue
      decision = config.finishTurn(lastCompletedTurn)
      emit(turn_end)
      if decision == end: emit(agent_end); return

      # (h) 打捞 steering（用户在模型思考/工具运行期间输入的消息）
      pendingSteering = config.getSteeringMessages()

    # 内层退出 = 没有工具调用了。检查 follow-up（agent 停下之后才该处理的消息）
    followUps = config.getFollowUpMessages()
    if followUps: pendingSteering = followUps; continue
    if explicitContinuation: continue               # finishTurn 要求再来一回合
    break                                           # 真正结束
  emit(agent_end, newMessages)
```

### 关键源码引用

**双层循环骨架**（`agent-loop.ts` L179-321，节选）：

```typescript
// Outer loop: continues when queued follow-up messages arrive after agent would stop
while (true) {
    let hasMoreToolCalls = true;
    // Inner loop: process tool calls and steering messages
    while (hasMoreToolCalls || pendingMessages.length > 0) {
        let preparedMessages: AgentMessage[] = [];
        if (lastCompletedTurn) {
            const nextTurnSnapshot = await config.prepareNextTurn?.(lastCompletedTurn);
            // ... 可替换 context / model / thinkingLevel
        }
        // 注入 steering / prepared 消息 → prepareRequest → 流式响应
        const message = await streamAssistantResponse(currentContext, config, signal, emit, streamFunction);
        if (message.stopReason === "error" || message.stopReason === "aborted") {
            /* finishTurn + turn_end + agent_end, return */
        }
        const toolCalls = message.content.filter((c) => c.type === "toolCall");
        const toolResults: ToolResultMessage[] = [];
        hasMoreToolCalls = false;
        if (toolCalls.length > 0) {
            const executedToolBatch =
                message.stopReason === "length"
                    ? await failToolCallsFromTruncatedMessage(toolCalls, emit)
                    : await executeToolCalls(currentContext, message, config, signal, emit);
            toolResults.push(...executedToolBatch.messages);
            hasMoreToolCalls = !executedToolBatch.terminate;
            /* push 进 context 与 newMessages */
        }
        const decision = await config.finishTurn?.(lastCompletedTurn, signal);
        await emit({ type: "turn_end", message, toolResults });
        if (decision?.action === "end") { /* agent_end, return */ }
        pendingMessages = (await config.getSteeringMessages?.()) || [];
    }
    const followUpMessages = (await config.getFollowUpMessages?.()) || [];
    if (followUpMessages.length > 0) { pendingMessages = followUpMessages; continue; }
    if (explicitContinuation) { explicitContinuation = false; continue; }
    break;
}
await emit({ type: "agent_end", messages: newMessages });
```

**LLM 调用边界：AgentMessage 到 Message 的转换只发生在这里**（`streamAssistantResponse`，L381-468）：

```typescript
async function streamAssistantResponse(context, config, signal, emit, streamFunction) {
    let messages = context.messages;
    if (config.transformContext) {                       // 上下文窗口管理等 AgentMessage 级变换
        messages = await config.transformContext(messages, signal);
    }
    const llmMessages = await config.convertToLlm(messages);   // AgentMessage[] → Message[]
    const llmContext = normalizeContext({ messages: llmMessages });
    const resolvedApiKey =
        (config.getApiKey ? await config.getApiKey(config.model.provider) : undefined) || config.apiKey;
    const response = await streamFunction(config.model, llmContext, { ...config, apiKey: resolvedApiKey, signal });

    let partialMessage: AssistantMessage | null = null;
    for await (const event of response) {
        switch (event.type) {
            case "start":
                partialMessage = event.partial;
                context.messages.push(partialMessage);      // 流式中就把 partial 放进 transcript
                await emit({ type: "message_start", message: { ...partialMessage } });
                break;
            case "text_delta": /* ... 把 event.partial 原地替换最后一条，emit message_update */
            case "done":
            case "error": {
                const finalMessage = await result();        // 等最终 AssistantMessage
                context.messages[len-1] = finalMessage;     // 原位替换 partial
                await emit({ type: "message_end", message: finalMessage });
                return finalMessage;
            }
        }
    }
}
```

**停止条件总结**：内层循环继续的条件是"上一条 assistant 消息还有 tool call（未被 terminate）或有 steering 消息"；没有任何 tool call → 模型认为任务完成 → 检查 follow-up 队列和 finishTurn 的显式 continue → 都没有才 `agent_end`。错误与中止是唯一的中途硬退出。

**截断防护**（L478-503）：`stopReason === "length"` 时流式 tool 参数经 best-effort JSON salvage 可能"能解析但悄悄不完整"，pi 不执行它们，而是给每个 tool call 回一条 error result 让模型重发——这是一个很容易被忽略的坑的教科书处理。

**工具执行的统一管线**（`prepareToolCall` → `executePreparedToolCall` → `finalizeExecutedToolCall`）：

```text
prepareToolCall:   找工具（找不到→error result）→ prepareArguments 兼容垫片
                   → validateToolArguments（typebox schema 校验）
                   → beforeToolCall 钩子（block/terminate）→ 多次检查 signal.aborted
executePrepared:   tool.execute(toolCallId, args, signal, onUpdate)
                   onUpdate 流式部分结果 → tool_execution_update 事件
                   抛异常 → catch 成 error result（工具错误永不击穿循环）
finalizeExecuted:  afterToolCall 钩子可逐字段覆写结果（content/details/isError/usage/terminate）
```

parallel 模式（默认）：串行 preflight（校验+beforeToolCall），允许的工具并发执行，`tool_execution_end` 按完成顺序发出，而 toolResult 消息按 assistant 消息中的原始顺序入列——保证 provider 要求的 tool_use/tool_result 配对顺序。单个工具可用 `executionMode: "sequential"` 声明自己必须独占（如写文件）。

**事件协议**（`types.ts` L514-529）：`agent_start → (turn_start → message_* → tool_execution_* → turn_end)* → agent_end`。UI、持久化、extension 全部只消费这一条事件流——循环对"谁在听"零感知。

## AgentSession 与运行模式

### Agent（`agent.ts`）：裸 loop 的有状态外壳

`runAgentLoop` 是无状态纯函数；`Agent` 类包住它，提供：

- 持有 `AgentState`（messages / tools / model / thinkingLevel / isStreaming / streamingMessage / pendingToolCalls / errorMessage）；
- `prompt()` / `continue()` / `abort()` / `waitForIdle()`，同一时刻只允许一个 activeRun（重复 prompt 直接 throw）；
- `steer()` / `followUp()` 两个消息队列（`PendingMessageQueue`，支持 `one-at-a-time` / `all` 两种排空模式），分别对接 loop 的 `getSteeringMessages` / `getFollowUpMessages`；
- 事件订阅 `subscribe(listener)`，listener 收到 (event, signal)；
- 运行级异常被 `handleRunFailure` 合成为一条 `stopReason: "error"|"aborted"` 的 assistant 消息再走完整事件序列——**上层永远看到规整的事件流，而不是异常**。

### AgentSession（`agent-session.ts`）：策略与持久化层

AgentSession 持有一个 Agent，构造函数里这句注释就是它的全部职责说明（L499-500）：

```typescript
// Always subscribe to agent events for internal handling
// (session persistence, extensions, auto-compaction, retry logic)
this._unsubscribeAgent = this.agent.subscribe(this._handleAgentEvent);
```

它通过**包装 Agent 的钩子**（而不是改 loop）实现所有高级功能——`_installAgentToolHooks()`（extension 的 tool_call/tool_result）、`_installAgentNextTurnRefresh()`（compaction + system prompt 刷新挂在 `prepareNextTurnWithContext`）、`_installAgentBoundaryHooks()`（turn_end 边界事件挂在 `finishTurn`）。例如：

```typescript
this.agent.prepareNextTurnWithContext = async (turn, signal) => {
    const context = await this._compactBeforeNextAssistantResponse(turn.context);  // ← compaction 在这里进入循环
    // ...重建 system prompt sections、声明工具集变化
};
```

此外还管：模型切换与 thinking level、`!` bash 直执行、会话树分支/fork、按 session JSONL 持久化。

### 四种（+1）运行模式（`packages/coding-agent/src/modes/`）

| 模式 | 入口 | 形态 |
|------|------|------|
| interactive | `modes/interactive/interactive-mode.ts` | pi-tui 全屏 TUI，ESC 中断、双 ESC fork、Ctrl+P 换模型 |
| print | `modes/print-mode.ts` | `pi -p "prompt"` 单发，输出最终文本 |
| json | 同 print-mode + `json-event.ts` | `--mode json`，事件流以 JSONL 输出 |
| rpc | `modes/rpc/rpc-mode.ts` | stdio 上的 JSONL 命令协议（`{type, id}` 命令 / `{type:"response"}` 应答 + 事件流），用于嵌入其他应用 |
| SDK | `core/sdk.ts` 的 `createAgentSession()` | 进程内编程调用，直接拿 `session.prompt()`；OpenClaw 等项目以此集成 |

官方文档 `docs/how-pi-works.md` 明确："All interfaces use the same agent and session mechanisms"——模式只是 AgentSession 之上的 I/O 薄层。

## 流式处理（pi-ai 的 provider 抽象）

### 统一事件协议

每个 API 适配器（`packages/ai/src/api/*.ts`）把 provider 原生流归一化为 `AssistantMessageEvent`（`ai/src/types.ts` L769-785）：

```typescript
export type AssistantMessageEvent =
    | { type: "start"; partial: AssistantMessage }
    | { type: "text_start" | "text_delta" | "text_end"; contentIndex: number; ...; partial }
    | { type: "thinking_start" | "thinking_delta" | "thinking_end"; ...; partial }
    | { type: "toolcall_start" | "toolcall_delta" | "toolcall_end"; ...; partial }
    | { type: "done"; reason: "stop" | "length" | "toolUse" | "deferred"; message: AssistantMessage }
    | { type: "error"; reason: "aborted" | "error"; error: AssistantMessage };
```

设计要点：

1. **每个事件都携带 `partial`**——"response so far" 的累积快照。消费方不需要自己累积 delta；tool call 的 JSON 参数增量累积已经在 adapter 里做完（截断时用 `parseStreamingJson` 做 best-effort salvage，配合 loop 的 length 防护）。
2. **错误即事件**：`StreamFn` 的契约（`agent/src/types.ts` L33-37）是"不许 throw、不许 reject；失败必须编码为流内 `error` 事件 + `stopReason: "error"|"aborted"` 的最终 AssistantMessage"。
3. **`AssistantMessageEventStream`**（`ai/src/utils/event-stream.ts`）是一个手写的 push 型异步迭代器：生产者 `push(event)`，消费者 `for await`，同时暴露 `result(): Promise<AssistantMessage>`——事件流与最终结果一个类型搞定，agent-loop 的 `agentLoop()` 返回值也是同款 `EventStream<AgentEvent, AgentMessage[]>`。

### 分发路径

`ModelsImpl.streamSimple()`（`ai/src/models.ts` L900-908）：

```typescript
streamSimple(model, context, options) {
    const transcript = normalizeContext(context);        // Context → TranscriptContext（brand 类型）
    return lazyStream(model, async () => {
        const provider = this.requireChatProvider(model);
        const { requestModel, requestOptions } = await this.applyAuth(model, options);  // 鉴权/header/env 合并
        return provider.streamSimple(requestModel, transcript, requestOptions);
    });
}
```

- `normalizeContext` 产出带 brand 的 `TranscriptContext`——**system prompt 与 tool 声明永远由 transcript 的 system 消息携带**，裸 `Context` 类型到不了 provider 代码（类型系统强制）。
- `api/` 下每方言一个模块（anthropic-messages、openai-completions、openai-responses、google-generative-ai、bedrock-converse-stream、mistral-conversations、pi-messages…），统一满足 `ProviderStreams` 接口；`providers/` 下 40+ 家只是目录+鉴权差异。`lazy.ts` 做懒加载，冷启动不拉全部 SDK。

## 工具系统

### 工具定义接口（`agent/src/types.ts` L464-497）

```typescript
export interface AgentTool<TParameters extends TSchema = TSchema, TDetails = any> extends Tool<TParameters> {
    label: string;                                             // UI 显示
    prepareArguments?: (args: unknown) => Static<TParameters>; // schema 校验前的兼容垫片
    outputSchema?: TSchema;                                    // structuredContent 的 schema
    execute: (
        toolCallId: string,
        params: Static<TParameters>,
        signal?: AbortSignal,                                  // 中断信号直通工具
        onUpdate?: AgentToolUpdateCallback<TDetails>,          // 流式部分结果
    ) => Promise<AgentToolResult<TDetails>>;
    replay?: "never" | "safe";                                 // 持久化重放策略
    executionMode?: ToolExecutionMode;                         // 本工具是否必须串行
}
// 基础 Tool（ai/src/types.ts L717-722）：{ name, description, parameters: typebox TSchema }
```

schema 用 **typebox**（`Type.Object({...})`，见 `coding-agent/src/core/tools/bash.ts` 的 `bashSchema`），`Static<T>` 直接给 execute 的参数提供编译期类型。`validateToolArguments`（pi-ai）在执行前校验。

### 结果三通道（`AgentToolResult`，L424-446）

```typescript
content: (TextContent | ImageContent)[];  // 给模型看的
details: T;                               // 给 UI/日志看的
structuredContent?: JsonValue;            // 给程序调用方（codemode 脚本）看的，不进模型上下文
isError?: boolean;                        // 不抛异常也能报告失败
terminate?: boolean;                      // 提示循环在本批工具后停止
```

一个 bash 工具同时服务三种消费者，互不污染。内置工具：read / bash / edit / write（默认四件套）+ grep / find / ls / powershell，都在 `coding-agent/src/core/tools/`。

### 审批 / 权限钩子

- 循环层的机制是 `beforeToolCall`（返回 `{block, reason, terminate}` 阻止执行，loop 代为回 error result）和 `afterToolCall`（逐字段覆写结果）。
- AgentSession 把它们桥接到 extension 的 `tool_call` / `tool_result` 事件（`agent-session.ts` L646-712），权限系统由 extension 实现。
- **注意**：pi 本体刻意不带权限系统（README "Pi does not include a built-in permission system"），官方建议用容器化或 Gondolin/OpenShell 沙箱；`beforeToolCall` 是留给审批 UI 的挂点。
- `runToolCall()` 导出函数（`agent-loop.ts` L810-818）让"工具调工具"（嵌套调用）走完全相同的校验+钩子管线。

### 工具集的动态宣告

`declareToolChanges()`（L333-363）：transcript 的 system 消息声明"模型可调用什么"，`context.tools` 是"运行时可执行什么"；每次请求前 diff 出 `toolsAdded` / `toolsRemoved`，以 system 消息插入。重放所有 system 消息恰好得到当前工具集——工具增删对模型是可审计的事件，而不是隐式全局状态。

## 上下文管理与 compaction

### Session 文件格式：append-only JSONL 树

`session-manager.ts`（L977 注释："Manages conversation sessions as append-only trees stored in JSONL files"）。文件为 `{timestamp}_{sessionId}.jsonl`，首行 `SessionHeader`，其后每行一个 entry，**都有 `id` / `parentId` 形成树**；当前 entry 决定 active branch。entry 类型（L43-194）：

```
message | model_change | thinking_level_change | usage | compaction | branch_summary
| custom（extension 私有，不进上下文） | custom_message（进上下文） | context_edit | label | session_info
```

亮点是 `context_edit`：append 一条 edit entry 即可把历史某条 entry 从模型上下文中剔除（`replacement: null`）或替换其 content——**文件永不重写**，回滚/重试/截断都只是追加。`buildSessionProjection()` 重放分支得到模型可见消息序列。

### System prompt 的组装与演进

`system-prompt.ts` 把 prompt 拆成有序 section（`preamble`、`tools`、`rules`、`docs`、`project_context`、`skills`、`cwd`、自定义 section），每个非 preamble section 用 `<name>` 标签包裹。prompt 不是每次整体重发，而是作为 `SystemMessage.sections` 状态记录在 transcript 里；后续 system 消息只发 diff patch（`diffSystemPromptSections`，改了哪个 section 就替换哪个，`null` 表示删除）。模型能把"后来的指令"对应到"原来的段落"，靠的就是标签自界定。

### Compaction：怎么触发、怎么替换历史

触发（`compaction.ts` L120-270 + `agent-session.ts` L2913-3100）：

```typescript
export const DEFAULT_COMPACTION_SETTINGS = { enabled: true, reserveTokens: 16384, keepRecentTokens: 20000 };
export function shouldCompact(contextTokens, contextWindow, settings) {
    return contextTokens > contextWindow - settings.reserveTokens;   // 预留 16k 缓冲
}
```

三条自动路径：(1) **overflow + 重试**——上下文溢出或可恢复截断，删除失败消息、compact、重试一次（只试一次，`_overflowRecoveryAttempted` 防死循环）；(2) **overflow 不重试**——成功响应但超窗口，compact 保留响应；(3) **阈值**——token 用量超 `contextWindow - reserveTokens`。token 计量 = 最后一条有效 assistant usage + 之后消息的 `estimateTokens` 估算（错误/全零 usage 也有兜底路径）。触发点在 `prepareNextTurnWithContext`（下一次请求之前）以及 `agent_end` / prompt 提交之前。

摘要生成（L645-766）：把对话 `convertToLlm` 后序列化进 `<conversation>` 标签，配 `SUMMARIZATION_PROMPT`；已有旧摘要则用 `UPDATE_SUMMARIZATION_PROMPT` 增量合并。这是一次独立的单发 LLM 调用（`completeSummarization`，禁用 cache 写入，自带重试），不是让主对话里的模型自己总结。

替换历史：compact 产生一条 `CompactionEntry {summary, firstKeptEntryId, tokensBefore, systemMessage}`——`firstKeptEntryId` 之后的近期消息（保 `keepRecentTokens` ≈ 20k）原样保留，之前的全部折叠进 summary；同时快照完整的 prompt+tool 状态。**原始 entry 仍留在 JSONL 树里**，随时可以退回。extension 可用 `session_before_compact` 钩子取消或整体替换摘要内容。

## 中断与错误处理

### ESC 中断链路

TUI 的 ESC → `session.abort()`（`agent-session.ts` L2420-2428）→ `agent.abort()` → 本次 run 的 `AbortController.abort()` → 同一个 `signal` 贯穿：`streamFunction`（provider 流以 `error/aborted` 事件终止）、每个 `tool.execute(toolCallId, args, signal, onUpdate)`（bash 工具杀进程树）、以及循环内多个 `signal?.aborted` 检查点（串行批在工具间 break，parallel 批在闭包入口兜底返回 "Operation aborted" error result）。中止后 assistant 消息 `stopReason: "aborted"`，排队消息退回编辑器。compaction、retry、bash 各自有独立 AbortController（`abortCompaction` / `abortRetry` / `abortBash`）。

### 重试

- **循环层不重试**：错误是数据（`stopReason: "error"` + `errorMessage`），走完整事件序列后正常退出——重试是策略，归 AgentSession。
- **AgentSession 自动重试**（L3747-3787）：`_willRetryAfterAgentEnd` 判定最后一条 assistant 是否可重试错误（`isRetryableAssistantError`，排除 context overflow——那归 compaction 管）；`_prepareRetry` 指数退避（`retryDelayMs`，可 `maxRetryDelayMs` 封顶 server 要求的长等待），期间 ESC 可取消；关键一步 `_omitRecoveryAttempt(message)`——追加 `context_edit(null)` 把失败尝试从模型投影中持久剔除，然后 `agent.continue()` 从 toolResult/用户消息处续跑。
- **loop 的续跑接口**：`agentLoopContinue` 要求 context 最后一条必须是 user 或 toolResult 消息（provider 会拒绝其他起点），这是重试能成立的约束。

## 扩展机制

Extension 是加载进 pi 进程的 TypeScript 模块（jiti 加载，支持热改），工厂函数签名 `(pi: ExtensionAPI) => void`（`extensions/types.ts` L2006）。能力面（L1553 起）：

- `registerTool / registerCommand / registerShortcut / registerFlag`——加工具、斜杠命令、快捷键、CLI flag；
- `on(event, handler)` 约 40 个事件，横跨整个分层：`before_agent_start`（可强制替换 system prompt）、`turn_end`（可返回 `{action:"continue"}` 强制续跑）、`message_end`（可整体替换消息）、`tool_call`（阻止执行=审批）、`tool_result`（改写结果）、`session_before_compact`（取消/替换 compaction）、`before_provider_request`（改 payload）…；
- `sendMessage(msg, {deliverAs: "steer" | "followUp" | "nextTurn"})`——直接映射到 loop 的两个消息队列；
- 渲染层：`registerToolRenderer` / `registerMessageRenderer` / `registerMarkdownTransformer`。

架构上的关键：**extension 从不触碰 loop 本身**。AgentSession 在构造时把自己的策略包装进 Agent 的钩子，extension 再挂在 AgentSession 的事件/钩子层——三层互不 import。子代理、plan mode 这类大功能都以此实现（或装别人发布的包）。

## 为什么这个实现适合作为"学习 agent loop 的教材"

与 codex 这类工业实现（Rust、多层抽象、沙箱/审批/会话管理深植于核心）对比，pi 的取舍是：

1. **循环本体独立成包且极小**：`agent-loop.ts` 一个文件装下全部控制流，`packages/agent` 只有两个依赖（pi-ai、typebox）。读完这一个文件 = 理解 agent loop 的全部本质问题：何时继续、何时停、错误往哪放、消息怎么进上下文。
2. **"循环纯函数、状态在外、策略全钩子"**：`runAgentLoop(prompts, context, config, emit, signal, streamFn)` 没有一行代码知道 JSONL、TUI、extension、重试的存在——但 `AgentLoopConfig` 的十来个钩子（prepareNextTurn / prepareRequest / finishTurn / getSteeringMessages / getFollowUpMessages / before/afterToolCall / transformContext / convertToLlm）恰好是所有这些功能的完整接入面。学 loop 时能看到"哪些决策属于循环、哪些不属于"的清晰划界。
3. **错误与中止是协议不是异常**：StreamFn 契约禁止 throw；一切失败收敛为 `stopReason` + 事件。上层（重试、UI、持久化）因此永远面对规整的数据流。
4. **细节坑都有示范解法**：截断的 tool 参数不执行、parallel 执行但结果按源顺序入列、steering 与 follow-up 的语义区分（turn 后注入 vs. run 后注入）、重试前把失败消息从投影中持久剔除——这些是写任何 agent 都会踩的坑。

## 值得借鉴的设计点

1. **双层 while + 两个消息队列内建于循环**。内层由 tool call 驱动，外层由 follow-up 驱动；`getSteeringMessages` 在回合边界、prepareNextTurn 之后等多个时点打捞。用户"边跑边插话"（steering）与"等它做完再说"（follow-up）是两种真实需求，pi 把它们做成循环原语而非外挂。
2. **AgentMessage / Message 双层消息模型 + `convertToLlm` 边界**。应用可以往 transcript 塞任意自定义消息（bashExecution、custom、notification），只在 LLM 调用边界一次性转换/过滤。UI 丰富性与 provider 兼容性互不牵制，且转换点是单一可审计的。
3. **错误即数据（stopReason）贯穿全栈**。从 provider 事件到循环退出到重试策略，没有一条异常路径穿透层边界；`Agent.handleRunFailure` 甚至把运行级异常也合成为规整的事件序列。
4. **append-only JSONL 会话树 + projection**。文件永不重写；分支、回滚、重试剔除（context_edit:null）、compaction 全是追加 entry，`buildSessionProjection` 重放出模型可见上下文。审计性和可恢复性是天然获得的。
5. **System prompt / 工具集作为 transcript 状态，用 diff 更新**。后续 system 消息只携带 sections patch 和 toolsAdded/toolsRemoved，重放即得当前状态。模型可见的每一次环境变化都有据可查，且对支持中途 system 消息的 provider 零成本。
6. **工具结果三通道（content / details / structuredContent）+ isError 不抛异常**。模型、UI、程序调用方各取所需；工具失败是正常业务数据，循环永不被工具异常击穿。
7. **截断消息的 fail-all 防护**。`stopReason === "length"` 时全部 tool call 拒绝执行并回错让模型重发——防止"JSON salvage 出看似合法实则残缺的参数"被静默执行。
8. **重试与 compaction 分层**：循环不重试；AgentSession 按错误类别分流（可重试错误→指数退避+剔除失败消息+continue；overflow→compact-and-retry 一次；阈值→compact 不重试），且 compaction 摘要是独立的旁路单发调用，不污染主对话。
9. **`partial` 携带在每个流事件里**。消费方零累积逻辑，tool call 参数的 delta 合并、签名处理全部封在 provider adapter 内。
10. **`StreamFn`/`setDefaultStreamFn` 注入反转**。循环包零 provider 依赖；换 provider、mock 测试、乃至完全自定的模型运行时都是一个函数参数的事。

## 参考文件清单（repo 相对路径）

核心循环与类型：
- `packages/agent/src/agent-loop.ts` — ★ 主循环（runLoop / streamAssistantResponse / executeToolCalls）
- `packages/agent/src/agent.ts` — Agent 有状态包装（steer/followUp 队列、abort、事件订阅）
- `packages/agent/src/types.ts` — AgentLoopConfig 全部钩子、AgentTool、AgentEvent
- `packages/agent/src/stream-fn.ts` — 默认 StreamFn 注入点

流式与 provider 抽象：
- `packages/ai/src/types.ts` — AssistantMessageEvent 协议、Message/Tool/TranscriptContext
- `packages/ai/src/utils/event-stream.ts` — EventStream / AssistantMessageEventStream
- `packages/ai/src/models.ts` — ModelsImpl.streamSimple 分发与鉴权合并
- `packages/ai/src/api/pi-messages.ts` — API 方言适配器示例

会话与策略层：
- `packages/coding-agent/src/core/agent-session.ts` — AgentSession（持久化/重试/compaction/extension 编排）
- `packages/coding-agent/src/core/session-manager.ts` — JSONL append-only 会话树与 projection
- `packages/coding-agent/src/core/compaction/compaction.ts` — 触发判定、token 估算、摘要生成
- `packages/coding-agent/src/core/system-prompt.ts` — sections 组装与 diff
- `packages/coding-agent/src/core/sdk.ts` — createAgentSession SDK 入口

工具与扩展：
- `packages/coding-agent/src/core/tools/bash.ts` — 内置工具范例（schema/结果三通道/输出截断）
- `packages/coding-agent/src/core/extensions/types.ts` — ExtensionAPI 全量事件与注册面

运行模式与文档：
- `packages/coding-agent/src/modes/interactive/interactive-mode.ts` — TUI 模式（ESC 中断链路）
- `packages/coding-agent/src/modes/print-mode.ts` / `modes/rpc/rpc-mode.ts` — print/json/rpc 模式
- `packages/coding-agent/docs/how-pi-works.md` — 官方架构速写（交叉验证用）
