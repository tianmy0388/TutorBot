# Tutor 稳定性、会话历史与资源预览修复计划

> 日期：2026-06-21  
> 范围：本计划只定义修复，不包含本次代码实现。  
> 推荐方案：统一任务状态源 + 持久化会话/知识库 + 异步文档摄取。

## 一、已确认根因

1. **知识库创建 422**：`frontend/lib/api.ts` 对所有请求强制写入 `Content-Type: application/json`，但创建知识库实际发送 `FormData`。FastAPI 无法解析表单字段，返回 422。
2. **PDF 上传 500 / ECONNRESET**：上传同样被错误标记为 JSON；同时后端在上传请求内同步执行 PDF 解析、切分和向量化，长请求经过 Next.js 代理时容易被重置。
3. **知识库 GET 请求风暴**：`refreshAll` 依赖 `detailsById`，自身又更新 `detailsById`，导致初始 `useEffect` 不断重新触发；定时轮询进一步叠加请求。
4. **聊天死循环与无思考输出**：新任务进度已进入 `jobsById.events`，但聊天视图仍读取旧的 `activeTurn` 缓冲区。`job_terminal` 调用 `completeActiveTurn` 后把 phase 留在 `success`，而 UI 以 `phase !== idle` 判断加载，因而永久显示“正在调用 Agent”。后端已经发送 `thinking` 事件，前端只是没有从正确状态源渲染。
5. **历史对话缺失**：页面会生成 `sessionId`，但任务提交没有稳定传入并持久化为可查询会话；消息仍主要保存在 Zustand 内存中，刷新或新会话后不可恢复。
6. **资源中心不可预览**：资源包弹窗只渲染元数据列表，列表项没有选中行为，也没有挂载项目中已有的 `ResourceDetail` 类型化预览器。

## 二、目标架构

- `ClientJob`/`jobsById` 是异步任务运行状态的唯一前端事实源；`activeTurn` 不再驱动任务进度 UI。
- `Conversation` 与 `Message` 在后端 SQLite 持久化，`session_id` 从新建会话开始贯穿计划、任务、事件和消息。
- 知识库元数据持久化；上传接口只负责校验与落盘并快速返回，摄取在后台执行，通过有限轮询获取状态。
- 资源中心统一复用已有 `ResourceDetail`，资源列表负责选择，详情区域负责渲染。

## 三、实施顺序

### 阶段 0：建立回归测试，锁定当前故障

**涉及文件**

- `frontend/lib/api.test.ts`（新增）
- `frontend/app/knowledge-bases/page.test.tsx`（新增）
- `frontend/components/chat/ChatMessages.test.tsx`（新增或扩展）
- `frontend/app/resources/page.test.tsx`（新增）
- `backend/tests/e2e/test_demo_scenarios.py`
- `backend/tests/services/knowledge_base/test_kb_service.py`

**先写失败测试**

- FormData 请求不得携带手写 `Content-Type`，JSON 请求必须携带 JSON 类型。
- 知识库页面首次加载只请求一次；一次状态变化不能递归触发刷新；同时最多一个轮询请求在途。
- 收到 `thinking → agent_end → job_terminal` 后，思考内容可见且加载态结束。
- 点击资源列表项后，右侧显示对应类型内容。
- 使用真实 multipart PDF 创建文档，接口快速返回，随后状态从 `uploaded/processing` 进入 `ready` 或带明确错误码的 `failed`。

### 阶段 1：修复请求协议与知识库轮询

**前端改动**

- 重构 `frontend/lib/api.ts::request`：
  - `body instanceof FormData` 时不设置 `Content-Type`，由浏览器生成 boundary；
  - JSON body 统一由 JSON helper 序列化并设置请求头；
  - `ApiError` 展示后端 `detail/code/request_id`，避免只有笼统 422/500。
