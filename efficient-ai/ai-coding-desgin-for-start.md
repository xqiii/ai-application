# 企业级AI开发实践

## 首次项目

核心：在动第一行代码之前，把产品边界、技术选型、运维预期全部想清楚，写进 CLAUDE.md（AGENTS.md）。

### 产品定义

1. 调研标杆

```
帮我梳理 xxx 这个产品的核心功能模块，
按类别分组，每个模块用一两句话说明它做什么。
```

2. 功能取舍

```
我要基于 xxx 做一个简化版的 xxx 平台，叫 xxx。
约束条件：一个人开发，面向团队内部 20-50 人使用，本地部署。
请从刚才梳理的功能列表中，帮我判断哪些是必须做的核心功能，
哪些可以砍掉，给出每个的理由。
```

第一问：没有它产品还成立吗？

第二问：做到什么程度够用？

第三问：能不能一句话说清楚？


3. 技术选型

4. 运维预期

```
xxx 是一个 xxx 平台，Docker Compose 本地部署，
目标 20-50 人同时在线，主要压力在xxxx。
帮我估算 QPS、建议缓存策略、列出需要提前考虑的运维事项。
```

5. 落地文字

### 架构设计

#### 应用架构

1. 模块结构

- 按 package 分层。所有功能放在一个模块里，按 controller/service/mapper 分 package。

- Maven 多模块，按功能拆。

- 微服务。每个功能一个独立服务，通过网关路由，服务间 Feign 调用。

2. 代码组织

```
xxx 是模块化单体，用 Spring Boot + MyBatis-Plus。
帮我定义代码组织规范，覆盖：每个模块内部的分层结构、
每一层的职责边界、跨模块调用的规则。要求具体到 AI 能直接
执行，不要模糊的描述。
```

```
src/main/java/com/xxx/{module}/
├── controller/        # REST 接口
├── service/           # 业务逻辑接口
├── service/impl/      # 业务逻辑实现
├── mapper/            # MyBatis-Plus Mapper
├── entity/            # 数据库实体
├── dto/               # 请求/响应对象
├── config/            # 配置类
├── exception/         # 模块级自定义异常
└── constant/          # 模块级常量
```

- Controller 只做两件事：参数校验和调用 Service。不写业务逻辑、不做数据查询、不处理事务。

- Service 处理所有业务逻辑，包括事务管理、数据校验、业务规则。Service 之间可以互相调用，但只能调接口（interface），不能直接 new 实现类。

- Mapper 只做数据库操作。不要在 Mapper 的 XML 里写业务逻辑（比如复杂的条件判断），那是 Service 的事。

- Entity 和数据库表一一对应。DTO 是给接口用的请求 / 响应对象。Entity 和 DTO 之间要做转换，不要把 Entity 直接返回给前端——Entity 里可能有敏感字段（API Key），DTO 可以控制暴露哪些字段。

3. 部署架构

```
xxx 是模块化单体，技术栈 Spring Boot + Vue + 
MySQL + Redis + pgvector。
目标 50 人内部使用，生产环境用 Docker + K8s 部署。
帮我设计当前阶段的部署架构：
有哪些组件、请求怎么流转、每个组件的职责是什么
```

4. 数据模型

```
基于 xxx 的功能范围（模型管理、Agent、对话、工作流、
知识库、MCP 工具），
帮我梳理核心数据表和它们之间的关系。
只要表名和关系，不展开字段。
```

5. 数据性能

```
xxx 用 MySQL 8.x + pgvector。
帮我定义数据库层面的性能规范，
覆盖：索引设计原则、大表预判和应对策略、
分页查询注意事项、通用字段约定。
要求具体到 AI 建表时能直接执行。
```

### 工程搭建

CLAUDE.md/AGENTS.md

一个放在项目根目录下的 Markdown 文件。项目根目录的 CLAUDE.md 对整个项目生效。你也可以在子目录下放 CLAUDE.md，它只对那个目录下的代码生效，比如 web/ 下放一份前端专属的规范，chat/ 下放一份对话引擎的特殊约定。子目录的规范会和根目录的叠加，不会覆盖。

结构组织：
1. 项目上下文：这是什么项目，做什么不做什么
2. 架构规范：模块划分、依赖关系、外部调用处理
3. 代码组织规范：分层结构、Controller/Service/Mapper  职责
4. 部署与数据库规范：部署架构、索引规则、分页规范、pgvector
5. 接口规范与行为指令

接口规范

```
## 接口规范

### 路径
RESTful 风格：/api/v1/{资源复数名}
GET    /api/v1/providers          # 列表（分页）
POST   /api/v1/providers          # 创建
GET    /api/v1/providers/{id}     # 详情
PUT    /api/v1/providers/{id}     # 更新
DELETE /api/v1/providers/{id}     # 删除
POST   /api/v1/providers/{id}/test-connection  # 非 CRUD 操作用动词

### 统一响应
所有接口返回 Result<T>：
{ "code": 200, "message": "success", "data": {...} }

### 分页
请求：page（从 1 开始）、pageSize（默认 20，最大 100）
响应：Result<PageResult<T>>，PageResult 包含 list、total、page、pageSize

### 空值
- 列表字段空时返回 []，不返回 null
- 字符串字段空时返回 ""，不返回 null
- 对象不存在时返回 null

### 错误码
四位数字，按模块分段：
1000-1999 通用 | 2000-2999 Provider | 3000-3999 Agent
4000-4999 Chat | 5000-5999 MCP | 6000-6999 Workflow | 7000-7999 Knowledge
```

