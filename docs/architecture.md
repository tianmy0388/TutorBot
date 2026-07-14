# TutorBot 系统架构

> 本文档描述 TutorBot 系统的整体架构、设计原则与核心组件。

## 总体架构（五层模型）

```
┌──────────────────────────────────────────────────────────────┐
│                  交互层 (Presentation)                         │
│  Chat UI │ Resource Viewer │ Dashboard │ Profile Panel        │
│  (Next.js 16 + React 19 + Tailwind)                          │
└──────────────────────────────────────────────────────────────┘
                            │ WebSocket + HTTP
┌──────────────────────────────────────────────────────────────┐
│                  编排层 (Orchestration)                        │
│  MainOrchestrator → 意图识别 → 能力路由 → 多Agent调度         │
│  (借鉴 DeepTutor: ChatOrchestrator, StreamBus)               │
└──────────────────────────────────────────────────────────────┘
                            │
┌──────────────────────────────────────────────────────────────┐
│                   能力层 (Capabilities)                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐        │
│  │ 画像构建  │ │ 资源生成  │ │ 路径规划  │ │ 智能辅导  │        │
│  │Capability│ │Capability│ │Capability│ │Capability│        │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘        │
│                      ┌──────────┐                             │
│                      │ 效果评估  │                             │
│                      │Capability│                             │
│                      └──────────┘                             │
└──────────────────────────────────────────────────────────────┘
                            │
┌──────────────────────────────────────────────────────────────┐
│                    智能体层 (Agents)                            │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐     │
│  │认知诊断│ │内容专家│ │教学设计│ │多媒体  │ │质量审核│     │
│  │ Agent  │ │ Agent  │ │ Agent  │ │ Agent  │ │ Agent  │     │
│  └────────┘ └────────┘ └────────┘ └────────┘ └────────┘     │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐                │
│  │习题生成│ │Manim视频│ │代码沙箱│ │防幻觉  │                │
│  │ Agent  │ │ Agent  │ │ Agent  │ │ Agent  │                │
│  └────────┘ └────────┘ └────────┘ └────────┘                │
└──────────────────────────────────────────────────────────────┘
                            │
┌──────────────────────────────────────────────────────────────┐
│                  工具/服务层 (Tools & Services)                │
│  RAG │ WebSearch │ CodeSandbox │ Manim │ MindMap │ PPT Gen   │
│  (LlamaIndex) (DuckDuckGo) (subprocess) (CE 0.20) (Mermaid)   │
└──────────────────────────────────────────────────────────────┘
```

## 核心设计原则

1. **能力分层**：Capability（顶层编排）→ Agent（领域智能体）→ Tool（原子能力）
2. **流式优先**：所有 LLM 调用走 StreamBus，事件扇出到 WebSocket → 前端 TracePanel 实时渲染
3. **画像驱动**：每个请求都附带 LearnerProfile 上下文，让生成内容自动适配
4. **多模态生成**：6+ 种资源类型，由不同 Agent 协作（生成-审核-编译三段式）
5. **防幻觉 + 安全**：事实核查、逻辑一致性、内容安全三层过滤
6. **可观测性**：每 LLM 调用都有完整 trace（输入、输出、token、耗时）

## 借鉴的开源项目

### DeepTutor (HKUDS/DeepTutor)

| 借鉴组件 | Tutor 对应 | 说明 |
|---|---|---|
| `core/stream_bus.py` | `tutor/core/stream_bus.py` | 异步扇出事件总线 |
| `core/capability_protocol.py` | `tutor/core/capability_protocol.py` | BaseCapability 抽象 |
| `core/tool_protocol.py` | `tutor/core/tool_protocol.py` | BaseTool 抽象 |
| `agents/base_agent.py` | `tutor/agents/base_agent.py` | LLM 调用 + 流式 + token 跟踪 |
| `services/prompt/manager.py` | `tutor/services/prompt/manager.py` | YAML 多语言 prompt |
| `services/config/env_store.py` | `tutor/services/config/env_store.py` | .env 管理 |
| `services/config/model_catalog.py` | `tutor/services/config/model_catalog.py` | 多模型 profile |
| `services/llm/provider_factory.py` | `tutor/services/llm/provider_factory.py` | 多 Provider 路由 |
| `runtime/orchestrator.py` | `tutor/runtime/orchestrator.py` | ChatOrchestrator 模式 |
| `runtime/registry/capability_registry.py` | `tutor/runtime/registry/capability_registry.py` | 能力注册 |
| `api/routers/unified_ws.py` | `tutor/api/routers/unified_ws.py` | WebSocket 路由 |

