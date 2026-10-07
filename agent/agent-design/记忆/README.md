# 记忆：沉淀之美（11~15 讲）

> 极客时间《Agent 设计模式之美》黄佳 · 第三模块学习笔记
> 课程主页：https://time.geekbang.org/column/intro/101162601?tab=catalog
> 课程大纲图见 [../dual-axis-framework.png](../dual-axis-framework.png)，总大纲见 [../OUTLINE.md](../OUTLINE.md)。

## 本模块主线

回答一个问题：**会话结束后上下文清零，跨会话的状态怎么保留积累**——记忆是 PRA 循环的时间维度，防止长程任务在时间中漂移。记忆不是存储的堆叠，而是**经验的治理**：什么值得留、留多久、何时取回、何时过期。

四模式口诀：**架 → 取 → 录 → 省**。

## 笔记目录

| 讲次 | 笔记 | 双轴坐标 | 核心内容 |
| --- | --- | --- | --- |
| 11 | [记忆模块导论](./11-记忆模块导论-从草稿纸到长期记忆建立Agent的经验沉淀秩序.md) | — | 记忆生命周期五段；四类记忆×四模式映射；auth.py 事故与任务漂移；记忆攻击 |
| 12 | [分层保留](./12-分层保留-给Agent的记忆建一套货架.md) | 记忆×层级★ | 五层货架（Policy/Project/User/Task/Scratchpad）；覆盖关系与升层机制；working set 管理器 |
| 13 | [检索增强](./13-检索增强-Agent的知识库和证据链.md) | 记忆×链式→循环 | RAG=知识供应链；证据契约四件套；索引六步建设；机械状态归 SessionState |
| 14 | [进度追踪](./14-进度追踪-长任务中别让Agent走丢.md) | 记忆×编排★ | 三平面分治（叙事锚账集/机械 Provenance/调度状态机）；三收敛器（复诵/哨兵/闸门） |
| 15 | [失败日记](./15-失败日记-让Agent把摔过的跤变成本事.md) | 记忆×循环★ | 6 层结构；结构化召回键；draft→approved 审查状态机；记忆投毒防御 |

★ 对应大纲图中的重点模式。

## 贯穿概念

- **Memory Trace**：记忆的仪表盘，排查链路 should_write → write → retrieve → use → avoid_repeat_failure。
- **叙事态 vs 机械态**：锚账集管意义，SessionState+Provenance 管真值——机械 id 绝不让 LLM 从自然语言拼接。
- **记忆生命周期**：context window → scratchpad → structured trace → long-term memory → retrieval/replay/forgetting。
- **记忆安全**：memory injection / poisoning / tampering 是持久攻击——写入端来源+人审+租户隔离，召回端当"边界提醒"不当"绝对真值"。

## 高频金句

- 记忆不是存储的堆叠，而是经验的治理。（11）
- 长期层要结构化像资产，临时层要可丢弃像草稿。（12）
- 相关性是给人看的排序，证据是给机器用的依据。（13）
- 流水账只记"做了什么"，账本能对账。（14）
- 错误日志服务一次维修，黑匣子服务下一次飞行。（15）

## 下一模块

[推理：演化之美（16~20 讲）](../推理/README.md)——感知信息和召回经验都摆在工作台后，Agent 怎样组织成可靠判断。
