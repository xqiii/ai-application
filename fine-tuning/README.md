# 模型微调

模型微调（Fine-tuning）是指在预训练模型的基础上，使用特定领域的数据集对模型进行进一步训练，使其适应特定任务或场景的过程。

## 为什么需要微调

- 预训练模型通用性强但针对性弱：像 Qwen、Llama、BERT 等基础模型在大规模语料上训练，具备广泛知识，但在专业领域（如医疗、法律、金融）或特定任务（如情感分析、代码生成）上表现可能不够精准。
- 提升任务性能：通过微调，可以让模型更好地理解特定领域的术语、逻辑和表达方式，显著提升准确率、流畅度和实用性。
- 适配业务需求：企业可以将内部数据（如客服对话、产品文档）用于微调，打造专属 AI 助手。

## 微调方法

1. **全参数微调（Full Fine-tuning）**
- 更新模型所有层的参数。
- 优点：效果通常最好。
- 缺点：计算资源消耗大，容易过拟合小数据集。

2. **参数高效微调（PEFT, Parameter-Efficient Fine-Tuning）**

- LoRA（Low-Rank Adaptation）：只训练低秩矩阵，冻结原模型权重，大幅减少显存占用。
- QLoRA (Quantized Low-Rank Adaptation)：量化预训练权重。
- Adapter Layers：在 Transformer 层间插入小型神经网络模块进行训练。
- Prefix Tuning / Prompt Tuning：仅优化输入端的可学习向量，不改动模型主体。

## LoRA原理

在预训练模型的权重矩阵中，不直接更新原始权重，而是通过训练两个低秩矩阵来近似权重的更新量。

### 数学推导

低秩假设 (Low-Rank Hypothesis)

LoRA 基于一个观察：模型在适应特定任务时，权重更新的内在秩（intrinsic rank）非常低。也就是说， 
$\Delta W$ 虽然维度很大，但它可以被两个小得多的矩阵 $B$ 和 $A$ 的乘积很好地近似：
$$
\Delta W \approx BA
$$

其中：$B \in \mathbb{R}^{d \times r}$，$A \in \mathbb{R}^{r \times k}$，$r \ll min(d,k)$（例如 r = 8 或 16，而 d,k 可能是几千）。

在 LoRA 中，前向传播的计算过程为：

$$
h = W_0x + \Delta W_0x + \frac{\alpha}{r}(BA)x
$$

$x \in R^k$ 是输入向量，$h \in R^d$ 是输出向量。$\alpha$ 是一个超参数（通常设为 r 的倍数，如 16,32 等）。

初始化策略：

- $A$ 使用随机高斯分布初始化（Random Gaussian）。
- $B$ 使用零初始化（Zero Initialization）。
- 这意味着在训练开始时，$\Delta W = 0$，所以 $W = W_0$。这保证了模型在训练初期的行为与预训练模型完全一致，不会破坏原有的知识。

参数量对比：

假设 $d = k = 4096$，秩 $r = 8$
- 全量微调参数量：$4096 \times 4096 \approx 1.67 \times 10^7$ （16.7M）
- LoRA 参数量：$(4096 \times 8) + (8 \times 4096) = 65536$（约0.065M)
- 压缩比：$\frac{16.7M}{0.065M} \approx 256 倍$

```
import torch
import torch.nn as nn
import math

class LoRALinear(nn.Module):
    def __init__(self, in_features, out_features, r=8, alpha=16, dropout=0.0):
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.r = r
        self.alpha = alpha
        
        # 1. 冻结的预训练权重 (W0)
        # 在实际应用中，这部分通常加载自预训练模型，并设置 requires_grad=False
        self.weight = nn.Parameter(torch.empty(out_features, in_features), requires_grad=False)
        
        # 2. LoRA 矩阵 A (r x k) -> 在代码中转置存储或直接定义，这里按数学定义 A: (r, in)
        # 初始化：高斯分布
        self.lora_A = nn.Parameter(torch.empty(r, in_features))
        nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))
        
        # 3. LoRA 矩阵 B (d x r)
        # 初始化：全零 (确保初始 Delta W = 0)
        self.lora_B = nn.Parameter(torch.zeros(out_features, r))
        
        # 缩放系数
        self.scaling = self.alpha / self.r
        
        # Dropout (可选，原论文中有提到)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor):
        # x shape: [batch, seq_len, in_features]
        
        # 原始路径: W0 * x
        # 注意：实际工程中 weight 通常是 frozen 的，这里为了演示逻辑
        result = torch.matmul(x, self.weight.transpose(-2, -1))
        
        # LoRA 路径: (B * A) * x * scaling
        # 步骤 1: x @ A^T  -> [batch, seq, r]
        lora_out = torch.matmul(x, self.lora_A.transpose(-2, -1))
        # 步骤 2: (x @ A^T) @ B^T -> [batch, seq, out]
        lora_out = torch.matmul(lora_out, self.lora_B.transpose(-2, -1))
        
        # 应用缩放
        lora_out = lora_out * self.scaling
        
        # 合并结果
        return result + lora_out

    def merge_weights(self):
        """
        推理阶段优化：将 LoRA 权重合并回主权重，消除额外计算开销。
        W_new = W_old + (B * A) * scaling
        """
        if not hasattr(self, 'merged'):
            # 计算增量
            delta_w = (self.lora_B @ self.lora_A) * self.scaling
            # 更新主权重 (注意：如果 W0 是 frozen 的，实际操作中可能需要临时解锁或创建新 tensor)
            # 这里仅作逻辑演示，实际库如 peft 会处理得更细致
            self.weight.data += delta_w
            self.merged = True
```
