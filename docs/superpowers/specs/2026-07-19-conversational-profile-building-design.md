# 对话式学习画像构建设计（画像对话化）

日期：2026-07-19
分支：直接在 `main` 上实现（用户明确指定；`codex/tutorbot-reliability` 已删除）
状态：待用户批准

## 背景与问题

需求 1 要求"通过自然语言对话（结合学生的专业、学习目标、学习历史）自动抽取特征，构建不少于 6 个维度的动态学生画像，并随学随新"。2026-07-19 全量审计结论：

- 6 维画像模型齐备（`backend/tutor/services/learner_profile/schema.py:230`：知识图谱、认知风格、易错模式、学习节奏、动机目标、模态偏好）。
- `FeatureExtractorAgent`（`backend/tutor/agents/profile/feature_extractor.py:104`）是真实 LLM 抽取（当前消息 + 最近 6 轮历史），覆盖 major/level/目标/风格/节奏/模态。
- **但抽取只在用户显式说"我的画像/了解我"等关键词时触发**（`backend/tutor/services/intent/router.py:50-53,298`）。典型自我介绍"我是 CS 研一，想学 LSTM，之前学过基础 NN 但对 RNN 不太熟"落入 tutoring 默认路由，特征不被抽取；辅导对话只读画像、从不写入。
- 抽取到的 `major`/`level` 存进 `metadata` 后两头落空：`to_summary()`（schema.py:287-305）不含 metadata → 生成提示词拿不到专业；`ProfilePanel` 不渲染 metadata → 用户看不到。
- 对话驱动的画像更新（`ProfileBuilder.ingest_signal` → `apply_diff`）会提升版本号但**不调度 `path_rebuild`**，学习路径停留在旧版本，前端显示 stale。
- "随学随新"目前只有练习评分一条自动路径（5 条评分事件 → EMA）。

注意：main 上另有已批准的"学习体验修复与持久化"设计（`docs/superpowers/specs/2026-07-19-learning-experience-persistence-design.md`，尚未跟踪）。本设计与其正交：只在 `event-handler.ts`/`ProfilePanel.tsx` 上做**纯新增**，不改其既有持久化逻辑。

## 用户已确认的决策

1. **混合式抽取**：冷启动/自我介绍消息做完整抽取（结果用户可见）；普通辅导轮次用廉价启发式门控，命中后异步抽取。
2. **同轮异步**：消息照常由辅导/资源生成回答（不阻塞）；抽取在同一任务内作为后置步骤完成；下一轮起画像生效。
3. 配套项全做：专业进生成提示词、画像面板显示专业/目标、画像更新联动路径重建。

## 组件设计

### 1. 信号检测器（新建 `backend/tutor/services/learner_profile/signal_detector.py`）

纯函数，无 I/O：

```python
def detect_profile_signal(message: str, *, has_profile: bool) -> bool
```

规则（对 message 做大小写无关匹配）：

- **强身份模式**（命中任一即触发）：`我是`、`我现在是`、`我就读`、`我的专业`、`专业是`、`研一/研二/研三`、`大一/大二/大三/大四`、`本科生`、`硕士生`、`博士生`、`I'm a`、`I am a`、`my major`、`I study`。
- **弱目标模式**：`我想学`、`我要学`、`我想了解`、`目标是`、`准备(考试|面试|考研|求职|期末)`、`打算学`、`I want to learn`、`my goal`。
- **弱历史模式**：`之前学过`、`以前学过`、`没学过`、`零基础`、`有…基础`、`不太熟`、`比较熟`、`熟悉`、`了解过`、`自学过`、`I've studied`、`new to`、`familiar with`。

触发条件：`强身份` 或 `(弱目标 ∧ 弱历史)` 或 `(¬has_profile ∧ (弱目标 ∨ 弱历史))`。
负例必须不触发：纯提问（"什么是反向传播？"）、纯资源请求（"帮我生成反向传播的讲解"）。

### 2. 对话摄取器（新建 `backend/tutor/services/learner_profile/dialogue_ingest.py`）

```python
async def ingest_dialogue_signal(
    *, user_id: str, message: str, history: list[ConversationTurn],
    session_id: str, stream: StreamBus, runner: JobRunner,
) -> bool
```

