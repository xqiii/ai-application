# 私有化部署

## Ollama

下载地址：https://ollama.com

验证

```
ollama --version
```

下载大模型（qwen2.5为例）
```
ollama run qwen2.5
```

硬件

- 内存 (RAM):
    - 运行 7B 参数模型：建议至少 8GB 内存（推荐 16GB）。
    - 运行 14B-32B 参数模型：建议 16GB-32GB 内存。
    - 运行 70B+ 参数模型：建议 64GB 以上内存或多卡环境。
- 显卡 (GPU):
    - Ollama 支持 NVIDIA GPU (CUDA) 和 Apple Silicon (Metal) 加速。
    - 有独立显卡时，推理速度会显著提升。如果没有独显，它将使用 CPU 运行，速度较慢但依然可用。
- 磁盘空间:
    - 模型文件较大，请确保有足够的剩余空间（一个 7B 模型约占用 4-5GB 硬盘）


## 分布式推理

分布式推理（Distributed Inference） 是指将一个大语言模型（LLM）的推理任务拆分到多台机器（多节点）或多张显卡上并行执行的技术


当模型参数量巨大（如 70B、405B 甚至万亿参数），单张显卡的显存（VRAM）无法容纳整个模型权重和 KV Cache 时，或者为了追求极致的低延迟和高吞吐，就必须使用分布式推理。

### 策略

1. **张量并行 (Tensor Parallelism, TP)**
- 原理：将模型的每一层内部的矩阵运算切分，分配到同一台机器内的多张 GPU 上同时计算。
- 特点：
    - 通信开销极大：需要 GPU 之间通过 NVLink/NVSwitch 进行高频通信。
    - 适用场景：单机多卡。通常限制在同一台服务器内（如 8 卡 H100 节点）。
    - 延迟：极低（因为所有卡在同步计算）。
    - 限制：跨机 TP 效率极低，一般不推荐跨节点做 TP。
```
# 单级多卡
python -m vllm.entrypoints.api_server \
    --model Qwen/Qwen2.5-72B-Instruct \
    --tensor-parallel-size 8 \
    --host 0.0.0.0 \
    --port 8000
```

2. **流水线并行 (Pipeline Parallelism, PP)**
- 原理：将模型按层（Layer）切分。例如，模型共 100 层，GPU 0 跑 1-25 层，GPU 1 跑 26-50 层，以此类推。数据像流水线一样在 GPU 间传递。
- 特点：
    - 通信开销较小：只需传递中间激活值（Activation），频率低于 TP。
    - 适用场景：跨机部署。可以跨越多个物理节点。
    - 延迟：略高于 TP（存在流水线气泡），但能跑超大模型。

3. **专家并行 (Expert Parallelism, EP) / MoE 专用**
- 原理：针对 MoE (Mixture of Experts) 架构模型（如 Qwen-MoE, Mixtral, Grok）。将不同的“专家（Experts）”子网络分布在不同 GPU 上。请求到来时，路由网络只激活部分专家。
- 特点：
    - 高吞吐：不同请求可以路由到不同节点的不同专家，天然负载均衡。
    - 显存优化：无需将所有专家加载到每张卡上。

4. **上下文并行 (Context Parallelism, CP) / Ring Attention**
- 原理：针对超长上下文（如 128K, 1M tokens）。将长序列切分，利用 Ring Attention 算法在多卡间交换 KV Cache 信息。
- 场景：处理超长文档、视频理解等场景。

### 架构

**PD 分离架构**

将推理过程拆分为两个独立的集群：
- Prefill 集群：专门负责处理输入提示词，计算完后将 KV Cache 传输给 Decode 节点。
- Decode 集群：专门负责生成 Token，专注于高并发解码。

vLLM 实现：vLLM 0.6+ 版本原生支持 Disaggregated Prefill/Decode，通过共享内存或高速网络（RDMA）传输 KV Cache。

### AI网关

Higress

1. **统一入口与负载均衡**：
- 无论后端是单机 TP 还是多机 PP，Higress 提供唯一的 VIP 入口。
- 如果部署了多个 vLLM 实例（例如多副本 PD 分离集群），Higress 负责将请求均匀分发。
2. **智能路由 (基于模型/租户)**：
- 根据请求头中的 model 字段，将小模型请求路由到小集群，大模型请求路由到大集群。
- 区分 VIP 用户和普通用户，路由到不同优先级的后端队列。
3. **故障转移 (Fallback)**：
- 如果某个分布式节点（如 PP 链路中的某一台机器）宕机，Higress 可快速探测并切断流量，切换到备用集群，避免用户看到 500 错误。
4. **流式响应优化**：
- 分布式推理涉及多次网络跳转，Higress 优化了 SSE (Server-Sent Events) 的透传，确保打字机效果流畅，减少首字延迟感知。


### 规模

| 模型规模 | 推荐策略 | 硬件需求 | 部署工具 |
| :--- | :--- | :--- | :--- |
| < 30B | 单机单卡 / 单机双卡 TP | 1-2 x A10/A800 | vLLM (TP=1/2) |
| 30B - 70B | 单机多卡 TP | 4-8 x A100/H100 | vLLM (TP=4/8) |
| 70B - 200B | 多机 TP+PP 或 纯 PP | 2+ 节点 (16 卡+) | vLLM (TP=8, PP=2) |
| > 200B / MoE | PD 分离架构 + EP | 多节点集群 | vLLM (Disaggregated) |
| 超长上下文 | Context Parallelism | 多节点 + 高速互联 | vLLM (CP enabled) |

### llama.cpp

项目地址：https://github.com/ggml-org/llama.cpp

核心优势：
- 使用 C/C++ 编写，无 Python 依赖，性能极高
- 支持 GGUF 量化模型格式（如 Q4_K_M、Q5_K_S 等）
- 跨平台：Windows / Linux / macOS / Android
- 提供 Web UI 和 OpenAI 兼容 API
- 支持 CPU + GPU 混合推理（通过 Vulkan / CUDA / Metal）