- 知识库创建接口建议改为 JSON Pydantic 请求体；上传接口继续使用 multipart。
- 重构 `frontend/app/knowledge-bases/page.tsx`：
  - 将首次加载、手动刷新、工作状态轮询拆成独立函数；
  - 使用函数式 state/ref 读取缓存，移除 `detailsById` 对刷新 callback 的依赖；
  - 只在存在非终态文档时每 2 秒轮询；
  - 增加 in-flight guard 与 `AbortController`，禁止重叠请求并在卸载时停止；
  - create/upload/retry/delete 均捕获错误并落到页面提示，不再产生 unhandled rejection。

**验收**

- 创建知识库返回 201/200，不再出现 422。
- 上传 PDF 时请求头包含正确 multipart boundary。
- 空闲页面不轮询；处理中文档每周期最多请求一次列表及必要详情；终态后停止。

### 阶段 2：异步化并持久化知识库摄取

**后端改动**

- 调整 `backend/tutor/api/routers/knowledge_bases.py`：上传校验、文件落盘、创建文档记录后返回 202，不在请求协程内完成全部摄取。
- 调整 `backend/tutor/services/knowledge_base/service.py`：把 `uploaded → parsing → chunking → embedding → ready/failed` 建模为显式状态机；所有异常写入稳定错误码和可读信息。
- 调整 `backend/tutor/services/knowledge_base/store.py`：将知识库、文档、chunk 元数据持久化到 SQLite，服务重启后可恢复。
- 修正 Embedding 调用契约：使用当前 `EmbedRequest.input`、异步 `embed()` 和响应 `vectors`；禁止把向量化失败静默标记为 ready。
- 后台执行策略：本地单机版本先使用应用内受控任务队列（有并发上限、异常收集和关闭处理）；未来部署再替换 Celery/RQ，不在本轮引入额外基础设施。
- 增加结构化日志：`request_id/lib_id/doc_id/stage/duration/error_code`，不记录文件正文或 API key。

**验收**

- 典型 PDF 上传响应时间小于 1 秒（不含文件传输时间）。
- 后端重启后知识库和文档状态仍存在；处理中任务可标记为 interrupted 并允许重试。
- PDF 无可提取文本时返回 `EMPTY_DOCUMENT`，加密/损坏/超限文件分别返回明确错误。

### 阶段 3：统一聊天任务状态，修复死循环并展示思考过程

**改动**

- `frontend/components/chat/ChatMessages.tsx` 从当前会话的 `ClientJob.events` 派生运行卡片、阶段、thinking 摘要和 terminal 状态；不再以 `activeTurn.phase !== idle` 判断异步任务是否进行中。
- `frontend/lib/event-handler.ts` 与 `frontend/lib/store.ts`：删除或隔离旧 activeTurn 兼容路径；终态必须是有限状态转换，`success/failed/cancelled` 后不再显示 spinner。
- `frontend/lib/job-reducer.ts`：保证 `thinking`、`agent_start/end`、`progress`、`error`、`job_terminal` 可重放且幂等；重复 terminal 事件不能重新打开加载态。
- `backend/tutor/services/jobs/runner.py`：在持久化与广播边界为每个事件强制补齐 `job_id/session_id/sequence/timestamp`；保证 terminal 事件只发送一次。
- UI 对“思考输出”只显示阶段化摘要（例如意图分析、检索、资源规划、生成进度），不暴露模型私有推理链；展开区显示 Agent、工具调用与依据来源。

**验收**

- 用户提交后 300 ms 内出现已接收/规划阶段反馈。
- thinking/progress 流式更新；资源完成后 spinner 在 terminal 事件到达时立即结束。
- 刷新页面后从事件回放恢复正确终态，不出现“资源已生成但仍调用 Agent”。

### 阶段 4：持久化历史会话并加入左侧栏

**后端**

- 新增 `Conversation`、`Message` 持久化模型及 repository/service。
- 新增 API：
  - `POST /conversations`
  - `GET /conversations?cursor=&limit=`
  - `GET /conversations/{session_id}`
  - `PATCH /conversations/{session_id}`
  - `DELETE /conversations/{session_id}`