流程：检测未命中 → 返回 False（零 LLM 调用）。命中 → `FeatureExtractorAgent.process` → `ProfileBuilder.ingest_signal` → 若版本号提升，通过 runner 调度 `path_rebuild` 跟随任务（复用现有 dedupe 键格式 `path_rebuild:{version}` 与提交路径，参照 `services/learning_events/workflow.py` 与 `capabilities/resource_generation.py` 提交 video_render 子任务的方式）→ 通过 stream 发 `profile_updated` 事件（metadata：`version`、`major`、`goal_type`）。**best-effort**：LLM/JSON/存储任何失败只记 WARNING 日志，返回 False，绝不抛出、不影响主流程；整体以 `asyncio.wait_for`（20s）包裹。

### 3. 接线（两处，各一行调用）

- `backend/tutor/capabilities/tutoring.py`：答疑与会话记录完成后、`run()` 返回前调用。助手答复文本先于抽取流出；任务终态最多延后摄取器的 20s 上限（典型 <5s）。
- `backend/tutor/capabilities/resource_generation.py`：打包阶段完成后、`run()` 返回前调用（本轮资源仍用旧画像，符合"同轮异步"决策）。
- 显式画像意图（PROFILE_KEYWORDS）仍走 `LearnerProfileCapability`，行为不变。

### 4. 专业进生成提示词

`LearnerProfile.to_summary()`（schema.py:287）增加 `major`、`level`（取自 `metadata`，缺省为空串）。各生成 Agent 提示词的 `## 学生画像` 段是 profile_snapshot JSON 插值，自动携带，无需改模板。

### 5. 画像面板与实时刷新（前端）

- `ProfilePanel.tsx` 概览页加一行身份信息：`metadata.major` / `metadata.level`（目标 `motivation.goal_description` 已有展示，不动）。API 已返回 metadata（`api/routers/learning.py:148`），前端类型已有 `LearnerProfileDetail.metadata`。
- `event-handler.ts` 新增 `case "profile_updated"`：收到后调用画像重新拉取（复用 `useProfile` 的 refresh 对应的数据通路，模块级实现，不引入 hook 依赖）。纯新增 case，不改既有 case。

### 6. 路径重建联动

摄取器在画像版本提升后调度 `path_rebuild`（见组件 2）。幂等：dedupe 键保证一版本一重建；`path_rebuild` 自身的 insert-if-absent 语义不变。

## 错误处理

- 检测器：纯函数，无失败模式。
- 摄取器：全链路 try/except + 20s 上限；失败仅日志。画像缺失时 `has_profile=False` 按冷启动规则处理。
- 流事件异常不影响任务终态合同。

## 测试

后端（pytest，`E:\Anaconda3\anaconda\envs\tutor\python.exe`）：

- 检测器单测：强身份/弱组合/冷启动各正负例（含"什么是反向传播"不触发、"我想学 LSTM，之前学过 NN"触发、有画像时"我想学反向传播"不触发）。
- 摄取器：命中→ingest 被调且发事件并调度 `path_rebuild:{version}`；未命中→零 LLM 调用；extractor 抛异常→返回 False、无事件、主流程无恙。
- 接线：tutoring 入口（mock extractor）自我介绍说完后画像含 major；resource_generation 入口提示词 snapshot 含 major（to_summary 测试 + 一处管线断言）。
- 集成：自我介绍经辅导 → 画像含 major → 版本提升 → path_rebuild 被调度（dedupe）。

前端（vitest）：

- `ProfilePanel` 渲染 metadata.major/level。
- `event-handler` 收到 `profile_updated` → 触发画像重新拉取。

## 明确不做（YAGNI）

- 不做逐轮无条件 LLM 抽取（成本）；门控规则即全部。
- 不做"先抽取后回答"的同步编排。
- 不修改显式画像 capability、练习评分 EMA 链路的既有行为。
- 不触碰"学习体验持久化"工作的任何文件语义（仅在其后追加独立 case）。

## 验证命令

```powershell
cd E:\github\TutorBot
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests -q
cd frontend && npm test -- --run && npm run type-check
```

注意：`backend/tests` 有 5 个已确认的预存在 Windows 环境失败（cjk font、3× health_runtime、learning_router reconcile），与本次无关；`frontend/next-env.d.ts` 是开发服务器改写的脏文件，永不 stage。
