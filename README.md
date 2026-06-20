# Tutor — 个性化学习资源生成多智能体系统

> 基于大模型的个性化资源生成与学习的多智能体系统，借助多智能体协同为高等教育学生打造专属的个性化学习智能体。

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![Next.js](https://img.shields.io/badge/Next.js-16-black)](https://nextjs.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![Phase](https://img.shields.io/badge/phase-4%2F5-yellow.svg)](#路线图)

## 项目简介

Tutor 是一个面向高等教育学生的 AI 辅助学习系统，通过多智能体协同工作，自动生成针对学生个性化需求的多模态学习资源。核心目标是解决传统教育中"资源繁杂无序、难以精准匹配、缺乏个性化指导"的问题。

## 核心特性

- **🎯 对话式学习画像**：通过自然语言对话自动构建包含 6 维度（知识基础、认知风格、易错点偏好、学习节奏、动机目标、模态偏好）的动态学生画像
- **🤖 多智能体协同**：9+ 角色 Agent 协同工作（认知诊断、内容专家、教学设计、习题生成、多媒体编译、Manim 视频、代码沙箱、质量审核、防幻觉、即时答疑、效果评估）
- **📚 多模态资源生成**：6 种类型 — 课程讲解文档、知识点思维导图、练习题库、拓展阅读材料、教学视频/动画、代码实操案例
- **🛤️ 学习路径规划**：基于知识图谱的拓扑排序 + 画像驱动的精准推送
- **💬 即时智能辅导**：4 层答案结构（TL;DR / 直觉 / 原理 / 示例）+ 多模态补充（图表 / 代码 / 练习 / 参考 / 视频）
- **📊 学习效果评估**：6 维评分 + trajectory 分析 + 自适应策略生成

## 技术栈

| 层级 | 选型 |
|---|---|
| 后端 | Python 3.11 + FastAPI + uvicorn + WebSocket |
| 前端 | Next.js 16 + React 19 + TypeScript + Tailwind CSS |
| Agent 框架 | 自研 BaseAgent + BaseCapability + BaseTool（借鉴 DeepTutor）|
| 流式事件 | StreamBus 异步扇出 → WebSocket → 前端 TracePanel |
| LLM Provider | OpenAI-compatible + Anthropic + DeepSeek（多 Provider） |
| RAG | LlamaIndex |
| 视频生成 | Manim Community + 两阶段 AI + 代码重试闭环 |
| 知识图谱 | YAML 静态定义 + NetworkX 内存遍历 |
| 存储 | SQLite + JSON 文件 |

## 快速开始

### 0. 环境要求

- **Python 3.11+**（建议 3.11.15）
- **Node.js 18+**（建议 24.x）+ npm
- **可选**: Manim + ffmpeg + LaTeX（用于视频/动画生成）
- **LLM API Key**（OpenAI / Anthropic / DeepSeek 任选其一；没有也能跑通,只是部分 Agent 优雅降级）

### 1. 后端启动

```bash
# 克隆并进入项目
cd Tutor

# 创建虚拟环境
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
# source .venv/bin/activate

# 安装依赖 (两种方式任选)
# 方式 A: pip install editable
pip install -e ".[dev]"

# 方式 B: 如果方式 A 不可用,从 backend 目录启动(用 PYTHONPATH)
# cd backend && PYTHONPATH=. python -m tutor api

# 复制并编辑环境变量
cp .env.example .env
# 编辑 .env，至少填入一个 LLM API Key：
#   TUTOR_LLM_PROVIDER=openai
#   TUTOR_LLM_API_KEY=sk-...

# 启动后端 (默认端口 8000)
python -m tutor api
# 服务监听 http://0.0.0.0:8000
```

### 2. 前端启动

新开一个终端:

```bash
cd frontend
npm install
npm run dev
# 浏览器访问 http://localhost:3010
```

### 3. 验证

```bash
# 后端健康检查
curl http://localhost:8000/api/v1/health
# 期望: {"status":"ok","version":"0.1.0","python":"3.11.15"}

# 能力列表
curl http://localhost:8000/api/v1/capabilities
# 期望: 5 个 capability (profile / resource_generation / path_planning / tutoring / assessment)

# Swagger UI
# 浏览器打开 http://localhost:8000/docs
```

### 4. 第一条对话

打开 `http://localhost:3010`,在聊天框输入:

- **画像构建**: `我是计算机专业大二学生,想系统学习深度学习,目标是做毕业项目`
- **资源生成**: `系统学习 Transformer 注意力机制`(等待 30-60 秒,生成 6 类资源)
- **即时答疑**: `解释一下 self-attention 的 QKV`(4 层答案 + 模态补充)
- **效果评估**: `评估一下我的学习效果`(6 维评分 + 自适应策略)

每条消息都会以 **流式事件** 推送到前端,右侧面板自动切换到对应结果。

## 项目结构

```
Tutor/
├── backend/                       # Python 后端
│   └── tutor/                     # 主包
│       ├── core/                  # 核心抽象 (StreamBus, Capability, Tool)
│       ├── agents/                # Agent 实现 (profile, resource, tutor, assessment, safety)
│       ├── capabilities/          # Capability 编排层 (5 个能力)
│       ├── runtime/               # Orchestrator + Registry
│       ├── services/              # 服务层 (LLM, RAG, Profile, KG, Manim, fact-check)
│       ├── tools/                 # 工具层 (RAG, web_search, code_exec, paper_search)
│       ├── api/                   # FastAPI 路由 (REST + WebSocket)
│       ├── knowledge_base/        # 课程知识库 (ai_introduction 示例)
│       └── prompts/               # Prompt YAML (中英双语)
├── frontend/                      # Next.js 前端
│   ├── app/                       # 路由 (主页面 /)
│   ├── components/                # 组件 (chat / profile / resources / tutor / assessment / kg / layout)
│   ├── hooks/                     # 自定义 Hooks (useWebSocket / useProfile / useKG)
│   ├── lib/                       # 客户端封装 (API client, store, event-handler, types)
│   └── store/                     # 状态管理 (Zustand)
├── docs/                          # 文档 (architecture, agents, knowledge-base)
└── data/                          # 运行时数据 (SQLite profiles + event store)
```

## 核心能力一览

| Capability | 阶段数 | 关键 Agent / 服务 | 触发方式 |
|---|---|---|---|
| **profile** | 5 | feature_extractor, cognitive_diagnostic, profile_updater | 每次对话自动 |
| **resource_generation** | 10 | content_expert, pedagogy, exercise, multimedia, manim_video, code, quality_reviewer, anti_hallucination | 关键词 "系统学习"/"学习 XXX" |
| **path_planning** | 5 | KG planner (NetworkX topo-sort) | 资源生成后自动 |
| **tutoring** | 5 | question_understanding, RAG, TutoringAgent, MultiModalEnrichment | 关键词 "解释"/"为什么"/"不懂" |
| **assessment** | 5 | event_collection, AssessmentAgent, AdaptiveStrategyEngine | 关键词 "评估"/"复盘" |

## 致谢与开源引用

本项目在架构设计上参考了以下优秀开源项目：

- **[DeepTutor](https://github.com/HKUDS/DeepTutor)** — Agent 架构、StreamBus、CapabilityRegistry、PromptManager 等核心抽象的设计灵感来源
- **[ManimCat](https://github.com/MathInspector/ManimCat)** — Manim 视频生成的两阶段 AI、StaticGuard、CodeRetry 模式
- **[Manim Community](https://www.manim.community/)** — 数学/概念可视化动画引擎
- **[LlamaIndex](https://www.llamaindex.ai/)** — RAG 框架
- **[Next.js](https://nextjs.org)** + **[Tailwind CSS](https://tailwindcss.com)** + **[Recharts](https://recharts.org)** — 前端框架与可视化

详见 `docs/architecture.md`。

## 路线图

- [x] **Phase 1** — 项目脚手架 + 核心基础设施（BaseAgent / StreamBus / Orchestrator / FastAPI）
- [x] **Phase 2** — 画像系统 + 知识图谱 + 7 类资源生成（含 PPT 教案）+ Manim 视频 + 防幻觉
- [x] **Phase 3** — 学习路径规划 + 即时智能辅导 + 多维效果评估 + 自适应策略
- [x] **Phase 4** — 前端 UI 完整实现（Chat / Profile / Resource Cards / KG Path / Tutor / Assessment）
- [x] **Phase 5** — 资源持久化 + 异步后台任务 + PPT 生成（python-pptx）
- [ ] **Phase 6（可选）** — 知识库扩充 + 单元测试 + Docker Compose

## 完整能力一览（Phase 5 后）

| 能力 | 阶段 | 关键特性 |
|---|---|---|
| **profile** | 5 | 6 维画像（认知风格/节奏/动机/模态/知识/错误）+ ProfileStore 持久化 |
| **resource_generation** | 12 | 7 类资源（document / mindmap / exercise / reading / video / code / **ppt**）+ 资源包持久化 + 异步执行 |
| **path_planning** | 5 | YAML 知识图谱 + NetworkX 拓扑排序 + 画像剪枝 |
| **tutoring** | 5 | 4 层答案（TL;DR / 直觉 / 原理 / 示例）+ 多模态补充 |
| **assessment** | 5 | 6 维评分 + trajectory + 自适应策略 |
| **jobs (Phase 5.2)** | — | 异步任务队列：submit / subscribe / cancel / replay |
| **persistence (Phase 5.1)** | — | 资源包 + jobs + profile + events 全 SQLite 持久化 |

## 常见问题

### 后端启动报 `ModuleNotFoundError: No module named tutor`
原因：工作目录不在 `backend/` 下，`python -m tutor` 找不到包。
解决：
```bash
cd backend
PYTHONPATH=. python -m tutor api
```
或者：
```bash
pip install -e .
```

### 前端启动报 `Cannot connect to backend`
检查：
- 后端是否在 `http://localhost:8000` 正常监听
- `.env` 中 `TUTOR_CORS_ORIGINS` 是否包含 `http://localhost:3010`
- 浏览器控制台是否有 CORS 错误

### 没有 LLM API Key 能跑吗？
可以，但部分 sub-agent 会报错并优雅降级。**完整功能需要至少一个 LLM Provider 的 API Key**（OpenAI / Anthropic / DeepSeek 任选），在 `.env` 中设置：
```bash
TUTOR_LLM_PROVIDER=openai
TUTOR_LLM_API_KEY=sk-...
TUTOR_LLM_MODEL=gpt-4o-mini
```
Agent 默认用 `gpt-4o-mini`，对中文支持足够；要更好的输出质量可设 `gpt-4o` 或 `claude-sonnet`。

### Manim 视频生成失败？
确认已安装：
```bash
python -c "import manim; print(manim.__version__)"   # 期望 0.20.x
ffmpeg -version                                       # 任意较新版本
latex --version                                       # 可选，用于公式渲染
```

### 任务一直 "pending" / "running" 不结束？
查看后端日志是否有 LLM 调用错误（最常见的：`401 invalid api key`）。Job 会跑完所有 stage 直到 done/error，单个 stage 失败不会终止整个 job，但最终 result 事件中可能缺一些子资源。

## 许可证

MIT License — 详见 [LICENSE](./LICENSE)

## 联系方式

项目作者: Tian Mingyu (tmy0388@qq.com)