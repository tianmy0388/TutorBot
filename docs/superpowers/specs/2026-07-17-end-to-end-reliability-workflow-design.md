# TutorBot 端到端可靠性与学习闭环修复设计

**日期：** 2026-07-17  
**状态：** 已确认，待实施计划  
**适用范围：** 本地单机 TutorBot，Python 运行环境为 Conda `tutor`

## 1. 背景与目标

当前系统已经具备会话、异步任务、资源包、画像、知识图谱、Manim、代码执行和联网工具等模块，但这些模块没有共享一致的身份、生命周期和编排契约。用户可见问题包括：

- 刷新或重启后会话与资源无法恢复，历史会话聚合返回 403。
- Matplotlib 虽然实际生成了图片，页面仍显示 Agg/字体缓存警告，图片预览尺寸和缩放体验不足。
- 练习题支持生成 `code` 类型，却没有代码编辑、上传、执行和提交入口。
- Manim 代码引用不存在的 SVG 文件；渲染错误只保存 traceback 开头，主页面永久显示“渲染中”。
- 资源已经持久化，主任务、聊天顶栏和任务队列仍可能保持运行状态。
- 画像没有形成有效知识数据，学习路径能力仍是占位实现。
- 已存在多个路由入口和前端任务状态源，异步提交绕过意图路由。
- Web Search 只有全局配置，没有每次对话可控的开关，也没有能力层真实调用闭环。

本设计的目标是收敛这些分叉，使身份、会话、任务、资源、画像和学习路径形成可恢复、可观测、可测试的单机学习闭环，同时保留后续扩展为多用户或分布式执行的边界。

## 2. 已验证的根因

### 2.1 身份与会话所有权冲突

运行配置为 `TUTOR_MULTI_USER_ENABLED=false`，前端却在浏览器 localStorage 中不断生成随机 `u_*` 身份，后端会话接口又无条件执行所有权校验。

数据库证据：

- `backend/data/profiles.db` 中已有 34 个用户身份，近期画像均为 `version=1` 且知识分数为空。
- 会话 `sess_ebb5a8f5dfdb` 的 Conversation 所有者为 `u_664b09a5103745d6`。
- 同一会话下后续 Job 和 ResourcePackage 又分别属于多个其他 `u_*` 身份。
- 聚合接口以当前浏览器身份查询旧 Conversation 时返回 403；即使绕过 403，也会因 Job/Resource 的用户过滤而丢失同会话中的后续结果。

### 2.2 数据目录与 artifact 地址不稳定

`TUTOR_DATA_DIR=./data` 被当前设置代码错误地锚定到 `backend/`，而文档和代码注释声称它位于仓库根目录。仓库中同时存在 `data/` 与 `backend/data/`。

部分历史 artifact 保存了绝对路径，例如旧仓库名 `E:\github\Tutor\backend\data\...`；仓库移动或重命名后文件接口无法再解析这些路径。

### 2.3 任务终态由多个组件竞争决定

资源 Job `7ed0acd3b0c043899e7a5fda042a6b2d` 已持久化资源包并写入 `result`、`done` 事件，数据库状态仍为 `running`，且没有 `job_terminal`。

Capability 自行发送 `done` 并关闭 StreamBus，JobRunner 同时运行 watchdog、消费流并负责终态，视频又以 fire-and-forget 任务继续使用已经关闭的 StreamBus。生命周期所有权不唯一，使“能力已完成”“事件流已关闭”“数据库已终态”成为三个不同事实。

前端也有双状态源：聊天主体主要读取 `jobsById`，页面顶栏仍读取旧 `activeTurn.phase`；`completeActiveTurn` 将 phase 设为 `success`，而顶栏以 `phase !== idle` 判断处理中，因此成功后也可能继续显示处理中。

### 2.4 路由和学习闭环未接通

代码中同时存在 `MainOrchestrator.route()` 与 `services.intent.router.classify()`，但异步 JobRunner 在未指定 capability 时直接默认 `resource_generation`。普通问答、画像构建和路径规划不会按统一策略触发。

`PathPlanningCapability` 仍是占位实现，不返回结构化 `result`。LearningEventStore 已存在，但当前数据库只有 1 条学习事件，练习交互没有写入学习事件；因此画像和评估没有足够输入。

