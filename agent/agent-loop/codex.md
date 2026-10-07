# Codex (OpenAI) 的 Agent Loop 实现

> 研究对象：`openai/codex` 的 Rust 实现 `codex-rs/`。
> 快照：`2f412e60d735b47caeec6b0e79070f673a191c4f`（2026-10-06）。
> 方法：直接读源码，结论附 `文件:行号`。未验证的推断标注 **未确认**。

> **考古提醒**：网上关于 codex 主循环的分析多指向 `core/src/codex.rs` 的 `Codex::run_task`、`Op::UserInput`、`SQ/EQ`、`core/src/conversation_history.rs`。**本快照里这些都已不存在**：`core/src/codex.rs` 已拆分，`Op::UserInput` 演进为 `Op::TurnInput`，历史搬到 `core/src/context_manager/`，会话编排搬到 `core/src/session/`。本文描述当前真实结构。

---

## 项目概览

| 维度 | 数值 |
| --- | --- |
| 语言 | Rust（edition 2024，`rust-toolchain.toml` 锁定） |
| crate 数 | 118（`codex-rs/` 下顶层目录） |
| `codex-rs/` 全部 `.rs` 行数 | 约 201 万（含大量生成代码与测试） |
| `core/src` | 249,220 行 |
| `protocol/src` | 31,775 行 |

行数被生成/大表文件显著放大（`protocol/src/models.rs` 4,566 行、`permissions.rs` 4,674 行、`openai_models.rs` 2,009 行）。真正与 loop 相关的逻辑集中在 `core/src/` 十余个文件里。

```
codex-rs/
├── core/src/
│   ├── session/       # session.rs / turn.rs / turn_input.rs / handlers.rs /
│   │                  # input_queue.rs / step_context.rs / turn_context.rs /
│   │                  # context_window.rs / submission.rs / mod.rs
│   ├── tasks/         # SessionTask 抽象：regular / compact / review / user_shell
│   ├── tools/         # router / registry / spec_plan / parallel / orchestrator /
│   │                  # approvals / sandboxing / handlers/
│   ├── context_manager/history.rs    # 对话历史
│   ├── compact.rs     # 摘要压缩
│   ├── client.rs      # 模型流式通信（SSE / WebSocket）
│   ├── stream_events_utils.rs        # item 完成处理、tool future 生成
│   ├── safety.rs / exec_policy.rs / agents_md.rs / spawn.rs
│   └── codex_thread.rs               # 对外高层 API
├── protocol/src/protocol.rs          # Op / EventMsg / Event / AskForApproval / SandboxPolicy
├── codex-api/src/common.rs           # ResponseEvent
├── codex-api/src/sse/responses.rs    # SSE 解析
├── tools/src/                        # ToolSpec / ToolExecutor
├── sandboxing/src/                   # seatbelt / landlock / bwrap / windows / manager
├── history/src/lib.rs                # RolloutItem / RolloutLine / InitialHistory
├── rollout/src/                      # JSONL 持久化、压缩、sqlite 索引、resume
├── app-server/                       # JSON-RPC 服务层（TUI / exec / IDE 共用后端）
├── exec/src/lib.rs                   # 非交互模式事件循环
└── tui/src/                          # 交互式终端 UI
```

---

## 核心循环在哪：关键文件地图

循环**不在一个文件里**，而是四层嵌套。以下是本快照最重要的结构事实。

| 层 | 位置 | 作用 |
| --- | --- | --- |
| L3 会话调度 | `core/src/session/handlers.rs:423` `submission_loop` | 每线程一个 tokio task，`tokio::select!` 收 `Submission`；不是采样循环 |
| L2 turn 重启 | `core/src/tasks/regular.rs:104` `loop { run_turn(..) }` | 一轮结束后若队列还有 pending input，再起一轮 |
| **L1 Agent Loop** | `core/src/session/turn.rs:427` `loop { .. }`（函数 `run_turn:164`） | **主循环**：采样 ↔ 执行工具 ↔ 判定 follow-up |
| L0 流重试 | `core/src/session/turn.rs:1647` `loop { .. }`（函数 `run_sampling_request:1612`） | 单次请求的重试与 transport 切换 |
| L0′ 事件泵 | `core/src/session/turn.rs:2615` `loop { .. }`（函数 `try_run_sampling_request:2519`） | 消费 SSE 直到 `response.completed` |

其他锚点：

- Channel 创建 `core/src/session/mod.rs:601-602`：
  ```rust
  let (tx_sub, rx_sub) = async_channel::bounded(SUBMISSION_CHANNEL_CAPACITY); // = 512, 见 :513
  let (tx_event, rx_event) = async_channel::unbounded();
  ```
- 会话循环 spawn `core/src/session/mod.rs:958`；Task spawn `core/src/tasks/mod.rs:404`；Task 收尾 `core/src/tasks/mod.rs:619`。

---

## 主循环详解

### L3：submission_loop —— 事件驱动调度器

`core/src/session/handlers.rs:423`。**不是** while-poll 循环，而是 `tokio::select!` 分发器：

```rust
loop {
    let sub = tokio::select! {
        biased;
        _ = sess.services.local_agent_runtime.shutdown.cancelled() => { /* teardown */ break; }
        sub = rx_sub.recv() => match sub { Ok(sub) => sub, Err(_) => break },
        update = async { /* mailbox watch */ } => { /* 唤醒 idle turn */ continue; }
    };
    match sub.op {
        Op::Interrupt => { interrupt(&sess).await; false }
        Op::TurnInput { request, mode, reply } => {
            let result = turn_input::handle(&sess, request, mode, sub.id.clone()).await;
            let _ = reply.send(result);   // ← oneshot 回执
            false
        }
        Op::Compact => { /* spawn CompactTask */ }
        Op::Review { .. } => { /* spawn ReviewTask */ }
        // ... 约 30 个变体
    }
}
```

1. **Submission 不是「用户输入」，而是「任意控制操作」**。`Op` 是非穷尽枚举（`protocol/src/protocol.rs:565`），含审批回复、配置变更、中断、realtime 音频、MCP 刷新等。用户输入只是 `Op::TurnInput` + `TurnInputMode`（`StartOrSteer` / `StartIfIdle` / `ContinueIfIdle` / `Steer`）。
2. **回复走 oneshot**：`Op::TurnInput { reply: oneshot::Sender<CodexResult<TurnInputSubmission>> }`。提交方能同步得到「已接受 / 被拒绝 / 被 steer 进当前 turn」的判定，无需去事件流里猜。
3. **提交队列有界 512，事件队列无界**。事件回压交给消费端，避免工具执行与流事件被 UI 卡住。

`Submission` 结构（`core/src/session/submission.rs:10`）：