- 每次任务提交必须携带当前 `session_id`；用户消息在接收任务时写入，助手消息在 terminal 时写入。写入使用 job_id 幂等键。
- 首条用户消息自动生成短标题；允许用户重命名。旧任务若有 session_id 则回填会话，否则每个旧任务作为独立历史项导入。

**前端**

- 将 `sessionId` 从 `frontend/app/page.tsx` 的局部 state 收敛到会话 store。
- `frontend/components/layout/Sidebar.tsx` 增加历史会话区：新建、按更新时间分组、切换、重命名、删除、加载更多。
- 切换会话时加载持久化 messages 与 jobs，取消当前订阅但不取消后台任务；返回会话后继续订阅/回放。

**验收**

- 刷新浏览器和重启前后端后，历史会话仍可见并可恢复。
- 多轮任务的 session_id 一致；切换会话不串消息、不串资源、不重复写消息。

### 阶段 5：资源中心可交互预览

**改动**

- 在 `frontend/app/resources/page.tsx` 增加 `selectedResourceId`；打开资源包时默认选择第一项。
- 左侧资源项改为可访问按钮，支持鼠标与键盘选择并显示选中态。
- 右侧直接复用 `frontend/components/resources/ResourceCard.tsx` 导出的 `ResourceDetail`，覆盖文档、思维导图、练习、阅读、视频/动画、代码和 PPT。
- 桌面使用双栏预览，窄屏改为列表/详情切换；保留下载与返回操作。

**验收**

- 点击任意资源立即显示对应内容；切换资源不会重复请求完整资源包。
- 七类资源均有渲染测试，未知类型有安全 fallback。

### 阶段 6：全链路回归与非功能验证

**自动化检查**

```powershell
npm test --workspace frontend
npm run typecheck --workspace frontend
conda run -n tutor python -m pytest backend/tests/services/knowledge_base backend/tests/services/jobs backend/tests/e2e/test_demo_scenarios.py -q
```

**浏览器验收场景**

1. 新建会话并提交普通问答：只产生文字/必要资源，不误触发视频。
2. 观察 thinking/progress，等待任务完成，确认加载态终止。
3. 刷新页面并从左侧历史恢复对话。
4. 新建知识库，上传含可提取文字的 PDF，观察状态进度与请求频率。
5. 重启服务，确认知识库、文档和历史会话保留。
6. 在资源中心逐项点击并预览已生成资源。

**性能与稳定性门槛**

- 无 unhandled promise rejection。
- 空闲知识库页面 30 秒内无轮询请求。
- 工作轮询无并发重叠，终态后 1 个周期内停止。
- 每个 job 恰好一个 terminal 事件；重复事件处理幂等。
- API key、文档正文、模型私有推理链不进入日志。

## 四、提交拆分与审查门槛

建议按以下独立提交执行，便于定位回归：

1. `test: cover multipart polling and terminal-state regressions`
2. `fix: handle multipart requests and bound knowledge-base polling`
3. `feat: persist knowledge bases and ingest documents asynchronously`
4. `fix: unify chat job state and render streamed progress`
5. `feat: persist conversations and add history sidebar`
6. `feat: preview generated resources in resource center`
7. `test: add end-to-end stability and restart coverage`

每个提交必须先通过相关单元测试；阶段 3、4、6 完成后分别进行一次代码审查和浏览器人工回归。不得把所有改动压成一个提交。

## 五、明确不采用的短期补丁

- 不用 localStorage 作为历史会话的唯一存储，因为无法跨设备、无法与任务事件一致恢复。
- 不仅把 `activeTurn` 强制重置为 idle；这会隐藏 spinner，但仍保留双状态源和丢失 thinking 的问题。
- 不通过延长 Next.js 代理超时掩盖同步 PDF 摄取；上传请求应快速返回。
- 不让知识库页面常驻轮询所有库；轮询必须由非终态文档显式驱动。

