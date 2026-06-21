# Tutor 会话、任务、资源执行与 RAG 可靠性改造设计

> 日期：2026-06-21  
> 状态：已确认  
> 范围：会话记忆、任务反馈、Manim 动画、代码运行、练习交互、持久化、课程—知识库关系与向量 RAG。

## 1. 背景与目标

当前系统已经存在会话、任务、资源包、知识库和渲染服务的部分实现，但各模块之间缺少稳定的数据归属和恢复边界。典型表现包括：刷新后对话消失、历史对话不能恢复右侧资源、任务长期停留在“正在调用 Agent”、Manim 只产生占位脚本、代码运行误报缺少 matplotlib、知识库和资源在不同启动目录下不可见，以及上传文档的向量从未真正进入答疑检索链路。

本次改造采用“领域模型收敛”方案：以持久化会话为交互主线，以 Job/Event 为异步状态唯一事实源，以 Course/KnowledgeBase 为 RAG 范围边界，以 Artifact 为资源执行产物。改造必须保留现有功能并提供旧数据迁移，不采用仅重置 UI 状态或延长超时的临时补丁。

## 2. 已确认的代码根因

### 2.1 会话与资源恢复

- `frontend/app/page.tsx` 维护局部 `sessionId`，Zustand 也维护 `sessionId`，两者会分叉。
- 新建对话先 `setSessionId`，随后 `resetSession` 又生成另一个 ID。
- `frontend/hooks/useJobQueue.ts` 提交任务时没有传入当前 `session_id`。
- `loadConversationIntoStore` 只恢复消息，主动清空 jobs 和 resource package，因此右侧资源无法随历史会话恢复。

### 2.2 任务反馈与死循环

- `ChatMessages` 用 `jobsById` 判断运行态，却继续从旧 `activeTurn` 读取 thinking、events 和 error。
- 顶部处理中提示仍读取 `activeTurn.phase`。
- 终态依赖 WebSocket 事件；断线或事件缺失时没有 REST 兜底收敛。
- 视频代码生成当前只是并行资源 Agent，并未执行 Manim 渲染；因此它不会正常完成视频产物，也不应被误判为有效视频。

### 2.3 动画与代码执行

- Manim prompt 面向 Community Edition，环境中也存在稳定的 `manim`，应继续使用 Manim CE。
- `ManimVideoAgent` 在 LLM 输出无效时生成标题占位动画，并仍以 pending 资源返回。
- `ManimRenderService` 未接入资源生成主链路。
- `CodeSandboxAgent` 使用 `sys.executable`，即启动后端的 Python。根目录脚本只调用普通 `python`，不能保证是 Conda `tutor` 环境。
- 已验证 `tutor` 解释器为 `E:\Anaconda3\anaconda\envs\tutor\python.exe`，其中 matplotlib 3.11.0 可用。

### 2.4 练习题

- 后端可能生成 `fill_blank`，但 `ExerciseViewer` 只为 `short_answer` 渲染输入框。
- 前后端缺少共享、版本化的题型契约。

### 2.5 持久化与 RAG

- KnowledgeBaseStore 仍是进程内字典；源文件和 chunk 虽落盘，库和文档元数据重启后丢失。
- `Settings.data_dir` 默认是相对路径 `./data`。从项目根目录或 `backend` 启动会产生不同数据库。现场已经同时存在根目录 `data` 和 `backend/data`。
- Zhipu 未注册为 Embedding provider；异常被 `_embed` 捕获后返回空向量。
- chunk 索引在文档的 embedding model 状态更新前写入，索引 manifest 可能记录空模型。
- `TutorService` 不读取上传文档的 chunk/vector 索引，仅扫描预置 Markdown 做关键词重叠，因此当前回答不是上传知识库驱动的向量 RAG。
- 左侧课程来自固定知识图谱文件，前端默认值硬编码为 `ai_introduction`。

## 3. 核心领域模型

```text
Course 1 ── N KnowledgeBase
                 course_id nullable

Conversation 1 ── N Message
Conversation 1 ── N Job
Conversation 1 ── N ResourcePackage

Job 1 ── N JobEvent
Job 1 ── N Artifact

KnowledgeBase 1 ── N Document
Document 1 ── N Chunk
Chunk 1 ── 1 Embedding
```

约束如下：

- 一个知识库最多属于一门课程，也可以保持独立；不允许同时属于多门课程。
- 删除课程默认将其知识库的 `course_id` 置空，不级联删除文档。
- 每个 Message、Job、ResourcePackage 必须带稳定 `session_id`。
- 每个资源包记录 `job_id`、`session_id`、可选 `course_id` 和本轮 `retrieval_scope`。
- 文件类产物存磁盘，关系和状态存数据库；数据库记录持有相对 artifact path，不持有依赖启动目录的绝对临时路径。