```rust
pub(crate) struct Submission {
    pub id: String,
    pub op: Op,
    pub turn_extension_init: Option<ExtensionDataInit>,
    pub trace: Option<W3cTraceContext>,
    pub parent_turn_id: Option<String>,
    pub root_turn_id: Option<String>,
    pub residency_guard: Option<OwnedRwLockReadGuard<()>>,
}
```

### L1：run_turn —— Agent 主循环

`core/src/session/turn.rs:164-845`。函数前的文档注释即语义契约（`:150-163`）：*"Takes initial turn input and runs a loop where, at each sampling request, the model replies with either: requested function calls, or an assistant message. ... If the model requests a function call, we execute it and send the output back to the model in the next sampling request. If the model sends only an assistant message, we record it in the conversation history and consider the turn complete."*

忠实于 `:427-842` 的伪代码：

```
async fn run_turn(sess, turn_context, mut input, mcp_reqs, prewarmed_client, cancellation_token):
    # ---- 前置阶段（循环外，只跑一次）----
    client_session = prewarmed ?? sess.new_session()      # turn 级，缓存 WS + sticky routing
    run_pre_sampling_compact(..)                          # PreTurn 压缩；失败则仍记录 input 并返回
    mcp_reqs ← 解析 input 里的 @插件 / MCP 依赖
    first_step_context = capture_step_context_with_required_mcp_servers(..)
    world_state = record_context_updates_and_set_reference_context_item(first_step_context)
    injection_items ← build_skills_and_plugins(..)        # skills / plugins 注入
    run_pending_session_start_hooks(); finalize_guardian_input(..)
    run_hooks_and_record_inputs(input, PersistContext::TurnStart)
    prewarm_shell_snapshots(); merge_connector_selection(); set_previous_turn_settings()
    turn_diff_tracker = TurnDiffTracker::with_environment_display_roots(display_roots)

    # ---- 主循环 ----
    next_step_context = Some(first_step_context)
    loop:
        pending_input = can_drain_pending_input ? input_queue.get_pending_input(active_turn) : []
        if run_hooks_and_record_inputs(pending_input, PersistContext::SteeredUserInput): break

        # 每个 step 捕获一次快照，供「上下文 / 广告的工具 / 工具调用」共享同一视角
        step_context = next_step_context.take() ?? capture_step_context_with_required_mcp_servers(..)

        match run_sampling_request(sess, step_context, .., cancellation_token.child_token()):
          Ok({ needs_follow_up, last_agent_message }) =>
            can_drain_pending_input = true
            drain_async_hook_results(before_user_prompt=false)
            needs_follow_up     = needs_follow_up || input_queue.has_pending_input(active_turn)
            token_limit_reached = context_window_token_status(sess, turn_context).token_limit_reached

            # (A) 需续跑 且（客户端请求新窗口 或 触到 token 上限）→ MidTurn 压缩后 continue
            should_roll_over = needs_follow_up && (take_new_context_window_request() || token_limit_reached)
            if should_roll_over:
                run_auto_compact(CompactionReason::ContextLimit, CompactionPhase::MidTurn)
                run_pending_session_start_hooks(..);  continue

            # (B) 不需续跑 → 收尾
            if not needs_follow_up:
                stop_outcome = run_turn_stop_hooks(..)
                if stop_outcome.should_block: 注入 hook prompt; continue
                if stop_outcome.should_stop:  break
                run_legacy_after_agent_hook(..)
                if post_turn_compact_threshold_percent > 0
                   and turn_end_compaction_threshold_reached and no pending input:
                    run_auto_compact(ContextLimit, PostTurn)   # 失败仅 warn，保留已完成回答
                break                                          # ← turn 正常结束
            else:
                continue                                       # ← 工具已执行，带新历史再采样

          Err(ContextWindowExceeded) and 有 guardian 预算 → run_auto_compact(MidTurn); continue
          Err(TurnAborted)         → return Err    # 交给上层发 TurnAborted
          Err(InvalidImageRequest) → emit Error; break
          Err(e)                   → emit Error; break   # "let the user continue the conversation"

    return Ok(last_agent_message)
```

读出的设计主张：

- **「采样 → 工具批 → 再采样」是显式 `continue`，不是递归**。结果用 `SamplingRequestResult { needs_follow_up: bool, last_agent_message: Option<String> }` 表达（`turn.rs:1876`）。
- **注释里明说了无限循环假设**：`// as long as compaction works well in getting us way below the token limit, we shouldn't worry about being in an infinite loop.`（`:623`）
- **错误结束这一轮，不结束会话**。非致命错误一律「emit `EventMsg::Error` + `break`」。只有 `TurnAborted` / `ToolCollision` 才 `return Err`。

### L0：run_sampling_request —— 重试壳

`core/src/session/turn.rs:1612-1739`：

```rust
let max_retries = turn_context.provider.info().stream_max_retries();  // :1642
loop {
    let prompt_input = initial_input.take()
        .unwrap_or_else(|| sess.clone_history().await.for_prompt(&model_info.input_modalities));
    sess.services.executed_tool_calls.attach_to_prompt(&mut prompt_input, ..);  // 工具追踪注解
    let prompt = build_prompt(prompt_input, step_context, base_instructions);
    match try_run_sampling_request(..).await {
        Ok(output) => return Ok((output, original_input.unwrap_or(prompt.input))),
        Err(ContextWindowExceeded) => { sess.set_total_tokens_full(&turn_context).await; return Err(err) }
        Err(UsageLimitReached(e))  => { /* 更新 rate limits */ return Err(err) }
        Err(other)                 => other,
    }
    let retry = handle_response_stream_error(&mut retry_state, max_retries, err, client_session, ..)
        .or_cancel(&preempt).or_cancel(&cancellation_token).await?;
    if cancellation_token.is_cancelled() { return Err(CodexErr::TurnAborted); }
    if preempt.is_cancelled() {
        // 用户在流中途说话了：不算 abort，而是「有后续输入」
        return Ok((SamplingRequestResult { needs_follow_up: true, last_agent_message: None },
                   std::mem::take(original_input)));
    }
    retry??;
    turn_context.turn_timing_state.record_sampling_retry();
}
```

注意 **`preempt` 与 `cancellation_token` 是两个不同的 token**（见「中断与错误处理」）。

### L0′：try_run_sampling_request —— SSE 事件泵

`core/src/session/turn.rs:2519-3194`。循环体（`:2615` 起）实质是一个大 `match ResponseEvent`：