### 2.5 资源执行与渲染契约不完整

Matplotlib 运行实际成功并生成 `figure_1.png`，但：

- 每个运行使用全新 `MPLCONFIGDIR`，导致字体缓存反复构建。
- `plt.show()` 在 Agg 后端产生非交互警告。
- 旧 artifact 使用失效的绝对路径。
- 前端仅将图片放入卡片网格，缺少内置缩放、拖拽与原图查看体验。

给定 Manim 代码连续四次渲染失败的真实尾部错误是缺少 `person_silhouette.svg`；代码还引用 `cup.svg`、`brain_sketch.svg`、`car.svg` 和 `real_track.svg`。StaticGuard 只检查语法和危险调用，没有验证外部资产。错误字段仅保留 traceback 前 500 字符，恰好丢弃真正原因；重试循环也没有在代码未变化时提前停止。

### 2.6 测试覆盖与静态检查不一致

当前前端 57 个 Vitest 测试通过，但测试 stderr 含未 mock 的会话写入请求。`tsc --noEmit` 当前有 17 个错误，包含 ClientJob 类型漂移、资源事件枚举不一致、可空值和 API 类型不匹配。现有测试通过不能作为可发布标准。

## 3. 方案选择

### 3.1 不采用：继续叠加局部补丁

仅绕过 403、强制清除 spinner 或为代码题增加 textarea，无法消除随机身份、双状态源和任务生命周期竞争，问题会在刷新、重启或后台渲染时再次出现。

### 3.2 采用：收敛式修复

统一身份策略、数据位置、意图路由、任务终态和前端事实源；用持久化子任务承载视频、画像与路径等后续工作；让练习尝试进入学习事件。这一方案能在不引入外部队列基础设施的前提下形成完整闭环。

### 3.3 暂不采用：全面分布式重构

Celery/Redis、容器沙箱和全新 Agent 框架适合未来多用户部署，但会显著增加当前本地单机项目的迁移和运维成本。本轮保留可替换边界，不引入这些依赖。

## 4. 目标架构

### 4.1 分层职责

```text
Chat/API
  -> IdentityPolicy
  -> IntentRouter
  -> WorkflowOrchestrator
       -> Primary Capability
       -> Durable Follow-up Tasks
  -> JobRunner / JobStore
  -> Conversation Aggregate
  -> Frontend jobsById
```

- **IdentityPolicy**：根据服务端模式解析规范用户身份，所有入口共享。
- **IntentRouter**：唯一意图分类入口，显式 capability 优先。
- **WorkflowOrchestrator**：定义主任务及后续画像、路径、视频任务的依赖。
- **Capability**：完成单项能力并返回结构化结果，不关闭事件流，不写 Job 终态。
- **Agent**：执行专业生成或判断，返回结构化产物，不拥有工作流生命周期。
- **JobRunner**：持久化执行、事件序号、取消、超时和唯一终态。
- **Frontend Job Store**：聊天、顶栏和任务队列的唯一运行状态源。

### 4.2 主任务与后续任务

每次用户消息只创建一个用户可见的主任务。主任务在答案或资源包完成后立即终态。以下工作以持久化子任务执行：

- 视频渲染；
- 满足触发条件的画像增量更新；
- 资源生成或画像变化后的路径重算；
- 需要较长时间的事实核验或资源后处理。

子任务包含 `parent_job_id`、`task_kind`、状态、尝试次数、时间戳和错误摘要。子任务失败不重新打开已完成的主任务。

## 5. 身份、迁移与持久化设计

### 5.1 单用户规范身份

单用户模式统一使用服务端规范身份 `local-user`。前端不再创建或信任随机 `u_*`。REST、WebSocket、Job、Conversation、Resource、Profile 和 LearningEvent 都先通过 IdentityPolicy。

多用户模式在本轮不扩展认证，但保留接口；若启用多用户却没有受验证身份，服务应拒绝启动或返回明确配置错误，而不是信任 query/path 中的任意用户 ID。

### 5.2 可回滚历史迁移

迁移在写入前完成以下步骤：