### ManimCat (MathInspector/ManimCat)

| 借鉴组件 | Tutor 对应 | 说明 |
|---|---|---|
| 两阶段 AI（designer → coder） | `agents/resource/manim_video.py` | 场景设计 → Manim 代码 |
| StaticGuard | `services/manim_render/static_guard.py` | py_compile 预检 |
| CodeRetry | `services/manim_render/code_retry.py` | 渲染失败自动重试 |
| ManimExecutor | `services/manim_render/executor.py` | subprocess + 超时 + 监控 |

## 数据流：一次完整的资源生成请求

```
用户输入："我想系统学习LSTM"
    │
    ▼
┌─────────────┐
│ MainOrchestrator  │
│ 意图识别: resource_generation
│ 参数提取: topic=LSTM, scope=系统学习
└──────┬──────┘
       │
       ▼
┌─────────────────────────┐
│ Step 1: 画像检查 (并行)   │
│ LearnerProfileCapability │
│ → relevant_features     │
│ → RNN=0.2, NN=0.8       │
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────────┐
│ Step 2: 资源规划          │
│ ResourceGenerationCap    │
│ → 生成资源清单 (6+ 类型)  │
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────────┐
│ Step 3: 多Agent并行生成   │
│                          │
│  pipeline([              │
│    "LSTM回顾卡片",       │
│    "LSTM门控文档",       │
│    "LSTM思维导图",       │
│    "LSTM变体对比",       │
│    "LSTM代码案例",       │
│    "分层练习题",         │
│    "门控动画",           │
│  ],                       │
│  content_gen,            │
│  review_and_revise,      │
│  compile_to_final        │
│ )                        │
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────────┐
│ Step 4: 防幻觉审核        │
│ AntiHallucinationAgent   │
│ → 置信度评分             │
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────────┐
│ Step 5: 资源包组装        │
│ ResourcePackageBuilder   │
│ → 6+ 张资源卡片          │
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────────┐
│ Step 6: 学习路径整合      │
│ PathPlanningCapability   │
│ → 推荐学习顺序            │
└──────┬──────────────────┘
       │
       ▼
   流式推送到前端（WebSocket）
   → 资源卡片化展示
```

## 关键数据结构

```python
# 6 维学习画像
class LearnerProfile(BaseModel):
    user_id: str
    knowledge_map: dict[str, float]            # {concept: mastery_score 0-1}
    cognitive_style: str                       # visual/verbal/deductive/inductive/active/reflective
    error_patterns: list[ErrorPattern]
    learning_pace: PaceProfile                 # {avg_session_duration, chunk_size, review_interval}
    motivation_profile: MotivationProfile      # {goal_type, urgency, self_efficacy}
    modality_preferences: dict[str, float]     # {text/video/interactive/diagram/code}
    updated_at: datetime

# 资源
class Resource(BaseModel):
    resource_id: str
    type: Literal["document", "mindmap", "exercise", "reading", "video", "code", "ppt"]
    title: str
    content: str
    format_specific: dict                     # 类型特定字段
    difficulty: int                            # 1-5
    estimated_minutes: int
    prerequisites: list[str]
    generated_by: list[str]                    # 参与的 Agent
    confidence_score: float                    # 0-1

# 资源包
class ResourcePackage(BaseModel):
    package_id: str
    topic: str
    resources: list[Resource]
    learning_path: LearningPath
    target_profile_snapshot: LearnerProfile
```

## 部署架构（MVP）

```
┌────────────────┐
│  Browser       │
│  (Next.js SPA) │
└────────┬───────┘
         │ HTTP + WebSocket
         ▼
┌────────────────┐
│  FastAPI       │
│  (Uvicorn)     │
│  :8000         │
└────────┬───────┘
         │
    ┌────┴────┐
    ▼         ▼
┌───────┐ ┌────────────┐
│SQLite │ │ Manim      │
│+ JSON │ │ subprocess │
└───────┘ └────────────┘
```

后续可扩展：Celery/Arq 任务队列、Redis 缓存、Neo4j 知识图谱、Docker Compose。

## 后续演进方向

- **图数据库**：Neo4j 替代 NetworkX，支持更复杂的知识图谱查询
- **任务队列**：Celery/Arq 替代 asyncio 任务，支持长时间渲染任务
- **持久化升级**：PostgreSQL 替代 SQLite
- **多用户**：完整的认证 + 权限隔离
- **移动端**：响应式 + PWA
- **可观测性**：OpenTelemetry 接入