```
stream = client_session.stream(prompt, model_info, effort, ..)   # :2569
in_flight: FuturesOrdered<InFlightFuture> = new()                # :2586 ← 并行工具的容器
loop {
    event = stream.next().or_cancel(&preempt).or_cancel(&cancellation_token).await
    match event {
        Created{id}              => response_id 存进 extension_data
        OutputItemAdded(item)    => 生成 TurnItem、emit ItemStarted；
                                    若为 CustomToolCall 则建 ToolArgumentDiffConsumer（流式 diff）
        OutputTextDelta(delta)   => parse_delta → emit AgentMessageContentDelta
        ToolCallInputDelta{..}   => consumer.consume_diff(..) → emit 参数增量事件
        Reasoning*Delta          => emit ReasoningContentDelta / ReasoningRawContentDelta / SectionBreak
        OutputItemDone(item)     => ★核心分支（见下）
        ServerModel / ModelVerifications / SafetyBuffering / RateLimits / ModelsEtag => 旁路状态
        Completed{usage, end_turn} => 记录 usage；end_turn == Some(false) 时 needs_follow_up = true
                                     break Ok(SamplingRequestResult { needs_follow_up, last_agent_message })
    }
}
drop(sampling_span); flush_assistant_text_segments_all(..);
if !in_flight.is_empty() { drain_in_flight(&mut in_flight, sess, step_context).await?; }  # :3159
if should_emit_token_count { sess.send_token_count_event(..).await }
if cancellation_token.is_cancelled() { return Err(CodexErr::TurnAborted) }
if should_emit_turn_diff { emit TurnDiffEvent }
```

`OutputItemDone` 分支（`:2685-2813`）：补 `response_item_id` → 结算流式 diff consumer → plan-mode 特例 → 构造 `HandleOutputCtx` → `handle_output_item_done` → **把返回的 tool future 塞进 `in_flight`**（`:2784-2786`）：

```rust
if let Some(tool_future) = output_result.tool_future {
    in_flight.push_back(tool_future);
}
if let Some(agent_message) = output_result.last_agent_message {
    last_agent_message = Some(agent_message);
}
needs_follow_up |= output_result.needs_follow_up;
```

**关键取舍：工具执行「边收流边启动」，结果回填「流结束后按序」。** `drain_in_flight`（`:2466-2494`）是 `while let Some(res) = in_flight.next().await { sess.record_annotated_conversation_items(..) }`。`FuturesOrdered` 保证回填顺序与模型给出的调用顺序一致，即使完成顺序不同。

### L2：RegularTask —— turn 自动重启

`core/src/tasks/regular.rs:104-121`：

```rust
loop {
    let last_agent_message = run_turn(Arc::clone(&sess), Arc::clone(&ctx), next_input,
                                      &mut mcp_startup_requirements,
                                      prewarmed_client_session.take(),
                                      cancellation_token.child_token()).await?;
    if ctx.terminal_error.lock().await.is_some() { return Ok(last_agent_message); }
    if !sess.input_queue.has_pending_input(&sess.active_turn).await { return Ok(last_agent_message); }
    next_input = Vec::new();
}
```

`run_turn` 结束时若仍有 pending input，**同一 turn_id 下再跑一轮 `run_turn`**，而非开新 turn。这是「用户连续打断追问」语义的实现。

---

## 一次请求的完整生命周期

TUI 里用户敲一句话回车：

1. **UI → app-server**：TUI 走 app-server JSON-RPC（`tui/src/app_server_session.rs`）；`app-server/src/request_processors/turn_processor.rs:652` 调 `thread.start_or_steer_turn(..)`。
2. **进入 Op 队列**：`CodexThread::start_or_steer_turn`（`codex_thread.rs:408`）→ `Session::submit_turn_input`（`session/mod.rs:1036`）包装成 `Submission { id, op: Op::TurnInput { .. } }`，`tx_sub.send(..)` 投入有界队列（512）。
3. **submission_loop 收到**（`handlers.rs:521-533`）→ `turn_input::handle`（`turn_input.rs:216-271`）按 mode 分派：
   - **已有活跃 turn**：`Steer` 把输入投进 `InputQueue.pending_input`，并经 `step_context.preempt` 触发当前采样的 preempt token（`turn_input.rs:582-632`）——「边跑边插话」。
   - **无活跃 turn**：`start_or_steer` 准备 settings → `Session::spawn_task(turn_context, input, RegularTask::new())`。
4. **task 启动**（`tasks/mod.rs:272-421`）：`abort_all_tasks(Replaced)` → `start_task`：建 cancellation_token 与 `Notify`、登记 `ActiveTurn`、`emit_turn_start_lifecycle`、`admit_turn`（多 agent 准入）→ **`tokio::spawn`** 整个 task future，把 `RunningTask { done, handle: AbortOnDropHandle, kind, task, cancellation_token, .. }` 挂到 `active_turn.task`。提交方随即通过 oneshot 收到 `TurnInputSubmission::Started { turn_id }`。
5. **`RegularTask::run` → `run_turn`**：前置阶段（压缩检查、MCP 解析、skills/plugins 注入、hooks、session-start hooks）。
6. **主循环首次迭代**：`capture_step_context_with_required_mcp_servers` 冻结 `StepContext`（模型设置、环境快照、MCP binding、`ToolRouter`、AGENTS.md）→ `run_sampling_request`。
7. **HTTP/WS 请求**：`client_session.stream(..)`（`client.rs:2237`；`stream_responses_api:1662` / `stream_responses_websocket:1853`）。响应体交给 `process_sse_with_treatment`（`codex-api/src/sse/responses.rs:524`），逐帧 `serde_json::from_str` 成 `ResponsesStreamEvent`，经 `process_responses_event`（同文件 `:344`）映射为 `ResponseEvent`（`codex-api/src/common.rs:81`），进 mpsc。
8. **事件泵**（`turn.rs:2615`）：deltas 转 `EventMsg` 推给 UI（首 token 记 TTFT）；`OutputItemDone` 里的 tool call 由 `ToolRouter::build_tool_call`（`router.rs:246`）解析成 `ToolCall`，再经 `handle_output_item_done`（`stream_events_utils.rs:315`）启动 `ToolCallRuntime::handle_tool_call` 并 push 进 `in_flight`。
9. **工具执行**（`tools/parallel.rs:125-295`）：`tokio::spawn` dispatch task → 按 `supports_parallel` 取 `RwLock` 读锁或写锁 → `router.dispatch_tool_call_with_state(..)` → 具体 handler。若命中审批策略，emit `ExecApprovalRequest` 并 await 一个 oneshot。
10. **流结束**：`ResponseEvent::Completed { token_usage, end_turn }` → 记录 usage → `end_turn == Some(false)` 则置 `needs_follow_up`。然后 `drain_in_flight` **按模型给出顺序**把工具结果写回历史。
11. **回到主循环判定**：`needs_follow_up = model_needs_follow_up || has_pending_input`；查 token 状态；决定 `should_roll_over`（压缩后 continue）还是收尾 break。
12. **turn 收尾**：`run_turn_stop_hooks` →（可选 PostTurn 压缩）→ `break` → `RegularTask` 检查 pending input → task future 结束 → `flush_rollout()`（`tasks/mod.rs:383`）→ `on_task_finished`（`tasks/mod.rs:619`）emit `TurnComplete` / `TurnAborted` → `done.notify_waiters()`。UI 侧由 `TurnComplete` 解除 spinner，`EventMsg::TokenCount` 更新状态栏。

