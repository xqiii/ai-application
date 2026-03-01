# 微调

## 项目流程

###  需求分析与技术选型

目标：明确“为什么要微调”以及“用什么方案微调”。

1. 明确任务目标：
- 是风格模仿（如：变成小红书文案风格）？
- 是领域知识注入（如：医疗、法律、代码）？
- 是指令遵循（如：让模型听懂复杂的系统指令）？
- 注意：如果是简单的知识问答，RAG（检索增强生成）可能比微调更合适。

2. 选择基座模型
- 参数量：根据显存和推理延迟要求选择（7B/8B 适合端侧，70B+ 适合复杂推理）。
- 许可证：确认商用许可（如 Llama 3, Qwen2.5, Yi 等）。
- 版本：选择 Base（基座，适合继续预训练或强指令微调）还是 Instruct/Chat（指令版，适合特定任务适配）。通常建议在 Base 模型上进行全量 SFT，或在 Instruct 模型上进行轻量适配。

3. 确定微调策略
- QLoRA：单卡/低显存首选，性价比最高（推荐）。
- LoRA：多卡环境，追求稍高性能。
- Full Fine-tuning：算力极其充足，且需要模型发生“质变”时。

### 数据工程

1. 数据收集：
- 公开数据集（Alpaca, OpenOrca, COIG 等）。
- 业务私有数据（日志、文档、客服记录、代码库）。

2. 数据清洗：
- 去重（Remove Duplicates）。
- 去除乱码、无关符号、PII（个人隐私信息）。
- 过滤低质量样本（如回答过于简短、逻辑混乱的）。

3. 数据格式化：
- 转换为标准格式（通常是 JSONL）。
- 构造 Prompt 模板：必须与基座模型的 Chat Template 一致（如 [INST] ... [/INST] 或 <|im_start|>）。

4. 数据配比与混合：
- 混合通用数据（防止灾难性遗忘）和领域数据。
- 典型比例：通用 : 领域 = 1 : 3 或 1 : 5（视具体任务而定）。

### 环境搭建与配置

1. 硬件
- 计算显存需求：模型权重(4bit/16bit)+梯度+优化器状态+激活值。
- QLoRA 微调 7B 模型约需 8-12GB 显存；70B 约需 48GB+。

2. 软件
- 基础：PyTorch, CUDA, Python
- 核心库：transformers, peft, accelerate, bitsandbytes (QLoRA必备), flash-attn。
- 框架：LLaMA-Factory (一站式), Axolotl, DeepSpeed-Chat。

## 实操

### 模型下载

下载 modelscope 包：
```
pip install modelscope
```

下载基座模型：

```
import torch
from modelscope import snapshot_download
import os
model_dir = snapshot_download('LLM-Research/Meta-Llama-3-8B-Instruct',
cache_dir='/root/autodl-tmp', revision='master')
```

包：

1. transformers： 它提供了许多预训练的模型，这些模型可以⽤于⾃然语⾔处理（NLP）任务，⽐如⽂本分类、信息抽取、问答、摘要、翻译以及其他与语⾔相关的任务。它⽀持 BERT、GPT、T5、XLNet 等多种模型架构。注意：模型架构和模型本⾝是不⼀样的，⽐如 GPT4 是基于 GPT 的模型架构设计的， LLaMA 也是基于 GPT 的模型架构设计的。
2. peft： ⽤于efficient fine tuning, ⽐如 Lora 等
3. bitsandbytes专注于提供⾼效的位操作和字节级处理功能，尤其适⽤于深度学习和⾃然语⾔处理任务中对性能要
求较⾼的场景。这个库提供了⼀些底层操作，可以加速数据处理，尤其是在需要进⾏位打包、解包
或进⾏位级逻辑运算时。
4. datasets： datasets 是 Hugging Face 提供的⼀个轻量级、易于使⽤、可扩展的 NLP 数据集库。
它提供了快速访问的接⼝，可以下载和处理多种 NLP 任务所需的数据集。
5. trl： trl 是“transformer-reinforcement learning”，这是⼀个结合了变换器 （transformers）模型
和强化学习（reinforcement learning）的库。它通常⽤于优化语⾔模型的⽣成性能，主要⽤于SFT, reward model, 强化学习。