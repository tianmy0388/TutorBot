# Tutor — 演示脚本

本脚本配合 `docs/architecture.md` 使用。`backend/tests/e2e/test_demo_scenarios.py`
覆盖了脚本中提到的全部七条主线，可在 CI 中自动复演。

## 1. 启动

```bash
# 后端
cd E:/github/Tutor
python -m pip install -e ".[dev]"
python -m tutor api            # http://127.0.0.1:8000

# 前端
cd frontend
npm install
npm run dev                   # http://127.0.0.1:3010
```

打开 `http://127.0.0.1:3010/`，顶部出现四个一级导航：**学习工作台**、**知识库**、**资源中心**、**设置**。

## 2. 画像对话（六维更新）

在"学习工作台"输入框连续发 4–5 条对话：

- "我是一名大二学生，目标是备考机器学习期末考试"
- "我比较喜欢看图，不喜欢长篇文字解释"
- "我每天大概能学 40 分钟"
- "我对 Transformer 还不太熟"
- "我不喜欢 PPT，麻烦别给我出"

观察：
- 每条消息触发一次**意图路由**（"评估" / "路径" / "学习" 等关键词）。
- 路由将对话分发给 `profile` 或 `tutoring` capability，**不进入资源生产路径**。
- 学习画像在 `/api/v1/profile/{user_id}/summary` 实时变化（维度 ≥ 6）。
- 终端消息来自 `JobResultContract.assistant_message`，**不再由前端猜测**。

## 3. 即时答疑（无视频）

输入："解释 self-attention"

观察：
- 后端意图路由判定为 `tutoring`，**没有**走 `/api/v1/plans`，因此没有视频任务。
- 终端消息包含引用片段（来自 `ai_introduction` 知识库），并显示"未使用课程知识库"提示（如未配置）。
- 顶部**任务卡**显示 `tutoring / 已完成`，附带引用与置信度。

## 4. 资源生成（计划确认）

输入："为 Transformer 制定学习资源"

观察：
- 后端立即返回 `ResourcePlan`（topic="Transformer"，推荐三类：document / mindmap / exercise）。
- 前端在"工作台"渲染 `ResourcePlanCard`；用户**取消勾选 video/PPT** 即不会生成。
- 点击"确认生成 (3 项)"后调用 `POST /api/v1/plans/{plan_id}/confirm`，立即返回 `job_id`。
- `JobProgressCard` 实时显示阶段、活跃 Agent 与已完成的资源。
- 终态消息来自服务端合同，不再被前端启发式覆盖。

## 5. 部分失败 + 重试

复现：发送"为 Transformer 制定学习资源"+ 手动勾选 **video**（在某些环境会触发 Manim 渲染失败）。

观察：
- 终态 `JobResultContract.status === "partial"`，`artifacts[]` 中 document / mindmap / exercise 成功，video 失败。
- 顶部任务卡显示"部分完成"徽标 + 红色失败项 + `[MANIM_RENDER_FAILED]` 错误码。
- 点击"重试失败项"调用 `POST /api/v1/jobs/{user_id}/{job_id}/retry`，只对 video 重新跑；前端展示 preserved_artifacts。

## 6. 知识库上传

进入 **/knowledge-bases** 页面：

- 看到预置的 `ai_introduction` 库（is_seeded=true）。
- 点"新建"创建一个 `操作系统进阶` 库。
- 在卡片里上传一份 PDF（讲义）或 TXT。状态机可见：
  `uploaded → extracting → chunking → embedding → ready`。
- 失败时显示错误码 + 重试按钮。

回到工作台提问："CPU 调度算法"，观察：终端消息的引用片段会优先来自**当前激活的库**。

## 7. 设置与连接测试

进入 **/settings** 页面：

- LLM / Embedding / WebSearch 三组配置，**密钥以掩码形式显示**（`sk-…ab12`）。
- 留空 Key = 保留旧值；点"清除"= 移除。
- 改错 Provider 名（输入 `not-a-real-provider`）→ 表单 PATCH 返回 422。
- 点"测试连接" → 真实拨测当前 LLM/Embedding；返回 `{ok, latency_ms, code}`。

## 8. 断线恢复（任务快照）

在工作台提交一个长任务（5 类资源 + video）。在生成过程中：

- `Ctrl-R` 刷新页面。
- 前端通过 `GET /api/v1/jobs/{user_id}` 拿到完整快照，
  `applyReducerEvent({type: "snapshot", job: ...})` 重建 `jobsById[job_id]`。
- `WebSocket` 续订阅 `/api/v1/ws subscribe_job`，从 `event_cursor` 之后继续。
- **不会出现"已完成但无输出"**：终端消息的 `assistant_message` 来自持久化 `result.contract`，不是前端拼出来的。

## 9. 验收

- `python -m pytest -q`（后端）— 全绿
- `npm test --workspace frontend`（vitest）— 34 用例
- `npm run type-check --workspace frontend`（tsc --noEmit）— 无错误
- `npm run build --workspace frontend`（next build）— 三个页面 `/`、 `/knowledge-bases`、`/resources`、`/settings` 全部成功静态化

## 10. 反幻觉与证据

- 每个 `Resource` 持久化字段 `citations[] / confidence_score / review / safety / generated_by[]`。
- 未验证的声明落到 `unverified_claims[]`，UI 以 warning 形式呈现，**不会被静默当作真值**。
- `Resource` 的 `metadata` 同时保存 `safety` 与 `review` 字典；`ResourcePackage.metadata` 保存整体审核结论。