---

## 工具系统

### 两套 trait，分居两 crate

对外契约在 `tools/` crate（`tools/src/tool_executor.rs:106`）：

```rust
pub trait ToolExecutor<Invocation>: Send + Sync {
    fn tool_name(&self) -> ToolName;
    fn spec(&self) -> ToolSpec;
    fn exposure(&self) -> ToolExposure { ToolExposure::Direct }
    fn search_info(&self) -> Option<ToolSearchInfo> { .. }
    fn supports_parallel_tool_calls(&self) -> bool { false }   // ← 默认串行
    fn handle<'a>(&'a self, invocation: Invocation) -> ToolExecutorFuture<'a>;
}
```

core 侧扩展现 `CoreToolRuntime: ToolExecutor<ToolInvocation>`（`core/src/tools/registry.rs:56`），额外提供 `immutable_spec`、`cached_code_mode_definitions`、`wait_until_ready`、`is_third_party_tool`、`mcp_server_name`、`matches_kind`、`telemetry_tags`、`on_tool_result_accepted`、`pre/post_tool_use_payload`、`create_diff_consumer`。

### Schema

`ToolSpec` 直接对应 Responses API 的工具形态（`tools/src/tool_spec.rs:22`）：

```rust
pub enum ToolSpec {
    #[serde(rename = "function")]    Function(ResponsesApiTool),
    #[serde(rename = "namespace")]   Namespace(ResponsesApiNamespace),
    #[serde(rename = "tool_search")] ToolSearch { execution, description, parameters },
    #[serde(rename = "web_search")]  WebSearch { .. },
    #[serde(rename = "custom")]      Freeform(FreeformTool),
}
```

`Namespace` 是较新的一层——多个工具可折进一个命名空间（例：多 agent 的 `spawn_agent`/`wait_agent` 在 `MULTI_AGENT_V1_NAMESPACE` 下，`core/src/tools/handlers/multi_agents_spec.rs:89-92`）。

### 路由与组装

- `ToolRegistry`（`registry.rs:301`）：`register_trusted` / `register_external` / `remove` / `record_collision`。
- `ToolRouter`（`router.rs:74`）：`model_visible_specs()` 返回实际发给模型的 schema（`Arc<[ToolSpec]>`）。
- 组装入口 `core/src/tools/spec_plan.rs`：`build_tool_router:123`、`build_core_tool_registry:295`、`finalize_tool_router:370`、`build_model_visible_specs:614`。按 feature flag、模型能力、审批策略、namespace override（`apply_mcp_tool_exposure_policy:199`）决定哪些工具「对模型可见」。
- handler 目录 `core/src/tools/handlers/`：`shell`/`unified_exec`、`apply_patch`、`view_image`、`plan`、`request_user_input`、`request_permissions`、`send_message_to_user`、`tool_search`、`mcp`/`mcp_resource`、`multi_agents`(v1/v2)、`sleep`、`new_context_window`、`get_context_remaining`、`wait_for_environment`、`extension_tools`、`dynamic`。

### 并行：`RwLock` 二分法

`core/src/tools/parallel.rs`。`ToolCallRuntime` 持有 `parallel_execution: Arc<RwLock<()>>`（`:50`）。dispatch task 内（`:196-228`）：

```rust
let guard = if supports_parallel { Either::Left(lock.read().await) }
            else                  { Either::Right(lock.write().await) };
// Admission through the parallel-execution gate marks the end
// of dispatch waiting and the start of handler execution.
```

即：声明 `supports_parallel_tool_calls() == true` 的工具拿**读锁**，彼此可并发；其余拿**写锁**，独占。默认 `false`。语义是「只读工具并发，有副作用工具串行」——极简且无需调参。拿到锁的时刻被定义为 handler 执行开始时刻（`:210-214`），使遥测里 `dispatch_waiting` 与 `handler_execution` 天然分离。

每个工具调用单独 `tokio::spawn`（`:196`），`AbortOnDropHandle` 包装，外层 `tokio::select!` 监听 `cancellation_token`（`:249-283`）：取消且未完成则 `dispatch_handle.abort()`，结果替换为 `aborted_response` 并 `notify_tool_aborted`。

### 结果回填

`handle_output_item_done`（`stream_events_utils.rs:315`）四种出口：

| 情况 | 行为 |
| --- | --- |
| `Ok(Some(call))` | 记录 tool call → 立即持久化该 item → 生成 tool future → `needs_follow_up = true` |
| `Ok(None)` | 非工具 item：finalize 成 `TurnItem`，emit `ItemStarted`/`ItemCompleted`，提取 `last_agent_message` |
| `Err(RespondToModel(msg))` | 不执行，错误文本包成 `FunctionCallOutput` 塞回对话，`needs_follow_up = true`（模型看到的是一次「失败的工具返回」） |
| `Err(Fatal(msg))` | `return Err(CodexErr::Fatal)`，中断整个 turn |

结果由 `drain_in_flight`（`turn.rs:2466`）经 `record_annotated_conversation_items` 写入历史；下一轮采样时 `clone_history().for_prompt(..)` 自动带上。

---

## 审批与沙箱

### 策略形态

`AskForApproval`（`protocol/src/protocol.rs:961`）——旧文的 `untrusted / on-failure / on-request / never` 现为：

```rust
pub enum AskForApproval {
    UnlessTrusted,   // serde: "untrusted" —— 默认拒绝，除非 execpolicy 显式允许
    OnRequest,       // serde: "on-request"（alias "on-failure"），#[default]
    Granular(GranularApprovalConfig),  // sandbox_approval / rules / skill_approval /
                                       // request_permissions / mcp_elicitations 五个布尔开关
    Never,           // 永不询问；失败直接返回给模型
}
```

`SandboxPolicy`（同文件 `:1047`）：`DangerFullAccess` / `ReadOnly { network_access }` / `ExternalSandbox { network_access }` / `WorkspaceWrite { writable_roots, network_access, exclude_tmpdir_env_var, exclude_slash_tmp }`。

### 沙箱后端选择

`sandboxing/src/manager.rs:49` `get_platform_sandbox`：

