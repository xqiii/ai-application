# AI概念

## LLM

大语言模型（Large Language Model，简称 LLM）是一种基于深度学习的人工智能模型，专门用于理解、生成和处理人类语言。它们通常基于Transformer 架构，通过在海量文本数据上进行预训练，学习语言的统计规律、语义关联以及世界知识，从而能够完成翻译、问答、写作、编程、逻辑推理等多种任务。

### 模型参数

模型参数（Model Parameters）是深度学习模型（包括大语言模型）在训练过程中自动学习到的内部数值。B 代表 Billion（十亿）。如Qwen3-8B 约有 80 亿个参数。

显存占用：
- FP16/BF16 (半精度)：每个参数占 2 Bytes。7B 模型约占 14GB显存，72B 约占144 GB。
- INT4 (4-bit 量化)：每个参数占 0.5 Bytes。7B 模型约占 3.5GB显存。72B 约占 36GB。

### 模型架构

1. 密集架构 (Dense Architecture)

模型的每一层都包含一个前馈神经网络（FFN）。当模型处理任何一个输入（比如一个字或词）时，该层中所有的神经元（参数）都会参与计算。假设模型有 70 亿参数（7B），那么处理每一个字，这 70 亿参数都要进行一次矩阵乘法运算。

2. 混合专家架构 (Mixture of Experts, MoE)

模型将传统的单一前馈网络层替换为多个“专家”网络（Experts），并引入一个路由网络（Router/Gating Network）。当输入一个 token 时，路由网络会分析这个 token 的特征，然后从几十个甚至上百个“专家”中，挑选出最擅长处理该任务的 Top-K 个专家（通常 K=2 或 4）。

### 模型类型

1. 预训练模型

预训练模型是指通过在海量无标注数据上进行自监督学习（Self-Supervised Learning）而训练出来的基础模型。


2. 蒸馏模型 (Knowledge Distillation)

降本增效。用一个小模型（学生）去模仿一个大模型（老师）的行为。现在流行的思维链蒸馏（CoT Distillation），是直接把大模型生成的详细推理步骤（Reasoning Trace）作为训练数据教给小模型，让小模型也学会“一步步思考”。

3. 量化模型 (Quantization)

大模型默认使用 FP16 或 BF16（半精度浮点数），每个参数占 2 Bytes (16 bit)。量化是将这些高精度的浮点数，映射到低精度的整数（如 INT8, INT4, 甚至 INT1）。

4. 微调模型 (Fine-tuning)  

使用高质量的特定领域数据集，在基座模型的基础上继续训练（更新参数）。

- 全量微调 (Full Fine-tuning)：更新模型的所有参数。效果最好，但成本极高，需要多卡集群。

- 参数高效微调 (PEFT)：只更新极少部分参数，冻结大部分。
    - LoRA（Low-Rank Adaptation）：只训练低秩矩阵，冻结原模型权重，大幅减少显存占用。
    - QLoRA (Quantized Low-Rank Adaptation)：量化预训练权重。


## Prompt

Prompt（提示词） 是用户与大型语言模型交互时输入的指令、问题或描述。


## SKILL

带目录的说明书，或者“渐进式披露提示词的机制”。skill将提示词分成了三层：元数据、指令、资源。

流行Skills

1. Code Reviewer：自动检查代码安全性、性能隐患，并对照团队规范打分。
2. Git Commit Standard：强制将提交信息格式化为 Conventional Commits 标准。
3. Legacy Refactor：专门用于理解老旧代码（如 jQuery），并逐步重构为现代框架（如 React/Vue）的向导。
4. Doc Generator：自动读取代码库，生成符合公司模板的 API 文档和 README。
5. Security Guard：在代码生成前扫描潜在的 SQL 注入、XSS 风险。

## MCP

模型上下文协议。ai agent和大模型直接的通信协议，让ai模型方便、安全连接外部数据源和工具。


## Agent

具备自主决策能力的软件实体，可以感知环境、规划行动、调用工具
并执行任务。


## RAG

检索增强生成。RAG 让大语言模型（LLM）在回答问题之前，先去外部的知识库或互联网上“查找”相关资料，然后基于这些查到的真实信息来生成答案，而不是仅仅依赖模型训练时记忆的内部知识。