1. 检查后端未运行，记录源目录和数据库清单。
2. 为仓库根 `data/` 与 `backend/data/` 创建带时间戳的完整备份。
3. 以 `backend/data/` 的近期活动数据为主，合并根 `data/` 中独有记录。
4. 将 Conversation、Message、Job、ResourcePackage、Resource、Profile 和 LearningEvent 的历史 `u_*` 统一重写为 `local-user`。
5. 保留原用户 ID 到迁移元数据或审计日志，不丢弃原始来源。
6. 对同一 session 下不同历史用户的 Job/Resource 重新归属并验证聚合结果。
7. 将有效数据落到仓库根 `data/`，生成迁移报告，并支持重复运行时安全跳过。

迁移工具必须支持 `--dry-run`，报告记录数量、冲突、孤立资源和无法重定位的 artifact；任何验证失败都不得删除源数据。

### 5.3 数据目录与 artifact key

所有 Store 从同一个绝对 `settings.data_dir` 读取仓库根 `data/`。设置代码以仓库根为锚，不依赖当前工作目录。

新 artifact manifest 保存：

```json
{
  "name": "figure_1.png",
  "kind": "png",
  "artifact_key": "code_runs/<run_id>/figure_1.png"
}
```

文件接口只在 `data_dir` 下解析 artifact key。迁移器将现有绝对路径转换为相对 key；对旧仓库名和 `backend/data` 路径按 `data/` 后缀重定位。找不到文件时返回 typed `ARTIFACT_GONE`，不会使会话聚合失败。

### 5.4 原子会话聚合

聚合响应包含 Conversation、Messages、Jobs、ResourcePackage 摘要、当前 Profile 和 Path 引用。服务端按规范身份和 session 一次性查询，前端不再串行请求多个所有权可能不一致的接口。

完整资源详情按需加载；单个资源或附件损坏只产生资源级错误占位。

## 6. Job 与事件生命周期

### 6.1 唯一终态所有者

JobRunner 是 Job 状态的唯一写入者。Capability 返回 `CapabilityResult` 或抛出受分类异常；不得调用 `stream.done()` 或写 JobStore。

JobRunner 执行顺序：

1. 持久化 `pending`。
2. 切换 `running` 并记录 `started_at`。
3. 注册事件消费者后启动 Capability，避免启动期事件丢失。
4. 接收结构化结果或异常。
5. 在同一个受保护的终态事务中写入兼容事件、`job_terminal`、结果、最终状态、`finished_at`、错误日志引用和持久化 `terminal_event_id`。
6. 事务成功后向订阅者广播终态并关闭订阅。

每个 Job 恰好一个 `job_terminal`。幂等性由独立的 `terminal_event_id` 判定，不依赖会被截断的重放缓冲区。取消与正常完成共用该终态事务；取消 API 只能在终态已持久化后返回成功。`done` 作为旧协议输入只允许在兼容层转换，不再关闭核心事件流或决定成功。

订阅者先注册实时队列，再重读持久化事件并按 `event_id` 去重，避免在“回放完成”与“开始实时监听”之间丢失终态。Capability 对外事件只保留 `progress | stage_start | stage_end | resource | sources`，其他内部追踪类型由 JobRunner 投影为 `progress`。

### 6.2 状态与错误

主任务状态：`pending | running | succeeded | partial | failed | cancelled | interrupted`。

子任务状态：`pending | running | succeeded | failed | cancelled | interrupted`。

错误使用稳定 code，例如：

- `JOB_TIMEOUT`
- `CAPABILITY_FAILED`
- `PROCESS_RESTARTED`
- `VIDEO_ASSET_MISSING`
- `VIDEO_RENDER_FAILED`
- `CODE_RUNTIME_ERROR`
- `CODE_DEPENDENCY_MISSING`
- `ARTIFACT_GONE`
- `WEB_SEARCH_UNAVAILABLE`

公开 Job 记录、事件、API 和应用日志只保存稳定 code、通用摘要和受保护的错误产物引用；原始异常消息、provider 响应和 traceback 只写入 UTF-8 `error.log` 产物。

后端启动时将不能恢复的运行中任务标记为 `interrupted`，写入唯一终态；具备安全重试契约的视频子任务可重新排队。

### 6.3 前端唯一事实源

删除或彻底隔离 `activeTurn` 的运行状态职责。以下 UI 均从 `jobsById` 和子任务状态派生：