```rust
if cfg!(target_os = "macos")      { Some(SandboxType::MacosSeatbelt) }
else if cfg!(target_os = "linux") { Some(SandboxType::LinuxSeccomp) }
else if cfg!(target_os = "windows") { windows_sandbox_enabled ? Some(WindowsRestrictedToken) : None }
else { None }
```

- **macOS**：`sandboxing/src/seatbelt.rs` + SBPL 模板 `seatbelt_base_policy.sbpl`、`seatbelt_network_policy.sbpl`、`seatbelt_preferences_policy.sbpl`、`seatbelt_read_only_platform_defaults.sbpl`。
- **Linux**：`landlock.rs`（Landlock LSM）+ `bwrap.rs`（bubblewrap）+ `linux_pid_namespace.rs`；另有独立 crate `linux-sandbox/`、`bwrap/`。
- **Windows**：`windows.rs`、`windows_mxc.rs`，另加 `windows-sandbox-rs/`。
- 违反检测 `violation.rs`、`denial.rs`；patch 走 `PatchSandboxRoute::{ExecutorManaged, Platform(..)}`（`core/src/safety.rs:26`）。

### 审批判定链条

1. **命令策略** `core/src/exec_policy.rs`：用 `codex_execpolicy` 的 `Policy`/`Decision`/`Evaluation`，支持从配置层累加规则（`blocking_append_allow_prefix_rule`、`blocking_append_network_rule`），命中后产出 `RequirementsExecPolicy`（allow / prompt / forbidden）。
2. **patch 安全** `core/src/safety.rs:67` `assess_patch_safety` → `SafetyCheck::{AutoApprove, AskUser, Reject { reason }}`，依据 `is_write_patch_constrained_to_writable_paths:146` 与 `PatchPolicyMatcher::can_write_path:59`。
3. **统一入口** `core/src/tools/approvals.rs:479` `request_approval(..)`。早退分支（`:489-521`）：命中缓存批准直接返回 `ReviewDecision::Approved`；否则 `request_reviewer_approval` / `request_guardian_approval` / `request_user_approval`。
4. **`ReviewDecision` → 工具结果**（`:444-477`）：`Denied{rejection}` → `ToolError::Rejected`；`TimedOut` → `ToolError::Rejected`；**`Abort` → `CodexErr::TurnAborted`**（用户点「中止」等于中断整个 turn）。
5. UI 回话通道：`Op::ExecApproval { id, turn_id, decision }` / `Op::PatchApproval`（`protocol.rs` 内）。

### escalation：沙箱拒绝 → 提权重试

`core/src/tools/orchestrator.rs`。文件头注释即设计说明：*"retry with an escalated sandbox strategy on denial (no re-approval thanks to [caching])"*。流程（`:380-540`）：

```
第一次尝试：按 baseline sandbox policy 执行
  ├─ Ok → 直接返回
  └─ Err(SandboxErr::Denied { output, network_policy_decision }) →
       if !tool.escalate_on_failure()                       → 原样返回拒绝
       if !tool.wants_no_sandbox_approval(approval_policy)  → 只有 OnRequest + 网络审批场景才放行
       if !unsandboxed_allowed                              → 拒绝
       bypass_retry_approval = !strict_auto_review
                               && tool.should_bypass_approval(policy, already_approved)
                               && 无网络上下文
       if !bypass_retry_approval → 构造 ApprovalContext { retry_reason, network_approval_context, .. } 找用户/guardian 批
       然后不带沙箱重试，并记录 escalated_duration 遥测
```

硬约束：*"Strict auto-review approval covers the sandboxed attempt only; retrying without the sandbox requires a fresh guardian review."*（`:481-485`）。**沙箱内批准 ≠ 沙箱外批准**。

---

## 上下文管理与压缩

### 历史的组织

`ContextManager`（`core/src/context_manager/history.rs:234`）。核心 API：

- `record_items(items, TruncationPolicy)` / `record_annotated_items`（`:504`/`:516`）—— 追加
- `for_prompt(input_modalities) -> Vec<ResponseItem>`（`:600`）—— **每次采样时现算**，按模态过滤
- `estimate_token_count(turn_context)`（`:644`）、`update_token_info`（`:876`）、`get_total_token_usage`（`:929`）
- `replace_compacted(..)`（`:711`）、`drop_last_n_user_turns(n)`（`:767`）
- 世界状态：`update_world_state` / `render_step_world_state` / `set_world_state_baseline`（`:448`/`:459`/`:484`）

历史项是 `ResponseItemEnvelope`（`history/src/lib.rs`）而非裸 `ResponseItem`，携带注解（retained context、来源等）。token 估算逐项计算并缓存（`estimate_item_token_count:1064`）。

### 触发条件

`core/src/session/context_window.rs:26-141`：

```rust
let token_limit_reached = buffered_auto_compact_limit
        .is_some_and(|limit| auto_compact_scope_tokens >= limit)
    || full_context_window_limit_reached;
let turn_end_compaction_threshold_reached = post_turn_percent > 0
    && (token_limit_reached
        || full_context_window_limit.is_some_and(|limit| {
            i128::from(active_context_tokens) * 100 >= i128::from(limit) * i128::from(post_turn_percent) }));
```

两个正交的 scope（`AutoCompactTokenLimitScope`）：

- `Total`：`active_context_tokens` 直接比 `model_info.auto_compact_token_limit()`。
- `BodyAfterPrefix`：只算「初始前缀之后新增的 token」（`active_context_tokens - window.prefill_input_tokens`），避免系统提示词占满预算。

`full_context_window_limit = context_window * effective_context_window_percent / 100` 与压缩 scope 无关，是硬上限。`fallback_buffer_tokens` 只在存在 fallback prompt 时才预留（`:98-103`）。

### 三个触发阶段

| 阶段 | 触发点 | 位置 |
| --- | --- | --- |
| **PreTurn** | 每轮开始前估算「新输入 + 上下文 diff 会不会推过阈值」 | `turn.rs:184` `run_pre_sampling_compact`（含 TODO 说明这是预判式触发） |
| **MidTurn** | 采样后 `should_roll_over = needs_follow_up && (new_window_requested \|\| token_limit_reached)` | `turn.rs:624-659` |
| **PostTurn** | 回合完成且 `turn_end_compaction_threshold_reached` 且无 pending input 且未取消 | `turn.rs:724-762`（失败只 warn，**保留已完成的回答**） |

### 压缩实现

`core/src/compact.rs`：

