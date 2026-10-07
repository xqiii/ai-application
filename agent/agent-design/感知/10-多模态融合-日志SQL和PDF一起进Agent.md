# 10｜多模态融合：日志、SQL 和 PDF 一起进 Agent

> 来源：极客时间《Agent 设计模式之美》黄佳 · 感知：世界之美
> 原文链接：https://time.geekbang.org/column/article/986182 （时长 25:05）
> 课程主页：https://time.geekbang.org/column/intro/101162601?tab=catalog
> 本笔记为学习总结，版权归原作者所有。

## 一句话总结

多模态融合（感知×并行）= **数据形态工程**：在数据进入 Agent 之前判断每种输入最适合什么形态（保留为图 / 转 Markdown / 先过滤再进），带着关联关系合并到推理层——让 Agent 看到恰当的少。

## 模式定位

- 双轴坐标：**感知 × 并行**。多种异构数据源同时进入，每种走最适合自己的处理路径。
- 与前三讲的本质区别：分诊/压缩/渐进发现处理"哪些 token 进来、怎么压、怎么找"；多模态融合处理更前面一步——**数据该先变成什么形态**。
- 开场案例（80 页金融研报）：大一统 all-in-one prompt 会把"5800 亿"误读成"5800 万"（y 轴没看清）；全转文本会丢光图表空间信息。根因是**数据形态不对**，与 prompt 好坏、模型强弱无关。
- 编辑类比：财经稿件——市场规模趋势用折线图（空间关系是信号）、财务数据用表格（结构是信号）、分析观点用文字（逻辑是信号）。

**总原则**：空间关系本身是信号就保留为图；否则转成紧凑、可检索、易压缩的文本或结构（JSON Schema）。

## 切片一：Claude Vision API 的 token 数学

- 图片 token ≈ width × height / 750；1024×1024 ≈ 1400 token，约为同长度中文文本的 1.5 倍——**图片没想象中贵**。一张 5 组件 8 连线的架构图转文字要 800-1200 字，保留为图反而更划算。
- PDF 不同：文本密集型 30 页 PDF 走 PDF API 约 5-6 万 token（每页近 2000）；80 页研报暴力喂会超 15 万 token——成本与稳定性双重压力。
- **提示词缓存（prompt caching）**：同一份合同/研报被反复问答、摘要、核查时，cache 直接改变 Agent 的经济模型。该统计：Agent 每天处理多少重复文档。

## 切片二：Hermes 的多模态工程与 lazy import

音频输入纳入流水线（麦克风实时录音/mp3/wav，跨平台适配），但**音频依赖必须 lazy load，不能 module 顶层 import**——否则在 SSH/Docker/WSL/无 PortAudio 环境里 Agent 启动即崩。

推广原则：凡 80% 用户用不到且依赖运行环境的库（cv2、pydub、pdfplumber、transformers）都应函数内 lazy import 或 try-except 优雅降级。对 Agent 部署兼容性至关重要。

## 8 框架横切

多模态投入与场景强相关：编程 Agent 主战场是代码/文件/终端/git diff，Codex CLI、Aider 不重多模态；通用助理/客服/研报 Agent 输入天然混杂，多模态是基础能力。**问题不是"框架有没有多模态"，而是你的 Agent 面对的世界是不是本来就多模态。**

Gemini CLI 大窗口路线：窗口足够大可减少预处理（视频、复杂图文有价值，手工抽帧转写成本高）；但窗口变大只提高上限，不解决成本、噪声、延迟、可控性——长日志、大结果集、批量 PDF 仍需流水线。

## 最小骨架（MultiModalFuser）

```python
class ModalityType(Enum):
    TEXT = "text"        # 直接进上下文
    IMAGE = "image"      # 空间关系是信号，保留为图
    TABLE = "table"      # 转 markdown
    LOG = "log"          # bash 预过滤 + sub-agent
    PDF = "pdf"          # TOC + 关键页 + 抽取文本
    AUDIO = "audio"      # STT 转文本
    SQL_RESULT = "sql_result"  # 抽样 / compact table

# ModalityInput: type + payload + hint(业务含义) + keep_as_image(强制保留例外)
# FusionEvent: modality / tokens_out / processing_ms / method —— 没有它 fusion 就是黑盒
```

关键决策：① 图片可强制保留（keep_as_image=True 处理双 Y 轴图、架构图等例外）；② 日志必须走流水线 bash_filter → log_subagent → structured summary；③ PDF 不默认整份塞（TOC + key_pages + business_hint）。工具用原子注入（OCR/STT/PDF 解析可替换）。

