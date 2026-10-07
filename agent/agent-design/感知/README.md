# 感知：世界之美（06~10 讲）

> 极客时间《Agent 设计模式之美》黄佳 · 第二模块学习笔记
> 课程主页：https://time.geekbang.org/column/intro/101162601?tab=catalog
> 课程大纲图见 [../dual-axis-framework.png](../dual-axis-framework.png)，总大纲见 [../OUTLINE.md](../OUTLINE.md)。

## 本模块主线

回答一个问题：**当下这个 session 里，Agent 怎么看清楚世界**——感知是 PRA 循环的入口闸，决定 Agent 看见什么，也就决定 Agent 能想什么。核心目标：找出"使下一次推理质量最大化的最小高信号 token 集合"。

四模式口诀：**选 → 压 → 探 → 融**，顺序有讲究：融合定形态 → 分诊定谁进 → 压缩保记忆 → 发现探未知。

## 笔记目录

| 讲次 | 笔记 | 双轴坐标 | 核心内容 |
| --- | --- | --- | --- |
| 06 | [感知模块导论](./06-感知模块导论-如何优雅设计Agent感知层.md) | — | 感知=架构问题；Perception Trace 行车记录仪；感知工程与 DB/OS/CDN 同源 |
| 07 | [上下文分诊](./07-上下文分诊-如何科学分流处置不同信息.md) | 感知×路由★ | P0/P1/P2/P3：高优进 context、中优压缩、低优挂 handle；多租户 tenant_id 硬约束 |
| 08 | [语义压缩](./08-语义压缩-让200K装下1M的日志.md) | 感知×链式★ | 三层压缩链；Anchor 五问（含 excluded_approaches）；ObservationMasking；错误堆栈不能压 |
| 09 | [渐进发现](./09-渐进发现-信息的觅食循环.md) | 感知×循环★ | Forage→Focus→Deepen；Agentic Search vs RAG；atomic tools；max_cycles=3 |
| 10 | [多模态融合](./10-多模态融合-日志SQL和PDF一起进Agent.md) | 感知×并行 | 数据形态工程：保留为图 or 转 Mermaid/Markdown/CSV；token 数学；三类生产事故 |

★ 对应大纲图中的重点模式。

## 贯穿概念

- **Perception Trace**：记录推理前经历了什么（读了什么/丢了什么/证据落在哪）——感知的行车记录仪。
- **感知指标族**：budget_usage、dropped_count、compression_ratio、p3_hit_rate、cycles_to_success_p50、zero_signal_rate、bash_filter_compression_ratio、token_distribution_by_modality。
- **Karpathy 类比**：LLM=CPU，上下文窗口=RAM，文件系统=磁盘——感知是一条信息流 pipeline。

## 高频金句

- 模型是在你喂给它的那一小块上下文里推理，它看不到的事实对它不存在。（06）
- 上下文窗口是急诊室而非数据库：数据库追求完整保存，急诊室追求优先处置。（07）
- 压缩的安全感来自关键证据的保护，尤其是错误堆栈。（08）
- Discovery 是侦探：给它原子工具、好的 keyword 推导、satisficing 纪律。（09）
- 好的 fusion 让 Agent 看到恰当的少；看图靠 vision，算数靠结构化数据。（10）

## 下一模块

[记忆：沉淀之美（11~15 讲）](../记忆/README.md)——会话结束后上下文清零，跨会话的状态如何保留积累。
