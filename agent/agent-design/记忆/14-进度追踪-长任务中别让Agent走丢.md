# 14｜进度追踪：长任务中别让 Agent 走丢

> 来源：极客时间《Agent 设计模式之美》黄佳 · 记忆：沉淀之美
> 原文链接：https://time.geekbang.org/column/article/990515 （时长 26:49）
> 课程主页：https://time.geekbang.org/column/intro/101162601?tab=catalog
> 本笔记为学习总结，版权归原作者所有。

## 一句话总结

进度追踪（记忆×编排★）不是 todo list，而是**长任务的防迷失机制**：三平面分治（叙事态=锚账集、机械态=带 Provenance 的真值、调度态=任务状态机）+ 三个收敛器（复诵、漂移哨兵、验证闸门），由编排器横切维护任务台账。

## 人类团队的启示：靠外部支架推进长项目

人会迷失是因为没有防跑偏装置：会议纪要（固定原始目标）、项目计划（拆阶段）、任务清单、测试清单、审批节点、同事提醒。人不是靠脑子记住一切，是靠一整套外部化支架。

Agent 需要对应的支架，且要更显式、更结构化、更可恢复：
1. 目标契约（像会议纪要）：反复提醒"这次要交付什么、不要交付什么"
2. 里程碑状态（像项目计划）：现在推进到哪一段
3. 进度账本（像工作日志）：关键决策、证据、下一步
4. 验证闸门（像测试清单+审批节点）：防止"看起来完成了其实没验收"
5. **漂移哨兵**：比人类提醒更自动化——持续监控自己是否偏离原目标

进度追踪 = 把草稿纸上值钱的判断蒸馏成下一轮还能接着用的进度账单。

## 模式定位与五种迷失

双轴坐标：**记忆 × 编排**。是记忆（保存任务执行轨迹）；是编排而非链式（进度状态横切在所有步骤之上，编排是各模式组中最复杂的模式）。

五种常见迷失（一点点积累，很少一步崩掉）：
1. **目标漂移**：要"完成可上线的重构"跑成"把当前 import error 修干净"——局部小目标做完，没回全局。
2. **状态漂移**：以为文件改完了其实保存失败、以为测试跑过了其实只跑了一部分——对世界的记账错了。
3. **错误放大**：链式依赖，第一步误判 schema 字段，后面全部顺着误判长出来。
4. **细节过载**：围着一条 Warning 越钻越深，把预算耗在小坑里。
5. **完成幻觉**：把"todo 都打勾"当成"目标达成"——文档更新了，忘了验证真实系统有没有同步。

## 学术与工业脉络

- **Saga**（1987）：长事务拆子事务+补偿回滚 → 验证闸门+回滚的祖先
- **ARIES**（1992）：预写日志 WAL，崩了按日志恢复 → 进度账+断点续跑
- **Event Sourcing**（Fowler 2005）：只追加的事件、会计账本、纠错靠补偿条目 → "写账本，不是写流水账"
- 一致性快照 → checkpoint；**对账循环（reconciliation loop）→ 漂移哨兵的原型**
- CoALA：进度追踪监控的是情景记忆（这次任务发生了什么）

不能照搬的原因：传统系统状态确定（dashboard 要写哪张表设计时定死）；Agent 状态一半是语义叙事，会被压缩、改写、重新解释——**要专门防目标和判断在时间里漂移**。

工业界主流：Microsoft Magentic-One 双账本（Task Ledger 记事实/猜测/计划 + Progress Ledger 记进度/分工/完成判断）；Anthropic 多智能体 SubAgents（主 Agent 写计划进持久记忆，子 Agent 独立上下文只回传 1-2K 蒸馏摘要）。**编排和链式的分界**：让子 Agent 把本地消息/重试/中间失败灌回共享上下文会污染主 Agent——进度状态由协调者集中维护，局部噪音隔离在子上下文。

## 三平面分治

| 平面 | 内容 | 职责 |
| --- | --- | --- |
| SessionWorkspace 调度态 | 任务 DAG：ready/blocked/completed | 管**顺序**（接下来做什么） |
| SessionNarrative 叙事态 | 锚、账、集 | 管**意义**（为什么做、做到哪） |
| SessionState 机械态 | API 入参的可审计真值（带 Provenance） | 管**真值**（精确参数） |

> 一句话：**叙事态管意义，机械态管真值，调度态管顺序，编排器负责把三者对齐。**

很多长任务跑偏就是三件事混在一起：自然语言摘要夹着业务 id、工具结果夹着目标解释、还要求 LLM 从一堆文本里自己找下一步该传哪个参数。

### 叙事态三关键词：锚、账、集

**锚（GoalContract）**——防目标漂移。目标冻结成可引用契约，比 todo 稳定（todo 随便增删，契约要改必须写 goal_changed 事件说明谁改的为什么）：
- success_criteria：员工范围、复用上月规则记录差异、id 由 SessionState 托管、异常核验、代发报税前人审
- **non_goals：不修改员工主数据、不改规则模板、不直接发起银行付款**——防止细节过载的护栏（看到薪资项名称不统一就去做完整科目治理 = 跑偏）

**账（ledger，不是 log）**——防历史断片。流水账只记"做了什么"，账本能对账。一条进度账记四样：
```
event: 创建 6 月上海市场部薪资组
decision: 复用 5 月薪资规则模板
reason: 用户要求"按上月规则"，本月规则变更未确认
evidence_refs: [tool:create_payroll_group#..., policy/payroll-rule-2026-05.md]
state_delta: write: [STATE.payroll_group_id]
next_action: 生成薪资批次，绑定 STATE.payroll_group_id
```
恢复时不用读完整对话，只读契约+当前里程碑+最近几笔账；人审时能一眼看出哪个决策带偏了任务。

