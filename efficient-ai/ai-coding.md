# AI 编程实践

## Cursor

### 方法论

1. 在接手复杂项目或模块时，先让 Cursor 分析整个代码库，生成一份包含核心架构、模块职责和数据流的文档。这一步非常关键，因为它决定了后续协作的质量。只有当我和 AI 对项目有一致理解时，后续产出才会稳定、高质量。

2. 对于每个独立的开发任务，开启一个新的对话，并提供必要的上下文，包括需求背景、涉及模块和约束条件。这种方式能显著减少上下文污染，让 AI 生成的代码更加精准，基本不需要大幅返工。

3. 定期删除冗余实现和废弃代码。旧代码会误导 AI 的判断，增加上下文噪音，长期不清理会直接影响协作效率。

### 原则

1. AI 生成代码之后必须人工 Review。

2. 关键逻辑必要时自己重写。

3. 核心路径必须做压测和边界测试。

## Spec Coding

在让 AI 编写代码之前，人类开发者必须先与 AI 共同制定一份详细、结构化的“规格说明书”（Specification，简称 Spec）。AI 将严格基于这份文档来执行开发任务，而不是仅凭模糊的自然语言指令（Prompt）去“猜”用户的需求。

1. Specify（产品定义）： 充当产品经理，编写类 PRD 文档。明确产品功能、目标用户以及要解决的核心痛点。这一步决定了“做什么”。

2. Plan（技术规划）： 进入技术方案阶段。确定技术栈（如 Java 21 + Spring Boot 3）、系统架构、代码规范及核心契约。这一步决定了“怎么做”。

3. Tasks（任务拆解）： 将技术计划拆解为一个个原子化的 Task，并为每个 Task 设定明确的验收标准（Acceptance Criteria）。

4. Implement（AI 执行）： 将前面的 Spec（requirements.md, design.md, tasks.md）一并丢给 AI 编程工具。此时 AI 会基于这些“前置共识”独立工作，开发者不再需要盯着它看，直接等待验收即可。

### OpenSpec

Claude Code 支持读取项目根目录下的 .claude/commands/ 文件夹中的 Markdown 文件作为“快捷指令”。OpenSpec 利用这一机制，将复杂的 SDD 流程固化为几个简单的命令（如 /opsx:propose），让 Claude Code 自动执行对应的规范步骤。

安装

`npm install -g @fission-ai/openspec@latest
`

初始化

`openspec init`

目录

```
openspec/
├── specs/            # 所有系统规范的当前真相来源。
│   └── [capability]/
│       └── spec.md
└── changes/          # 所有提议、活动和已归档变更的目录。
    └── [change-name]/
        ├── proposal.md     # 变更的"原因"和"内容"。
        ├── tasks.md        # AI 的实施检查清单。
        └── specs/          # 规范的增量"补丁"。
            └── [capability]/
                └── spec.md

```

工作流

1. 提案阶段 (Propose)

    ```
    /opsx:propose 添加用户登录功能，支持 GitHub OAuth
    ```

    它会读取 `.claude/commands/opsx-propose.md` 中的指令。
    生成一份详细的 `changes/login-feature/proposal.md` 文档。
    文档包含：需求背景、技术选型、API 设计、文件修改列表、潜在风险。

    审查这份文档。如果不满意，直接在对话中要求修改，直到文档完美。此时不产生任何代码变更。


2. 实施阶段 (Implement/Apply)

    确认无误后

    ```
    /opsx:apply
    ```

3. 归档阶段 (Archive)

    功能开发完成并测试通过后。

    ```
    /opsx:archive
    ```

    claude 将 changes/ 下的提案移动到 specs/ 永久存储。更新项目主文档（如 AGENTS.md 或 README.md），记录本次变更。清理临时上下文。