- `run_inline_auto_compact_task:113` —— turn 内联压缩。用 `config.compact_prompt` 或默认 `SUMMARIZATION_PROMPT`，包成一条 `UserInput::Text` 后交 `run_compact_task_inner`。
- `SUMMARIZATION_PROMPT` 实际内容（`prompts/templates/compact/prompt.md`）：*"You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task. Include: Current progress and key decisions made / Important context, constraints, or user preferences / What remains to be done (clear next steps) / Any critical data, examples, or references needed to continue."*
- `build_compacted_history:673` / `build_compacted_history_with_limit:686` 重建历史；`collect_user_messages:567` 与 `insert_initial_context_before_last_real_user_or_summary:615` 保留「初始上下文 + 用户真实提问」；`COMPACT_USER_MESSAGE_MAX_TOKENS = 20_000`（`:63`）限流。
- 手动压缩走 `CompactTask`（`core/src/tasks/compact.rs:26`），由 `Op::Compact` 触发，并按 provider 能力选择 `RemoteCompactionSupport::V2` 远端压缩（`compact_remote_v2.rs`）。
- **TokenBudget 是另一条路**：`Feature::TokenBudget` 开启时压缩不产生摘要、直接重置窗口（`turn.rs:725`、`core/src/compact_token_budget.rs`）。所以代码里到处是 `!features.enabled(Feature::TokenBudget)` 分支。

### 压缩后的记录

结果写成 `RolloutItem::Compacted(CompactedItem)`（`history/src/lib.rs:286`），其中 `replacement_history: Option<Vec<ResponseItemEnvelope>>` 保存重建后的历史，`latest_token_usage_record` 保存当时的 token 快照，另有 `window_number` / `first_window_id` / `previous_window_id` / `window_id` / `compaction_response_id` 等窗口元数据。**这让 resume 不必从头扫描整个 JSONL。**

---

## 中断与错误处理

### 两个 token：`preempt` 与 `cancellation_token`

本实现里最值得注意的区分（`turn_input.rs:582-632`、`turn.rs:1622-1629`、`:1724-1735`、`:2634-2650`）：

| token | 来源 | 语义 | 结果 |
| --- | --- | --- | --- |
| `preempt` | `StepContext::preempt`，由新输入触发（`input_queue.watch_user_input(..)`） | 用户插话 | 当前 sampling 提前收束；`return Ok(SamplingRequestResult { needs_follow_up: true, .. })`；随后被替换为新的 `CancellationToken`（`turn.rs:2648`）继续读完响应以复用连接与历史 |
| `cancellation_token` | `start_task` 创建，存在 `RunningTask.cancellation_token` | 用户按 ESC | `Err(CodexErr::TurnAborted)`，由 `on_task_finished` 发 `TurnAborted` |

`turn.rs:2643-2651` 的注释说得很直白：

```rust
Ok(Err(_)) => {
    if let Some(interrupt) = stream.interrupt.take() {
        if step_context.settings.model_info.use_responses_lite { let _ = interrupt.send(()); }
        // Drain the response normally before reusing its connection and history.
        preempt = CancellationToken::new();
        needs_follow_up = true;
        continue;
    }
    drop(stream);
    break Ok(SamplingRequestResult { needs_follow_up: true, last_agent_message });
}
```

### ESC 的完整路径

1. TUI 发 `Op::Interrupt`。
2. `submission_loop` → `interrupt(&sess)`（`handlers.rs:58`）→ `Session::interrupt_task`（`session/mod.rs:4988`）：
   ```rust
   pub async fn interrupt_task(self: &Arc<Self>) {
       info!("interrupt received: abort current task, if any");
       let had_active_turn = self.active_turn.lock().await.is_some();
       self.abort_all_tasks(TurnAbortReason::Interrupted).await;
       if !had_active_turn { self.cancel_mcp_startup(); }
   }
   ```
3. `abort_all_tasks`（`tasks/mod.rs:537-565`）：取出 `active_turn.task` → `handle_task_abort`（cancel token + `abort()` 钩子）→ `input_queue.clear_pending(&active_turn)`。顺序有讲究：*"Let interrupted tasks observe cancellation before dropping pending approvals, or an in-flight approval wait can surface as a model-visible rejection before TurnAborted."*（`:558-560`）——先取消再清 pending，避免竞态把中断误报成工具被拒。
4. 若 `reason == Interrupted` 且确实中断：`maybe_start_turn_for_pending_work()`（`:562-564`），中断后若队列还有东西就继续跑。
5. `on_task_finished`（`tasks/mod.rs:619-645`）把 `CodexErr::TurnAborted` 翻译成 `abort_reason = Some(TurnAbortReason::Interrupted)`，emit `EventMsg::TurnAborted`。
6. 还有更精细的 `Op::InterruptIfNoPendingInput { turn_id, reply }`（处理器 `handlers.rs:476`）——「只在没有排队输入时才中断」，避免丢掉用户刚打的字；`core/src/session/extension_interruption.rs:40` 也在用。

### 流与 API 重试

`core/src/responses_retry.rs:57` `handle_response_stream_error`：

- `ResponsesStreamRetryState { retries, connection_retries, connection_retry_delay }`（`:32`），退避升级（`:114-119`）。预算来自 `provider.info().stream_max_retries()`（`turn.rs:1642`；`model-provider-info/src/lib.rs:506`）。
- **服务端建议优先**：有 `err.retry_after()` 时 `sleep_until(retry_after.deadline())`（`:127-129`），且 *"Changing transport must not bypass the server's retry deadline."*（`:126`）。切换 transport（WS ↔ SSE）时 `retry_state.retries = 0`（`:137`）。
- 用户可见：`EventMsg::StreamError` + `"Reconnecting... {retry_count}/{max_retries}"`（`:151-156`）；release 构建下隐藏第一次 WS 重试通知（`:145-147`）。
- 用尽后保留服务端建议 `ExhaustedResponseRetry { retry_at }`（`:50`），供上层告知用户何时再试。

### 主循环里的错误分类

| 错误 | 处理 |
| --- | --- |
| `ContextWindowExceeded` | guardian 预算场景且未压缩过 → `run_auto_compact(MidTurn)` + `continue`（per-model-step 只重试一次，`turn.rs:767-797`）；否则 `set_total_tokens_full` 后上抛 |
| `UsageLimitReached(e)` | 更新 rate limits，上抛 |
| `TurnAborted` | 直接 `return Err`（`:798-800`） |
| `InvalidImageRequest` | emit `CodexErrorInfo::BadRequest` + 面向用户的「请移除图片重试」文本，`break` |
| `MisalignmentPolicyViolation` | 额外 `conversation.retire_handoffs_for_misalignment()` |
| 其他 | `emit_turn_error_lifecycle` + `track_turn_codex_error` + `EventMsg::Error`，`break`（`turn.rs:838`） |
| `FunctionCallError::Fatal` | 从工具层直接冒泡成 `CodexErr::Fatal`，终结整个任务 |

---

## 会话持久化与恢复

### Rollout JSONL

每行一条 `RolloutLine`（`history/src/lib.rs:361`）：