- 聊天中的进度卡；
- 页面顶栏“处理中”；
- 右上角任务队列；
- 资源渲染状态；
- 刷新后的任务回放。

终态到达后同时清除活动阶段、spinner 和运行计数。重复终态或乱序事件必须幂等。

## 7. 意图路由与学习闭环

### 7.1 统一路由

只保留 `services.intent.router.classify()` 作为分类实现。优先级为：

1. 用户显式选择能力；
2. 评估；
3. 画像；
4. 路径规划；
5. 明确资源请求；
6. 普通即时答疑。

Job 提交时先分类，再持久化实际 capability、topic、显式资源类型和搜索/RAG 选择。MainOrchestrator 和 JobRunner 不再维护另一份关键词规则。

### 7.2 画像更新策略

每次有效用户交互记录轻量 LearningEvent。画像更新按以下条件之一触发：

- 首次会话或画像为空；
- 用户明确提供背景、目标、偏好或自我评价；
- 完成练习并产生分数；
- 距离上次画像更新超过配置间隔且积累了新事件。

画像子任务读取一段稳定事件窗口，更新知识掌握、认知偏好、节奏、动机和薄弱点；不得只追加资源历史而保持知识分数为空。

### 7.3 真实路径规划

PathPlanningCapability 使用 KnowledgeGraphService 和持久化画像执行定位、已掌握节点剪枝、依赖拓扑排序、资源匹配和下一步推荐，并返回结构化 PlannedPath。

触发条件：

- 用户明确请求路径；
- 资源包成功生成；
- 练习或画像变化使掌握度跨过阈值。

自动路径重算作为子任务，不阻塞主对话终态。

### 7.4 多 Agent 边界

保留内容专家、教学设计、练习、代码、视频、质量审核和安全审核等专业 Agent，但由显式 DAG 定义依赖：

```text
intent
  -> profile snapshot
  -> source content -> pedagogy
  -> [mindmap, exercise, code, video-code, reading]
  -> successful artifacts only -> [quality, safety]
  -> package persistence
  -> [video render child, profile child, path child]
```

并发只用于无共享可变状态的分支。每个节点有超时、输入输出 schema 和降级策略；失败产物不进入质量审核，审核失败不伪装为成功资源。

## 8. Matplotlib 资源设计

### 8.1 执行环境

后端启动和健康接口记录并展示：

- `sys.executable`
- `execution_python`
- Python、Matplotlib、NumPy、Manim、ffmpeg 版本和位置
- `data_dir`

本地开发必须通过 Conda `tutor` 解释器启动；配置的执行解释器不存在或关键依赖缺失时应快速失败并给出可执行提示。

### 8.2 无交互绘图捕获

代码 wrapper 在用户代码前配置 Agg、UTF-8 和 CJK 字体，并代理 `plt.show()`。`show()` 不尝试打开 GUI；执行完成后按原始 figure size 和 DPI 保存所有未关闭 figure。

字体缓存采用预热的共享只读模板：启动或首次使用时在受锁目录生成一次，每个运行目录复制或引用稳定缓存，避免每次输出 “Matplotlib is building the font cache”。

stderr 中过滤由 wrapper 已正确处理的 Agg 非交互警告，但保留用户代码真实 warning 和 exception。

### 8.3 图片浏览器

ArtifactPreview 的缩略图保持原始纵横比。点击后打开应用内全屏 lightbox，支持：

- 鼠标滚轮和按钮缩放；
- 拖拽平移；
- 适应窗口；
- 1:1；
- 重置；
- 打开原图；
- 下载。

键盘支持 Escape 关闭、`+/-` 缩放和 `0` 重置。移动端支持双指缩放或至少提供明确缩放按钮。

## 9. 代码练习设计

### 9.1 题目 schema

`ExerciseQuestion(type="code")` 扩展 `code_spec`：

```json
{
  "language": "python",
  "filename": "solution.py",
  "starter_code": "def solve(...):\n    pass",
  "entrypoint": "solve",
  "public_tests": [],
  "hidden_tests": [],
  "time_limit_seconds": 5,
  "max_file_bytes": 131072
}
```

生成器必须输出可解析测试；质量门检查测试可运行、参考答案通过、starter code 未直接泄露答案。测试定义不能依赖网络或外部文件。