**集（working collection）**——防上下文过载。从账里蒸馏裁剪出当前步骤最相关的一小包材料，和锚一起注入。每次注入带着锚（不忘原始目标），只给当前子任务的材料（不被历史淹没）。这就是长任务的"**接手包**"：新一轮 Agent 先读锚和集，再按需回账本查证。集必须带锚，否则会退化成最近几轮聊天的摘要。

### 机械态：别让 LLM 拼接真值

叙事态不把批次 ID `pg_84721` 塞进摘要，而是写"下一步创建薪资批次，需要 STATE.payroll_group_id"——执行前编排器从 SessionState 解析引用绑定真实值。

```yaml
key: payroll_batch_id
scope: company:acme / org:shanghai-sales / month:2026-06
provider: create_payroll_batch
runtime_layer: plan_exec.M3.step1
value_ref: STATE.payroll_batch_id
trust: tool_output
```

完整动作闭环：叙事态提意图 → 调度态确认 ready → 编排器解析状态引用 → 机械态给精确参数 → 工具执行 → 机械态写回新真值 → 账本追加事件 → 验证闸门对账 → 调度态推进。

反例：LLM 创建快照时误用上月薪资组 id 或测试环境 id——异常核验"通过"了，核验的却是另一个批次。每步都有日志、看起来在好好干活，最后才暴露对象错、月份错、账号错。

## 三个调度收敛器

1. **复诵（Recitation）**：Manus 实践——典型复杂任务平均约 50 次工具调用，很容易偏题；做法是不断重写 todo.md，把全局计划反复推回上下文尾部。recitation_prompt() 每次继续执行前推回：原始目标/成功标准/当前里程碑/活跃子目标/非目标/约束/阻塞/下一步 + "Do not report completion until the current verification gate passes."
2. **漂移哨兵（Drift Watchdog）**：定期问"你现在做的事还跟原目标有关吗"。可用便宜模型或规则看四个分数：goal_relevance、milestone_progress、evidence_health、error_pressure。分级处理：相关度下降→触发复诵；里程碑停滞→缩小范围；证据变差→禁止汇报完成；错误压力上升→暂停进入诊断。
3. **验证闸门（Verification Gate）**：把每个里程碑的验收条件落成具体检查（快照人数=员工范围？金额波动可解释？异常项标出？关键 id 有 Provenance？高风险动作 blocked？待人审清单生成？）。不通过 → needs_rework + failed_gate 事件，回到对应里程碑。**很多企业长程 Agent 失败就是没有闸门，上一阶段的错误自然滑进下一阶段**——Saga 补偿思路的具体实现。

## 长程任务状态 Schema

`LongHorizonTaskState` = task_id + GoalContract + Milestones + current_milestone_id + status + ledger(append-only ProgressEvent) + working_collection + mechanical_state + open_blockers + next_action + last_drift_signal。

两个关键方法：
- **resume_packet()**：断点恢复包——goal、current_milestone、working_collection、recent_ledger[-5:]、open_blockers、mechanical_state_keys、last_drift_signal、next_action。中断后新 Agent 读包接上，不翻聊天记录。
- **recitation_prompt()**：复诵提示，继续执行前重新对齐。

这就是进度追踪和 todo 的差别：todo 只能说"还有哪些没做"；长程任务需要的是能维持目标、状态、证据和恢复路径的**执行状态结构**。

## 工业界的"可恢复"

- LangGraph checkpointer：按线程存状态快照，同 thread_id 从最后检查点重建、time travel 回任意一步
- Temporal + OpenAI Agents SDK（2026-03 GA）：durable execution，每步 journaling 崩溃精确续跑——WAL 在 Agent 工作流的现代版

落地对齐：锚和长期目标放 store/项目文件；任务 DAG 和里程碑放 checkpointer；账本走 append-only 日志；机械状态走 SessionState；中断从 resume_packet 一次性恢复。

## 企业落地三件事（挑高重复高损失任务族：薪资快照/报销审批/合同审阅）

1. 把反复出问题的目标冻成 Goal Contract（写清 success_criteria 和 non_goals）
2. 关键业务参数全部存为 SessionState + Provenance，不让 LLM 在自然语言里处理 id 和金额
3. 每个里程碑配验证闸门，断点用 resume_packet 恢复

观测指标：同类失败复发率持续下降 = Agent 在积累经验，进度追踪有效。

## 关键要点（复习用）

1. 一本好账本五问：为什么这么做（决策）、根据什么（证据）、到哪一步（状态）、下一步不能做什么（边界）、出错能不能追回哪笔带偏（trace）。
2. 三平面分治：叙事（锚账集）+ 机械（Provenance）+ 调度（状态机），编排器对齐。
3. 三收敛器：复诵拉回目标、哨兵监控漂移、闸门强制验收。
4. 长任务防迷失要答五问：还在服务原目标吗、里程碑推进了吗、错误放大了吗、进下一阶段前验收过吗、关键参数还在正确的状态平面里吗。

## 思考题（课程留题）

1. 最容易跑偏的长任务审计：Goal Contract 写得出来吗？non_goals 写了吗？最近一次跑偏是不是做了"看起来有用、当前却不该做"的事？
2. 进度记录是流水账还是能对账的账本？最近一次错误放大能追回哪个早期决策吗？哪些关键参数还在让 LLM 从上下文里复制传递？
3. 今天断掉，明天新 session 能不能只靠 resume_packet 接上，而不是翻聊天记录？

## 下一讲预告

15 失败日记：进度追踪记录任务怎么往前走，失败日记记录任务在哪里摔倒过——把失败做成可召回的经验。
