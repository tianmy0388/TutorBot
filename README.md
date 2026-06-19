# Tutor — 个性化学习资源生成多智能体系统

> 基于大模型的个性化资源生成与学习的多智能体系统，借助多智能体协同为高等教育学生打造专属的个性化学习智能体。

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![Next.js](https://img.shields.io/badge/Next.js-16-black)](https://nextjs.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)

## 项目简介

Tutor 是一个面向高等教育学生的 AI 辅助学习系统，通过多智能体协同工作，自动生成针对学生个性化需求的多模态学习资源。核心目标是解决传统教育中"资源繁杂无序、难以精准匹配、缺乏个性化指导"的问题。

## 核心特性

- **🎯 对话式学习画像**：通过自然语言对话自动构建包含 ≥6 维度（知识基础、认知风格、易错点偏好、学习节奏、动机目标、模态偏好）的动态学生画像
- **🤖 多智能体协同**：9+ 角色 Agent 协同工作（认知诊断、内容专家、教学设计、习题生成、多媒体编译、Manim 视频、代码沙箱、质量审核、防幻觉）
- **📚 多模态资源生成**：≥5 种类型 — 课程讲解文档、知识点思维导图、练习题库、拓展阅读材料、教学视频/动画、代码实操案例
- **🛤️ 学习路径规划**：基于知识图谱的拓扑排序 + 画像驱动的精准推送
- **💬 智能辅导**：即时、多模态的答疑解惑（加分项）
- **📊 学习效果评估**：多维度评估 + 动态调整推送策略（加分项）

## 技术栈

| 层级 | 选型 |
|---|---|
| 后端 | Python 3.11 + FastAPI + uvicorn + WebSocket |
| 前端 | Next.js 16 + React 19 + TypeScript + Tailwind CSS |
| Agent 框架 | 自研 BaseAgent + BaseCapability + BaseTool（借鉴 DeepTutor）|
| LLM Provider | OpenAI-compatible + Anthropic + DeepSeek（多 Provider） |
| RAG | LlamaIndex |
| 视频生成 | Manim Community + 两阶段 AI + 代码重试 |
| 知识图谱 | YAML 静态定义 + NetworkX 内存遍历 |
| 存储 | SQLite + JSON 文件 |

## 致谢与开源引用

本项目在架构设计上参考了以下优秀开源项目：

- **[DeepTutor](https://github.com/HKUDS/DeepTutor)** — Agent 架构、StreamBus、CapabilityRegistry、PromptManager、MathAnimatorPipeline 等核心抽象的设计灵感来源
- **[ManimCat](https://github.com/MathInspector/ManimCat)** — Manim 视频生成的两阶段 AI、StaticGuard、CodeRetry 模式
- **[Manim Community](https://www.manim.community/)** — 数学/概念可视化动画引擎
- **[LlamaIndex](https://www.llamaindex.ai/)** — RAG 框架
- **[Next.js](https://nextjs.org)** + **[Tailwind CSS](https://tailwindcss.com)** — 前端框架

详见 `docs/architecture.md`。

## 快速开始

### 后端

```bash
cd E:\github\Tutor
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env 填入 LLM API Key
python -m tutor api
# 服务将监听 http://localhost:8000
```

### 前端

```bash
cd frontend
npm install
npm run dev
# 浏览器访问 http://localhost:3000
```

### 健康检查

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok","version":"0.1.0"}
```

## 项目结构

```
Tutor/
├── backend/                       # Python 后端
│   └── tutor/                     # 主包
│       ├── core/                  # 核心抽象（StreamBus, Capability, Tool）
│       ├── agents/                # Agent 实现
│       ├── capabilities/          # Capability 编排层
│       ├── runtime/               # Orchestrator + Registry
│       ├── services/              # 服务层（LLM, RAG, Profile, KG, Manim）
│       ├── tools/                 # 工具层
│       ├── api/                   # FastAPI 路由
│       ├── knowledge_base/        # 课程知识库
│       └── prompts/               # Prompt YAML（中英）
├── frontend/                      # Next.js 前端
│   ├── app/                       # 路由
│   ├── components/                # 组件
│   └── hooks/                     # 自定义 Hooks
├── docs/                          # 文档
└── data/                          # 运行时数据
```

## 路线图

- [x] **Phase 1**: 项目脚手架 + 核心基础设施（BaseAgent, StreamBus, Orchestrator, FastAPI）
- [ ] **Phase 2**: 画像系统 + 多类型资源生成（6 种）
- [ ] **Phase 3**: 学习路径规划 + 智能辅导 + 效果评估
- [ ] **Phase 4**: 前端 UI 完整实现（Chat, Profile, Resource Cards, Knowledge Hub）
- [ ] **Phase 5**: 知识库扩充 + 测试 + 打磨

## 许可证

MIT License — 详见 [LICENSE](./LICENSE)

## 联系方式

项目作者: Tian Mingyu