## 4. 会话生命周期与历史恢复

前端删除页面局部 session 状态，Zustand 的 `sessionId` 成为唯一来源。用户打开空白会话时可先使用未持久化 draft；第一次发送问题时，前端调用 create conversation，并以首条问题前 60 个字符作为标题，再使用返回的 `session_id` 提交任务。这样不会产生大量从未使用的空会话。

任务提交顺序固定为：

1. 创建或确认 Conversation。
2. 持久化用户 Message。
3. 使用同一 `session_id` 提交 Job。
4. Job 完成时由后端事务性写入助手 Message 和 ResourcePackage 关联，前端不再负责终态消息的唯一持久化。

历史切换调用 conversation detail 聚合接口，并行返回 messages、jobs、resource package summaries、最近选择资源和 retrieval scope。前端使用一次原子 store 更新替换当前会话视图，避免先清空再逐项加载的闪烁和串会话。后台运行任务不会因切换会话被取消；返回该会话时通过 REST snapshot 和 WebSocket replay 恢复。

## 5. Job/Event 状态和可见工作过程

异步任务只使用以下有限状态：

```text
pending → running → succeeded | partial | failed | cancelled
```

`jobsById[job_id].events` 是聊天运行卡片的唯一数据源。旧 `activeTurn` 不再控制聊天、顶部状态或终态。每个事件在后端广播和落盘边界强制补齐 `job_id`、`session_id`、单调 `seq`、`timestamp` 和 `event_id`。

可见过程包括需求识别、RAG 检索、资源规划、Agent 开始/结束、工具调用、质量检查、持久化和子任务进度。界面不展示模型私有思维链，只展示可审计的工作摘要、依据和阶段结果。

每个任务提供心跳、阶段超时、总超时和取消。WebSocket 断线后使用指数退避重连；超过重连窗口后通过 REST 查询 Job snapshot。只要持久化状态已经终止，前端必须立即收敛，不能继续显示 running。

## 6. Manim CE 动画执行设计

保留 Manim Community Edition，要求 LLM 生成 `from manim import *` 和 `class MainScene(Scene)`。动画链路为：

```text
教学目标 → 分镜 → Manim 代码 → 静态检查 → 低清试渲染
→ 基于真实 stderr 修复（最多两次）→ 正式渲染 → 发布 Artifact
```

代码质量约束：

- 至少包含概念图形、状态变化、关键标签和分步教学动作。
- 不能仅展示标题、字幕或“生成中”信息。
- 不能把 fallback 占位 Scene 作为成功资源。
- 静态检查覆盖 AST、导入白名单、Scene 类、动画动作数量、危险调用和最大代码长度。
- 试渲染成功后才允许生成正式视频资源。

视频渲染作为 ResourceGeneration Job 的子 Job。父任务可先返回其他资源并标记 partial/pending artifact；视频子任务独立处于 generating_code、validating、rendering、publishing 和终态。渲染失败或超时不会阻塞父任务，也不会被包装成有效视频。持久化源码、分镜、Manim 版本、命令参数、日志摘要和 MP4 artifact。

## 7. 代码资源执行环境

增加明确的 `execution_python` 配置。默认使用后端 `sys.executable`，开发启动脚本在 Windows 上明确通过 `conda run -n tutor` 启动后端；设置页和健康接口显示实际 Python 路径、版本、matplotlib 和 manim 状态。

执行器使用配置后的解释器和隔离临时目录，设置 `MPLBACKEND=Agg` 与独立 `MPLCONFIGDIR`。运行结果记录 runtime、dependency versions、stdout、stderr、exit code、duration 和 artifacts。生成的 PNG/SVG 等图像被收集为代码资源产物。环境缺失返回 `RUNTIME_DEPENDENCY_MISSING`，生成代码错误返回 `CODE_EXECUTION_FAILED`，两者不得混淆。

## 8. 练习题契约

前后端统一题型枚举：

```text
single_choice | multiple_choice | true_false | fill_blank | short_answer
```

`fill_blank` 支持单空和多空，答案可包含多个可接受值；比较规则支持去除首尾空格、大小写归一和可选数值容差。旧资源中被当作 short answer 的填空题在读取时规范化，无需重新生成。提交后逐空展示正确性和解析，作答结果写为 LearningEvent，供评估和画像更新。

## 9. 持久化根目录与迁移

应用使用唯一绝对 `TUTOR_DATA_DIR`。推荐布局：

