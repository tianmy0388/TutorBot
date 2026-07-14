# TutorBot 架构总览（Agent / Capability / Tool 三层）

> 用户问题："用 Agent 分工，每个 Agent 具备能力、可调用工具。现在的项目是这么做吗？"

## 简短回答

**是的，当前项目就是按 Agent / Capability / Tool 三层抽象来组织的**，跟你的设想一致。但目前有**两个偏差**值得说一下：

1. **Tool 这一层目前是「能力签名」（declarative schema）而不是「LLM 自主决策」（agentic tool-calling）** —— 现在 Tools 由 Agent 在 Python 代码里显式调用，不把 tool schema 透传给 LLM 让它自己决定要不要调。这是有意的设计（更可控、更便宜），但代价就是不够"自主"。
2. **Agent 之间没有横向通信** —— 每个 Agent 看到的是上游 Capability 准备好的 context，没有"互相叫"的机制；想加得引入 message bus。

---

## 三层抽象（已经实现的）

```
┌────────────────────────────────────────────────────────────┐
│ Capability  —  Orchestrator 调度的顶层工作流               │
│   backend/tutor/capabilities/                               │
│     ├─ resource_generation.py   (5 个阶段, 12 个子任务)     │
│     ├─ tutoring.py              (5 个阶段)                  │
│     ├─ assessment.py            (5 个阶段)                  │
│     ├─ profile.py               (持续)                      │
│     └─ path_planning.py         (基于 KG 拓扑排序)          │
│                                                            │
│   BaseCapability.run(context, stream) -> None               │
│   每个 Capability 持有 N 个 Agent，按 stage 串/并行编排     │
└────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────┐
│ Agent       —  一个具体任务（生成内容、审校、跑代码）       │
│   backend/tutor/agents/                                     │
│     ├─ resource/                                            │
│     │   ├─ content_expert.py      — 长文档                  │
│     │   ├─ pedagogy.py            — 教学化改写              │
│     │   ├─ multimedia.py          — 思维导图(Mermaid)       │
│     │   ├─ exercise_generator.py  — 练习题                  │
│     │   ├─ manim_video.py         — Manim 视频 (两阶段)     │
│     │   ├─ code_sandbox.py        — 可运行代码 + 真跑       │
│     │   ├─ ppt_generator.py       — PPT 课件                │
│     │   ├─ quality_reviewer.py    — 6 维质量评分            │
│     │   └─ anti_hallucination.py  — 防幻觉 + 安全           │
│     ├─ tutor/                                               │
│     │   ├─ question_understanding.py                        │
│     │   ├─ tutoring.py                                     │
│     │   └─ multimodal_enrichment.py                         │
│     ├─ profile/  (feature_extractor / cognitive_diagnostic / │
│     │             profile_updater / motivation)             │
│     ├─ assessment/                                          │
│     ├─ path/                                                │
│     └─ safety/                                              │
│                                                            │
│   BaseAgent.process(context, stream, ...) -> Resource       │
│   每个 Agent 持有一个 LLMProvider 引用                      │
└────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────┐
│ Tool        —  无状态 / 准无状态的原子操作                  │
│   backend/tutor/tools/                                      │
│     ├─ rag_tool.py              — RAG 检索 (LlamaIndex)     │
│     ├─ web_search_tool.py       — 网页搜索                  │
│     ├─ mcp_web_search_tool.py   — MCP 协议版搜索            │
│     ├─ paper_search_tool.py     — 论文检索                  │
│     └─ code_execution_tool.py   — 通用代码执行              │
│                                                            │
│   BaseTool.get_definition()  →  OpenAI function-call schema│
│   BaseTool.execute(**kwargs)  →  ToolResult                 │
└────────────────────────────────────────────────────────────┘
```

## 数据流（一条用户消息的生命周期）