行为指令

```
## 行为指令

### 写代码时
- 每个功能用最简单直接的方式实现
- 不引入不必要的设计模式，除非我明确要求
- 不做过度抽象
- 不引入技术栈以外的依赖，需要时先问我
- 所有外部调用必须有超时设置
- 配置项外化到 application.yml，不硬编码

### 改代码时
- 先理解相关模块的设计意图
- 不要为了新功能破坏已有接口契约
- 改完确保已有测试通过

### 不确定时
- 架构选择给我 2-3 个方案对比，我来拍板
- 规范没覆盖的情况，先问我，不要自己编规矩
```
#### 任务拆解

任务拆解标准：

- 生成的代码量是否超出你一次能 review 的范围。十几行代码，肉眼扫一遍就行，不用拆。几十个文件、几百行配置，一次看不过来，必须拆。
- 步骤之间是否有依赖关系。如果第二步依赖第一步的结果（比如业务模块的 pom 依赖公共模块的 pom），那就应该先完成第一步、验证正确，再做第二步。否则第一步错了，第二步会错得更离谱。


#### 后端骨架与基础设施

1. Maven 多模块骨架（父 pom +  子模块 pom +  目录结构）


```
按照 CLAUDE.md 中的项目结构和技术栈，创建 xxx 的 Maven 多模块工程骨架。
父 pom 声明所有子模块，统一管理 Spring Boot、MyBatis-Plus、Redis 等版本号。
子模块之间的依赖关系按 CLAUDE.md 中定义的架构来。只创建 pom 和目录结构，不需要写 Java 代码。
```

2. xxx 公共基础设施（Result、BizException、全局异常处理、配置类

    任务一：统一响应 Result 和分页 PageResult
    ```
    在 xxx 中创建统一响应类。按照 CLAUDE.md 接口规范：Result 
    包含 code、message、data 三个字段，提供 ok() 和 fail() 
    静态方法。PageResult 继承 Result，额外包含 total、page、size。
    ```
    任务二：错误码枚举和业务异常

    ```
    在 xxx 中创建错误码枚举 ErrorCode 和业务异常
    BizException。ErrorCode 包含 code 和 message，
    覆盖通用错误（参数错误、未授权、系统内部错误等）。BizException 持有 ErrorCode，支持自定义 message 覆盖。
    ```

    任务三：全局异常处理器

    ```
    在 xxx 中创建全局异常处理器 
    GlobalExceptionHandler，使用 @RestControllerAdvice。捕
    获 BizException 返回对应错误码，捕获 
    MethodArgumentNotValidException 返回参数校验错误，兜底捕
    获 Exception 返回系统内部错误。所有异常响应必须使用 Result.
    fail() 和 ErrorCode 枚举。
    ```
    任务四：MyBatis-Plus 配置
    ```
    在 xxx 中创建 MyBatis-Plus 配置类。包含：分页插
    件、自动填充（createTime、updateTime）、逻辑删除配置。
    ```

    任务五：Redis 配置
    ```
    在 xxx 中创建 Redis 配置类。包含：RedisTemplate 
    序列化配置（key 用 String，value 用 JSON）、基础的 
    RedisUtil 工具类（get/set/delete/expire）。
    ```

3. 业务模块空壳（每个模块的 package 结构和启动验证）

    ```
    为 xxx、xxx 等业务模块创建标准的 package 结构。按照
    CLAUDE.md 代码组织规范，每个模块包含 
    controller/service/service-impl/mapper/entity/dto/
    config 目录。每个模块只创建 package 和
    一个空的占位类，不需要写业务代码。
    ```

    启动类
    ```
    在 xxx 中创建 Spring Boot 启动类 HifyApplication，以及 
    application.yml 配置文件。配置项包括：数据库连接、Redis 连
    接、MyBatis-Plus 配置、服务端口 8080。
    ```

4. 验收，启动项目，确认一切正常

- 第一优先级：**结构性问题**。模块依赖关系对不对？pom 里的依赖声明和
 CLAUDE.md 里定义的架构一致吗？package 路径对不对？这些如果错
 了，后面所有东西都建在错误的地基上。

- 第二优先级：**公共模块的核心代码**。Result 类的字段和方法对不对？全
局异常处理器的捕获优先级对不对？MyBatis-Plus 的自动填充逻辑对不
对？这些代码所有业务模块都会依赖，错了影响范围最大。

- 第三优先级：**配置文件**。application.yml 里的配置项对不对？有没有遗漏？有没有硬编码不该硬编码的东西？影响范围相对小，后面随时可以调整。最后才看：业务模块空壳。只是 package 结构和占位类，几乎没什么可出错的，扫一眼确认结构一致就行。

#### 前端工程

```
初始化 xxx 前端项目 xxx-web。Vue 3 + TypeScript + Vite + 
Element Plus。目录结构按 CLAUDE.md 中定义的前端结构来。Vite 开
发服务器配置代理：/api 请求转发到 localhost:8080。
```

#### 基础组件