### 9.2 提交体验

Python 代码题提供：

- 多行等宽代码编辑区；
- 恢复起始代码；
- 上传单个 `.py` 文件；
- 运行公开测试；
- 提交全部测试；
- stdout/stderr、逐项结果、耗时和得分。

非 Python 代码题首期支持编辑和文件保存，明确标记“暂不自动运行”。

### 9.3 服务端执行与持久化

新增代码题尝试 API，以规范身份、session、resource 和 question 定位题目。服务端忽略客户端提供的参考答案或隐藏测试，只使用持久化题目定义。

执行复用 CodeSandbox 的隔离目录、超时、AST 危险调用检查、禁止网络和 artifact 限制。Windows 环境下不能提供强安全边界，因此 UI 和文档明确标注为本地可信学习代码执行器；未来部署需替换为容器沙箱。

每次运行或提交写入 Attempt 记录和 LearningEvent；提交事件包含得分、通过数、错误类别、概念和耗时，但日志不泄露隐藏测试答案。

## 10. Manim 生成与视频子任务

### 10.1 生成约束与静态检查

Manim 提示明确禁止引用未随请求提供的外部 SVG、位图、字体、音频或数据文件。优先使用 Circle、Rectangle、Line、Arc、Text、VGroup 等 Manim 原生对象。

StaticGuard 使用 AST 检测：

- `SVGMobject`、`ImageMobject` 等外部资产调用；
- 字符串形式的文件路径；
- 未声明的字体和数据文件；
- 危险调用；
- Scene/construct、动画数量和代码规模。

若资产未提供，返回 `VIDEO_ASSET_MISSING` 并列出缺失文件；自动修复提示优先用原生图形替换。

### 10.2 有效重试

重试历史保存 code hash、错误分类和 patch 摘要。若新代码 hash 与上一轮相同，立即停止，不重复执行相同渲染。语法、资产和运行时错误采用不同修复提示。

错误摘要保存分类、最后 traceback、完整日志 artifact key 和 renderer 命令；页面展示真正异常尾部而不是固定截取开头 500 字符。

### 10.3 持久化视频任务

视频代码资源先以 `pending` 持久化，然后创建 `video_render` 子任务。子任务执行：

```text
pending -> validating -> rendering -> ready | failed | interrupted
```

每次状态变化更新 Resource、子任务和事件。前端通过子任务订阅或聚合刷新获取状态；不得向已关闭的主任务 StreamBus 发送事件。

成功结果包含可播放 URL、相对 MP4 key、时长、分辨率和 renderer provenance；失败结果包含稳定错误 code、可读摘要、重试入口和日志链接。

## 11. 联网搜索设计

### 11.1 前端交互

聊天输入区增加独立“联网搜索”开关，与 RAG 范围选择分开。默认关闭，同一会话记忆选择；新建会话恢复关闭。

每次发送把当前值固化为 `web_search_requested`，会话历史可显示当时是否启用联网。

### 11.2 后端执行门

只有同时满足以下条件才允许调用 WebSearchTool：

1. Job 的 `web_search_requested=true`；
2. 服务端 `web_search_enabled=true`；
3. provider 配置和连通性有效；
4. 当前 capability/stage 允许搜索。

关闭时必须做到零搜索调用。开启时：

- Tutoring 可检索时效性信息；
- ResourceGeneration 主要用于拓展阅读和事实核验；
- 搜索结果的标题、URL、摘要、provider 和时间随 Job/Resource 持久化。

搜索失败返回 `WEB_SEARCH_UNAVAILABLE` 的阶段级降级提示，继续使用模型知识或 RAG，不使主任务失败或卡住。

## 12. 错误反馈与恢复体验

- 主任务和子任务显示状态、耗时、错误码、错误摘要和可用的重试操作。
- 视频失败立即替换“渲染中”；资源卡不允许无限 pending。
- artifact 丢失显示资源级占位和重新生成入口。
- 会话聚合发生历史迁移或部分损坏时显示非阻塞恢复通知。
- 403 只在真实多用户越权场景出现；单用户历史迁移后不得用浏览器随机身份触发。
- 后端不得将 API key、隐藏测试、模型私有推理链或完整用户代码写入普通日志。