```
用户发送 "什么是反向传播？"
  │
  ▼
FastAPI /ws  →  Orchestrator 路由
  │   ├─ IntentAgent → "这是 tutoring 还是 resource_generation？"
  │   └─ 路由到对应 Capability
  │
  ▼
Capability.run(context, stream)
  │  ├─ 阶段 1: profile snapshot（读 ProfileStore）
  │  ├─ 阶段 2: KG summary（读知识图谱）
  │  ├─ 阶段 3-N: 串/并行调 Agent
  │  │    │
  │  │    ├─ Agent A.process()    → Resource
  │  │    │     └─ LLM call (BaseAgent.call_llm)
  │  │    │     └─ Tool.invoke()  ←─ Python 代码显式调用
  │  │    │
  │  │    └─ Agent B.process()    → Resource
  │  │
  │  └─ 阶段 N: 资源包组装 + 持久化
  │
  ▼
StreamBus.thinking/content/error/observation/done  →  WebSocket  →  前端
```

## 你的设想 vs 当前实现 — 差异表

| 维度 | 你的设想 | 当前实现 | 评估 |
|---|---|---|---|
| **Agent 分工** | 每个 Agent 做一种资源 | ✅ 12 个 Agent 各自负责一种资源/职责 | **已对齐** |
| **Agent 持有能力** | Agent 自带 LLM 推理 + 工具调用 | ✅ BaseAgent 持有 llm 引用，能调 Tool | **已对齐** |
| **Tool 调用方式** | LLM 自主决定调哪个 Tool | ⚠️ Agent 代码里显式 `tool.execute()`，未把 schema 透传给 LLM | **降级**（更可控） |
| **Agent 间通信** | Agent 可以互叫 / 协同 | ⚠️ 没有；只看上游准备好的 context | **未实现** |
| **记忆/上下文共享** | Agent 共享用户画像 | ✅ ProfileStore + UnifiedContext 注入每个 Agent | **已对齐** |
| **错误隔离** | 一个 Agent 失败不影响其他 | ✅ `_safe()` 包装器 + `asyncio.gather(return_exceptions=True)` | **已对齐** |
| **可观测性** | Agent 行为可追溯 | ✅ StreamBus → WS → TracePanel | **已对齐** |

## 如果你想往「更自主」的 Agent 走

下面这些都是**当前没做、可以渐进加**的方向：

1. **OpenAI-style Tool Calling**：把 `Tool.get_definition()` 拼到 LLM `tools=` 参数，让 Agent 用 `tool_choice="auto"`，LLM 自己决定调哪个 Tool。需要：
   - 在 `BaseAgent` 加 `available_tools: list[BaseTool]` 字段
   - 修改 `call_llm` 处理 `tool_calls` 响应
   - 给 Capability 加 tool-selection 配置

2. **Agent 间横向通信**：加一个 `AgentMessageBus`，让 Agent A 可以订阅 Agent B 的中间输出（比如 ContentExpert 的草稿实时推到 Pedagogy）。需要：
   - 在 `UnifiedContext` 加 `event_stream` 字段
   - 定义 pub/sub 协议

3. **更细的 Capability 复用**：现在 Capability 是「一个用户问题对应一个 Capability」。可以改成 Capability-as-Tool，让 Agent 把 tutoring 当成 Tool 调，这样多 Agent 协同能跨 Capability。

## 现有问题（已在本次修复中处理）

- ✅ code_sandbox.py 缺 `from loguru import logger` —— 导致任何走空 code 兜底的请求都 NameError
- ✅ code_sandbox.py 用 subprocess 默认编码读 stdout —— 中文 Windows 上 GBK 解码崩溃
- ✅ 前端 `getPackage` 不存在 —— 切换对话时右侧资源栏空白
- ✅ `_extract_first_python_block` 只识别 Manim 代码 —— CodeSandbox 的 `import numpy` 等通用代码被丢弃
- ✅ 视频渲染 `_render_pending_videos` 方法存在但 stage 12 没调用 + `await` 缺失 —— 视频永远 pending

---

最后修改：2026-07-07