```text
data/
  tutor.db
  knowledge_bases/
  resources/
  videos/
  code_runs/
  temp/
```

元数据统一进入带版本迁移的 SQLite。KnowledgeBaseStore 从内存实现替换为持久化 repository。服务启动日志和 health API 必须显示实际 data dir 与 DB path。

迁移工具在写入前备份现有数据库，扫描根目录 `data` 和 `backend/data`，按稳定业务 ID 合并 conversations、jobs、resources、knowledge bases 和 documents，并以 checksum 去重文档。冲突记录写入 migration report；原目录不自动删除。

## 10. Course 与 KnowledgeBase

Course 是可持久化实体，包含 id、name、description、可选 knowledge_graph_id 和时间戳。KnowledgeBase 增加 nullable `course_id`。API 支持课程 CRUD、在课程下创建知识库、移入、移出和跨课程移动。

预置 AI 导论通过 seed migration 创建为普通 Course/KnowledgeBase 数据，不再在前端 store 中硬编码。删除课程时知识库默认移出；删除知识库需要独立确认并处理文档和索引。

## 11. Zhipu Embedding 3 与向量检索

Embedding factory 注册 `zhipu` provider，并配置模型、base URL、API key 和可选 dimensions。连接测试必须实际请求一个短文本，验证向量非空、数量匹配、维度稳定，而不是只检查 HTTP 状态。

摄取过程保存 provider、model、dimension、index version 和文本 checksum。配置改变或维度不一致时标记 `reindex_required`，禁止混用向量。Embedding 失败将文档标为 failed；关键词 fallback 只能作为明确标识的降级模式。

RetrievalService 对问题使用与索引相同的模型生成 query vector，只在请求的 retrieval scope 内加载 ready chunks，执行余弦相似度 Top-K 和阈值过滤。结果携带 knowledge base、document、页码/段落 anchor、score 和 chunk id。无足够证据时明确返回 no evidence，不让 LLM假装来自知识库。

## 12. 输入框 RAG 范围

输入框上方提供三种状态：不使用知识库、选择课程、选择独立或课程内知识库。选择课程时检索该课程所有 ready 知识库；选择知识库时只检索该库。`retrieval_scope` 随 Job 和 Conversation 持久化，历史恢复时同步恢复。

后端必须校验 scope 是否存在、是否属于请求用户、是否 ready、是否需要重建索引。失效范围返回结构化错误，不静默切换到普通 LLM。

## 13. 错误模型与可观测性

错误统一包含 code、stage、message、retryable、request_id 和可选 job_id/artifact_id。日志不得包含 API key、完整文档正文或模型私有推理。Embedding、Manim 和代码执行环境在启动时预检；设置页展示实际运行状态。

资源子任务失败使父任务进入 partial，不阻塞其余资源。持久化失败必须反馈“本地已显示但未保存”，并提供重试，不能静默吞掉。

## 14. 实施拆分

### A. 会话与持久化基础

统一数据目录、数据库迁移、session 单一来源、对话自动创建和历史聚合恢复。

### B. 任务反馈与练习交互

统一 Job/Event 渲染、终态兜底、断线恢复、超时取消和 fill_blank 作答。

### C. 内容执行链

接入 Manim CE 试渲染/正式渲染，固定代码运行解释器，收集 matplotlib 图像产物。

### D. 课程、知识库与 RAG

Course/KnowledgeBase 动态关系、Zhipu Embedding 3、索引生命周期、范围选择和带引用回答。

实施顺序固定为 A → B → C → D。每部分独立测试、代码审查和提交，禁止合并为一个超大变更。

## 15. 验收标准

- 刷新页面、重启前后端和切换历史会话后，消息、任务和右侧资源一致恢复。
- 第一条问题自动形成命名对话，不创建未使用的空历史。
- 所有 Job 最终进入明确终态；断开 WebSocket、Manim 超时或子资源失败均不能留下永久 spinner。
- thinking/progress 展示来自当前 Job events，包含实际 Agent 和阶段信息。
- fill_blank 单空、多空均可输入、提交、判分和记录学习事件。
- Manim CE 实际渲染一个非占位教学动画 MP4。
- 配置的 Python 实际导入 matplotlib 并产生可展示 PNG。
- 两次不同工作目录启动使用同一数据库；旧数据迁移后数量和关联正确。
- PDF 经 Zhipu mock/live embedding 形成向量，选定范围内的问题返回带 anchor 的引用。
- 课程知识库可移入、移出和跨课程移动，且始终最多属于一门课程。
- 不使用知识库时明确走普通 LLM；选择失效知识库时返回错误而非静默降级。