## 13. 测试与验收

### 13.1 自动化测试

必须以失败回归测试先行，覆盖：

- 单用户规范身份和跨旧身份迁移；
- `sess_ebb5a8f5dfdb` 聚合恢复；
- data_dir cwd 独立性和 artifact key 重定位；
- 主任务和子任务恰好一个终态；
- 主任务完成不受后台视频任务影响；
- 前端聊天、顶栏和任务队列同步停止；
- Matplotlib show 捕获、字体缓存和原图尺寸；
- 图片 lightbox 交互；
- Python 代码题编辑、上传、公开测试、隐藏测试和学习事件；
- Manim 缺失资产预检、code hash 无变化提前停止、错误尾部；
- 画像产生非空知识/偏好信息；
- PathPlanning 返回结构化路径；
- 联网关闭零调用、联网开启持久化来源、搜索失败降级；
- 会话刷新、服务重启和 interrupted 恢复。

发布门槛：

```powershell
npm --prefix frontend test
npm --prefix frontend run type-check
npm --prefix frontend run build
$env:PYTHONPATH = "backend"
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests -q
```

测试输出不得包含未解释的 error/warning；现有前端测试中的真实 fetch stderr 必须通过正确 mock 消除。

### 13.2 浏览器验收

Browser 插件不可用时使用项目 Playwright 或临时 Playwright 脚本，验证桌面和移动端：

1. 打开历史会话并恢复 `sess_ebb5a8f5dfdb`。
2. 提交普通问答，确认走 Tutoring 且终态一致。
3. 提交资源请求，确认主任务完成后后台视频状态独立变化。
4. 刷新页面和重启服务，确认消息、资源、任务、画像和路径仍存在。
5. 打开 Matplotlib 图片，执行缩放、拖拽、1:1 和下载。
6. 编辑或上传 Python 解答，运行并提交，检查得分与画像/路径更新。
7. 渲染给定世界模型 Manim 代码，确认预检列出缺失 SVG，或修复后产生可播放 MP4。
8. 分别在联网关闭和开启状态提交查询，检查搜索调用和来源展示。

## 14. 实施分解与顺序

本设计按以下可独立验收的子项目实施：

1. **身份与数据迁移**：备份、规范身份、data_dir、会话聚合和 artifact key。
2. **任务状态与编排**：唯一终态、子任务、统一路由、前端唯一事实源。
3. **学习闭环**：LearningEvent、画像触发、真实路径规划。
4. **代码与图片资源**：Matplotlib 捕获、lightbox、Python 代码题提交与评分。
5. **Manim 视频**：资产预检、有效重试、持久化渲染子任务和错误展示。
6. **联网搜索**：会话开关、能力调用门、来源持久化和降级。
7. **全链路稳定性**：类型错误、测试污染、构建、重启和浏览器验收。

每个子项目采用测试驱动方式完成并独立提交；后续子项目只能依赖前序已定义的稳定接口。

## 15. 明确不做

- 不在本轮引入 Redis、Celery、Kafka 或容器编排。
- 不宣称 Windows 本地 subprocess 是不可信代码的强安全沙箱。
- 不为非 Python 语言实现自动判题运行时。
- 不暴露模型私有推理链。
- 不用 localStorage 作为历史会话、任务或资源的唯一事实源。
- 不通过延长超时、隐藏 spinner 或忽略 403 来掩盖状态和身份错误。

## 16. 完成标准

本轮完成必须同时满足：

- 所有历史数据已备份并迁移，`sess_ebb5a8f5dfdb` 可完整恢复。
- 单用户模式不再生成随机用户身份或返回历史会话 403。
- 主任务、子任务、聊天顶栏和任务队列状态一致且可跨重启恢复。
- Matplotlib 图片可生成、正确加载并在应用内浏览缩放。
- Python 代码题可提交、运行、评分并写入学习事件。
- 给定 Manim 代码得到明确缺失资产诊断，修复后可播放或以 typed failure 结束。
- 画像包含有效学习信息，路径规划返回真实 PlannedPath。
- 联网搜索可按会话控制，关闭时零调用，开启时有来源。
- 全量后端测试、前端测试、TypeScript 检查、生产构建和浏览器验收通过且输出干净。