```rust
pub struct RolloutLine {
    pub timestamp: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ordinal: Option<u64>,
    #[serde(flatten)]
    pub item: RolloutItem,
}
```

`RolloutItem`（`:211`）是 tagged union：

```rust
pub enum RolloutItem {
    SessionMeta(SessionMetaLine),
    ResponseItem(ResponseItemEnvelope),
    InterAgentCommunication(InterAgentCommunication),
    InterAgentCommunicationMetadata { trigger_turn: bool },
    Compacted(CompactedItem),
    TurnContext(TurnContextItem),
    TokenUsageRecord(TokenUsageRecord),
    WorldState(WorldStateItem),
    SecurityRiskScore(SecurityRiskScore),
    RetainedContext(RetainedContextEvent),
    EventMsg(EventMsg),
    RealtimeItem(RealtimeItem),
}
```

- **`RolloutLine` 故意不实现 `Deserialize`**（`:356-359`）：JSONL 读取必须走 `codex_rollout` 的规范解析器，以保住扁平化 envelope 里的十进制精度。序列化走 `rollout_payload::RolloutItemWire`。
- **`EventMsg` 也被持久化**，不只 model-visible item。回放时 UI 状态可完整重建（`send_event_raw_with_persistence`，`session/mod.rs:2493-2542`：先 `persist_rollout_items(vec![RolloutItem::EventMsg(..)])` 再 `deliver_event_raw`）。
- `Compacted` 记录自带 `replacement_history`，resume 时可跳过被压缩掉的部分。

### 恢复与 fork

`InitialHistory`（`history/src/lib.rs:380`）：

```rust
pub enum InitialHistory { New, Cleared, Resumed(ResumedHistory), Forked(Vec<RolloutItem>) }
```

- `ResumedHistory { conversation_id, history: Arc<Vec<RolloutItem>>, history_revision, rollout_path }`（`:370`）。
- fork 来源靠 `SessionMetaLine.meta.forked_from_id` 追溯（`:396-405`）。
- 加载：`rollout/src/recorder.rs:995` 的 `RolloutRecorderParams::Resume { path }` → `InitialHistory::Resumed(..)`（`:1180`）；入口 `RolloutRecorder::resume(path)`（`:365`）。
- 索引：`rollout/src/state_db.rs`（sqlite，`find_rollout_path_by_id:486`）、`session_index.rs`、`rollout_reference_index.rs`；查找逻辑 `rollout/src/list.rs:1478`。
- 体积控制：`compression.rs`、`reverse_jsonl_scanner.rs`（反向扫描找最近 checkpoint）、`maintenance.rs`、`policy.rs`（哪些事件值得持久化）。
- 并发保护：`writer_lock.rs`（一个 thread 一个写者）。
- **写盘时机**：task 结束前显式 `sess.flush_rollout()`（`tasks/mod.rs:383-395`），失败给用户 warning（"Codex will continue retrying"）；另有 `Session::flush_rollout`（`session/mod.rs:1449`）与 `ensure_rollout_materialized`（`:1470`）。
- 上层还叠了 `codex-thread-store`（`thread-store/src/local/rollout_lineage.rs`），把 rollout 文件组织成血缘段。

---

## 值得借鉴的设计点

1. **四层嵌套循环，每层职责单一且命名准确。** `submission_loop`（队列分发）→ `RegularTask`（turn 重启）→ `run_turn`（agent 循环）→ `run_sampling_request` / `try_run_sampling_request`（网络重试 / SSE 泵）。多数 agent 框架把所有东西塞进一个 `while`，结果「重试」「续跑」「插话」「压缩」四种语义互相纠缠。分层后每层可独立测试，失败模式也清晰（例如 L0 只管传输，L1 只管 follow-up 判定）。

2. **`preempt` 与 `cancellation_token` 是两个 token，不是一个。** 「用户插话」应保留已产生输出并把新输入喂进去（`needs_follow_up = true`），「用户 ESC」应丢弃并报 `TurnAborted`。用两个 `CancellationToken` 表达两种意图，远比在业务代码里到处判断「如果是插话就…」干净。

3. **工具「边收流边启动、结果按序回填」。** `in_flight: FuturesOrdered<InFlightFuture>` + 流结束后 `drain_in_flight`。既拿到并行收益，又保证历史里工具结果顺序与模型输出顺序一致——这对后续 prompt 有效性很关键。这个结构天然支持「流未结束就有工具返回」的早期通知（`call_trace::result_ready`）。

4. **用一个 `RwLock` 表达工具并行性，而非并发度配置。** 读锁 = 可并发（只读工具），写锁 = 串行（有副作用工具），默认 `false`。抽象极简、无需调参，语义直接对应「读写互斥」。顺带把「拿到锁的时刻」定义为 handler 执行起点，使遥测自动区分 dispatch 等待与 handler 执行。

5. **`StepContext` 作为不可变快照，让上下文 / 工具 / 执行共享同一视角。** 每个 sampling request 前 `capture_step_context` 冻结 `Arc<ResolvedStepSettings>`、`TurnEnvironmentSnapshot`、`Arc<McpBinding>`、`Arc<ToolRouter>`、`loaded_agents_md`（`step_context.rs:24-49`）。代码注释写明理由：*"Capture once so context, advertised tools, and tool calls share one request view."*（`turn.rs:462`）。这消除了「发给模型的 schema 与实际执行的 handler 不是一套」这类极难排查的 bug。

6. **审批结果用 oneshot 回注，且「沙箱内批准 ≠ 沙箱外批准」。** UI 批准经 `Op::ExecApproval { id, decision }` 回到 core，用 call id 配对。escalation 里有硬规则：*"Strict auto-review approval covers the sandboxed attempt only; retrying without the sandbox requires a fresh guardian review."*（`orchestrator.rs:481-485`）。把「提权重试是新的授权事件」显式建模，是安全边界上的正确取舍。

7. **错误「结束这一轮」而不是「结束会话」。** `turn.rs:824-840` 的兜底是 emit `EventMsg::Error` + `break`，注释写着 *"let the user continue the conversation"*。只有 `TurnAborted` / `Fatal` / `ToolCollision` 才向上抛。这让长会话遇到偶发 API 错误、图片格式错误、窗口超限后仍可继续。

8. **两个正交的 token 预算维度：`scope` 与 `full_context_window_limit`。** `AutoCompactTokenLimitScope::{Total, BodyAfterPrefix}` 让「自动压缩触发」只算新增部分，而 `full_context_window_limit` 独立作为硬上限。系统提示词 / AGENTS.md 这类固定前缀不该吃掉压缩预算——这解决了很多 agent 在长会话里「一开始就莫名其妙被压缩」的问题。