health_check 异常预警：image token 占比 >50%（该转表的图没转）、log 占比 >40%（bash 预过滤没生效）、PDF 持续过高（关键页抽取规则太松）。

## 业务实战：金融研报分析 Agent

80 页 14MB PDF → 三任务（核心论点摘要 / 数字核查 / 销售要点）。

**融合层分发**：PDF 主体抽 TOC+关键页（约 6K token）+ 5 张关键图表保留为图（约 7K）+ 12 张表转 markdown（约 2.4K）+ **装饰图丢弃**；总产出约 16K token vs 暴力全喂近 90K。

- **CriticalChartSpec 是行业知识**：市场规模/市占率/增长趋势/营收/利润率/ROE/渗透率/ARPU/估值/PE/PB/PS 关键词命中的图优先保留——这一步比 prompt 调优更重要。
- 数字核查必须强制引用：输出 claim/number/page_or_chart_ref/confidence，找不到引用 confidence 标 low——降低"数字自信但无出处"风险。
- 三决策：关键图表识别是核心；装饰图必须丢（白名单+黑名单）；同一份报告配合提示词缓存复用。

**一句话**：文本给逻辑，表格给结构，图表给空间关系，trace 给质量审查。

## 可观测性三指标

| 指标 | 健康区间 | 异常含义 |
| --- | --- | --- |
| token_distribution_by_modality | text 40-60% / image 10-30% / structured 10-20% / log 5-15% | image 突涨 60% = 该转的图没转 |
| fusion_processing_p99_ms | < 5 秒 | 飙到 30 秒+ = PDF 抽取或 OCR 卡住 |
| bash_filter_compression_ratio | 0.01-0.05（500MB→5-25MB） | >0.1 过滤太松；<0.005 过滤太狠丢信号 |

## 决策卡：保留为图还是转文本

| 输入类型 | 推荐 | 理由 |
| --- | --- | --- |
| 架构图/流程图/调用链 | 优先转 Mermaid | 8 节点架构图 Mermaid 几十 token，模型读得清，可检索可 diff；UI 布局/视觉标注才留图 |
| 普通表格/财务表/SQL 结果 | 转 Markdown/CSV | Markdown 能还原 95% 信息就转；跨页表/多层表头/合并单元格才走 vision |
| 柱状图/折线图/热力图 | 保留图 + 数字抽成 CSV/JSON | 视觉模型看趋势可以，读精确数字/算 CAGR 风险高——**看图靠 vision，算数靠结构化数据** |
| 密集文字截图（整页代码/报告） | 有时直接当图更划算 | OCR 又长又有格式噪声；但要搜索/diff/引用字段时仍需转文本 |

## 三类生产事故

1. **看错图**：5800 亿读成 5800 万——图像抽出的数字要和正文、表格同名数字交叉校验。
2. **图片账单爆炸**：Agent loop 每步重新打包同一张图，生产几十步按轮次放大——用缩略图 + 提示缓存。
3. **Sub-Agent 死循环与关键发现丢失**：每次传整段上下文没预算上限就烧钱——关键结论放状态存储、上下文只传指针；每个 Sub-Agent 启动前声明 token/时间/递归预算，触发即停。

## 感知模块收束

四模式顺序有意义：**多模态融合最靠前**（先定数据形态）→ 上下文分诊（谁靠近模型）→ 语义压缩（长会话工作记忆）→ 渐进发现（未知空间探索）。形态一开始错了，后面都是 在错误材料上消耗。

感知管当前会话内看见什么；会话结束上下文清零——下一模块进入记忆：跨会话怎么沉淀。

## 思考题（课程留题）

1. 统计 Agent 输入的 token 分布（text/image/table/log/pdf/audio）：有没有占 60%+ token 只贡献 20% 信息量的内容？
2. 写出 5 条具体的"图片 vs 转文本"规则（如"普通条形图转表格"）。
3. 拿一张真实图表设计 10 个问题（数值 3/趋势 2/比较 2/坐标 2/图例 1），让模型直接看图回答，对比原始数据记录错误类型。
4. 给业务的 PDF Agent 设计一张 Fusion 决策卡（输入类型/处理方式/保留理由/token 估算）。

## 下一讲预告

11 记忆模块导论：感知管当下，记忆管跨会话——用户三天前问过的问题、Agent 上次踩过的坑、本租户的偏好如何保留积累。