9. **`EventMsg` 与 model-visible item 一起持久化。** `RolloutItem` 同时含 `ResponseItem` 与 `EventMsg`，使 `resume` 不只恢复模型上下文，而是恢复完整 UI 会话状态（工具起止、审批请求、token 计数）。对「关掉终端再打开继续」是唯一可行做法。代价是文件更大，所以配套做了压缩、反向扫描、sqlite 索引与写入策略。

10. **`SessionTask` trait 把「有循环的任务」统一抽象。** `RegularTask`（正常对话）、`CompactTask`（压缩）、`ReviewTask`（代码审查）、`UserShellCommandTask`（`!shell`）实现同一个 `SessionTask`（`tasks/mod.rs:180-219`），共享 `start_task` 的完整生命周期：cancellation token、`AbortOnDropHandle`、rollout flush、`on_task_finished` 统一收尾。新增任务类型只需实现 `run`/`abort`。`ReviewTask` 甚至能在内部启动子 Codex thread（`run_codex_thread_one_shot`）。

---

## 参考文件清单

路径相对于仓库根的 `codex-rs/`。

**主循环与调度**
`core/src/session/turn.rs`（`run_turn:164`、主循环 `:427`、`drain_in_flight:2466`、`run_sampling_request:1612`、重试循环 `:1647`、`try_run_sampling_request:2519`、事件泵 `:2615`、`SamplingRequestResult:1876`）· `core/src/tasks/mod.rs`（`SessionTask:180`、`start_task:287`、spawn `:404`、`abort_all_tasks:537`、`on_task_finished:619`）· `core/src/tasks/regular.rs:104` · `core/src/tasks/{compact,review,user_shell}.rs` · `core/src/session/handlers.rs`（`interrupt:58`、`submission_loop:423`）· `core/src/session/mod.rs`（`SessionIo:411`、`tx_sub:414`、`SUBMISSION_CHANNEL_CAPACITY:513`、channel `:601`、loop spawn `:958`、`interrupt_task:4988`、事件投递 `:2493`/`:2544`）· `core/src/session/submission.rs:10` · `core/src/session/turn_input.rs`（`handle:216`、`start_or_steer:312`、`steer:582`）· `core/src/session/input_queue.rs`（`TurnInputQueue:90`、`get_pending_input:400`）· `core/src/session/step_context.rs:24` · `core/src/session/turn_context.rs:321` · `core/src/codex_thread.rs`（`CodexThread:207`、`start_or_steer_turn:408`）

**协议**
`protocol/src/protocol.rs`（`Op:565`、`AskForApproval:961`、`GranularApprovalConfig:987`、`SandboxPolicy:1047`、`Event:1315`、`EventMsg:1333`）

**流式通信**
`codex-api/src/common.rs:81` · `codex-api/src/sse/responses.rs`（`process_responses_event:344`、`process_sse_with_treatment:524`）· `core/src/client.rs`（`stream_responses_api:1662`、`stream_responses_websocket:1853`、`stream:2237`）· `core/src/responses_retry.rs:57` · `core/src/stream_events_utils.rs:315`

**工具系统**
`tools/src/tool_executor.rs:106` · `tools/src/tool_spec.rs:22` · `core/src/tools/registry.rs`（`CoreToolRuntime:56`、`ToolRegistry:301`）· `core/src/tools/router.rs`（`ToolRouter:74`、`build_tool_call:246`、`tool_supports_parallel:235`）· `core/src/tools/parallel.rs`（`ToolCallRuntime:45`、RwLock 门 `:196-228`、取消 `:249-283`）· `core/src/tools/spec_plan.rs`（`build_tool_router:123`、`build_core_tool_registry:295`、`build_model_visible_specs:614`）· `core/src/tools/orchestrator.rs:380-540` · `core/src/tools/approvals.rs`（`request_approval:479`、`ApprovalAction:67`）· `core/src/tools/handlers/`

**审批与沙箱**
`core/src/safety.rs`（`SafetyCheck:19`、`assess_patch_safety:67`）· `core/src/exec_policy.rs` · `sandboxing/src/manager.rs:49` · `sandboxing/src/seatbelt.rs` + `*.sbpl` · `sandboxing/src/{landlock,bwrap,violation}.rs` · `linux-sandbox/` · `bwrap/` · `windows-sandbox-rs/` · `core/src/spawn.rs`

**上下文与压缩**
`core/src/context_manager/history.rs`（`ContextManager:234`、`for_prompt:600`、`estimate_item_token_count:1064`）· `core/src/session/context_window.rs:26` · `core/src/compact.rs`（`run_inline_auto_compact_task:113`、`run_compact_task:150`、`build_compacted_history:673`）· `core/src/compact_remote_v2.rs` · `core/src/compact_token_budget.rs` · `prompts/templates/compact/{prompt,summary_prefix}.md` · `core/src/session/{auto_compact_window,token_budget}.rs`

**持久化**
`history/src/lib.rs`（`RolloutItem:211`、`CompactedItem:286`、`RolloutLine:361`、`ResumedHistory:370`、`InitialHistory:380`）· `rollout/src/recorder.rs`（`resume:365`、`Resume:995`）· `rollout/src/{state_db,list,compression,writer_lock,policy}.rs`

**其他**
`core/src/agents_md.rs`（`load_project_instructions:58`、`DEFAULT_AGENTS_MD_FILENAME:43`、`AGENTS_MD_SEPARATOR:49`）· `core/src/agents_md_manager.rs` · `core/src/tools/handlers/multi_agents_spec.rs`（`spawn_agent:92`、`send_input:193`、`wait_agent:292`、`close_agent:346`）· `core/src/tools/handlers/multi_agents_v2/` · `exec/src/lib.rs`（事件循环 `:1302`、`run_exec_session:854`）· `app-server/src/request_processors/turn_processor.rs:652` · `tui/src/`

---

## 未确认 / 未深入的点

以下未逐行读完源码，如需在实现中参照请自行验证：

- `core/src/context_manager/updates.rs` 里「上下文 diff / 增量更新」的具体算法（本快照引入的新机制，`StepContext` 与 `WorldState` 的交互细节）。
- `RemoteCompactionSupport::V2` 远端压缩的协议细节（`compact_remote_v2.rs` 47KB，只读了接口层）。
- `codex_delegate.rs` / `run_codex_thread_one_shot` 中子 Codex thread 的完整生命周期。
- `unified_exec` 的 PTY 会话管理（`core/src/unified_exec/`）。
- `guardian` 子系统的完整审核协议（`core/src/guardian/`）。
- `code-mode` 机制（模型生成代码来编排工具调用，`core/src/tools/code_mode/`）——本快照里已相当庞大，但与传统 agent loop 并行，本文只在与主循环交互处提及。
